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
"""

import re
from collections import namedtuple

import frappe
import requests
from frappe import _
from frappe.utils import add_days, date_diff, flt, getdate, today

from al_qubaisi_modifications.api import resolve_branch_customer

ENDPOINT = "adsr_item_summery"
SALE = "Sale"
VOID_SALE = "Void_Sale"

# Longer ranges are refused by the API ("Date interwell should less than or equal to 1 month").
MAX_RANGE_DAYS = 31

# (connect, read). A missing timeout would wedge the single background worker
# indefinitely on a hung socket, taking the other daily jobs down with it.
REQUEST_TIMEOUT = (10, 60)

# Characters that are unsafe or awkward inside a document name.
UNSAFE_IN_CODE = re.compile(r"[<>/\\%\"'`\[\]{}|#?*:;]+")
WHITESPACE = re.compile(r"\s+")


def _collapse(value):
	"""Trim and collapse internal whitespace. POS names carry trailing spaces."""
	return WHITESPACE.sub(" ", (value or "").strip())


def _normalise(value):
	"""Comparison key for item names.

	Deliberately conservative: case and whitespace only. Trailing punctuation and
	``-A`` / ``_B`` style suffixes are left alone because they distinguish genuine
	size and variant differences in this menu ("7 UP-A" vs "7UP _B").
	"""
	return _collapse(value).casefold()


def get_settings():
	"""Return LithosPOS Settings, or None when the integration is switched off."""
	settings = frappe.get_cached_doc("LithosPOS Settings")
	return settings if settings.enabled else None


# What the sync needs to know about one store for one run.
StoreTarget = namedtuple("StoreTarget", ["store_id", "branch", "company"])


def get_store_targets(settings, store_id=None):
	"""Return the branches wired to a LithosPOS store.

	The mapping lives on Branch (``custom_lithos_store_id``), so adding an outlet
	is a change to that Branch rather than to this integration's settings. The
	field is unique, so a store id can never resolve to two branches.
	"""
	filters = {"custom_lithos_store_id": ("is", "set")}
	if store_id is not None:
		filters["custom_lithos_store_id"] = str(store_id).strip()
	else:
		filters["custom_lithos_sync_enabled"] = 1

	targets = []
	for row in frappe.get_all(
		"Branch",
		filters=filters,
		fields=["name", "custom_lithos_store_id", "custom_cost_center"],
		order_by="name",
	):
		store = (row.custom_lithos_store_id or "").strip()
		if not store:
			continue
		# Prefer the company implied by the branch's own cost centre; most branches
		# have none set, hence the configured fallback.
		company = (
			frappe.db.get_value("Cost Center", row.custom_cost_center, "company")
			if row.custom_cost_center
			else None
		)
		targets.append(StoreTarget(store, row.name, company or settings.default_company))

	return targets


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


def sale_groups(groups, store_id=None):
	"""Index the ``Sale`` groups by date, ignoring voids and flagging oddities."""
	by_date, unknown = {}, set()
	for group in groups:
		group_type = group.get("Type")
		if group_type == SALE:
			by_date[getdate(group.get("Date"))] = group
		elif group_type != VOID_SALE:
			unknown.add(group_type)

	if unknown:
		frappe.log_error(
			title=f"LithosPOS: unknown group type ({store_id})",
			message=f"Ignored unrecognised Type value(s): {sorted(unknown)}",
		)
	return by_date


class ItemResolver:
	"""Maps a POS (barcode, name) pair onto an ERPNext Item, creating one if needed.

	Lookups are prefetched once per store rather than issued per line.
	"""

	def __init__(self, item_group):
		self.item_group = item_group
		self.stock_uom = frappe.db.get_single_value("Stock Settings", "stock_uom") or "Nos"
		self.created = []
		self._resolved = {}

		enabled = {}
		for row in frappe.get_all("Item", filters={"disabled": 0}, fields=["name", "item_name"]):
			enabled[row.name] = row.item_name

		self._by_name = {}
		for code, item_name in enabled.items():
			self._by_name.setdefault(_normalise(item_name), []).append(code)
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
			if barcode and row.parent in enabled:
				self._by_barcode.setdefault(barcode, row.parent)

	def resolve(self, barcode, pos_name):
		key = ((barcode or "").strip(), _normalise(pos_name))
		if key not in self._resolved:
			self._resolved[key] = self._lookup(*key) or self._create(pos_name)
		return self._resolved[key]

	def _lookup(self, barcode, name_key):
		if barcode and barcode in self._by_barcode:
			return self._by_barcode[barcode]

		candidates = self._by_name.get(name_key)
		if not candidates:
			return None
		if len(candidates) > 1:
			frappe.logger("lithos").warning(
				f"Ambiguous item name {name_key!r} -> {candidates}; using {candidates[0]}"
			)
		return candidates[0]

	def _create(self, pos_name):
		clean = _collapse(pos_name)
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

		self._by_name.setdefault(_normalise(clean), []).append(item.name)
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
	"""Return (child rows, expected net total) for one day's Sale group."""
	rows, expected = [], 0.0

	for entry in group.get("Items") or []:
		pos_name = _collapse(entry.get("Item"))
		if not pos_name:
			continue

		qty = flt(entry.get("Qty"))
		net = flt(entry.get("Net"))
		tax = flt(entry.get("Tax"))

		if qty > 0:
			rate = net / qty
		else:
			# Not seen in 14 months of data, but amount = qty * rate would silently
			# drop the value from the day total, so carry it on a single unit.
			qty, rate = 1.0, net

		rows.append(
			{
				"item_code": resolver.resolve(entry.get("Barcode"), pos_name),
				"pos_item_name": pos_name[:140],
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
		expected += net

	return rows, expected


def post_day(target, day, group, resolver):
	"""Create and submit one Branch Sales. Returns the doc, or None if skipped."""
	day = getdate(day)

	# Keyed on the store, not the branch: nothing guarantees the mapping is 1:1,
	# and a branch-keyed check would silently skip a second store every night.
	if frappe.db.exists(
		"Branch Sales",
		{"lithos_store_id": target.store_id, "posting_date": day, "docstatus": ("<", 2)},
	):
		return None

	rows, expected = build_rows(group, resolver)
	if not rows:
		return None

	doc = frappe.get_doc(
		{
			"doctype": "Branch Sales",
			"branch": target.branch,
			"company": target.company,
			"posting_date": day,
			"customer": resolve_branch_customer(target.branch),
			"lithos_store_id": target.store_id,
			"items": rows,
			# cost_center and item_name are left unset - fetch_from populates them
			# server-side during validate.
		}
	)
	doc.insert(ignore_permissions=True)

	if resolver.created:
		doc.add_comment(
			"Comment",
			_("Created {0} new Item(s) from LithosPOS: {1}").format(
				len(resolver.created), ", ".join(resolver.created)
			),
		)

	# Reconcile against the item rows, not the group header - the header's own tax
	# disagrees with its items by ~0.02, which would false-alarm on every run.
	if flt(expected, 2) != flt(doc.total, 2):
		frappe.log_error(
			title=f"LithosPOS: totals mismatch ({target.store_id} {day})",
			message=(
				f"Expected net {flt(expected, 2)} but the document computed {flt(doc.total, 2)}. "
				f"Left as draft {doc.name} for review."
			),
			reference_doctype="Branch Sales",
			reference_name=doc.name,
		)
		return doc

	doc.submit()
	return doc


def sync_day(target, day, settings):
	"""Fetch and post a single day for a single store."""
	day = getdate(day)
	groups = fetch_item_summary(settings, target.store_id, day, day)
	# Filter on the echoed Date rather than trusting the range.
	group = sale_groups(groups, target.store_id).get(day)
	if not group:
		return None
	return post_day(target, day, group, ItemResolver(settings.pos_item_group))


def sync_date(day):
	"""Sync one calendar day across every enabled store."""
	settings = get_settings()
	if not settings:
		return

	day = getdate(day)
	for target in get_store_targets(settings):
		try:
			doc = sync_day(target, day, settings)
			frappe.db.commit()
			if doc:
				frappe.logger("lithos").info(f"{target.store_id} {day} -> {doc.name}")
		except Exception:
			# Roll back first: log_error inserts inside the current transaction, so
			# rolling back afterwards would discard the log along with the failure.
			frappe.db.rollback()
			frappe.log_error(
				title=f"LithosPOS sync failed ({target.store_id} {day})",
				message=frappe.get_traceback(with_context=True),
			)
			frappe.db.commit()


def sync_yesterday():
	"""Scheduled entry point. Runs at 02:00 Asia/Dubai."""
	# frappe.utils.today() is site-timezone aware; datetime.date.today() is not, and
	# at 02:00 Dubai the server's UTC date is still the previous day.
	sync_date(add_days(today(), -1))


@frappe.whitelist()
def sync_range(store_id, from_date, to_date):
	"""Backfill or re-pull a date range for one store. Already-synced days are skipped."""
	frappe.only_for("System Manager")

	settings = get_settings()
	if not settings:
		frappe.throw(_("LithosPOS Settings is disabled."))

	targets = get_store_targets(settings, store_id)
	if not targets:
		frappe.throw(
			_("No Branch has LithosPOS Store ID {0}. Set it on the Branch record.").format(store_id)
		)
	target = targets[0]

	from_date, to_date = getdate(from_date), getdate(to_date)
	created = []
	window_start = from_date

	while window_start <= to_date:
		window_end = min(add_days(window_start, MAX_RANGE_DAYS - 1), to_date)
		groups = sale_groups(
			fetch_item_summary(settings, target.store_id, window_start, window_end),
			target.store_id,
		)

		for day in sorted(groups):
			if not (from_date <= day <= to_date):
				continue
			try:
				doc = post_day(target, day, groups[day], ItemResolver(settings.pos_item_group))
				frappe.db.commit()
				if doc:
					created.append(doc.name)
			except Exception:
				frappe.db.rollback()
				frappe.log_error(
					title=f"LithosPOS backfill failed ({target.store_id} {day})",
					message=frappe.get_traceback(with_context=True),
				)
				frappe.db.commit()

		window_start = add_days(window_end, 1)

	return created
