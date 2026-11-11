# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

# Posting defaults that must belong to the store's company. A warehouse or account
# from another company would post the outlet's stock and costs into that company.
COMPANY_LINKS = (
	("warehouse", "Warehouse"),
	("cost_center", "Cost Center"),
	("consumption_account", "Account"),
	("taxes_and_charges", "Sales Taxes and Charges Template"),
)


class LithosStore(Document):
	def validate(self):
		# The id is part of the API URL, so stray whitespace would 404 every night.
		self.store_id = (self.store_id or "").strip()
		self.validate_company_links()
		self.validate_ledger()

	def validate_ledger(self):
		# The TRN gate (M2): a store posts revenue and VAT only once its company can carry them.
		if not self.ledger_from_date:
			return
		from al_qubaisi_modifications.lithos_ledger import ledger_problems

		problems = ledger_problems(self)
		if problems:
			frappe.throw(
				_("Ledger From Date needs these first:")
				+ "<ul>"
				+ "".join(f"<li>{frappe.utils.escape_html(p)}</li>" for p in problems)
				+ "</ul>",
				title=_("Ledger not ready"),
			)

	def validate_company_links(self):
		for fieldname, doctype in COMPANY_LINKS:
			value = self.get(fieldname)
			if value and frappe.db.get_value(doctype, value, "company") != self.company:
				frappe.throw(
					_("{0} {1} does not belong to company {2}.").format(
						_(self.meta.get_label(fieldname)), frappe.bold(value), frappe.bold(self.company)
					)
				)
