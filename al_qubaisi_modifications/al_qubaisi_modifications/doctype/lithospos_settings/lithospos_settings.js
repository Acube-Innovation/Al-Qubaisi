// Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("LithosPOS Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Sync Console"), () => frappe.set_route("lithos-sync-console"));
		frm.add_custom_button(__("Test Connection"), () => {
			frappe.prompt(
				[
					{
						fieldname: "store_id",
						fieldtype: "Link",
						options: "Lithos Store",
						label: __("Store"),
						description: __("Blank uses the first store with sync enabled."),
					},
				],
				(values) =>
					frappe
						.call({
							method: "al_qubaisi_modifications.lithos.test_connection",
							args: values,
							freeze: true,
							freeze_message: __("Asking LithosPOS..."),
						})
						.then((r) => {
							const t = r.message;
							frappe.msgprint({
								title: __("LithosPOS answered"),
								indicator: "green",
								message: __(
									"Store {0}, {1}: {2} item line(s), net {3}, VAT {4}, voided {5}. Nothing was posted.",
									[
										t.store,
										frappe.datetime.str_to_user(t.date),
										t.lines,
										format_currency(t.net, "AED"),
										format_currency(t.tax, "AED"),
										format_currency(t.void, "AED"),
									],
								),
							});
						}),
				__("Test Connection"),
				__("Pull Yesterday"),
			);
		});
	},
});
