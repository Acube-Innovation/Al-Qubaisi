# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

"""LithosPOS ADSR -> Branch Sales sync.

Pulls the per-day item summary from the LithosPOS openApi and posts it as a
submitted Branch Sales, one document per store per trading day.

Behaviour of the upstream feed, verified against 14 months of live responses for
stores 141 / 126 / 142. Several points are counter-intuitive:

* ``adsr_item_summery`` wraps its payload in ``{"status", "data", "msg"}``.
  The sibling ``/adsr`` endpoint does not - it returns a bare array. The useful
  error text arrives in the body of an HTTP 400, so the body is read first.
* ``data`` is grouped by ``(Date, Type)``. Only ``Sale`` and ``Void_Sale`` have
  ever been observed, and they are *disjoint*: ``Sale`` already excludes voids.
  ``Void_Sale`` is therefore ignored, not subtracted - subtracting double-counts.
* The group-level ``Net`` is tax **inclusive** while an item-level ``Net`` is tax
  **exclusive**, so an item ``Net`` maps straight onto Branch Sales Item ``amount``.
* The group header disagrees with its own items by a fil or two, so reconciliation
  is done against the sum of the item rows, never against the header.
* Ranges longer than 31 days are rejected outright.

How a run works (M4, M5). Every trigger - the 02:00 schedule, a Quick Sync, a
backfill, an hourly retry - creates a Lithos Sync Run and queues one background job
per store, so one slow or failing store never holds up the others. A job pulls its
range in 31-day windows and writes each store-day in its own transaction, recording
the result on that day's Lithos Sync Log. The store row is locked while a day is
written, so two runs that overlap can never post the same day twice.
"""

import json
import re
from collections import Counter, namedtuple

import frappe
import requests
from frappe import _
from frappe.utils import (
	add_days,
	add_to_date,
	cint,
	date_diff,
	escape_html,
	flt,
	get_url,
	getdate,
	now_datetime,
	strip_html,
	today,
)

from al_qubaisi_modifications.al_qubaisi_modifications.doctype.lithos_sync_log.lithos_sync_log import log_name
from al_qubaisi_modifications.api import resolve_branch_customer

ENDPOINT = "adsr_item_summery"
SALE = "Sale"
VOID_SALE = "Void_Sale"

# Longer ranges are refused by the API ("Date interwell should less than or equal to 1 month").
MAX_RANGE_DAYS = 31

# (connect, read). A missing timeout would wedge the single background worker
# indefinitely on a hung socket, taking the other daily jobs down with it.
REQUEST_TIMEOUT = (10, 60)

# One store's whole range runs in one job: a 301-day backfill is ten pulls and 301 posts.
JOB_TIMEOUT = 3600

LITHOS_ROLES = ("System Manager", "Lithos Manager")

# What a store does with a Lithos item that has no ERPNext item (M3).
HOLD = "Hold"
SUSPENSE = "Suspense"
AUTO_CREATE = "Auto-create"

# Lithos Sync Run triggers, and what their store jobs do.
SCHEDULED, QUICK, BACKFILL, RETRY, REPOST_HELD, REPOST, LEDGER = (
	"Scheduled",
	"Quick",
	"Backfill",
	"Retry",
	"Re-post held",
	"Re-post",
	"Ledger",
)
PULL, FROM_LOGS, MOVE, POST_LEDGER = "pull", "from_logs", "move", "ledger"

# Lithos Sync Log statuses.
POSTED = "Posted"
DRAFT = "Draft"
HELD = "Held"
CHANGED = "Changed"
NO_SALES = "No sales"
FAILED = "Failed"

# Lithos Item Map statuses the sync acts on (see lithos_item_map.py).
MAP_UNMAPPED = "Unmapped"
MAP_IGNORED = "Ignored"

# How one line resolved.
MAPPED, UNMAPPED, IGNORED, SUSPENDED = "mapped", "unmapped", "ignored", "suspended"
Line = namedtuple("Line", ["item_code", "state"])

# Characters that are unsafe or awkward inside a document name.
UNSAFE_IN_CODE = re.compile(r"[<>/\\%\"'`\[\]{}|#?*:;]+")
WHITESPACE = re.compile(r"\s+")


def collapse(value):
	"""Trim and collapse internal whitespace. POS names carry trailing spaces."""
	return WHITESPACE.sub(" ", (value or "").strip())


def normalise(value):
	"""Comparison key for item names.

	Deliberately conservative: case and whitespace only. Trailing punctuation and
	``-A`` / ``_B`` style suffixes are left alone because they distinguish genuine
	size and variant differences in this menu ("7 UP-A" vs "7UP _B").
	"""
	return collapse(value).casefold()


def get_settings():
	"""Return LithosPOS Settings, or None when the integration is switched off."""
	settings = frappe.get_cached_doc("LithosPOS Settings")
	return settings if settings.enabled else None


# What the sync needs to know about one store for one run.
StoreTarget = namedtuple(
	"StoreTarget",
	["store_id", "branch", "company", "customer", "cost_center", "sync_from_date", "sync_policy"],
)


