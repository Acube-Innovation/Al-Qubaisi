// Al-Qubaisi: one document, many cheques.
//
// Keeps the party/account pickers narrowed to what the chosen Payment Type can
// actually use, and pulls the party's own receivable/payable account so the user
// does not have to remember it.

frappe.ui.form.on("Multi Cheque Payment", {
	setup(frm) {
		frm.set_query("party_type", () => ({
			filters: { name: ["in", ["Customer", "Supplier"]] },
		}));

		// The cheques sit in a bank-like control account, never in a party ledger.
		frm.set_query("deposit_account", () => ({
			filters: {
				company: frm.doc.company,
				is_group: 0,
				account_type: ["in", ["Bank", "Cash"]],
			},
		}));

		frm.set_query("party_account", () => ({
			filters: {
				company: frm.doc.company,
				is_group: 0,
				account_type: frm.doc.payment_type === "Receive" ? "Receivable" : "Payable",
			},
		}));

		frm.set_query("cost_center", () => ({
			filters: { company: frm.doc.company, is_group: 0 },
		}));

		frm.set_query("reference_name", "cheques", () => ({
			filters: {
				docstatus: 1,
				company: frm.doc.company,
				outstanding_amount: [">", 0],
				[frm.doc.payment_type === "Receive" ? "customer" : "supplier"]: frm.doc.party,
			},
		}));
	},

	refresh(frm) {
		set_reference_doctype(frm);
	},

	payment_type(frm) {
		frm.set_value("party_type", frm.doc.payment_type === "Receive" ? "Customer" : "Supplier");
		frm.set_value("party", null);
		frm.set_value("party_account", null);
		set_reference_doctype(frm);
	},

	party(frm) {
		fetch_party_defaults(frm);
	},

	company(frm) {
		fetch_party_defaults(frm);
	},
});

frappe.ui.form.on("Multi Cheque Payment Detail", {
	cheques_add(frm, cdt, cdn) {
		frappe.model.set_value(cdt, cdn, "reference_doctype", invoice_doctype(frm));
	},

	amount(frm) {
		set_totals(frm);
	},

	cheques_remove(frm) {
		set_totals(frm);
	},
});

function invoice_doctype(frm) {
	return frm.doc.payment_type === "Receive" ? "Sales Invoice" : "Purchase Invoice";
}

function set_reference_doctype(frm) {
	(frm.doc.cheques || []).forEach((row) => {
		row.reference_doctype = invoice_doctype(frm);
	});
	frm.refresh_field("cheques");
}

function set_totals(frm) {
	let total = 0;
	(frm.doc.cheques || []).forEach((row) => {
		total += flt(row.amount);
	});
	frm.set_value("total_amount", total);
	frm.set_value("cheque_count", (frm.doc.cheques || []).length);
}

function fetch_party_defaults(frm) {
	if (!frm.doc.company || !frm.doc.party_type || !frm.doc.party) {
		return;
	}

	frappe.call({
		method: "al_qubaisi_modifications.al_qubaisi_modifications.doctype.multi_cheque_payment.multi_cheque_payment.get_party_defaults",
		args: {
			company: frm.doc.company,
			party_type: frm.doc.party_type,
			party: frm.doc.party,
		},
		callback(r) {
			if (!r.message) {
				return;
			}
			frm.set_value("party_account", r.message.party_account);
			frm.set_value("party_balance", r.message.party_balance);
		},
	});
}
