# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

"""Move a store's posted history to its outlet company (D3), and rebuild posted days.

Branch Sales posts nothing to the ledger or to stock yet, so moving a day is a cancel
and an amendment. The amendment is posted in the store's company with its lines
resolved again through the Lithos Item Map, and it must match the cancelled day to
the fil or nothing is changed. Amounts are copied from the posted day, which
reconciled with Lithos when it was posted, so no call to LithosPOS is needed.

The same rebuild moves lines off the suspense item once their items are mapped.

Set Re-post Into Company on the Lithos Store (the workbook import does it for stores
that are live in another company), then use its Re-post button, or::

	bench --site <site> execute al_qubaisi_modifications.lithos_repost.preview_move --args "['141']"
"""

from collections import Counter

import frappe
from frappe import _
from frappe.utils import flt, getdate, now_datetime, today

from al_qubaisi_modifications import lithos
from al_qubaisi_modifications.lithos_setup import CONSUMPTION, SALES_TAX_TEMPLATE

# Totals an amendment must reproduce exactly.
RECONCILED = ("total_qty", "total", "total_taxes_and_charges", "grand_total")


def outlet_defaults(company):
	"""The posting defaults a store takes from its outlet company, as lithos_setup creates them."""
	abbr, cost_center = frappe.db.get_value("Company", company, ["abbr", "cost_center"])

	def first(doctype, *names):
		return next((name for name in names if name and frappe.db.exists(doctype, name)), None)

	return {
		"warehouse": first("Warehouse", f"Stores - {abbr}"),
		"cost_center": first("Cost Center", cost_center, f"Main - {abbr}"),
		"consumption_account": first("Account", f"{CONSUMPTION} - {abbr}"),
		"taxes_and_charges": first("Sales Taxes and Charges Template", f"{SALES_TAX_TEMPLATE} - {abbr}"),
	}


def pending_days(store_id, company):
	"""The store's live Branch Sales that are not yet in ``company``."""
	return frappe.get_all(
		"Branch Sales",
		filters={"lithos_store_id": store_id, "docstatus": ("<", 2), "company": ("!=", company)},
		fields=["name", "posting_date", "docstatus", "company", "total", "grand_total"],
		order_by="posting_date",
	)


@frappe.whitelist()
def preview_move(store_id):
	"""What the Re-post button would do for a store. Writes nothing."""
	frappe.only_for(lithos.LITHOS_ROLES)
	store = frappe.get_doc("Lithos Store", store_id)
	company = store.move_to_company or store.company
	days = pending_days(store.name, company)
	defaults = outlet_defaults(company)

	resolver = lithos.ItemResolver(frappe.get_single("LithosPOS Settings"), store.name)
	changes = Counter()
	lines = frappe.get_all(
		"Branch Sales Item",
		filters={"parenttype": "Branch Sales", "parent": ("in", [day.name for day in days] or [""])},
		fields=["item_code", "pos_item_name"],
	)
	for line in lines:
		item = resolver.mapped_item(line.pos_item_name)
		if item and item != line.item_code:
			changes[line.item_code, item] += 1

	warnings = []
	if not defaults["cost_center"]:
		warnings.append(_("{0} has no default cost center.").format(company))
	for fieldname, label in (
		("warehouse", _("an outlet warehouse")),
		("consumption_account", _("a {0} account").format(CONSUMPTION)),
		("taxes_and_charges", _("a {0} sales tax template").format(SALES_TAX_TEMPLATE)),
	):
		if not defaults[fieldname]:
			# Not needed by Branch Sales; recipe consumption and the Sales Invoice need it (M6).
			warnings.append(_("{0} has no {1} yet.").format(company, label))

	return {
		"store": store.name,
		"store_company": store.company,
		"to_company": company,
		"from_companies": sorted({day.company for day in days}),
		"days": len(days),
		"submitted": sum(1 for day in days if day.docstatus == 1),
		"drafts": sum(1 for day in days if day.docstatus == 0),
		"first": str(days[0].posting_date) if days else None,
		"last": str(days[-1].posting_date) if days else None,
		"net": flt(sum(flt(day.total) for day in days), 2),
		"grand_total": flt(sum(flt(day.grand_total) for day in days), 2),
		"lines": len(lines),
		"lines_changing_item": sum(changes.values()),
		"item_changes": [{"from": old, "to": new, "lines": n} for (old, new), n in changes.most_common(20)],
		"defaults": defaults,
		"blocked": not defaults["cost_center"],
		"warnings": warnings,
	}


