# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.model.mapper import get_mapped_doc


def resolve_branch_customer(branch):
	"""Return the branch-customer mapped to a given Branch (custom_is_branch = 1)."""
	if not branch:
		return None
	return frappe.db.get_value(
		"Customer", {"custom_branch": branch, "custom_is_branch": 1}, "name"
	)


@frappe.whitelist()
def make_sales_order(source_name, target_doc=None):
	"""Map a Material Request to a Sales Order, using the branch-customer saved on the MR."""

	def set_missing_values(source, target):
		target.customer = source.custom_customer or resolve_branch_customer(source.custom_branch)
		target.company = source.company
		if source.schedule_date:
			target.delivery_date = source.schedule_date
		target.run_method("set_missing_values")
		target.run_method("calculate_taxes_and_totals")

	def update_item(source, target, source_parent):
		target.warehouse = source.warehouse
		# Seed a selling rate from the item master so the SO is not zero-value.
		target.rate = frappe.db.get_value("Item", source.item_code, "standard_rate") or 0
		if source.schedule_date:
			target.delivery_date = source.schedule_date

	doclist = get_mapped_doc(
		"Material Request",
		source_name,
		{
			"Material Request": {
				"doctype": "Sales Order",
				"validation": {"docstatus": ["=", 1]},
			},
			"Material Request Item": {
				"doctype": "Sales Order Item",
				"field_map": {
					"name": "material_request_item",
					"parent": "material_request",
					"uom": "stock_uom",
				},
				"postprocess": update_item,
			},
		},
		target_doc,
		set_missing_values,
	)
	return doclist
