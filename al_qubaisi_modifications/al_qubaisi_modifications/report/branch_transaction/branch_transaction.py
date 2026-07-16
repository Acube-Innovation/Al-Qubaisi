# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_months, flt, getdate, today


def execute(filters=None):
	filters = frappe._dict(filters or {})

	# Default date range: last one month up to today.
	if not filters.from_date:
		filters.from_date = add_months(today(), -1)
	if not filters.to_date:
		filters.to_date = today()

	columns = get_columns()

	# A customer (branch) is required to build the statement.
	if not filters.customer:
		return columns, []

	branch = frappe.db.get_value("Customer", filters.customer, "custom_branch")

	data = []

	# 1) Opening balance up to (but excluding) the from_date. Zero when nothing exists.
	opening = get_opening_balance(filters, branch)
	data.append(
		{
			"branch": branch,
			"transaction": _("Opening Balance"),
			"date": filters.from_date,
			"reference": None,
			"purchase": None,
			"sales": None,
			"balance": opening,
			"is_opening": 1,
		}
	)

	# 2) Transactions inside the period, ordered by date.
	balance = opening
	total_purchase = total_sales = 0.0
	for txn in get_transactions(filters, branch):
		balance += flt(txn["purchase"]) - flt(txn["sales"])
		total_purchase += flt(txn["purchase"])
		total_sales += flt(txn["sales"])
		data.append(
			{
				"branch": branch,
				"transaction": txn["transaction"],
				"transaction_doctype": txn["transaction_doctype"],
				"date": txn["date"],
				"reference": txn["reference"],
				"purchase": flt(txn["purchase"]) or None,
				"sales": flt(txn["sales"]) or None,
				"balance": balance,
			}
		)

	# 3) Closing total row.
	data.append(
		{
			"branch": None,
			"transaction": _("Total"),
			"date": None,
			"reference": None,
			"purchase": total_purchase,
			"sales": total_sales,
			"balance": balance,
			"is_total": 1,
		}
	)

	return columns, data


def get_opening_balance(filters, branch):
	"""Purchases (Delivery Notes) minus Sales (Branch Sales) before the from_date."""
	opening_purchase = flt(
		frappe.db.get_value(
			"Delivery Note",
			{
				"customer": filters.customer,
				"docstatus": 1,
				"posting_date": ["<", filters.from_date],
			},
			"sum(grand_total)",
		)
	)

	opening_sales = 0.0
	if branch:
		opening_sales = flt(
			frappe.db.get_value(
				"Branch Sales",
				{
					"branch": branch,
					"docstatus": 1,
					"posting_date": ["<", filters.from_date],
				},
				"sum(grand_total)",
			)
		)

	return opening_purchase - opening_sales


def get_transactions(filters, branch):
	"""Delivery Notes (branch purchases) + Branch Sales, amount only, sorted by date."""
	rows = []

	delivery_notes = frappe.get_all(
		"Delivery Note",
		filters={
			"customer": filters.customer,
			"docstatus": 1,
			"posting_date": ["between", [filters.from_date, filters.to_date]],
		},
		fields=["name", "posting_date", "grand_total"],
	)
	for dn in delivery_notes:
		rows.append(
			{
				"transaction": "Delivery Note",
				"transaction_doctype": "Delivery Note",
				"date": getdate(dn.posting_date),
				"reference": dn.name,
				"purchase": flt(dn.grand_total),
				"sales": 0.0,
			}
		)

	if branch:
		branch_sales = frappe.get_all(
			"Branch Sales",
			filters={
				"branch": branch,
				"docstatus": 1,
				"posting_date": ["between", [filters.from_date, filters.to_date]],
			},
			fields=["name", "posting_date", "grand_total"],
		)
		for bs in branch_sales:
			rows.append(
				{
					"transaction": "Branch Sales",
					"transaction_doctype": "Branch Sales",
					"date": getdate(bs.posting_date),
					"reference": bs.name,
					"purchase": 0.0,
					"sales": flt(bs.grand_total),
				}
			)

	# Order by date; on ties show the purchase (delivery) before the sale.
	rows.sort(
		key=lambda r: (
			r["date"],
			0 if r["transaction"] == "Delivery Note" else 1,
			r["reference"],
		)
	)
	return rows


def get_columns():
	return [
		{
			"fieldname": "branch",
			"label": _("Branch"),
			"fieldtype": "Link",
			"options": "Branch",
			"width": 170,
		},
		{
			"fieldname": "transaction",
			"label": _("Transaction"),
			"fieldtype": "Data",
			"width": 130,
		},
		{
			"fieldname": "date",
			"label": _("Date"),
			"fieldtype": "Date",
			"width": 100,
		},
		{
			"fieldname": "reference",
			"label": _("Reference"),
			"fieldtype": "Dynamic Link",
			"options": "transaction_doctype",
			"width": 180,
		},
		{
			"fieldname": "purchase",
			"label": _("Branch Purchase"),
			"fieldtype": "Currency",
			"width": 140,
		},
		{
			"fieldname": "sales",
			"label": _("Branch Sales"),
			"fieldtype": "Currency",
			"width": 140,
		},
		{
			"fieldname": "balance",
			"label": _("Balance"),
			"fieldtype": "Currency",
			"width": 150,
		},
	]
