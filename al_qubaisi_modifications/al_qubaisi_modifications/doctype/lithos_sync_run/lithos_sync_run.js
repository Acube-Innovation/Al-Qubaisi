// Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Lithos Sync Run", {
	refresh(frm) {
		frm.add_custom_button(__("Open Sync Console"), () =>
			frappe.set_route("lithos-sync-console", {
				from_date: frm.doc.from_date,
				to_date: frm.doc.to_date,
			}),
		);
	},
});
