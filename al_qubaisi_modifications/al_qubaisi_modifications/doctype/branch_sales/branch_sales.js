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
	tax_rate: calculate_item_row,
	items_remove(frm) {
		calculate_totals(frm);
	},
});

function calculate_item_row(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	row.amount = flt(row.qty) * flt(row.rate);
	row.tax_amount = (flt(row.amount) * flt(row.tax_rate)) / 100;
	row.total = flt(row.amount) + flt(row.tax_amount);
	frm.refresh_field("items");
	calculate_totals(frm);
}

function calculate_totals(frm) {
	let total_qty = 0,
		net_total = 0,
		tax_total = 0,
		grand_total = 0;
	(frm.doc.items || []).forEach((r) => {
		total_qty += flt(r.qty);
		net_total += flt(r.amount);
		tax_total += flt(r.tax_amount);
		grand_total += flt(r.total);
	});
	frm.set_value("total_qty", total_qty);
	frm.set_value("total", net_total);
	frm.set_value("total_taxes_and_charges", tax_total);
	frm.set_value("grand_total", grand_total);
}

// ---------- Payments ----------
frappe.ui.form.on("Branch Sales Payment", {
	amount: calculate_payment_row,
	discount_percentage: calculate_payment_row,
	payments_remove(frm) {
		calculate_payments(frm);
	},
});

function calculate_payment_row(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	row.discount_amount = (flt(row.amount) * flt(row.discount_percentage)) / 100;
	row.net_amount = flt(row.amount) - flt(row.discount_amount);
	frm.refresh_field("payments");
	calculate_payments(frm);
}

function calculate_payments(frm) {
	let total_payment = 0,
		total_discount = 0,
		net_payment = 0;
	(frm.doc.payments || []).forEach((r) => {
		total_payment += flt(r.amount);
		total_discount += flt(r.discount_amount);
		net_payment += flt(r.net_amount);
	});
	frm.set_value("total_payment_amount", total_payment);
	frm.set_value("total_discount", total_discount);
	frm.set_value("net_payment", net_payment);
}