def get_store_targets(store_ids=None):
	"""Return the Lithos Store records to pull: the named ones, else every enabled one.

	The mapping lives on Lithos Store, one record per store id, so adding an outlet
	is a new record rather than a change to this integration's settings. Several
	stores may post into one company; a store never posts into two.
	"""
	if store_ids is None:
		filters = {"sync_enabled": 1}
	else:
		if isinstance(store_ids, str | int):
			store_ids = [store_ids]
		filters = {"name": ("in", [str(store_id).strip() for store_id in store_ids])}

	return [
		StoreTarget(
			row.name,
			row.branch,
			row.company,
			row.customer,
			row.cost_center,
			row.sync_from_date,
			row.sync_policy,
		)
		for row in frappe.get_all(
			"Lithos Store",
			filters=filters,
			fields=["name", "branch", "company", "customer", "cost_center", "sync_from_date", "sync_policy"],
			order_by="name",
		)
	]


def lock_store(store_id):
	"""Hold the store's row until the transaction ends, so its days are written one at a time."""
	frappe.db.sql("select name from `tabLithos Store` where name = %s for update", store_id)


def record_status(target, day, status, message=None):
	"""Keep the store's latest result on the store itself. Backfills never move it backwards."""
	last = frappe.db.get_value("Lithos Store", target.store_id, "last_synced_date")
	if last and getdate(last) > getdate(day):
		return
	frappe.db.set_value(
		"Lithos Store",
		target.store_id,
		{
			"last_synced_date": getdate(day),
			"last_sync_status": status,
			"last_sync_message": (message or "")[:1000],
		},
		update_modified=False,
	)


def fetch_item_summary(settings, store_id, from_date, to_date):
	"""Return the raw ``data`` groups for a store over an inclusive date range."""
	if date_diff(to_date, from_date) >= MAX_RANGE_DAYS:
		frappe.throw(_("LithosPOS accepts a maximum range of {0} days.").format(MAX_RANGE_DAYS))

	url = f"{(settings.base_url or '').rstrip('/')}/{store_id}/{ENDPOINT}"
	response = requests.post(
		url,
		json={"from": f"{getdate(from_date)} 00:00:00", "to": f"{getdate(to_date)} 23:59:59"},
		headers={
			"Content-Type": "application/json",
			"key": settings.get_password("api_key"),
			"keyid": settings.get_password("api_keyid"),
		},
		timeout=REQUEST_TIMEOUT,
	)

	# Read the body before the status code raises - a 400 carries the reason.
	try:
		payload = response.json()
	except ValueError:
		response.raise_for_status()
		frappe.throw(
			_("LithosPOS returned a non-JSON response for store {0} ({1}).").format(
				store_id, response.status_code
			)
		)

	# A failure can arrive with HTTP 200, so trust "status" rather than the code.
	if payload.get("status") != "success":
		frappe.throw(
			_("LithosPOS rejected the request for store {0}: {1}").format(
				store_id, payload.get("msg") or response.status_code
			)
		)

	return payload.get("data") or []


def split_groups(groups, store_id=None):
	"""Index the ``Sale`` and ``Void_Sale`` groups by date, flagging any other type."""
	sales, voids, unknown = {}, {}, set()
	for group in groups:
		group_type = group.get("Type")
		if group_type == SALE:
			sales[getdate(group.get("Date"))] = group
		elif group_type == VOID_SALE:
			voids[getdate(group.get("Date"))] = group
		else:
			unknown.add(group_type)

	if unknown:
		frappe.log_error(
			title=f"LithosPOS: unknown group type ({store_id})",
			message=f"Ignored unrecognised Type value(s): {sorted(unknown)}",
		)
	return sales, voids


def item_totals(group):
	"""(lines, net, tax) of a group's item rows, straight from the payload."""
	lines, net, tax = 0, 0.0, 0.0
	for entry in (group or {}).get("Items") or []:
		if collapse(entry.get("Item")):
			lines += 1
			net += flt(entry.get("Net"))
			tax += flt(entry.get("Tax"))
	return lines, net, tax


