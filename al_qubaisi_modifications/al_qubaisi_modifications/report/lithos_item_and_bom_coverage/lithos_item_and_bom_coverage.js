// Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Lithos Item and BOM Coverage"] = {
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
			fieldname: "coverage",
			label: __("Coverage"),
			fieldtype: "Select",
			options: ["", "BOM", "Issued as stock", "No BOM", "Suspense item", "Unmapped", "Ignored"],
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "coverage" && data) {
			const colour = {
				BOM: "green",
				"Issued as stock": "blue",
				"No BOM": "red",
				"Suspense item": "orange",
				Unmapped: "red",
				Ignored: "gray",
			}[data.coverage];
			return colour ? `<span class="indicator-pill ${colour}">${__(data.coverage)}</span>` : value;
		}
		return value;
	},
};
