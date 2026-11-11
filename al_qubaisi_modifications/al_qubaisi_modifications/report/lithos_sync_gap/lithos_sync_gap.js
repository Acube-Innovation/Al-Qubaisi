// Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Lithos Sync Gap"] = {
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
			description: __("Blank: each store's Sync From Date"),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -1),
		},
		{
			fieldname: "reason",
			label: __("Reason"),
			fieldtype: "Select",
			options: [
				"",
				"Not synced",
				"Failed",
				"Held",
				"Draft",
				"Changed in Lithos",
				"Re-post pending",
				"Ledger missing",
				"Consumption pending",
				"Ledger failed",
			],
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "reason" && data && data.reason) {
			const colour = {
				"Not synced": "red",
				Failed: "red",
				Held: "yellow",
				Draft: "orange",
				"Changed in Lithos": "purple",
				"Re-post pending": "blue",
				"Ledger missing": "red",
				"Consumption pending": "orange",
				"Ledger failed": "red",
			}[data.reason];
			return `<span class="indicator-pill ${colour || "gray"}">${__(data.reason)}</span>`;
		}
		return value;
	},
};
