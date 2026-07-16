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
		"""Compute per-line discount, amount, tax and the document totals."""
		total_qty = net_total = tax_total = grand_total = discount_total = 0.0

		for row in self.items:
			base_amount = flt(row.qty) * flt(row.rate)

			# Percent takes precedence; else use a manually entered discount amount.
			if flt(row.discount_percent):
				row.discount_amount = base_amount * flt(row.discount_percent) / 100.0

			row.amount = base_amount - flt(row.discount_amount)
			row.tax_amount = flt(row.amount) * flt(row.tax_rate) / 100.0
			row.total = flt(row.amount) + flt(row.tax_amount)

			total_qty += flt(row.qty)
			net_total += flt(row.amount)
			tax_total += flt(row.tax_amount)
			grand_total += flt(row.total)
			discount_total += flt(row.discount_amount)

		self.total_qty = total_qty
		self.total = net_total
		self.total_discount = discount_total
		self.total_taxes_and_charges = tax_total
		self.grand_total = grand_total

	def calculate_payments(self):
		"""Sum the payment lines and compute the balance against the grand total."""
		total_payment = sum(flt(row.amount) for row in self.payments)

		self.total_payment_amount = total_payment
		self.net_payment = flt(self.grand_total) - total_payment
