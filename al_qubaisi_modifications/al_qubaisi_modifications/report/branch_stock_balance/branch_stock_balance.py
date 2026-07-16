# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	return [
		{
			"fieldname": "branch",
			"label": _("Branch"),
			"fieldtype": "Link",
			"options": "Branch",
			"width": 150,
		},
		{
			"fieldname": "item_code",
			"label": _("Item"),
			"fieldtype": "Link",
			"options": "Item",
			"width": 150,
		},
		{
			"fieldname": "item_name",
			"label": _("Item Name"),
			"fieldtype": "Data",
			"width": 200,
		},
		{
			"fieldname": "opening_qty",
			"label": _("Opening Qty"),
			"fieldtype": "Float",
			"width": 110,
		},
		# Delivered to the branch during the period (outward stock from the company)
		{
			"fieldname": "outward_qty",
			"label": _("Delivered (Out)"),
			"fieldtype": "Float",
			"width": 120,
		},
		{
			"fieldname": "outward_amount",
			"label": _("Delivered Amount"),
			"fieldtype": "Currency",
			"width": 140,
		},
		# Sold by the branch during the period (Branch Sales)
		{
			"fieldname": "sold_qty",
			"label": _("Sold Qty"),
			"fieldtype": "Float",
			"width": 110,
		},
		{
			"fieldname": "sold_amount",
			"label": _("Sold Amount"),
			"fieldtype": "Currency",
			"width": 130,
		},
		# Opening + Delivered - Sold
		{
			"fieldname": "balance_qty",
			"label": _("Balance Qty"),
			"fieldtype": "Float",
			"width": 110,
		},
	]


def get_data(filters):
	from_date = filters.get("from_date")
	to_date = filters.get("to_date")
	rows = {}

	def bucket(branch, item_code, item_name):
		key = (branch, item_code)
		return rows.setdefault(
			key,
			{
				"branch": branch,
				"item_code": item_code,
				"item_name": item_name,
				"opening_qty": 0.0,
				"outward_qty": 0.0,
				"outward_amount": 0.0,
				"sold_qty": 0.0,
				"sold_amount": 0.0,
			},
		)

	# Opening: net stock at the branch before the period start
	# (everything delivered minus everything sold prior to from_date)
	if from_date:
		for row in get_outward_stock(filters, date_to=from_date, inclusive_to=False):
			bucket(row.branch, row.item_code, row.item_name)["opening_qty"] += flt(row.qty)
		for row in get_branch_sales(filters, date_to=from_date, inclusive_to=False):
			bucket(row.branch, row.item_code, row.item_name)["opening_qty"] -= flt(row.qty)

	# Movements within the reporting period
	for row in get_outward_stock(filters, date_from=from_date, date_to=to_date):
		record = bucket(row.branch, row.item_code, row.item_name)
		record["outward_qty"] += flt(row.qty)
		record["outward_amount"] += flt(row.amount)

	for row in get_branch_sales(filters, date_from=from_date, date_to=to_date):
		record = bucket(row.branch, row.item_code, row.item_name)
		record["sold_qty"] += flt(row.qty)
		record["sold_amount"] += flt(row.amount)

	data = []
	for record in rows.values():
		record["balance_qty"] = (
			flt(record["opening_qty"]) + flt(record["outward_qty"]) - flt(record["sold_qty"])
		)
		if filters.get("hide_zero_balance") and not record["balance_qty"]:
			continue
		data.append(record)

	data.sort(key=lambda r: (r["branch"] or "", r["item_name"] or r["item_code"] or ""))
	return data


def get_outward_stock(filters, date_from=None, date_to=None, inclusive_to=True):
	"""Delivery Notes issued to a branch's customer account (stock into the branch)."""
	conditions = [
		"dn.docstatus = 1",
		"ifnull(cust.custom_is_branch, 0) = 1",
		"cust.custom_branch is not null",
	]
	values = {}
	if filters.get("company"):
		conditions.append("dn.company = %(company)s")
		values["company"] = filters.company
	if filters.get("branch"):
		conditions.append("cust.custom_branch = %(branch)s")
		values["branch"] = filters.branch
	if filters.get("item_code"):
		conditions.append("dni.item_code = %(item_code)s")
		values["item_code"] = filters.item_code
	if date_from:
		conditions.append("dn.posting_date >= %(date_from)s")
		values["date_from"] = date_from
	if date_to:
		conditions.append(
			"dn.posting_date <= %(date_to)s" if inclusive_to else "dn.posting_date < %(date_to)s"
		)
		values["date_to"] = date_to

	return frappe.db.sql(
		"""
		select cust.custom_branch as branch, dni.item_code, dni.item_name,
			dni.qty as qty, dni.base_amount as amount
		from `tabDelivery Note Item` dni
		inner join `tabDelivery Note` dn on dn.name = dni.parent
		inner join `tabCustomer` cust on cust.name = dn.customer
		where {conditions}
		""".format(conditions=" and ".join(conditions)),
		values,
		as_dict=True,
	)


def get_branch_sales(filters, date_from=None, date_to=None, inclusive_to=True):
	"""Items sold by the branch, recorded via Branch Sales."""
	conditions = ["bs.docstatus = 1"]
	values = {}
	if filters.get("company"):
		conditions.append("bs.company = %(company)s")
		values["company"] = filters.company
	if filters.get("branch"):
		conditions.append("bs.branch = %(branch)s")
		values["branch"] = filters.branch
	if filters.get("item_code"):
		conditions.append("bsi.item_code = %(item_code)s")
		values["item_code"] = filters.item_code
	if date_from:
		conditions.append("bs.posting_date >= %(date_from)s")
		values["date_from"] = date_from
	if date_to:
		conditions.append(
			"bs.posting_date <= %(date_to)s" if inclusive_to else "bs.posting_date < %(date_to)s"
		)
		values["date_to"] = date_to

	return frappe.db.sql(
		"""
		select bs.branch as branch, bsi.item_code, bsi.item_name,
			bsi.qty as qty, bsi.amount as amount
		from `tabBranch Sales Item` bsi
		inner join `tabBranch Sales` bs on bs.name = bsi.parent
		where {conditions}
		""".format(conditions=" and ".join(conditions)),
		values,
		as_dict=True,
	)
