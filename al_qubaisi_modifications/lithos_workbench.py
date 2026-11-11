# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

"""Lithos Item Workbench (M3): the queue of Lithos names to map, review or give a BOM.

The page reads the Lithos Item Map together with what was sold under each name and
what is held for it, and acts on the map rows. Held days are re-posted from the
Sync Console's method, so the Workbench and the Console share one way to do it.
"""

import json
from difflib import SequenceMatcher

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, today

from al_qubaisi_modifications.al_qubaisi_modifications.doctype.lithos_item_map.lithos_item_map import (
	scope_key,
)
from al_qubaisi_modifications.lithos import (
	HELD,
	LITHOS_ROLES,
	MAP_IGNORED,
	MAP_UNMAPPED,
	collapse,
	normalise,
)
from al_qubaisi_modifications.lithos_setup import MOTHER_COMPANY

# View -> which map rows it shows. "No BOM" is decided by the item, not the row.
VIEWS = {
	"Unmapped": (MAP_UNMAPPED,),
	"To review": ("Auto-created", "Matched by name"),
	"No BOM": None,
	"Ignored": (MAP_IGNORED,),
	"All": None,
}
SALES_DAYS = 90
MAX_ROWS = 500
MAX_SUGGESTIONS = 8


def scope_stores(company=None, store=None):
	"""Store ids in the filter, or None for every store."""
	if store:
		return [store]
	if company:
		return frappe.get_all("Lithos Store", filters={"company": company}, pluck="name")
	return None


def sales_stats(stores):
	"""(store, match_key) -> qty, net, days and last date sold over the last SALES_DAYS days."""
	condition = "and bs.lithos_store_id in %(stores)s" if stores else ""
	rows = frappe.db.sql(
		f"""
		select bs.lithos_store_id as store, bsi.pos_item_name as name, sum(bsi.qty) as qty,
			sum(bsi.amount) as net, count(distinct bs.posting_date) as days, max(bs.posting_date) as last_date
		from `tabBranch Sales Item` bsi
		join `tabBranch Sales` bs on bs.name = bsi.parent
		where bs.docstatus = 1 and bs.posting_date >= %(since)s and bs.lithos_store_id is not null
			{condition}
		group by bs.lithos_store_id, bsi.pos_item_name
		""",
		{"since": add_days(today(), -SALES_DAYS), "stores": tuple(stores or [""])},
		as_dict=True,
	)
	stats = {}
	for row in rows:
		# The same name arrives in more than one spelling; the map matches on the key.
		entry = stats.setdefault(
			(row.store, normalise(row.name)), frappe._dict(qty=0, net=0, days=0, last=None)
		)
		entry.qty += flt(row.qty)
		entry.net += flt(row.net)
		entry.days += cint(row.days)
		entry.last = max(filter(None, [entry.last, getdate(row.last_date)]))
	return stats


def held_stats(stores):
	"""(store, match_key) -> held days and net, read from the payload kept on each held log."""
	filters = {"status": HELD}
	if stores:
		filters["store"] = ("in", stores)
	stats = {}
	for log in frappe.get_all("Lithos Sync Log", filters=filters, fields=["store", "payload"]):
		seen = set()
		for entry in (json.loads(log.payload) if log.payload else {}).get("Items") or []:
			key = (log.store, normalise(collapse(entry.get("Item"))[:140]))
			held = stats.setdefault(key, frappe._dict(days=0, net=0))
			held.net += flt(entry.get("Net"))
			if key not in seen:
				seen.add(key)
				held.days += 1
	return stats


