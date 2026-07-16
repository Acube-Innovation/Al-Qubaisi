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
		# Input side: stock delivered to the branch's customer account (outward from company)
		{
			"fieldname": "outward_qty",
			"label": _("Outward Qty"),
			"fieldtype": "Float",
			"width": 110,
		},
		{
			"fieldname": "outward_amount",
			"label": _("Outward Amount"),
			"fieldtype": "Currency",
			"width": 130,
		},
		# Output side: what the branch sold (Branch Sales)
		{
			"fieldname": "sales_qty",
			"label": _("Branch Sales Qty"),
			"fieldtype": "Float",
			"width": 130,
		},
		{
			"fieldname": "sales_amount",
			"label": _("Branch Sales Amount"),
			"fieldtype": "Currency",
			"width": 150,
		},
		# Balance: what was delivered but not yet sold
		{
			"fieldname": "balance_qty",
			"label": _("Balance Qty"),
			"fieldtype": "Float",
			"width": 110,
		},
		{
			"fieldname": "balance_amount",
			"label": _("Balance Amount"),
			"fieldtype": "Currency",
			"width": 130,
		},
	]


def get_data(filters):
	rows = {}

	def bucket(branch, item_code, item_name):
		key = (branch, item_code)
		return rows.setdefault(
			key,
			{
				"branch": branch,
				"item_code": item_code,
				"item_name": item_name,
				"outward_qty": 0.0,
				"outward_amount": 0.0,
				"sales_qty": 0.0,
				"sales_amount": 0.0,
			},
		)

	for row in get_outward_stock(filters):
		record = bucket(row.branch, row.item_code, row.item_name)
		record["outward_qty"] += flt(row.qty)
		record["outward_amount"] += flt(row.amount)

	for row in get_branch_sales(filters):
		record = bucket(row.branch, row.item_code, row.item_name)
		record["sales_qty"] += flt(row.qty)
		record["sales_amount"] += flt(row.amount)

	data = []
	for record in rows.values():
		record["balance_qty"] = flt(record["outward_qty"]) - flt(record["sales_qty"])
		record["balance_amount"] = flt(record["outward_amount"]) - flt(record["sales_amount"])
		if filters.get("hide_zero_balance") and not record["balance_qty"]:
			continue
		data.append(record)

	data.sort(key=lambda r: (r["branch"] or "", r["item_name"] or r["item_code"] or ""))
	return data


def get_outward_stock(filters):
	"""Input side: Delivery Notes issued to a branch's customer account.

	From the company's point of view this is outward stock; from the branch's
	point of view it is the stock that came in and is available to sell.
	"""
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
	if filters.get("from_date"):
		conditions.append("dn.posting_date >= %(from_date)s")
		values["from_date"] = filters.from_date
	if filters.get("to_date"):
		conditions.append("dn.posting_date <= %(to_date)s")
		values["to_date"] = filters.to_date
	if filters.get("item_code"):
		conditions.append("dni.item_code = %(item_code)s")
		values["item_code"] = filters.item_code

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


def get_branch_sales(filters):
	"""Output side: items sold by the branch, recorded via Branch Sales."""
	conditions = ["bs.docstatus = 1"]
	values = {}
	if filters.get("company"):
		conditions.append("bs.company = %(company)s")
		values["company"] = filters.company
	if filters.get("branch"):
		conditions.append("bs.branch = %(branch)s")
		values["branch"] = filters.branch
	if filters.get("from_date"):
		conditions.append("bs.posting_date >= %(from_date)s")
		values["from_date"] = filters.from_date
	if filters.get("to_date"):
		conditions.append("bs.posting_date <= %(to_date)s")
		values["to_date"] = filters.to_date
	if filters.get("item_code"):
		conditions.append("bsi.item_code = %(item_code)s")
		values["item_code"] = filters.item_code

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
