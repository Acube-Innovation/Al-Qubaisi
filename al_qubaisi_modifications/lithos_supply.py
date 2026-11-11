# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

"""Supply from Al Qubaisi Group to the outlet companies (M7).

Al Qubaisi Group buys and holds the stock. An outlet asks for goods with a Material
Request of the "Sales Order" purpose in Al Qubaisi Group, which becomes Sales Order
-> Delivery Note -> Sales Invoice to the outlet's internal customer, at Al Qubaisi
Group's own cost (D12): the Sales Order at the current valuation rate, the invoice
at the cost the Delivery Note actually took out of stock.

Submitting that invoice creates the outlet's inter-company Purchase Invoice as a
draft, updating stock into the outlet warehouse. The outlet submits it once it has
counted what arrived; anything short is noted on Al Qubaisi Group's invoice.

Branches without an outlet company of their own keep today's flow: their branch
customer, priced from the price list.
"""

import frappe
from frappe import _
from frappe.utils import flt, strip_html

from al_qubaisi_modifications.api import resolve_branch_customer
from al_qubaisi_modifications.lithos_setup import INTER_COMPANY_PRICE_LIST, SALES_TAX_TEMPLATE


def outlet_company(customer, company):
	"""The outlet company ``customer`` stands for, when ``company`` sells to it. Else None."""
	if not customer:
		return None
	internal, represents = frappe.get_cached_value(
		"Customer", customer, ["is_internal_customer", "represents_company"]
	) or (0, None)
	if internal and represents and represents != company:
		return represents
	return None


def supply_customer(branch, company):
	"""Who a supply to ``branch`` from ``company`` is invoiced to.

	A branch whose Lithos Store posts into its own outlet company buys as that
	company's internal customer; any other branch as its branch customer.
	"""
	if not branch:
		return None
	outlet = frappe.db.get_value("Lithos Store", {"branch": branch}, "company")
	if outlet and outlet != company:
		internal = frappe.db.get_value("Customer", {"is_internal_customer": 1, "represents_company": outlet})
		if internal and frappe.db.exists(
			"Allowed To Transact With", {"parent": internal, "parenttype": "Customer", "company": company}
		):
			return internal
	return resolve_branch_customer(branch)


@frappe.whitelist()
def get_supply_customer(branch, company):
	"""For the Material Request form: the customer for a branch's request."""
	return supply_customer(branch, company)


def valuation_rate(item_code, company, warehouse=None):
	# The internal helper: the whitelisted one checks the user's warehouse permissions,
	# and a supply priced at cost must not depend on who saves the order.
	from erpnext.stock.get_item_details import _get_valuation_rate

	return flt((_get_valuation_rate(item_code, company, warehouse) or {}).get("valuation_rate"))


def use_inter_company_price_list(doc):
	if frappe.db.exists("Price List", INTER_COMPANY_PRICE_LIST):
		doc.selling_price_list = INTER_COMPANY_PRICE_LIST
	doc.ignore_pricing_rule = 1


def price_at_cost(doc):
	"""D12: an order to an outlet carries Al Qubaisi Group's valuation rate, not a selling price."""
	if doc.docstatus != 0 or not outlet_company(doc.customer, doc.company):
		return
	use_inter_company_price_list(doc)
	for item in doc.items:
		warehouse = item.get("warehouse") or doc.get("set_warehouse")
		rate = valuation_rate(item.item_code, doc.company, warehouse) * flt(item.conversion_factor or 1)
		item.update(
			{
				"price_list_rate": rate,
				"rate": rate,
				"discount_percentage": 0,
				"discount_amount": 0,
				"margin_rate_or_amount": 0,
			}
		)


def invoice_at_delivered_cost(doc):
	"""D12 on the invoice: each delivered line at the cost its Delivery Note took out of stock."""
	if doc.docstatus != 0 or doc.is_return or not outlet_company(doc.customer, doc.company):
		return
	use_inter_company_price_list(doc)
	for item in doc.items:
		if not item.dn_detail:
			continue
		incoming = flt(frappe.db.get_value("Delivery Note Item", item.dn_detail, "incoming_rate"))
		if incoming:
			rate = incoming * flt(item.conversion_factor or 1)
			item.update(
				{
					"price_list_rate": rate,
					"rate": rate,
					"discount_percentage": 0,
					"discount_amount": 0,
					"margin_rate_or_amount": 0,
				}
			)


def sales_order_before_validate(doc, method=None):
	price_at_cost(doc)


def sales_invoice_before_validate(doc, method=None):
	invoice_at_delivered_cost(doc)


def outlet_store(company):
	"""The outlet's first Lithos Store: where supplies are received, and their cost center."""
	return frappe.db.get_value(
		"Lithos Store",
		{"company": company, "warehouse": ("is", "set")},
		["name", "warehouse", "cost_center"],
		as_dict=True,
		order_by="name",
	)


