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
		"Company Document Type",
		filters={"has_expiry": 1},
		pluck="name",
		order_by="name",
	)


def get_columns(document_types):
	columns = [
		{
			"fieldname": "company",
			"label": _("Company"),
			"fieldtype": "Link",
			"options": "Company",
			"width": 200,
		},
		{
			"fieldname": "tax_id",
			"label": _("Tax ID"),
			"fieldtype": "Data",
			"width": 140,
		},
		{
			"fieldname": "country",
			"label": _("Country"),
			"fieldtype": "Link",
			"options": "Country",
			"width": 120,
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
	conditions = ["1 = 1"]
	values = {}
	if filters.get("company"):
		conditions.append("parent.company = %(company)s")
		values["company"] = filters.company
	if filters.get("country"):
		conditions.append("parent.country = %(country)s")
		values["country"] = filters.country

	rows = frappe.db.sql(
		"""
		select parent.company, parent.tax_id, parent.country,
			child.document_type, child.expiry_date
		from `tabCompany Documents` parent
		left join `tabCompany Documents Detail` child on child.parent = parent.name
		where {conditions}
		order by parent.company
		""".format(conditions=" and ".join(conditions)),
		values,
		as_dict=True,
	)

	today = getdate(nowdate())
	companies = {}
	expiries = {}
	for row in rows:
		companies.setdefault(
			row.company,
			{
				"company": row.company,
				"tax_id": row.tax_id,
				"country": row.country,
			},
		)
		if row.document_type in document_types and row.expiry_date:
			fieldname = frappe.scrub(row.document_type)
			expiries.setdefault(row.company, {}).setdefault(fieldname, []).append(
				getdate(row.expiry_date)
			)

	for company, per_type in expiries.items():
		for fieldname, dates in per_type.items():
			# expired rows are superseded by a renewal: if any un-expired row exists,
			# show the earliest upcoming expiry; otherwise show the latest expired one
			upcoming = [d for d in dates if d >= today]
			companies[company][fieldname] = min(upcoming) if upcoming else max(dates)

	data = list(companies.values())

	if filters.get("expiring_within_days"):
		cutoff = add_days(getdate(nowdate()), int(filters.expiring_within_days))
		fieldnames = [frappe.scrub(dt) for dt in document_types]
		data = [
			record
			for record in data
			if any(record.get(f) and getdate(record[f]) <= cutoff for f in fieldnames)
		]

	return data
