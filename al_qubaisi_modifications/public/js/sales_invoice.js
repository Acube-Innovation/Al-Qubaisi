frappe.ui.form.on("Sales Invoice", {
	customer(frm) {
		set_branch_and_cost_center_from_customer(frm);
	},
});

frappe.ui.form.on("Sales Invoice Item", {
	items_add(frm, cdt, cdn) {
		// Newly added rows should inherit the document's cost center.
		if (frm.doc.cost_center) {
			frappe.model.set_value(cdt, cdn, "cost_center", frm.doc.cost_center);
		}
	},
});

function set_branch_and_cost_center_from_customer(frm) {
	if (!frm.doc.customer) {
		return;
	}

	// The Branch field on Sales Invoice is a custom field. Support either
	// the customize-form name (custom_branch) or a plain "branch" field.
	const branch_field = ["custom_branch", "branch"].find(
		(f) => frm.fields_dict[f]
	);

	frappe.db.get_value("Customer", frm.doc.customer, "custom_branch").then((r) => {
		const branch = r.message && r.message.custom_branch;
		if (!branch) {
			return;
		}

		if (branch_field) {
			frm.set_value(branch_field, branch);
		}

		frappe.db
			.get_value("Branch", branch, "custom_cost_center")
			.then((res) => {
				const cost_center = res.message && res.message.custom_cost_center;
				if (cost_center) {
					frm.set_value("cost_center", cost_center);
					apply_cost_center_to_items(frm, cost_center);
				}
			});
	});
}

function apply_cost_center_to_items(frm, cost_center) {
	(frm.doc.items || []).forEach((row) => {
		frappe.model.set_value(row.doctype, row.name, "cost_center", cost_center);
	});
}
