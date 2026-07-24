import frappe
from frappe import _
from frappe.model.mapper import get_mapped_doc
from frappe.utils import flt

from erpnext.stock.doctype.material_request.material_request import MaterialRequest

# The custom "Sales Order" purpose lets a Material Request collect items that are
# meant to be sold to a customer and then turned into a Sales Order.
SALES_ORDER_PURPOSE = "Sales Order"


class CustomMaterialRequest(MaterialRequest):
	def validate_material_request_type(self):
		# The stock controller clears `customer` for every purpose except
		# "Customer Provided". Our "Sales Order" purpose also needs the customer,
		# so keep it for both.
		if self.material_request_type not in ("Customer Provided", SALES_ORDER_PURPOSE):
			self.customer = None


@frappe.whitelist()
def make_sales_order(source_name, target_doc=None):
	"""Map a "Sales Order" purpose Material Request onto a Sales Order.

	Used both by the "Create > Sales Order" button on the Material Request and by
	the "Get Items From > Material Request" button on the Sales Order.
	"""

	def set_missing_values(source, target):
		if source.customer and not target.customer:
			target.customer = source.customer

		# Carry the parent Branch accounting dimension onto the Sales Order.
		if source.get("branch") and not target.get("branch"):
			target.branch = source.branch

		target.run_method("set_missing_values")
		target.run_method("calculate_taxes_and_totals")

	def update_item(source, target, source_parent):
		target.qty = flt(source.qty)
		target.stock_qty = flt(source.stock_qty)
		# Rates on a Material Request are buying rates; let the Sales Order fetch
		# selling rates from the price list during set_missing_values.
		target.rate = 0
		target.price_list_rate = 0
		if source_parent.get("branch"):
			target.branch = source_parent.branch

	doc = get_mapped_doc(
		"Material Request",
		source_name,
		{
			"Material Request": {
				"doctype": "Sales Order",
				"field_map": {"schedule_date": "delivery_date"},
				"validation": {
					"docstatus": ["=", 1],
					"material_request_type": ["=", SALES_ORDER_PURPOSE],
				},
			},
			"Material Request Item": {
				"doctype": "Sales Order Item",
				"field_map": {
					"name": "material_request_item",
					"parent": "material_request",
					"schedule_date": "delivery_date",
					"bom_no": "bom_no",
				},
				"field_no_map": [
					"rate",
					"amount",
					"price_list_rate",
					"base_rate",
					"base_amount",
					"base_price_list_rate",
				],
				"postprocess": update_item,
				"condition": lambda item: flt(item.qty) > 0,
			},
		},
		target_doc,
		set_missing_values,
	)

	return doc
