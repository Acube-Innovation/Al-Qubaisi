# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

"""Outlet Stock Movement (M8): what came into each outlet warehouse, what left it, what is left.

In is the group's supply (an internal-supplier Purchase Invoice, M7). Out is the
recipe Material Issue of a day's sales (M6). Anything else, such as a stock count,
is Other. Pending is what Material Issues still waiting for stock would take.
"""

import frappe
from frappe import _
from frappe.utils import flt

from al_qubaisi_modifications.lithos_tower import Scope, date_range


def execute(filters=None):
	filters = frappe._dict(filters or {})
	from_date, to_date = date_range(filters.from_date, filters.to_date)
	scope = Scope(filters.company, None, filters.store)
	rows = get_rows(scope, from_date, to_date, filters.item_code)
	if filters.get("hide_no_movement", 1):
		rows = [row for row in rows if row.moved]
	return get_columns(), rows, None, None, get_summary(rows)


def get_columns():
	def column(fieldname, label, fieldtype="Float", width=100, **extra):
		return {"fieldname": fieldname, "label": label, "fieldtype": fieldtype, "width": width, **extra}

	return [
		column("warehouse", _("Warehouse"), "Link", 150, options="Warehouse"),
		column("stores", _("Store"), "Data", 70),
		column("item_code", _("Item"), "Link", 120, options="Item"),
		column("item_name", _("Item Name"), "Data", 170),
		column("stock_uom", _("UOM"), "Link", 70, options="UOM"),
		column("opening_qty", _("Opening Qty")),
		column("in_qty", _("In from Group")),
		column("in_value", _("In Value"), "Currency", 110),
		column("out_qty", _("Out by Recipe")),
		column("out_value", _("Out Value"), "Currency", 110),
		column("other_qty", _("Other Qty"), width=90),
		column("closing_qty", _("Closing Qty")),
		column("closing_value", _("Closing Value"), "Currency", 115),
		column("pending_qty", _("Pending Consumption"), width=120),
		column("short_qty", _("Short"), width=90),
	]


def get_rows(scope, from_date, to_date, item_code=None):
	params = scope.params(from_date, to_date, item_code=item_code)
	item_condition = "and {0}.item_code = %(item_code)s" if item_code else ""
	rows = {
		(row.warehouse, row.item_code): row
		for row in frappe.db.sql(
			f"""
			select sle.warehouse, sle.item_code,
				sum(if(sle.posting_date < %(from_date)s, sle.actual_qty, 0)) as opening_qty,
				sum(if(sle.posting_date >= %(from_date)s and supply.name is not null, sle.actual_qty, 0)) as in_qty,
				sum(if(sle.posting_date >= %(from_date)s and supply.name is not null,
					sle.stock_value_difference, 0)) as in_value,
				sum(if(sle.posting_date >= %(from_date)s and recipe.consumption_entry is not null,
					-sle.actual_qty, 0)) as out_qty,
				sum(if(sle.posting_date >= %(from_date)s and recipe.consumption_entry is not null,
					-sle.stock_value_difference, 0)) as out_value,
				sum(if(sle.posting_date >= %(from_date)s and supply.name is null
					and recipe.consumption_entry is null, sle.actual_qty, 0)) as other_qty,
				sum(sle.actual_qty) as closing_qty,
				sum(sle.stock_value_difference) as closing_value,
				sum(sle.posting_date >= %(from_date)s) as entries
			from `tabStock Ledger Entry` sle
			left join `tabPurchase Invoice` supply
				on sle.voucher_type = 'Purchase Invoice' and supply.name = sle.voucher_no
				and supply.is_internal_supplier = 1
			left join (
				select distinct consumption_entry from `tabBranch Sales`
				where docstatus = 1 and ifnull(consumption_entry, '') != ''
			) recipe on sle.voucher_type = 'Stock Entry' and recipe.consumption_entry = sle.voucher_no
			where sle.is_cancelled = 0 and sle.warehouse in %(warehouses)s
				and sle.posting_date <= %(to_date)s {item_condition.format("sle")}
			group by sle.warehouse, sle.item_code
			""",
			params,
			as_dict=True,
		)
	}

	# Material Issues waiting for stock: what they would take, whatever their date.
	for row in frappe.db.sql(
		f"""
		select sed.s_warehouse as warehouse, sed.item_code, sum(sed.transfer_qty) as qty
		from `tabStock Entry Detail` sed
		join `tabStock Entry` se on se.name = sed.parent
		join `tabBranch Sales` bs on bs.consumption_entry = se.name
		where se.docstatus = 0 and bs.docstatus = 1 and sed.s_warehouse in %(warehouses)s
			{item_condition.format("sed")}
		group by sed.s_warehouse, sed.item_code
		""",
		params,
		as_dict=True,
	):
		key = (row.warehouse, row.item_code)
		rows.setdefault(
			key, frappe._dict(warehouse=row.warehouse, item_code=row.item_code)
		).pending_qty = row.qty

	stores = {}
	for store in scope.stores:
		stores.setdefault(store.warehouse, []).append(store.name)
	items = {
		item.name: item
		for item in frappe.get_all(
			"Item",
			filters={"name": ("in", list({key[1] for key in rows}) or [""])},
			fields=["name", "item_name", "stock_uom"],
		)
	}

	result = []
	for (warehouse, code), row in sorted(rows.items()):
		item = items.get(code) or {}
		for field in (
			"opening_qty",
			"in_qty",
			"in_value",
			"out_qty",
			"out_value",
			"other_qty",
			"closing_qty",
			"closing_value",
			"pending_qty",
		):
			row[field] = flt(row.get(field), 3 if field.endswith("_qty") else 2)
		row.update(
			{
				"stores": ", ".join(stores.get(warehouse, [])),
				"item_name": item.get("item_name"),
				"stock_uom": item.get("stock_uom"),
				"short_qty": flt(max(row.pending_qty - row.closing_qty, 0), 3),
				"moved": bool(row.get("entries") or row.pending_qty or row.closing_qty),
			}
		)
		result.append(row)
	return result


def get_summary(rows):
	def total(field):
		return flt(sum(flt(row.get(field)) for row in rows), 2)

	short = sum(1 for row in rows if row.short_qty)
	return [
		{"label": _("In from Group"), "value": total("in_value"), "datatype": "Currency"},
		{"label": _("Out by Recipe"), "value": total("out_value"), "datatype": "Currency"},
		{"label": _("Closing Value"), "value": total("closing_value"), "datatype": "Currency"},
		{
			"label": _("Items Short"),
			"value": short,
			"datatype": "Int",
			"indicator": "Red" if short else "Green",
		},
	]
