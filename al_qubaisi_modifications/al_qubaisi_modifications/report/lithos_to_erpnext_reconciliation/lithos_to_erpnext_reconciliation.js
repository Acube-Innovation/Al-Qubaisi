// Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Lithos to ERPNext Reconciliation"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "MultiSelectList",
			get_data: (txt) => frappe.db.get_link_options("Company", txt),
		},
		{
			fieldname: "store",
			label: __("Store"),
			fieldtype: "MultiSelectList",
			get_data: (txt) =>
				frappe.xcall("al_qubaisi_modifications.lithos_tower.store_options", {
					txt,
					companies: frappe.query_report.get_filter_value("company"),
				}),
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -30),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -1),
			reqd: 1,
		},
		{
			fieldname: "only_variances",
			label: __("Only Days with an Issue"),
			fieldtype: "Check",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		const flagged = ["sync_variance", "ledger_variance"].includes(column.fieldname)
			? data && Math.abs(flt(data[column.fieldname])) >= 0.005
			: column.fieldname === "issues" && data && data.issues;
		return flagged ? `<span style="color: var(--red-600); font-weight: bold;">${value}</span>` : value;
	},
};
