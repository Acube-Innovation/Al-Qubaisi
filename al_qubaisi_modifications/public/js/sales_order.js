// Al-Qubaisi: pull items into a Sales Order from a "Sales Order" purpose
// Material Request via "Get Items From > Material Request".

frappe.ui.form.on("Sales Order", {
	refresh(frm) {
		if (frm.doc.docstatus !== 0) {
			return;
		}

		frm.add_custom_button(
			__("Material Request"),
			() => {
				erpnext.utils.map_current_doc({
					method: "al_qubaisi_modifications.overrides.material_request.make_sales_order",
					source_doctype: "Material Request",
					target: frm,
					setters: {
						customer: frm.doc.customer || undefined,
					},
					get_query_filters: {
						docstatus: 1,
						material_request_type: "Sales Order",
						per_ordered: ["<", 99.99],
						company: frm.doc.company,
					},
				});
			},
			__("Get Items From")
		);
	},
});
