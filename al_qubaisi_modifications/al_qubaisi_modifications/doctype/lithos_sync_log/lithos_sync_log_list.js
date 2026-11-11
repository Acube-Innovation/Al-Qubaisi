// Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
// For license information, please see license.txt

frappe.listview_settings["Lithos Sync Log"] = {
	get_indicator(doc) {
		const colour = {
			Posted: "green",
			Draft: "orange",
			Held: "yellow",
			Changed: "purple",
			"No sales": "gray",
			Failed: "red",
		}[doc.status];
		return [__(doc.status), colour || "gray", `status,=,${doc.status}`];
	},
};
