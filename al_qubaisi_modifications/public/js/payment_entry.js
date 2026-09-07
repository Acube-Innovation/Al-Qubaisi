// Al-Qubaisi: realise a post-dated cheque.
//
// A PDC is a Payment Entry whose Reference Date (the date on the cheque) is later
// than the posting date. Submitting it clears the party but parks the money in a
// control account. "Create > Bank Entry for Cheque" opens the Journal Entry that
// finally moves it into the bank, pre-filled from this payment.

frappe.ui.form.on("Payment Entry", {
	refresh(frm) {
		add_bank_entry_button(frm);
	},
});

function is_post_dated(frm) {
	if (!frm.doc.reference_date || !frm.doc.posting_date) {
		return false;
	}

	return frappe.datetime.str_to_obj(frm.doc.reference_date) > frappe.datetime.str_to_obj(frm.doc.posting_date);
}

function add_bank_entry_button(frm) {
	if (frm.doc.docstatus !== 1 || !frm.doc.reference_no || !is_post_dated(frm)) {
		return;
	}

	if (!["Receive", "Pay"].includes(frm.doc.payment_type)) {
		return;
	}

	// Hide the button once the realisation entry exists, so a cheque cannot be
	// banked twice. A cancelled entry frees it again.
	frappe.db
		.get_value("Journal Entry", { custom_pdc_payment_entry: frm.doc.name, docstatus: ["<", 2] }, "name")
		.then((r) => {
			const existing = r.message && r.message.name;
			if (existing) {
				frm.add_custom_button(
					__("Bank Entry for Cheque"),
					() => frappe.set_route("Form", "Journal Entry", existing),
					__("View")
				);
				return;
			}

			frm.add_custom_button(
				__("Bank Entry for Cheque"),
				() => make_bank_entry(frm),
				__("Create")
			);
		});
}

function make_bank_entry(frm) {
	frappe.call({
		method: "al_qubaisi_modifications.pdc.make_bank_entry",
		args: { payment_entry: frm.doc.name },
		freeze: true,
		freeze_message: __("Building bank entry..."),
		callback(r) {
			if (!r.message) {
				return;
			}

			const doc = frappe.model.sync(r.message)[0];
			frappe.set_route("Form", doc.doctype, doc.name);
			frappe.show_alert({
				message: __("Select the bank account the cheque was deposited into"),
				indicator: "orange",
			});
		},
	});
}