class ItemResolver:
	"""Maps a POS (barcode, name) pair onto an ERPNext Item for one store.

	A Lithos Item Map row decides first: one for this store, then one for all
	stores. A row set to Ignored drops the line. Without a row, the Item Barcode
	table and then the item name are tried, and a match is recorded as an
	all-stores row so it shows up for review and is reused from then on.

	A name nothing matches is handled by the store's policy (M3). Hold leaves the
	line unresolved, which holds the day. Suspense posts it to the suspense item.
	Auto-create makes a POS- item. All three record an Unmapped or Auto-created row
	for the Lithos Item Workbench. A row whose item someone has cleared is never
	guessed again, except by Auto-create, which posts a guess without recording it.

	Lookups are prefetched once per store rather than issued per line. Call
	start_day() before each day, and drop the resolver after a rollback: its caches
	may name rows and items the rollback removed.
	"""

	def __init__(self, settings, store_id=None, policy=AUTO_CREATE):
		self.item_group = settings.pos_item_group
		self.store_id = store_id
		self.policy = policy
		self.suspense_item = settings.suspense_item
		if policy == SUSPENSE and not self.suspense_item:
			frappe.throw(
				_("Store {0} uses the Suspense policy, but LithosPOS Settings has no Suspense Item.").format(
					store_id
				)
			)
		self.stock_uom = frappe.db.get_single_value("Stock Settings", "stock_uom") or "Nos"
		self.created = []
		self._resolved = {}
		# Map row name -> the barcode Lithos sent with it, for record_sold().
		self._used = {}

		self._enabled, self._boms = {}, {}
		for row in frappe.get_all(
			"Item", filters={"disabled": 0}, fields=["name", "item_name", "default_bom"]
		):
			self._enabled[row.name] = row.item_name
			self._boms[row.name] = row.default_bom

		# (store or "", match_key) -> map row. Rows with no item, or pointing at a
		# disabled item, are kept: they block a second row being recorded for the
		# same name, but never decide the item.
		self._maps = {}
		for row in frappe.get_all(
			"Lithos Item Map",
			or_filters=[["store", "=", store_id or ""], ["store", "is", "not set"]],
			fields=["name", "store", "match_key", "item_code", "status"],
		):
			self._maps[(row.store or "", row.match_key)] = row

		self._by_name = {}
		for code, item_name in self._enabled.items():
			self._by_name.setdefault(normalise(item_name), []).append(code)
		for codes in self._by_name.values():
			codes.sort()

		# Only the Item Barcode table is a valid barcode index. Matching a POS
		# barcode against tabItem.name looks plausible and is flatly wrong: the two
		# numbering spaces overlap by coincidence, so POS 1219 "SOFT DRINKS" would
		# resolve to Item 1219 "UNICORN BLUE CREAM".
		self._by_barcode = {}
		for row in frappe.get_all(
			"Item Barcode", filters={"parenttype": "Item"}, fields=["barcode", "parent"]
		):
			barcode = (row.barcode or "").strip()
			if barcode and row.parent in self._enabled:
				self._by_barcode.setdefault(barcode, row.parent)

	def start_day(self):
		"""Forget which map rows and new items the previous day used."""
		self.created = []
		self._used = {}

	def resolve(self, barcode, pos_name):
		"""Return a Line: the item to post, and whether it was mapped, unmapped, ignored or suspended."""
		barcode, name_key = (barcode or "").strip(), normalise(pos_name)
		key = (barcode, name_key)
		if key not in self._resolved:
			self._resolved[key] = self._resolve(pos_name, barcode, name_key)
		line, row_name = self._resolved[key]
		if row_name:
			self._used[row_name] = barcode
		return line

	def mapped_item(self, pos_name):
		"""The item a map row decides for this store, or None. Never guesses or records."""
		row = self._row(normalise(pos_name))
		if row and row.status != MAP_IGNORED and row.item_code in self._enabled:
			return row.item_code
		return None

	def has_bom(self, item_code):
		return bool(self._boms.get(item_code))

	def _row(self, name_key):
		return self._maps.get((self.store_id or "", name_key)) or self._maps.get(("", name_key))

	def _resolve(self, pos_name, barcode, name_key):
		"""Return (Line, name of the map row it came from)."""
		row = self._row(name_key)
		if row:
			if row.status == MAP_IGNORED:
				return Line(None, IGNORED), row.name
			if row.item_code in self._enabled:
				return Line(row.item_code, MAPPED), row.name
			if self.policy == AUTO_CREATE:
				item_code = self._lookup(barcode, name_key)[0] or self._create(pos_name)
				return Line(item_code, MAPPED), row.name
			return self._unresolved(), row.name

		item_code, status = self._lookup(barcode, name_key)
		if not item_code and self.policy == AUTO_CREATE:
			item_code, status = self._create(pos_name), "Auto-created"
		row_name = self._record(pos_name, barcode, name_key, item_code, status or MAP_UNMAPPED)
		return (Line(item_code, MAPPED) if item_code else self._unresolved()), row_name

	def _unresolved(self):
		if self.policy == SUSPENSE:
			return Line(self.suspense_item, SUSPENDED)
		return Line(None, UNMAPPED)

	def _record(self, pos_name, barcode, name_key, item_code, status):
		row = frappe.get_doc(
			{
				"doctype": "Lithos Item Map",
				"lithos_item_name": collapse(pos_name)[:140],
				"barcode": barcode,
				"item_code": item_code,
				"status": status,
			}
		)
		row.flags.keep_status = True
		row.insert(ignore_permissions=True)
		self._maps[("", name_key)] = frappe._dict(name=row.name, item_code=item_code, status=row.status)
		return row.name

	def _lookup(self, barcode, name_key):
		if barcode and barcode in self._by_barcode:
			return self._by_barcode[barcode], "Matched by barcode"

		candidates = self._by_name.get(name_key)
		if not candidates:
			return None, None
		if len(candidates) > 1:
			frappe.logger("lithos").warning(
				f"Ambiguous item name {name_key!r} -> {candidates}; using {candidates[0]}"
			)
		return candidates[0], "Matched by name"

	def record_sold(self, day):
		"""Stamp first and last sold on every map row used today, and keep the barcode.

		A plain UPDATE: these are bookkeeping columns, and a full save per line would
		bump ``modified`` on every row every night.
		"""
		for name, barcode in self._used.items():
			frappe.db.sql(
				"""
				update `tabLithos Item Map`
				set first_sold = least(ifnull(first_sold, %(day)s), %(day)s),
					last_sold = greatest(ifnull(last_sold, %(day)s), %(day)s),
					barcode = if(ifnull(barcode, '') = '', %(barcode)s, barcode)
				where name = %(name)s
				""",
				{"day": getdate(day), "barcode": barcode or "", "name": name},
			)

	def _create(self, pos_name):
		clean = collapse(pos_name)
		item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": self._next_code(clean),
				"item_name": clean[:140],
				"item_group": self.item_group,
				"stock_uom": self.stock_uom,
				"is_stock_item": 0,
				"is_sales_item": 1,
				"is_purchase_item": 0,
				"description": _("Created automatically from LithosPOS."),
			}
		).insert(ignore_permissions=True)

		self._enabled[item.name] = item.item_name
		self._by_name.setdefault(normalise(clean), []).append(item.name)
		self.created.append(item.name)
		return item.name

	def _next_code(self, clean):
		"""A ``POS-`` prefix keeps generated codes clear of the numeric item master."""
		base = f"POS-{UNSAFE_IN_CODE.sub('', clean).upper()}".strip()[:130] or "POS-ITEM"
		code, suffix = base, 1
		while frappe.db.exists("Item", code):
			suffix += 1
			code = f"{base}-{suffix}"
		return code