def make_outlet_purchase_invoice(invoice, outlet):
	"""The outlet's draft Purchase Invoice for an Al Qubaisi Group invoice, receiving into its store."""
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_inter_company_purchase_invoice

	store = outlet_store(outlet)
	if not store:
		frappe.throw(_("{0} has no Lithos Store with a warehouse to receive into.").format(outlet))

	pi = make_inter_company_purchase_invoice(invoice.name)
	pi.update(
		{
			"update_stock": 1,
			"set_warehouse": store.warehouse,
			"bill_no": invoice.name,
			"bill_date": invoice.posting_date,
			# Rounded the same way, so what the outlet owes is what Al Qubaisi Group is owed.
			"disable_rounded_total": invoice.disable_rounded_total,
			"cost_center": store.cost_center,
			"remarks": _("Supply from {0}, invoice {1}. Submit once the goods are counted in.").format(
				invoice.company, invoice.name
			),
		}
	)
	branch = {row.name: row.get("branch") for row in invoice.items}
	for item in pi.items:
		item.warehouse = store.warehouse
		item.cost_center = store.cost_center
		item.branch = branch.get(item.sales_invoice_item) or invoice.get("branch")

	# VAT follows the invoice: the outlet claims input VAT only when Al Qubaisi Group
	# charged it, through its own purchase template.
	pi.taxes_and_charges = None
	pi.set("taxes", [])
	if invoice.taxes:
		set_input_vat(pi, outlet)
	pi.insert(ignore_permissions=True)
	if flt(pi.total_taxes_and_charges, 2) != flt(invoice.total_taxes_and_charges, 2):
		pi.add_comment(
			"Comment",
			_("Input VAT {0} differs from the {1} charged on {2}: check the taxes before submitting.").format(
				flt(pi.total_taxes_and_charges, 2), flt(invoice.total_taxes_and_charges, 2), invoice.name
			),
		)
	return pi


def set_input_vat(pi, outlet):
	from erpnext.controllers.accounts_controller import get_taxes_and_charges

	abbr = frappe.get_cached_value("Company", outlet, "abbr")
	template = f"{SALES_TAX_TEMPLATE} - {abbr}"
	if not frappe.db.exists("Purchase Taxes and Charges Template", template):
		return
	pi.taxes_and_charges = template
	for row in get_taxes_and_charges("Purchase Taxes and Charges Template", template):
		pi.append("taxes", row)


def sales_invoice_on_submit(doc, method=None):
	"""Draft the outlet's Purchase Invoice. A problem here never stops the invoice itself."""
	outlet = outlet_company(doc.customer, doc.company)
	if not outlet or doc.is_return:
		return
	if frappe.db.exists(
		"Purchase Invoice", {"inter_company_invoice_reference": doc.name, "docstatus": ("<", 2)}
	):
		return

	frappe.db.savepoint("lithos_supply")
	try:
		pi = make_outlet_purchase_invoice(doc, outlet)
	except Exception as e:
		frappe.db.rollback(save_point="lithos_supply")
		frappe.log_error(
			title=f"Outlet Purchase Invoice not created ({doc.name})",
			message=frappe.get_traceback(with_context=True),
			reference_doctype="Sales Invoice",
			reference_name=doc.name,
		)
		doc.add_comment(
			"Comment",
			_("The outlet's Purchase Invoice was not created: {0}. Use Create > Purchase Invoice.").format(
				strip_html(str(e))[:300]
			),
		)
		return
	doc.add_comment(
		"Comment",
		_("Draft Purchase Invoice {0} created in {1}, receiving into {2}, for the outlet to confirm.").format(
			frappe.get_desk_link("Purchase Invoice", pi.name), outlet, pi.set_warehouse
		),
	)


def sales_invoice_before_cancel(doc, method=None):
	"""An outlet invoice nobody has confirmed goes with Al Qubaisi Group's invoice."""
	if not outlet_company(doc.customer, doc.company):
		return
	for name in frappe.get_all(
		"Purchase Invoice",
		filters={"inter_company_invoice_reference": doc.name, "docstatus": 0},
		pluck="name",
	):
		frappe.delete_doc("Purchase Invoice", name, ignore_permissions=True)


def purchase_invoice_on_submit(doc, method=None):
	"""Note on Al Qubaisi Group's invoice what the outlet received short."""
	if not (doc.is_internal_supplier and doc.inter_company_invoice_reference):
		return
	received = {}
	for item in doc.items:
		if item.sales_invoice_item:
			received[item.sales_invoice_item] = received.get(item.sales_invoice_item, 0) + flt(item.stock_qty)

	short = []
	for row in frappe.get_all(
		"Sales Invoice Item",
		filters={"parent": doc.inter_company_invoice_reference},
		fields=["name", "item_code", "stock_qty", "stock_uom"],
	):
		got = received.get(row.name, 0)
		if flt(got, 3) < flt(row.stock_qty, 3):
			short.append(f"{row.item_code} {flt(got, 3)} of {flt(row.stock_qty, 3)} {row.stock_uom}")
	if short:
		frappe.get_doc("Sales Invoice", doc.inter_company_invoice_reference).add_comment(
			"Comment",
			_("{0} received less than invoiced on {1}: {2}. Issue a credit note for the difference.").format(
				doc.company, frappe.get_desk_link("Purchase Invoice", doc.name), "; ".join(short)
			),
		)
