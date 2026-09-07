# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

"""Post-dated cheque helpers.

A post-dated cheque is entered as an ordinary Payment Entry whose Reference Date
(the date printed on the cheque) is later than the posting date, and whose bank
side points at a PDC control account instead of the real bank. That clears the
party's outstanding straight away while leaving the bank balance untouched.

When the cheque is finally banked, the money still has to move from the control
account into the bank. ERPNext has no action for that step, so this module builds
the Journal Entry for it and links it back to the Payment Entry.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate

# The realisation entry is a bank movement, so it is stamped with the cheque
# details the same way a manually written Bank Entry would be.
BANK_ENTRY = "Bank Entry"


def is_post_dated(pe) -> bool:
	"""True when the cheque date falls after the date the payment was booked."""
	if not (pe.reference_date and pe.posting_date):
		return False

	return getdate(pe.reference_date) > getdate(pe.posting_date)


def get_control_account(pe):
	"""The account holding the cheque until it is banked, and the side it sits on.

	On a receipt the cheque was debited to the control account, so realising it
	credits that account and debits the bank. On a payment it is the mirror.
	"""
	if pe.payment_type == "Receive":
		return pe.paid_to, "credit"

	return pe.paid_from, "debit"


def get_existing_bank_entry(payment_entry: str) -> str | None:
	"""Name of a draft/submitted realisation Journal Entry, if one already exists."""
	return frappe.db.get_value(
		"Journal Entry",
		{"custom_pdc_payment_entry": payment_entry, "docstatus": ("<", 2)},
		"name",
	)


@frappe.whitelist()
def make_bank_entry(payment_entry: str):
	"""Return an unsaved Journal Entry moving a realised cheque into the bank."""
	pe = frappe.get_doc("Payment Entry", payment_entry)
	pe.check_permission("read")

	if pe.docstatus != 1:
		frappe.throw(_("Payment Entry {0} is not submitted").format(pe.name))

	if pe.payment_type not in ("Receive", "Pay"):
		frappe.throw(_("Only Receive and Pay payments can be realised through a bank entry"))

	if not pe.reference_no:
		frappe.throw(_("Payment Entry {0} has no Cheque/Reference No").format(pe.name))

	existing = get_existing_bank_entry(pe.name)
	if existing:
		frappe.throw(
			_("Bank entry {0} already exists for this cheque").format(
				frappe.utils.get_link_to_form("Journal Entry", existing)
			)
		)

	control_account, control_side = get_control_account(pe)
	# Amounts are taken in company currency: the control account and the bank are
	# both company-currency ledgers in every PDC setup we support.
	amount = flt(pe.base_received_amount if pe.payment_type == "Receive" else pe.base_paid_amount)

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = BANK_ENTRY
	je.company = pe.company
	# The cheque is realised on the day it is banked, which is the date printed on it.
	je.posting_date = pe.reference_date
	je.cheque_no = pe.reference_no
	je.cheque_date = pe.reference_date
	je.custom_pdc_payment_entry = pe.name
	je.user_remark = _("Cheque {0} dated {1} realised against {2}").format(
		pe.reference_no, frappe.format(pe.reference_date, {"fieldtype": "Date"}), pe.name
	)

	control_row = {
		"account": control_account,
		"cost_center": pe.cost_center,
		f"{control_side}_in_account_currency": amount,
	}
	# The party is carried across only when the control account is a party ledger,
	# otherwise Journal Entry rejects the row.
	if frappe.get_cached_value("Account", control_account, "account_type") in ("Receivable", "Payable"):
		control_row.update({"party_type": pe.party_type, "party": pe.party})

	bank_side = "debit" if control_side == "credit" else "credit"

	je.append("accounts", control_row)
	# The bank account is left blank on purpose: a company can hold several bank
	# ledgers and picking the wrong one is the classic mistake this entry invites.
	je.append(
		"accounts",
		{
			"account": None,
			"cost_center": pe.cost_center,
			f"{bank_side}_in_account_currency": amount,
		},
	)

	return je.as_dict()