def build_rows(group, resolver):
	"""Resolve one day's Sale group into Branch Sales Item rows.

	Returns ``rows`` and their net ``expected``, plus what keeps lines out: the
	``unmapped`` Lithos names, the ``no_bom`` items (and ``no_bom_lines``), the
	count of ``suspended`` lines, and the net of ``ignored`` lines.
	"""
	built = frappe._dict(
		rows=[], expected=0.0, unmapped=[], suspended=0, no_bom=[], no_bom_lines=0, ignored=0.0
	)

	for entry in group.get("Items") or []:
		# Cut to the stored width up front so the map row's key matches the next lookup.
		pos_name = collapse(entry.get("Item"))[:140]
		if not pos_name:
			continue

		qty = flt(entry.get("Qty"))
		net = flt(entry.get("Net"))
		tax = flt(entry.get("Tax"))

		line = resolver.resolve(entry.get("Barcode"), pos_name)
		if line.state == IGNORED:
			built.ignored += net
			continue
		if line.state == UNMAPPED:
			built.unmapped.append(pos_name)
			continue
		if line.state == SUSPENDED:
			built.suspended += 1
		elif not resolver.has_bom(line.item_code):
			built.no_bom_lines += 1
			if line.item_code not in built.no_bom:
				built.no_bom.append(line.item_code)

		if qty > 0:
			rate = net / qty
		else:
			# Not seen in 14 months of data, but amount = qty * rate would silently
			# drop the value from the day total, so carry it on a single unit.
			qty, rate = 1.0, net

		built.rows.append(
			{
				"item_code": line.item_code,
				"pos_item_name": pos_name,
				"qty": qty,
				# Left unrounded on purpose. BranchSales.validate recomputes
				# amount = qty * rate, and rounding rate to the 2dp display precision
				# corrupts a quarter of all lines (worst case 0.13 on one line). The
				# column is decimal(21,9), so the full value survives the round trip.
				"rate": rate,
				# Derived per line rather than a flat 5. The POS rounds each line's tax
				# to 2dp, so a flat rate drifts against the POS figure - about 0.026 on
				# a 840 day. tax_amount = amount * (Tax/Net) reproduces Tax exactly.
				"tax_rate": (tax / net * 100.0) if net else 0.0,
				"discount_amount": 0,
				"discount_percent": 0,
			}
		)
		built.expected += net

	return built


def hold_reasons(built, hold_without_bom):
	reasons = []
	if built.unmapped:
		reasons.append(
			_("{0} Lithos item(s) not mapped: {1}").format(len(set(built.unmapped)), shorten(built.unmapped))
		)
	if hold_without_bom and built.no_bom:
		reasons.append(
			_("{0} item(s) without a default BOM: {1}").format(len(built.no_bom), shorten(built.no_bom))
		)
	return reasons


def shorten(names, limit=10):
	names = sorted(set(names))
	text = ", ".join(names[:limit])
	if len(names) > limit:
		text += " " + _("and {0} more").format(len(names) - limit)
	return text


def make_branch_sales(target, day, rows):
	return frappe.get_doc(
		{
			"doctype": "Branch Sales",
			"branch": target.branch,
			"company": target.company,
			"posting_date": day,
			"customer": target.customer or resolve_branch_customer(target.branch),
			# The store's cost center. A blank one is filled from the branch.
			"cost_center": target.cost_center,
			"lithos_store_id": target.store_id,
			"items": rows,
			# item_name is left unset - fetch_from populates it server-side.
		}
	)


def existing_sales(store_id, day):
	"""The day's live Branch Sales. Keyed on the store, not the branch: nothing
	guarantees the mapping is 1:1, and a branch-keyed check would silently skip a
	second store every night."""
	return frappe.db.get_value(
		"Branch Sales",
		{"lithos_store_id": store_id, "posting_date": getdate(day), "docstatus": ("<", 2)},
		["name", "docstatus", "total", "company"],
		as_dict=True,
	)


