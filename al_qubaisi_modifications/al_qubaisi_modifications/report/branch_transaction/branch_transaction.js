// Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Branch Transaction"] = {
	filters: [
		{
			fieldname: "customer",
			label: __("Customer"),
			fieldtype: "Link",
			options: "Customer",
			reqd: 1,
			get_query: () => ({ filters: { custom_is_branch: 1 } }),
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			reqd: 1,
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			reqd: 1,
			default: frappe.datetime.get_today(),
		},
	],

	onload(report) {
		// Always default to a branch-customer so the statement is populated on open.
		if (!frappe.query_report.get_filter_value("customer")) {
			frappe.db
				.get_list("Customer", {
					filters: { custom_is_branch: 1 },
					limit: 1,
					order_by: "name asc",
				})
				.then((rows) => {
					if (rows && rows.length) {
						frappe.query_report.set_filter_value("customer", rows[0].name);
					}
				});
		}
	},

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (data && (data.is_opening || data.is_total)) {
			value = `<b>${value}</b>`;
		}
		if (column.fieldname === "balance" && data && flt(data.balance) < 0) {
			value = `<span style="color: var(--red-600);">${value}</span>`;
		}
		return value;
	},
};
