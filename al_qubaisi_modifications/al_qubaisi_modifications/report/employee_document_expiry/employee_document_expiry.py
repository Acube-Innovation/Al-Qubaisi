# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_days, getdate, nowdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	document_types = get_document_types()
	columns = get_columns(document_types)
	data = get_data(filters, document_types)
	return columns, data


def get_document_types():
	return frappe.get_all(
		"Employee Document Type",
		filters={"has_expiry": 1},
		pluck="name",
		order_by="name",
	)


def get_columns(document_types):
	columns = [
		{
			"fieldname": "employee",
			"label": _("Employee"),
			"fieldtype": "Link",
			"options": "Employee",
			"width": 120,
		},
		{
			"fieldname": "employee_name",
			"label": _("Employee Name"),
			"fieldtype": "Data",
			"width": 180,
		},
		{
			"fieldname": "company",
			"label": _("Company"),
			"fieldtype": "Link",
			"options": "Company",
			"width": 140,
		},
		{
			"fieldname": "department",
			"label": _("Department"),
			"fieldtype": "Link",
			"options": "Department",
			"width": 140,
		},
		{
			"fieldname": "designation",
			"label": _("Designation"),
			"fieldtype": "Data",
			"width": 130,
		},
	]
	for document_type in document_types:
		columns.append(
			{
				"fieldname": frappe.scrub(document_type),
				"label": _("{0} Expiry").format(document_type),
				"fieldtype": "Date",
				"width": 130,
			}
		)
	return columns


def get_data(filters, document_types):
	conditions = ["ifnull(parent.employee_status, 'Active') = 'Active'"]
	values = {}
	if filters.get("company"):
		conditions.append("parent.company = %(company)s")
		values["company"] = filters.company
	if filters.get("department"):
		conditions.append("parent.department = %(department)s")
		values["department"] = filters.department

	rows = frappe.db.sql(
		"""
		select parent.employee, parent.employee_name, parent.company,
			parent.department, parent.designation,
			child.document_type, child.expiry_date
		from `tabEmployee Documents` parent
		left join `tabEmployee Documents Detail` child on child.parent = parent.name
		where {conditions}
		order by parent.employee_name
		""".format(conditions=" and ".join(conditions)),
		values,
		as_dict=True,
	)

	employees = {}
	for row in rows:
		record = employees.setdefault(
			row.employee,
			{
				"employee": row.employee,
				"employee_name": row.employee_name,
				"company": row.company,
				"department": row.department,
				"designation": row.designation,
			},
		)
		if row.document_type in document_types and row.expiry_date:
			fieldname = frappe.scrub(row.document_type)
			# keep the earliest expiry if the same document appears twice
			if not record.get(fieldname) or getdate(row.expiry_date) < getdate(record[fieldname]):
				record[fieldname] = row.expiry_date

	data = list(employees.values())

	if filters.get("expiring_within_days"):
		cutoff = add_days(getdate(nowdate()), int(filters.expiring_within_days))
		fieldnames = [frappe.scrub(dt) for dt in document_types]
		data = [
			record
			for record in data
			if any(record.get(f) and getdate(record[f]) <= cutoff for f in fieldnames)
		]

	return data