class RunContext:
	"""One store job's share of a Lithos Sync Run: its store, settings, resolver and counts."""

	def __init__(self, run, settings, target):
		self.run = run
		self.settings = settings
		self.target = target
		self.policy = target.sync_policy or settings.default_sync_policy or HOLD
		self.hold_without_bom = cint(settings.hold_without_bom)
		self.counts = Counter()
		self.fetch_failed = False
		# Branch Sales this run already took to the ledger; the catch-up leaves them alone.
		self.ledger_seen = set()
		self._resolver = None

	def day_resolver(self):
		if not self._resolver:
			self._resolver = ItemResolver(self.settings, self.target.store_id, self.policy)
		self._resolver.start_day()
		return self._resolver

	def reset(self):
		"""After a rollback: the resolver may remember rows and items that no longer exist."""
		self._resolver = None

	def count(self, log):
		status = {
			POSTED: "days_existing" if log.flags.existing else "days_posted",
			DRAFT: "days_draft",
			HELD: "days_held",
			CHANGED: "days_changed",
			NO_SALES: "days_no_sales",
			FAILED: "days_failed",
		}[log.status]
		self.counts[status] += 1
		if log.status == FAILED:
			return
		self.counts["lithos_net"] += flt(log.lithos_net)
		# A day re-checked after posting was counted by the run that posted it.
		if not log.flags.existing:
			self.counts["lines_unmapped"] += cint(log.unmapped_lines)
			self.counts["lines_without_bom"] += cint(log.lines_without_bom)
			self.counts["erpnext_net"] += flt(log.erpnext_net)
		if log.flags.ledger:
			from al_qubaisi_modifications.lithos_ledger import count

			count(self, log.flags.ledger)


def get_log(target, day):
	name = log_name(target.store_id, day)
	if frappe.db.exists("Lithos Sync Log", name):
		return frappe.get_doc("Lithos Sync Log", name)
	# new_doc, not get_doc(dict): callers rely on is_new() for a day seen for the first time.
	log = frappe.new_doc("Lithos Sync Log")
	log.update({"store": target.store_id, "posting_date": getdate(day)})
	return log


def finish_log(log, target):
	log.variance = flt(flt(log.lithos_net) - flt(log.erpnext_net) - flt(log.ignored_amount), 2)
	if log.status != FAILED:
		log.retry_count = 0
	log.save(ignore_permissions=True)
	record_status(target, log.posting_date, log.status, log.branch_sales or log.hold_reason or log.message)


def process_day(ctx, day, group, void_total=0.0):
	"""Post one store-day, or record why not, on its Lithos Sync Log. The caller commits."""
	target, day = ctx.target, getdate(day)
	lock_store(target.store_id)

	lines, net, tax = item_totals(group)
	log = get_log(target, day)
	log.update(
		{
			"company": target.company,
			"branch": target.branch,
			"sync_run": ctx.run,
			"rows_received": lines,
			"lithos_net": net,
			"lithos_tax": tax,
			"void_total": void_total,
			"payload": json.dumps(group, indent=1) if group else None,
			"last_attempt": now_datetime(),
			"hold_reason": None,
			"message": None,
			"error_log": None,
		}
	)

	existing = existing_sales(target.store_id, day)
	if existing:
		check_posted(log, existing)
		finish_log(log, target)
		return log

	log.update({"branch_sales": None, "erpnext_net": 0, "unmapped_lines": 0, "lines_without_bom": 0})
	if not group:
		log.update({"status": NO_SALES, "ignored_amount": 0})
		finish_log(log, target)
		return log

	resolver = ctx.day_resolver()
	built = build_rows(group, resolver)
	resolver.record_sold(day)
	log.update(
		{
			"policy": ctx.policy,
			"unmapped_lines": len(built.unmapped) + built.suspended,
			"lines_without_bom": built.no_bom_lines,
			"ignored_amount": built.ignored,
		}
	)

	reasons = hold_reasons(built, ctx.hold_without_bom)
	if reasons:
		log.update({"status": HELD, "hold_reason": "\n".join(reasons)})
		finish_log(log, target)
		return log

	if not built.rows:
		log.update({"status": NO_SALES, "message": _("Every line is set to Ignored in the Lithos Item Map.")})
		finish_log(log, target)
		return log

	doc = make_branch_sales(target, day, built.rows)
	doc.insert(ignore_permissions=True)

	if resolver.created:
		doc.add_comment(
			"Comment",
			_("Created {0} new Item(s) from LithosPOS: {1}").format(
				len(resolver.created), ", ".join(resolver.created)
			),
		)

	messages = []
	# Reconcile against the item rows, not the group header - the header's own tax
	# disagrees with its items by ~0.02, which would false-alarm on every run.
	if flt(built.expected, 2) != flt(doc.total, 2):
		messages.append(
			_("Expected net {0} but the document computed {1}. Left as draft for review.").format(
				flt(built.expected, 2), flt(doc.total, 2)
			)
		)
		log.status = DRAFT
	else:
		doc.submit()
		log.status = POSTED
		# Submitting posted the day to the ledger when its store is due (M6).
		log.flags.ledger = {"sales_invoice": doc.sales_invoice, "ledger_status": doc.ledger_status}
		ctx.ledger_seen.add(doc.name)

	if built.suspended:
		messages.append(
			_(
				"{0} line(s) posted to the suspense item {1}. Map them in the Lithos Item Workbench, then re-post the day."
			).format(built.suspended, resolver.suspense_item)
		)
	log.update({"branch_sales": doc.name, "erpnext_net": doc.total, "message": "\n".join(messages) or None})
	finish_log(log, target)
	return log


