// Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Outlet Stock Movement"] = {
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
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "item_code",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			fieldname: "hide_no_movement",
			label: __("Hide Items with No Movement"),
			fieldtype: "Check",
			default: 1,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "short_qty" && data && flt(data.short_qty) > 0) {
			// Not enough on hand for the Material Issues waiting on this item.
			value = `<span style="color: var(--red-600); font-weight: bold;">${value}</span>`;
		}
		return value;
	},
};
