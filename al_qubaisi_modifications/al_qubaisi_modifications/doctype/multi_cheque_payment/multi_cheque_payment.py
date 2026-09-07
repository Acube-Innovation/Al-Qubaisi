# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

"""Capture a batch of cheques handed over by (or issued to) one party.

ERPNext holds a single Cheque/Reference No per Payment Entry, so a customer
dropping off six cheques means six documents typed by hand. This doctype takes
them in one grid and, on submit, creates one Payment Entry per cheque — keeping
per-cheque clearance tracking intact, which is the whole point of the PDC
control account. The generated payments appear under Connections.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate

INVOICE_BY_PAYMENT_TYPE = {"Receive": "Sales Invoice", "Pay": "Purchase Invoice"}
PARTY_BY_PAYMENT_TYPE = {"Receive": "Customer", "Pay": "Supplier"}


class MultiChequePayment(Document):
	def validate(self):
		self.validate_party_type()
		self.set_reference_doctype()
		self.validate_cheques()
		self.set_totals()

	def on_submit(self):
		self.create_payment_entries()

	def on_cancel(self):
		# Runs before Frappe's back-link check, so the payments are already
		# cancelled by the time it looks for submitted documents pointing here.
		self.cancel_payment_entries()

	def validate_party_type(self):
		expected = PARTY_BY_PAYMENT_TYPE[self.payment_type]
		if self.party_type != expected:
			frappe.throw(
				_("Party Type must be {0} for a {1} payment").format(
					frappe.bold(expected), frappe.bold(self.payment_type)
				)
			)

	def set_reference_doctype(self):
		invoice_type = INVOICE_BY_PAYMENT_TYPE[self.payment_type]
		for row in self.cheques:
			row.reference_doctype = invoice_type if row.reference_name else None

	def validate_cheques(self):
		if not self.cheques:
			frappe.throw(_("Add at least one cheque"))

		seen = {}
		for row in self.cheques:
			if flt(row.amount) <= 0:
				frappe.throw(_("Row #{0}: Amount must be greater than zero").format(row.idx))

			key = (row.cheque_no or "").strip().lower()
			if key in seen:
				frappe.throw(
					_("Row #{0}: Cheque No {1} is already entered in row #{2}").format(
						row.idx, frappe.bold(row.cheque_no), seen[key]
					)
				)
			seen[key] = row.idx

			self.warn_if_cheque_reused(row)

	def warn_if_cheque_reused(self, row):
		"""A repeated cheque number for the same party is nearly always a typo."""
		duplicate = frappe.db.exists(
			"Payment Entry",
			{
				"docstatus": 1,
				"company": self.company,
				"party_type": self.party_type,
				"party": self.party,
				"reference_no": row.cheque_no,
			},
		)
		if duplicate:
			frappe.msgprint(
				_("Row #{0}: Cheque No {1} was already used on {2}").format(
					row.idx,
					frappe.bold(row.cheque_no),
					frappe.utils.get_link_to_form("Payment Entry", duplicate),
				),
				title=_("Possible Duplicate Cheque"),
				indicator="orange",
			)

	def set_totals(self):
		self.total_amount = sum(flt(row.amount) for row in self.cheques)
		self.cheque_count = len(self.cheques)

	def create_payment_entries(self):
		for row in self.cheques:
			pe = self.build_payment_entry(row)
			pe.insert()
			pe.submit()
			row.db_set("payment_entry", pe.name, update_modified=False)

		frappe.msgprint(
			_("{0} Payment Entries created").format(len(self.cheques)),
			alert=True,
		)

	def build_payment_entry(self, row):
		pe = frappe.new_doc("Payment Entry")
		pe.payment_type = self.payment_type
		pe.company = self.company
		pe.posting_date = self.posting_date
		pe.party_type = self.party_type
		pe.party = self.party
		pe.mode_of_payment = self.mode_of_payment
		pe.cost_center = self.cost_center
		pe.custom_multi_cheque_payment = self.name

		# The cheque date is what makes this post-dated: it parks the money in the
		# control account until the cheque is actually banked.
		pe.reference_no = row.cheque_no
		pe.reference_date = row.cheque_date

		if self.payment_type == "Receive":
			pe.paid_from = self.party_account
			pe.paid_to = self.deposit_account
		else:
			pe.paid_from = self.deposit_account
			pe.paid_to = self.party_account

		pe.paid_amount = flt(row.amount)
		pe.received_amount = flt(row.amount)

		if row.reference_name:
			pe.append(
				"references",
				{
					"reference_doctype": row.reference_doctype,
					"reference_name": row.reference_name,
					"allocated_amount": flt(row.amount),
				},
			)

		return pe

	def cancel_payment_entries(self):
		for row in self.cheques:
			if not row.payment_entry:
				continue

			if not frappe.db.exists("Payment Entry", row.payment_entry):
				continue

			pe = frappe.get_doc("Payment Entry", row.payment_entry)
			if pe.docstatus == 1:
				pe.cancel()


@frappe.whitelist()
def get_party_defaults(company: str, party_type: str, party: str):
	"""Party account and current balance, for pre-filling the form."""
	from erpnext.accounts.party import get_party_account

	frappe.has_permission(party_type, "read", party, throw=True)

	account = get_party_account(party_type, party, company)
	balance = 0.0
	if account:
		from erpnext.accounts.utils import get_balance_on

		balance = get_balance_on(party=party, party_type=party_type, company=company)

	return {"party_account": account, "party_balance": balance}