@frappe.whitelist()
def get_queue(view="Unmapped", company=None, store=None, search=None):
	"""Map rows for one view of the Workbench, with what was sold and held under each name."""
	frappe.only_for(LITHOS_ROLES)
	if view not in VIEWS:
		frappe.throw(_("Unknown view {0}.").format(view))

	stores = scope_stores(company, store)
	sold, held = sales_stats(stores), held_stats(stores)
	settings = frappe.get_cached_doc("LithosPOS Settings")
	items = {
		row.name: row
		for row in frappe.get_all(
			"Item", fields=["name", "item_name", "default_bom", "disabled", "is_stock_item", "item_group"]
		)
	}

	maps = frappe.get_all(
		"Lithos Item Map",
		fields=[
			"name",
			"lithos_item_name",
			"store",
			"barcode",
			"item_code",
			"status",
			"match_key",
			"first_sold",
			"last_sold",
			"mapped_by",
			"mapped_on",
		],
		order_by="lithos_item_name",
	)
	# A store's own row decides that store's lines, so an all-stores row does not count them.
	overridden = {(row.store, row.match_key) for row in maps if row.store}
	activity_stores = set(stores or []) | {key[0] for key in sold} | {key[0] for key in held}

	out, counts = [], dict.fromkeys(VIEWS, 0)
	needle = normalise(search) if search else None
	for row in maps:
		if row.store and stores is not None and row.store not in stores:
			continue
		row_stores = (
			[row.store]
			if row.store
			else sorted(
				s
				for s in activity_stores
				if (s, row.match_key) not in overridden and (stores is None or s in stores)
			)
		)
		row.update(qty=0, net=0, days=0, last=None, held_days=0, held_net=0, stores=[])
		for s in row_stores:
			sale, hold = sold.get((s, row.match_key)), held.get((s, row.match_key))
			if sale:
				row.qty += sale.qty
				row.net += sale.net
				row.days += sale.days
				row.last = max(filter(None, [row.last, sale.last]))
			if hold:
				row.held_days += hold.days
				row.held_net += hold.net
			if sale or hold:
				row.stores.append(s)
		# With a company or store filter, an all-stores row shows only if that scope sold it.
		if stores is not None and not row.store and not row.stores:
			continue

		item = items.get(row.item_code)
		row.item_name = item.item_name if item else None
		row.item_disabled = bool(item and item.disabled)
		row.default_bom = item.default_bom if item else None
		row.is_pos_item = bool(item and is_pos_item(item, settings))
		row.qty, row.net, row.held_net = flt(row.qty, 2), flt(row.net, 2), flt(row.held_net, 2)

		row_views = views_of(row, settings)
		for v in row_views:
			counts[v] += 1
		if view not in row_views:
			continue
		if needle and needle not in row.match_key and needle not in normalise(row.item_code or ""):
			continue
		out.append(row)

	out.sort(key=lambda r: (-r.held_days, -r.net, r.lithos_item_name))
	return {
		"rows": out[:MAX_ROWS],
		"total": len(out),
		"counts": counts,
		"held_days": frappe.db.count(
			"Lithos Sync Log", {"status": HELD, **({"store": ("in", stores)} if stores else {})}
		),
		"sales_days": SALES_DAYS,
		"hold_without_bom": cint(settings.hold_without_bom),
		# BOMs are the master recipes, kept in the mother company.
		"bom_company": MOTHER_COMPANY,
	}


def views_of(row, settings):
	views = ["All"]
	if row.status == MAP_IGNORED:
		return [*views, "Ignored"]
	if row.status == MAP_UNMAPPED or not row.item_code or row.item_disabled:
		return [*views, "Unmapped"]
	if row.status in VIEWS["To review"]:
		views.append("To review")
	if not row.default_bom and row.item_code != settings.suspense_item:
		views.append("No BOM")
	return views


def is_pos_item(item, settings):
	"""An item the sync created by itself, as opposed to one from the item master."""
	return item.name.startswith("POS-") and item.item_group == settings.pos_item_group


@frappe.whitelist()
def get_suggestions(name):
	"""Items a Lithos name probably is: by barcode, by the same name mapped elsewhere, by similar name."""
	frappe.only_for(LITHOS_ROLES)
	row = frappe.get_doc("Lithos Item Map", name)
	settings = frappe.get_cached_doc("LithosPOS Settings")
	items = {
		item.name: item
		for item in frappe.get_all(
			"Item",
			filters={"disabled": 0, "is_sales_item": 1},
			fields=["name", "item_name", "item_group", "is_stock_item", "default_bom", "stock_uom"],
		)
	}
	out, seen = [], {row.item_code}

	def add(item_code, reason, score):
		item = items.get(item_code)
		if item and item_code not in seen:
			seen.add(item_code)
			out.append(
				{
					"item_code": item.name,
					"item_name": item.item_name,
					"item_group": item.item_group,
					"stock_uom": item.stock_uom,
					"default_bom": item.default_bom,
					"is_pos_item": is_pos_item(item, settings),
					"reason": reason,
					"score": round(score, 2),
				}
			)

	if row.barcode:
		for item_code in frappe.get_all(
			"Item Barcode", filters={"barcode": row.barcode, "parenttype": "Item"}, pluck="parent"
		):
			add(item_code, _("Has barcode {0}").format(row.barcode), 1)

	for other in frappe.get_all(
		"Lithos Item Map",
		filters={"match_key": row.match_key, "name": ("!=", row.name), "item_code": ("is", "set")},
		fields=["store", "item_code"],
	):
		add(other.item_code, _("Mapped for {0}").format(other.store or _("all stores")), 0.95)

	# Real items first: an auto-created POS- item is what the clean-up merges away.
	matcher = SequenceMatcher(autojunk=False)
	matcher.set_seq2(row.match_key)
	scored = []
	for item in items.values():
		matcher.set_seq1(normalise(item.item_name))
		if matcher.real_quick_ratio() >= 0.6 and matcher.quick_ratio() >= 0.6:
			ratio = matcher.ratio()
			if ratio >= 0.6:
				scored.append((ratio - (0.05 if is_pos_item(item, settings) else 0), item.name, ratio))
	for _rank, item_code, ratio in sorted(scored, reverse=True):
		if len(out) >= MAX_SUGGESTIONS:
			break
		add(item_code, _("Similar name ({0}%)").format(round(ratio * 100)), ratio)

	return {"row": row.as_dict(), "suggestions": out}