def check_posted(log, existing):
	"""Compare a day posted earlier with what Lithos says now. Never re-posts by itself."""
	log.flags.existing = True
	log.update({"branch_sales": existing.name, "erpnext_net": existing.total})
	# Ignored lines were left out on purpose; any other difference changed in Lithos.
	changed = flt(flt(log.lithos_net) - flt(existing.total) - flt(log.ignored_amount), 2) != 0

	if existing.docstatus == 0:
		log.update(
			{
				"status": DRAFT,
				"message": _("{0} is still a draft. Review it, then submit or delete it.").format(
					existing.name
				),
			}
		)
	elif changed:
		log.update(
			{
				"status": CHANGED,
				"message": _(
					"Lithos now shows {0} net against {1} posted. To take the new figures, cancel {2} and sync the day again."
				).format(flt(log.lithos_net, 2), flt(existing.total, 2), existing.name),
			}
		)
	else:
		log.status = POSTED


def error_text(exc):
	return (strip_html(str(exc)) or exc.__class__.__name__)[:1000]


def mark_failed(ctx, day, message, error_log):
	"""Record a failed day. Call after the rollback: it writes in the current transaction."""
	log = get_log(ctx.target, day)
	log.update(
		{
			"status": FAILED,
			"company": ctx.target.company,
			"branch": ctx.target.branch,
			"sync_run": ctx.run,
			"last_attempt": now_datetime(),
			"retry_count": cint(log.retry_count) + 1,
			"message": message,
			"error_log": error_log,
		}
	)
	finish_log(log, ctx.target)
	return log


def run_day(ctx, day, work):
	"""Run one store-day in its own transaction and count it. A failure is kept on the log."""
	try:
		log = work()
		frappe.db.commit()
	except Exception as e:
		# Roll back first: log_error inserts inside the current transaction, so
		# rolling back afterwards would discard the log along with the failure.
		frappe.db.rollback()
		ctx.reset()
		error = frappe.log_error(
			title=f"LithosPOS sync failed ({ctx.target.store_id} {getdate(day)})",
			message=frappe.get_traceback(with_context=True),
		)
		log = mark_failed(ctx, day, error_text(e), error.name)
		frappe.db.commit()
	ctx.count(log)
	return log


def days_between(from_date, to_date):
	day = getdate(from_date)
	while day <= getdate(to_date):
		yield day
		day = add_days(day, 1)


def pull_store(ctx, from_date, to_date):
	"""Pull a store's range from LithosPOS in 31-day windows and process every day."""
	target = ctx.target
	# Before go-live the store belonged to someone else's books, or did not trade.
	if target.sync_from_date:
		from_date = max(from_date, getdate(target.sync_from_date))

	window_start = from_date
	while window_start <= to_date:
		window_end = min(add_days(window_start, MAX_RANGE_DAYS - 1), to_date)
		try:
			sales, voids = split_groups(
				fetch_item_summary(ctx.settings, target.store_id, window_start, window_end),
				target.store_id,
			)
		except Exception as e:
			frappe.db.rollback()
			ctx.fetch_failed = True
			error = frappe.log_error(
				title=f"LithosPOS pull failed ({target.store_id} {window_start} to {window_end})",
				message=frappe.get_traceback(with_context=True),
			)
			# A posted day stays posted; only the days still missing are failures.
			for day in days_between(window_start, window_end):
				if not existing_sales(target.store_id, day):
					ctx.count(mark_failed(ctx, day, error_text(e), error.name))
			frappe.db.commit()
		else:
			for day in days_between(window_start, window_end):
				run_day(
					ctx,
					day,
					lambda day=day: process_day(ctx, day, sales.get(day), item_totals(voids.get(day))[1]),
				)
		window_start = add_days(window_end, 1)

	# Days that could not reach the ledger before - short stock, a recipe added since,
	# a Ledger From Date set in the past - are finished on every pull.
	from al_qubaisi_modifications.lithos_ledger import catch_up

	catch_up(ctx)


def repost_from_logs(ctx, from_date, to_date):
	"""Re-post a store's held days from the payload on their logs, without calling LithosPOS.

	Also rebuilds the days posted with lines on the suspense item, so lines mapped
	since then move to their real item.
	"""
	from al_qubaisi_modifications.lithos_repost import rebuild_day

	store = ctx.target.store_id
	dates = ("between", [getdate(from_date), getdate(to_date)])
	for row in frappe.get_all(
		"Lithos Sync Log",
		filters={"store": store, "status": HELD, "posting_date": dates},
		fields=["posting_date", "payload", "void_total"],
		order_by="posting_date",
	):
		group = json.loads(row.payload) if row.payload else None
		run_day(
			ctx,
			row.posting_date,
			lambda row=row, group=group: process_day(ctx, row.posting_date, group, row.void_total),
		)

	if not ctx.settings.suspense_item:
		return
	for row in frappe.get_all(
		"Lithos Sync Log",
		filters={"store": store, "status": POSTED, "unmapped_lines": (">", 0), "posting_date": dates},
		fields=["posting_date", "branch_sales"],
		order_by="posting_date",
	):
		run_day(ctx, row.posting_date, lambda row=row: rebuild_day(ctx, row.branch_sales))


