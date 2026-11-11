// Al-Qubaisi: "Sales Order" purpose for Material Request.
//
// When the purpose is "Sales Order" the request collects items to be sold to a
// customer. Selecting the Branch accounting dimension pre-fills the customer that
// represents that branch, and a "Create > Sales Order" button turns the submitted
// request into a Sales Order (required-by date -> delivery date).

const SALES_ORDER_PURPOSE = "Sales Order";

frappe.ui.form.on("Material Request", {
	refresh(frm) {
		toggle_customer_reqd(frm);
		add_create_sales_order_button(frm);
	},

	material_request_type(frm) {
		toggle_customer_reqd(frm);
	},

	branch(frm) {
		set_customer_from_branch(frm);
	},
});

function toggle_customer_reqd(frm) {
	// Core JS only makes customer mandatory for "Customer Provided"; keep it
	// mandatory for our "Sales Order" purpose too.
	const needs_customer = [SALES_ORDER_PURPOSE, "Customer Provided"].includes(
		frm.doc.material_request_type
	);
	frm.toggle_reqd("customer", needs_customer);
}

function add_create_sales_order_button(frm) {
	if (
		frm.doc.docstatus === 1 &&
		frm.doc.material_request_type === SALES_ORDER_PURPOSE &&
		flt(frm.doc.per_ordered) < 100
	) {
		frm.add_custom_button(
			__("Sales Order"),
			() => {
				frappe.model.open_mapped_doc({
					method: "al_qubaisi_modifications.overrides.material_request.make_sales_order",
					frm: frm,
				});
			},
			__("Create")
		);
		frm.page.set_inner_btn_group_as_primary(__("Create"));
	}
}

function set_customer_from_branch(frm) {
	// The Accounting Dimensions section (and its Branch field) is always visible,
	// but auto-selecting the customer only makes sense for the Sales Order purpose.
	if (frm.doc.material_request_type !== SALES_ORDER_PURPOSE || !frm.doc.branch) {
		return;
	}

	// A branch whose store has its own outlet company buys as that company's
	// internal customer; any other branch as its branch customer (M7).
	frappe
		.call({
			method: "al_qubaisi_modifications.lithos_supply.get_supply_customer",
			args: { branch: frm.doc.branch, company: frm.doc.company },
		})
		.then((r) => {
			if (r.message) {
				frm.set_value("customer", r.message);
			}
		});
}
