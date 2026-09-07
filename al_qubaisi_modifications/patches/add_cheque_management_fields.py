# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

"""Wire up the two cheque-handling features.

* Journal Entry gains a link back to the post-dated Payment Entry it realises,
  which is what stops a cheque being banked twice and drives the Connections tab.
* Payment Entry gains a link back to the Multi Cheque Payment batch that made it.

Both are idempotent, so re-running `bench migrate` is safe.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

CUSTOM_FIELDS = {
	"Journal Entry": [
		{
			"fieldname": "custom_pdc_payment_entry",
			"label": "PDC Payment Entry",
			"fieldtype": "Link",
			"options": "Payment Entry",
			"insert_after": "cheque_date",
			"read_only": 1,
			"no_copy": 1,
			"print_hide": 1,
			"description": "Post-dated cheque payment this bank entry realises.",
		}
	],
	"Payment Entry": [
		{
			"fieldname": "custom_multi_cheque_payment",
			"label": "Multi Cheque Payment",
			"fieldtype": "Link",
			"options": "Multi Cheque Payment",
			"insert_after": "reference_date",
			"read_only": 1,
			"no_copy": 1,
			"print_hide": 1,
			"description": "Cheque batch this payment was generated from.",
		}
	],
}

# Connections shown on the Payment Entry form. Multi Cheque Payment declares its
# own link in the doctype JSON; Payment Entry is a core doctype so its link has to
# be added as a custom row here.
DOCTYPE_LINKS = [
	{
		"parent": "Payment Entry",
		"link_doctype": "Journal Entry",
		"link_fieldname": "custom_pdc_payment_entry",
		"group": "Bank Entry",
	}
]


def execute():
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)

	for link in DOCTYPE_LINKS:
		add_doctype_link(link)


def add_doctype_link(link):
	exists = frappe.db.exists(
		"DocType Link",
		{
			"parent": link["parent"],
			"link_doctype": link["link_doctype"],
			"link_fieldname": link["link_fieldname"],
		},
	)
	if exists:
		return

	doctype = frappe.get_doc("DocType", link["parent"])
	doctype.append(
		"links",
		{
			"link_doctype": link["link_doctype"],
			"link_fieldname": link["link_fieldname"],
			"group": link.get("group"),
			"custom": 1,
		},
	)
	# Core doctypes are read-only outside developer mode; only the child row matters.
	doctype.flags.ignore_validate = True
	doctype.save(ignore_permissions=True)