def start_run(trigger, store_ids=None, from_date=None, to_date=None, mode=PULL):
	"""Create a Lithos Sync Run and queue one job per store. Returns the run's name."""
	targets = get_store_targets(store_ids)
	from_date, to_date = getdate(from_date), getdate(to_date)
	if mode == PULL and to_date >= getdate(today()):
		# Today is still trading. The next nightly run posts it once it has closed.
		to_date = getdate(add_days(today(), -1))
		if from_date > to_date:
			frappe.throw(_("Today is still trading: LithosPOS days are pulled once they have closed."))
	if from_date > to_date:
		frappe.throw(_("From Date cannot be after To Date."))

	run = frappe.get_doc(
		{
			"doctype": "Lithos Sync Run",
			"trigger": trigger,
			"status": "Queued",
			"requested_by": frappe.session.user,
			"from_date": from_date,
			"to_date": to_date,
			"store_ids": ", ".join(target.store_id for target in targets),
			"store_count": len(targets),
		}
	).insert(ignore_permissions=True)

	if not targets:
		run.db_set({"status": "Success", "finished_at": now_datetime(), "message": _("No store in scope.")})
		return run.name

	for target in targets:
		frappe.enqueue(
			"al_qubaisi_modifications.lithos.run_store_job",
			queue="long",
			timeout=JOB_TIMEOUT,
			enqueue_after_commit=True,
			run=run.name,
			store_id=target.store_id,
			from_date=str(from_date),
			to_date=str(to_date),
			mode=mode,
		)
	return run.name


def run_store_job(run, store_id, from_date, to_date, mode=PULL):
	"""Background job: one store of a Lithos Sync Run."""
	frappe.db.sql(
		"""update `tabLithos Sync Run` set status = 'Running', started_at = ifnull(started_at, %s)
		where name = %s and status = 'Queued'""",
		(now_datetime(), run),
	)
	frappe.db.commit()

	ctx, note = None, None
	try:
		targets = get_store_targets([store_id])
		if not targets:
			frappe.throw(_("No Lithos Store with Store ID {0}.").format(store_id))
		ctx = RunContext(run, frappe.get_single("LithosPOS Settings"), targets[0])
		if mode == MOVE:
			from al_qubaisi_modifications.lithos_repost import move_store

			move_store(ctx)
		elif mode == FROM_LOGS:
			repost_from_logs(ctx, from_date, to_date)
		elif mode == POST_LEDGER:
			from al_qubaisi_modifications.lithos_ledger import catch_up

			catch_up(ctx)
		else:
			pull_store(ctx, getdate(from_date), getdate(to_date))
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(
			title=f"LithosPOS sync job failed ({store_id})", message=frappe.get_traceback(with_context=True)
		)
		note = f"{store_id}: {error_text(e)}"
	finally:
		finish_store(run, store_id, ctx, note)


COUNT_FIELDS = (
	"days_posted",
	"days_existing",
	"days_draft",
	"days_no_sales",
	"days_held",
	"days_changed",
	"days_failed",
	"lines_unmapped",
	"lines_without_bom",
	"lithos_net",
	"erpnext_net",
	"invoices_posted",
	"consumption_pending",
)


def finish_store(run, store_id, ctx=None, note=None):
	"""Add one store's counts to its run. The last store to finish closes the run."""
	counts = ctx.counts if ctx else Counter()
	failed = bool(note or (ctx and ctx.fetch_failed))
	values = {field: counts.get(field, 0) for field in COUNT_FIELDS}
	values.update(run=run, failed=cint(failed))
	increments = ", ".join(f"`{field}` = ifnull(`{field}`, 0) + %({field})s" for field in COUNT_FIELDS)
	# The UPDATE locks the run's row until the commit, so finishing stores queue up here
	# and exactly one of them sees the last store done.
	frappe.db.sql(
		f"""update `tabLithos Sync Run` set {increments},
			stores_done = ifnull(stores_done, 0) + 1,
			stores_failed = ifnull(stores_failed, 0) + %(failed)s
		where name = %(run)s""",
		values,
	)
	done, total, message = frappe.db.get_value(
		"Lithos Sync Run", run, ["stores_done", "store_count", "message"]
	)
	if note:
		frappe.db.set_value(
			"Lithos Sync Run", run, "message", "\n".join(filter(None, [message, note])), update_modified=False
		)
	if done >= total:
		close_run(run)
	frappe.db.commit()
	frappe.publish_realtime(
		"lithos_sync_progress", {"run": run, "store": store_id, "done": done, "total": total}
	)


def close_run(run):
	doc = frappe.get_doc("Lithos Sync Run", run)
	if doc.store_count and cint(doc.stores_failed) >= doc.store_count:
		status = "Failed"
	elif any(
		cint(doc.get(field))
		for field in (
			"consumption_pending",
			"stores_failed",
			"days_failed",
			"days_held",
			"days_changed",
			"days_draft",
			"lines_unmapped",
		)
	):
		status = "Partial"
	else:
		status = "Success"
	doc.db_set({"status": status, "finished_at": now_datetime()}, update_modified=False)
	if doc.trigger in (SCHEDULED, RETRY) and status != "Success":
		send_alert(doc)