@frappe.whitelist()
def enqueue_move(store_id):
	"""Queue the move for a store as a Re-post run. Returns the run's name."""
	preview = preview_move(store_id)
	if preview["blocked"]:
		frappe.throw(" ".join(preview["warnings"]))
	if not preview["days"] and preview["store_company"] == preview["to_company"]:
		frappe.throw(
			_("Nothing to re-post: store {0} and all its days are already in {1}.").format(
				store_id, preview["to_company"]
			)
		)
	return lithos.start_run(
		lithos.REPOST,
		[store_id],
		preview["first"] or today(),
		preview["last"] or today(),
		mode=lithos.MOVE,
	)


def move_store(ctx):
	"""Job body of a Re-post run: move the store to its company, then re-post each day there.

	Safe to run again: it only touches days not yet in the store's company.
	"""
	store = frappe.get_doc("Lithos Store", ctx.target.store_id)
	company = store.move_to_company or store.company

	if store.company != company:
		previous = store.company
		# Every company-bound default changes together, so nothing of the old company is left.
		store.update({"company": company, **outlet_defaults(company)})
		store.notes = "\n".join(
			filter(None, [store.notes, _("Moved from {0} on {1} (D3).").format(previous, today())])
		)
		store.save(ignore_permissions=True)
		frappe.db.commit()
		ctx.target = lithos.get_store_targets([store.name])[0]
		ctx.reset()

	for day in pending_days(store.name, company):
		lithos.run_day(ctx, day.posting_date, lambda name=day.name: rebuild_day(ctx, name))

	if store.move_to_company and not pending_days(store.name, company):
		frappe.db.set_value("Lithos Store", store.name, "move_to_company", None)
		frappe.db.commit()


def rebuild_day(ctx, name):
	"""Post one Branch Sales again in the store's company, lines resolved through the item map.

	A submitted day is cancelled and amended; a draft is updated in place and stays
	a draft. A line the map does not decide keeps its item. The caller commits.
	"""
	target = ctx.target
	lithos.lock_store(target.store_id)
	old = frappe.get_doc("Branch Sales", name)
	# Taken before anything changes: cancelling sets old.docstatus to 2.
	submitted, old_company = old.docstatus == 1, old.company
	before = {fieldname: flt(old.get(fieldname), 2) for fieldname in RECONCILED}
	resolver = ctx.day_resolver()

	# ignore_no_copy=False: the amendment must not inherit the old day's ledger documents.
	doc = frappe.copy_doc(old, ignore_no_copy=False) if submitted else old
	doc.update(
		{
			"company": target.company,
			"cost_center": target.cost_center,
			"customer": target.customer or old.customer,
			"lithos_store_id": target.store_id,
		}
	)
	suspense, unmapped = ctx.settings.suspense_item, 0
	for row in doc.items:
		row.item_code = resolver.mapped_item(row.pos_item_name) or row.item_code
		unmapped += bool(suspense and row.item_code == suspense)

	doc.flags.ignore_permissions = True
	if submitted:
		doc.amended_from = old.name
		old.flags.ignore_permissions = True
		old.cancel()
		doc.insert()
	else:
		doc.save()

	for fieldname in RECONCILED:
		if flt(doc.get(fieldname), 2) != before[fieldname]:
			frappe.throw(
				_("{0}: {1} would change from {2} to {3}. The day was left as it was.").format(
					name, doc.meta.get_label(fieldname), before[fieldname], flt(doc.get(fieldname), 2)
				)
			)
	if submitted:
		doc.submit()
		ctx.ledger_seen.add(doc.name)

	log = lithos.get_log(target, old.posting_date)
	if log.is_new():
		# Posted before sync logs existed. It reconciled with Lithos when it was posted.
		log.update(
			{
				"rows_received": len(old.items),
				"lithos_net": old.total,
				"lithos_tax": old.total_taxes_and_charges,
			}
		)
	log.update(
		{
			"status": lithos.POSTED if doc.docstatus == 1 else lithos.DRAFT,
			"company": target.company,
			"branch": target.branch,
			"sync_run": ctx.run,
			"branch_sales": doc.name,
			"erpnext_net": doc.total,
			"unmapped_lines": unmapped,
			"last_attempt": now_datetime(),
			"hold_reason": None,
			"error_log": None,
			"message": _("Re-posted from {0} ({1}).").format(old.name, old_company)
			if submitted
			else _("Updated in place from {0}: it is still a draft.").format(old_company),
		}
	)
	log.flags.ledger = {"sales_invoice": doc.sales_invoice, "ledger_status": doc.ledger_status}
	lithos.finish_log(log, target)
	return log
