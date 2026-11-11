// Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
// For license information, please see license.txt

frappe.listview_settings["Lithos Sync Run"] = {
	get_indicator(doc) {
		const colour = {
			Queued: "gray",
			Running: "blue",
			Success: "green",
			Partial: "orange",
			Failed: "red",
		}[doc.status];
		return [__(doc.status), colour || "gray", `status,=,${doc.status}`];
	},
};
