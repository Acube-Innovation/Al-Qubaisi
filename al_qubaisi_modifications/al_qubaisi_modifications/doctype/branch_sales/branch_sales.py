# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class BranchSales(Document):
	def validate(self):
		self.calculate_totals()
		self.calculate_payments()

	def calculate_totals(self):
		"""Compute per-line amounts and the Sales-Invoice-style document totals."""
		total_qty = net_total = tax_total = grand_total = 0.0

		for row in self.items:
			row.amount = flt(row.qty) * flt(row.rate)
			row.tax_amount = flt(row.amount) * flt(row.tax_rate) / 100.0
			row.total = flt(row.amount) + flt(row.tax_amount)

			total_qty += flt(row.qty)
			net_total += flt(row.amount)
			tax_total += flt(row.tax_amount)
			grand_total += flt(row.total)

		self.total_qty = total_qty
		self.total = net_total
		self.total_taxes_and_charges = tax_total
		self.grand_total = grand_total

	def calculate_payments(self):
		"""Compute per-mode discount / net and the payment summary totals."""
		total_payment = total_discount = net_payment = 0.0

		for row in self.payments:
			row.discount_amount = flt(row.amount) * flt(row.discount_percentage) / 100.0
			row.net_amount = flt(row.amount) - flt(row.discount_amount)

			total_payment += flt(row.amount)
			total_discount += flt(row.discount_amount)
			net_payment += flt(row.net_amount)

		self.total_payment_amount = total_payment
		self.total_discount = total_discount
		self.net_payment = net_payment
