# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class BranchSales(Document):
	def validate(self):
		self.validate_cost_center()
		self.calculate_totals()
		self.calculate_payments()

	def on_submit(self):
		# A Lithos day past its store's Ledger From Date becomes a Sales Invoice and a
		# recipe Material Issue in the outlet company (M6).
		from al_qubaisi_modifications.lithos_ledger import on_submit

		on_submit(self)

	def on_cancel(self):
		from al_qubaisi_modifications.lithos_ledger import on_cancel

		on_cancel(self)

	def validate_cost_center(self):
		# The branch's cost center is only a fallback, and a branch can outlive a move
		# of its store to another company (D3).
		if (
			self.cost_center
			and frappe.db.get_value("Cost Center", self.cost_center, "company") != self.company
		):
			frappe.throw(
				_("Cost Center {0} does not belong to company {1}.").format(
					frappe.bold(self.cost_center), frappe.bold(self.company)
				)
			)

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