def send_alert(run):
	"""Email the alert recipients a summary of a run that needs attention."""
	settings = frappe.get_cached_doc("LithosPOS Settings")
	recipients = [
		email.strip() for email in re.split(r"[\s,;]+", settings.alert_recipients or "") if email.strip()
	]
	if not recipients:
		return

	problems = frappe.get_all(
		"Lithos Sync Log",
		filters={"sync_run": run.name, "status": ("in", [FAILED, HELD, CHANGED, DRAFT])},
		fields=["store", "posting_date", "status", "hold_reason", "message"],
		order_by="store, posting_date",
		limit=50,
	)
	rows = "".join(
		f"<tr><td>{escape_html(p.store)}</td><td>{p.posting_date}</td><td>{escape_html(p.status)}</td>"
		f"<td>{escape_html(p.hold_reason or p.message or '')}</td></tr>"
		for p in problems
	)
	counts = ", ".join(
		f"{label}: {cint(run.get(field))}"
		for label, field in (
			(_("posted"), "days_posted"),
			(_("held"), "days_held"),
			(_("failed"), "days_failed"),
			(_("changed"), "days_changed"),
			(_("draft"), "days_draft"),
		)
	)
	frappe.sendmail(
		recipients=recipients,
		subject=_("LithosPOS sync {0}: {1}").format(run.name, run.status),
		message=(
			f"<p>{escape_html(run.trigger)} run for {run.from_date} to {run.to_date}, "
			f"{cint(run.store_count)} store(s). {counts}.</p>"
			f"<table border='1' cellpadding='4' style='border-collapse: collapse'>"
			f"<tr><th>Store</th><th>Date</th><th>Status</th><th>Reason</th></tr>{rows}</table>"
			f"<p><a href='{get_url('/app/lithos-sync-console')}'>{_('Open the Lithos Sync Console')}</a></p>"
		),
		reference_doctype="Lithos Sync Run",
		reference_name=run.name,
	)


def sync_yesterday():
	"""Scheduled entry point. Runs at 02:00 Asia/Dubai.

	Pulls yesterday plus the look-back days in one call per store: a missed day is
	posted late, and a day that changed in Lithos after posting is flagged.
	"""
	settings = get_settings()
	if not settings:
		return
	# frappe.utils.today() is site-timezone aware; datetime.date.today() is not, and
	# at 02:00 Dubai the server's UTC date is still the previous day.
	yesterday = add_days(today(), -1)
	start_run(SCHEDULED, None, add_days(yesterday, -cint(settings.look_back_days)), yesterday)


def retry_failed():
	"""Hourly: retry failed days of enabled stores once their back-off (1, 2, 4 hours) has passed.

	Older failures are left to a Retry from the Sync Console.
	"""
	settings = get_settings()
	if not settings or not cint(settings.max_retries):
		return

	enabled = {target.store_id for target in get_store_targets()}
	now, due = now_datetime(), {}
	for row in frappe.get_all(
		"Lithos Sync Log",
		filters={
			"status": FAILED,
			"retry_count": ("<", cint(settings.max_retries)),
			"posting_date": (">=", add_days(today(), -(cint(settings.look_back_days) + 1))),
		},
		fields=["store", "posting_date", "retry_count", "last_attempt"],
	):
		wait = 2 ** max(cint(row.retry_count) - 1, 0)
		if row.store not in enabled or (row.last_attempt and add_to_date(row.last_attempt, hours=wait) > now):
			continue
		due.setdefault(row.store, []).append(getdate(row.posting_date))

	if due:
		dates = [day for days in due.values() for day in days]
		start_run(RETRY, sorted(due), min(dates), max(dates))


@frappe.whitelist()
def enqueue_sync_range(store_id, from_date, to_date):
	"""Backfill or re-pull a range for one store, for the button on Lithos Store.

	Days already posted are only re-checked against Lithos, never posted twice.
	"""
	frappe.only_for(LITHOS_ROLES)
	if not get_settings():
		frappe.throw(_("LithosPOS Settings is disabled."))
	return start_run(BACKFILL, [store_id], from_date, to_date)


@frappe.whitelist()
def test_connection(store_id=None):
	"""Pull yesterday for one store and report what came back. Posts nothing."""
	frappe.only_for(LITHOS_ROLES)
	targets = get_store_targets([store_id]) if store_id else get_store_targets()
	if not targets:
		frappe.throw(_("No Lithos Store to test with. Add one, or enable sync on one."))

	target, day = targets[0], getdate(add_days(today(), -1))
	sales, voids = split_groups(
		fetch_item_summary(frappe.get_single("LithosPOS Settings"), target.store_id, day, day),
		target.store_id,
	)
	lines, net, tax = item_totals(sales.get(day))
	return {
		"store": target.store_id,
		"date": str(day),
		"lines": lines,
		"net": flt(net, 2),
		"tax": flt(tax, 2),
		"void": flt(item_totals(voids.get(day))[1], 2),
	}