def parse_names(names):
	names = frappe.parse_json(names) if isinstance(names, str) else names
	if not names:
		frappe.throw(_("Select at least one Lithos item."))
	return names


@frappe.whitelist()
def map_rows(names, item_code, store=None):
	"""Map Lithos names to an item. With ``store``, an all-stores row gets a store-only row instead."""
	frappe.only_for(LITHOS_ROLES)
	if not frappe.db.get_value("Item", {"name": item_code, "disabled": 0}):
		frappe.throw(_("Item {0} does not exist or is disabled.").format(item_code))

	done = []
	for name in parse_names(names):
		row = frappe.get_doc("Lithos Item Map", name)
		if store and not row.store:
			existing = frappe.db.get_value("Lithos Item Map", {"scope_key": scope_key(store, row.match_key)})
			if existing:
				row = frappe.get_doc("Lithos Item Map", existing)
			else:
				row = frappe.get_doc(
					{
						"doctype": "Lithos Item Map",
						"lithos_item_name": row.lithos_item_name,
						"store": store,
						"barcode": row.barcode,
					}
				)
		# Set explicitly: confirming a guess keeps the item but is still a decision.
		row.update({"item_code": item_code, "status": "Manual"})
		row.save()
		done.append(row.name)
	return done


@frappe.whitelist()
def ignore_rows(names):
	"""Leave these Lithos names out of Branch Sales. Their value shows as the day's variance."""
	frappe.only_for(LITHOS_ROLES)
	for name in parse_names(names):
		row = frappe.get_doc("Lithos Item Map", name)
		row.status = MAP_IGNORED
		row.save()
	return True


@frappe.whitelist()
def merge_item(source, target):
	"""Point every Lithos name mapped to ``source`` at ``target``. See merge()."""
	frappe.only_for(LITHOS_ROLES)
	return merge(source, target)


def merge(source, target):
	"""Repoint the map rows of ``source`` to ``target`` and retire ``source`` if the sync created it.

	The item itself is not renamed into the target: a POS- item is a non-stock item
	and ERPNext only merges items with the same stock settings. Days already posted
	keep the old item until they are re-posted, which resolves them through the map.
	"""
	if source == target:
		frappe.throw(_("Choose a different item to merge into."))
	if not frappe.db.exists("Item", source):
		frappe.throw(_("Item {0} not found.").format(source))
	if not frappe.db.get_value("Item", {"name": target, "disabled": 0}):
		frappe.throw(_("Item {0} does not exist or is disabled.").format(target))

	names = frappe.get_all("Lithos Item Map", filters={"item_code": source}, pluck="name")
	for name in names:
		row = frappe.get_doc("Lithos Item Map", name)
		row.update({"item_code": target, "status": "Manual"})
		row.save(ignore_permissions=True)

	item = frappe.get_doc("Item", source)
	retired = is_pos_item(item, frappe.get_cached_doc("LithosPOS Settings")) and not item.disabled
	if retired:
		item.disabled = 1
		item.add_comment("Comment", _("Merged into {0} from the Lithos Item Workbench.").format(target))
		item.save(ignore_permissions=True)

	posted = frappe.db.sql(
		"""select count(distinct bs.name) from `tabBranch Sales Item` bsi
		join `tabBranch Sales` bs on bs.name = bsi.parent
		where bsi.item_code = %s and bs.docstatus < 2""",
		source,
	)[0][0]
	return {"maps": len(names), "disabled": retired, "posted_days": posted}


def item_dashboard(data):
	"""Item form: list the Lithos names mapped to the item (M3, "Lithos aliases")."""
	data.setdefault("transactions", []).append({"label": _("Lithos"), "items": ["Lithos Item Map"]})
	return data
