// Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Branch Sales", {
	validate(frm) {
		calculate_totals(frm);
		calculate_payments(frm);
	},
});

// ---------- Items ----------
frappe.ui.form.on("Branch Sales Item", {
	qty: calculate_item_row,
	rate: calculate_item_row,
	discount_percent: calculate_item_row,
	discount_amount: calculate_item_row,
	tax_rate: calculate_item_row,
	items_remove(frm) {
		calculate_totals(frm);
		calculate_payments(frm);
	},
});

function calculate_item_row(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	const base_amount = flt(row.qty) * flt(row.rate);
	if (flt(row.discount_percent)) {
		row.discount_amount = (base_amount * flt(row.discount_percent)) / 100;
	}
	row.amount = base_amount - flt(row.discount_amount);
	row.tax_amount = (flt(row.amount) * flt(row.tax_rate)) / 100;
	row.total = flt(row.amount) + flt(row.tax_amount);
	frm.refresh_field("items");
	calculate_totals(frm);
	calculate_payments(frm);
}

function calculate_totals(frm) {
	let total_qty = 0,
		net_total = 0,
		discount_total = 0,
		tax_total = 0,
		grand_total = 0;
	(frm.doc.items || []).forEach((r) => {
		total_qty += flt(r.qty);
		net_total += flt(r.amount);
		discount_total += flt(r.discount_amount);
		tax_total += flt(r.tax_amount);
		grand_total += flt(r.total);
	});
	frm.set_value("total_qty", total_qty);
	frm.set_value("total", net_total);
	frm.set_value("total_discount", discount_total);
	frm.set_value("total_taxes_and_charges", tax_total);
	frm.set_value("grand_total", grand_total);
}

// ---------- Payments ----------
frappe.ui.form.on("Branch Sales Payment", {
	amount(frm) {
		calculate_payments(frm);
	},
	payments_remove(frm) {
		calculate_payments(frm);
	},
});

function calculate_payments(frm) {
	let total_payment = 0;
	(frm.doc.payments || []).forEach((r) => {
		total_payment += flt(r.amount);
	});
	frm.set_value("total_payment_amount", total_payment);
	frm.set_value("net_payment", flt(frm.doc.grand_total) - total_payment);
}
