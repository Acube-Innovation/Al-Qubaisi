// Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Employee Document Expiry"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
		},
		{
			fieldname: "department",
			label: __("Department"),
			fieldtype: "Link",
			options: "Department",
		},
		{
			fieldname: "expiring_within_days",
			label: __("Expiring Within (Days)"),
			fieldtype: "Int",
			description: __("Only show employees with at least one document expiring within this many days (includes already expired)"),
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldtype === "Date" && data && data[column.fieldname]) {
			const expiry = frappe.datetime.str_to_obj(data[column.fieldname]);
			const today = frappe.datetime.str_to_obj(frappe.datetime.get_today());
			const soon = frappe.datetime.str_to_obj(
				frappe.datetime.add_days(frappe.datetime.get_today(), 30)
			);
			if (expiry < today) {
				value = `<span style="color: var(--red-600); font-weight: bold;">${value}</span>`;
			} else if (expiry <= soon) {
				value = `<span style="color: var(--orange-600); font-weight: bold;">${value}</span>`;
			}
		}
		return value;
	},
};
