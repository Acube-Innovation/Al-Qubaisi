# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

"""Lithos Item and BOM Coverage (M8): what each sold item takes out of stock.

One row per item sold in the range, classed the way the Material Issue treats it
(M6): by its default BOM, issued itself as a stock item, or with nothing to issue.
Lithos names still unmapped, or set to Ignored, are listed below them for Lithos
users: map rows cover every store.
"""

import frappe
from frappe import _
from frappe.utils import flt

from al_qubaisi_modifications import lithos
from al_qubaisi_modifications.lithos_tower import (
	AS_STOCK,
	BOM,
	NO_BOM,
	Scope,
	coverage,
	date_range,
	is_lithos_user,
)

UNMAPPED, IGNORED = "Unmapped", "Ignored"


def execute(filters=None):
	filters = frappe._dict(filters or {})
	from_date, to_date = date_range(filters.from_date, filters.to_date)
	scope = Scope(filters.company, None, filters.store)
	rows = sold_items(scope, from_date, to_date)
	if is_lithos_user():
		rows += map_rows(from_date)
	if filters.coverage:
		rows = [row for row in rows if row.coverage == filters.coverage]
	return get_columns(), rows, None, None, get_summary(rows, filters)


def get_columns():
	return [
		{"fieldname": "coverage", "label": _("Coverage"), "fieldtype": "Data", "width": 140},
		{"fieldname": "item_code", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 130},
		{"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 190},
		{
			"fieldname": "default_bom",
			"label": _("Default BOM"),
			"fieldtype": "Link",
			"options": "BOM",
			"width": 150,
		},
		{"fieldname": "lithos_names", "label": _("Lithos Names"), "fieldtype": "Data", "width": 220},
		{"fieldname": "stores", "label": _("Stores"), "fieldtype": "Data", "width": 90},
		{"fieldname": "qty", "label": _("Qty Sold"), "fieldtype": "Float", "width": 90},
		{"fieldname": "net", "label": _("Net Sold"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "share", "label": _("Share of Net %"), "fieldtype": "Percent", "width": 100},
		{"fieldname": "days", "label": _("Days Sold"), "fieldtype": "Int", "width": 80},
		{"fieldname": "last_sold", "label": _("Last Sold"), "fieldtype": "Date", "width": 105},
	]


def sold_items(scope, from_date, to_date):
	rows = frappe.db.sql(
		f"""
		select bsi.item_code, max(i.item_name) as item_name, max(i.default_bom) as default_bom,
			max(i.is_stock_item) as is_stock_item,
			group_concat(distinct bsi.pos_item_name order by bsi.pos_item_name separator ', ') as lithos_names,
			group_concat(distinct bs.lithos_store_id order by bs.lithos_store_id separator ', ') as stores,
			sum(bsi.qty) as qty, sum(bsi.amount) as net, count(distinct bs.name) as days,
			max(bs.posting_date) as last_sold
		from `tabBranch Sales Item` bsi
		join `tabBranch Sales` bs on bs.name = bsi.parent
		join `tabItem` i on i.name = bsi.item_code
		where bs.docstatus = 1 and {scope.sales_condition()}
			and bs.posting_date between %(from_date)s and %(to_date)s
		group by bsi.item_code
		order by net desc
		""",
		scope.params(from_date, to_date),
		as_dict=True,
	)
	suspense = frappe.db.get_single_value("LithosPOS Settings", "suspense_item")
	total = sum(flt(row.net) for row in rows)
	for row in rows:
		row.coverage = coverage(row.item_code, row.default_bom, row.is_stock_item, suspense)
		row.lithos_names = (row.lithos_names or "")[:200]
		row.share = flt(flt(row.net) / total * 100, 2) if total else 0
	return rows


def map_rows(from_date):
	"""Lithos names nothing posts yet: unmapped, or ignored and still selling."""
	rows = []
	for row in frappe.get_all(
		"Lithos Item Map",
		filters={"status": ("in", [lithos.MAP_UNMAPPED, lithos.MAP_IGNORED])},
		fields=["lithos_item_name", "status", "store", "last_sold"],
		order_by="last_sold desc",
	):
		if row.status == lithos.MAP_IGNORED and (not row.last_sold or row.last_sold < from_date):
			continue
		rows.append(
			frappe._dict(
				coverage=UNMAPPED if row.status == lithos.MAP_UNMAPPED else IGNORED,
				lithos_names=row.lithos_item_name,
				stores=row.store or _("All"),
				last_sold=row.last_sold,
			)
		)
	return rows


def get_summary(rows, filters):
	sold = [row for row in rows if row.item_code]
	total = sum(flt(row.net) for row in sold)
	issued = sum(flt(row.net) for row in sold if row.coverage in (BOM, AS_STOCK))
	no_bom = sum(1 for row in sold if row.coverage == NO_BOM)
	summary = [
		{"label": _("Items Sold"), "value": len(sold), "datatype": "Int"},
		{"label": _("With a BOM"), "value": sum(1 for row in sold if row.coverage == BOM), "datatype": "Int"},
		{
			"label": _("Without a BOM"),
			"value": no_bom,
			"datatype": "Int",
			"indicator": "Red" if no_bom else "Green",
		},
		{
			"label": _("Net Sales Covered"),
			"value": flt(issued / total * 100, 1) if total else 0,
			"datatype": "Percent",
			"indicator": "Green" if total and issued == total else "Orange",
		},
	]
	unmapped = sum(1 for row in rows if row.coverage == UNMAPPED)
	if is_lithos_user() and (unmapped or not filters.coverage):
		summary.append(
			{
				"label": _("Unmapped Lithos Names"),
				"value": unmapped,
				"datatype": "Int",
				"indicator": "Red" if unmapped else "Green",
			}
		)
	return summary
