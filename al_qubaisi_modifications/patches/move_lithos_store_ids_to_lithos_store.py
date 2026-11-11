# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

"""Move the LithosPOS store wiring from Branch onto Lithos Store.

Each Branch with ``custom_lithos_store_id`` becomes one Lithos Store, posting into
exactly the company the sync used before: the company of the branch's cost centre,
else the old LithosPOS Settings default company. Moving the live stores to their
outlet companies is a separate step, done together with the re-post of their
history (D3), so this patch changes nothing about where tonight's sales land.

Idempotent: a store id that already has a Lithos Store is left alone.
"""

import frappe

from al_qubaisi_modifications.api import resolve_branch_customer


def execute():
	if not frappe.db.has_column("Branch", "custom_lithos_store_id"):
		return

	# default_company is no longer a field on the settings doctype, so its stored
	# value has to be read straight from tabSingles.
	default_company = frappe.db.sql(
		"""select value from `tabSingles`
		where doctype = 'LithosPOS Settings' and field = 'default_company'"""
	)
	default_company = default_company[0][0] if default_company else None

	for branch in frappe.db.sql(
		"""select name, custom_lithos_store_id, custom_lithos_sync_enabled, custom_cost_center
		from `tabBranch` where ifnull(custom_lithos_store_id, '') != ''""",
		as_dict=True,
	):
		store_id = branch.custom_lithos_store_id.strip()
		if frappe.db.exists("Lithos Store", store_id):
			continue

		cost_center_company = (
			frappe.db.get_value("Cost Center", branch.custom_cost_center, "company")
			if branch.custom_cost_center
			else None
		)
		company = cost_center_company or default_company
		if not company:
			print(f"Lithos store {store_id} ({branch.name}): no company to post into, skipped.")
			continue

		first_posted = frappe.db.sql(
			"""select min(posting_date) from `tabBranch Sales`
			where lithos_store_id = %s and docstatus < 2""",
			store_id,
		)[0][0]

		frappe.get_doc(
			{
				"doctype": "Lithos Store",
				"store_id": store_id,
				"outlet_name": branch.name,
				"company": company,
				"branch": branch.name,
				"cost_center": branch.custom_cost_center if cost_center_company else None,
				"customer": resolve_branch_customer(branch.name),
				"sync_enabled": branch.custom_lithos_sync_enabled,
				"sync_from_date": first_posted,
				"notes": "Moved from the Branch record. Moves to its outlet company with the re-post (D3).",
			}
		).insert(ignore_permissions=True)
