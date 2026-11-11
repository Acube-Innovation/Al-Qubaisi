# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

"""Branch Sales -> Sales Invoice + recipe Material Issue in the outlet company (M6).

A submitted Branch Sales on or after its store's Ledger From Date creates two documents:

* A Sales Invoice to the store's walk-in customer, paid in full to the Lithos
  Clearing mode of payment (D13). The lines carry Lithos's net amounts exactly, and
  VAT is posted as the amount Lithos charged, so the invoice reconciles to the fil.
* A Material Issue from the outlet warehouse: every sold item's qty through its
  default BOM, exploded to raw materials and summed, charged to the store's
  consumption account (D6, D9). A stock item with no BOM is issued itself.

Revenue and VAT never wait for stock. If the raw materials are short, or a recipe
cannot be read, the Material Issue waits as a draft ("Consumption pending") and the
nightly catch-up tries it again. A day whose items had no BOM is posted with what
it could issue ("No BOM") and is rebuilt once one of those items gets a BOM.

Cancelling the Branch Sales cancels both documents, so a re-post (D3) moves them too.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.model.meta import get_field_precision
from frappe.utils import cint, flt, getdate, strip_html

from al_qubaisi_modifications import lithos
from al_qubaisi_modifications.lithos_setup import CLEARING

POSTED = "Posted"
NO_BOM = "No BOM"
PENDING = "Consumption pending"
FAILED = "Failed"

# Branch Sales field that links to each ledger document.
LINK_FIELDS = {"Sales Invoice": "sales_invoice", "Stock Entry": "consumption_entry"}

# Consumption is booked after the day's deliveries, so goods received that day are counted.
CONSUMPTION_TIME = "23:59:00"


def ledger_problems(store):
	"""What stops a store's days from being posted to the ledger. Empty when ready."""
	problems = []
	company = frappe.db.get_value("Company", store.company, ["tax_id"], as_dict=True) or {}
	if not company.get("tax_id"):
		problems.append(_("Company {0} has no TRN (Tax ID).").format(store.company))

	if not store.customer:
		problems.append(_("No walk-in customer."))
	else:
		branch_customer, internal = frappe.db.get_value(
			"Customer", store.customer, ["custom_is_branch", "is_internal_customer"]
		) or (0, 0)
		if branch_customer or internal:
			problems.append(
				_(
					"Customer {0} is a branch or inter-company account, used for supplies from Al Qubaisi Group. Choose a walk-in customer."
				).format(store.customer)
			)

	for fieldname in ("warehouse", "cost_center", "consumption_account", "taxes_and_charges"):
		if not store.get(fieldname):
			problems.append(_("{0} is not set.").format(_(store.meta.get_label(fieldname))))

	if store.taxes_and_charges and not vat_row(store.taxes_and_charges):
		problems.append(_("Tax template {0} has no tax row.").format(store.taxes_and_charges))
	if not clearing_account(store.company):
		problems.append(_("Mode of Payment {0} has no account for {1}.").format(CLEARING, store.company))
	return problems


def vat_row(template):
	return frappe.db.get_value(
		"Sales Taxes and Charges",
		{"parent": template, "parenttype": "Sales Taxes and Charges Template"},
		["account_head", "description"],
		as_dict=True,
		order_by="idx",
	)


def clearing_account(company):
	return frappe.db.get_value(
		"Mode of Payment Account", {"parent": CLEARING, "company": company}, "default_account"
	)


def due_store(bs):
	"""The store whose ledger this day belongs to, or None when the day stays Branch Sales only."""
	if not bs.lithos_store_id or not frappe.db.exists("Lithos Store", bs.lithos_store_id):
		return None
	store = frappe.get_doc("Lithos Store", bs.lithos_store_id)
	# A day still in its old company is posted when it is re-posted into the outlet (D3).
	if (
		not store.ledger_from_date
		or getdate(bs.posting_date) < getdate(store.ledger_from_date)
		or store.company != bs.company
	):
		return None
	return store


def on_submit(bs):
	"""Branch Sales on_submit: post the day to the ledger when its store is due."""
	store = due_store(bs)
	if store:
		post_ledger(bs, store)


def post_ledger(bs, store):
	problems = ledger_problems(store)
	if problems:
		frappe.throw(
			_("Store {0} cannot post to the ledger yet: {1}").format(store.name, " ".join(problems)),
			title=_("Ledger not ready"),
		)

	invoice = make_sales_invoice(bs, store)
	invoice.insert(ignore_permissions=True)
	check_invoice(invoice, bs)
	invoice.submit()
	bs.db_set("sales_invoice", invoice.name, update_modified=False)
	post_consumption(bs, store)


def amount_precision():
	return get_field_precision(frappe.get_meta("Sales Invoice Item").get_field("amount"))


def rate_precision():
	return get_field_precision(frappe.get_meta("Sales Invoice Item").get_field("rate"))


def invoice_rows(bs):
	"""Sales Invoice lines that reproduce each Branch Sales line's amount exactly.

	The invoice stores rate to 2 decimals and recomputes amount = qty x rate, so a
	line like 3 for 10.00 cannot be one row. It becomes 2 x 3.33 and 1 x 3.34.
	"""
	amount_digits, rate_digits = amount_precision(), rate_precision()
	rows = []
	for line in bs.items:
		qty, amount = flt(line.qty), flt(line.amount, amount_digits)
		stock_uom = frappe.get_cached_value("Item", line.item_code, "stock_uom")
		base = {
			"item_code": line.item_code,
			"item_name": line.item_name,
			"description": line.pos_item_name or line.item_name,
			"uom": stock_uom,
			"stock_uom": stock_uom,
			"conversion_factor": 1,
		}
		rate = flt(amount / qty, rate_digits) if qty else amount
		if qty > 0 and flt(rate * qty, amount_digits) == amount:
			rows.append({**base, "qty": qty, "rate": rate})
		elif qty > 1:
			rest = flt(amount - flt(rate * (qty - 1), amount_digits), amount_digits)
			rows.append({**base, "qty": qty - 1, "rate": rate})
			rows.append(
				{**base, "description": base["description"] + " " + _("(balance)"), "qty": 1, "rate": rest}
			)
		else:
			# A fraction of a unit (by weight) that no 2-decimal rate reproduces.
			rows.append(
				{
					**base,
					"description": f"{base['description']} ({qty} {stock_uom})",
					"qty": 1,
					"rate": amount,
				}
			)
	for row in rows:
		# Otherwise the price list fills in its own rate and reports the gap as a discount.
		row["price_list_rate"] = row["rate"]
	return rows


def make_sales_invoice(bs, store):
	vat = vat_row(store.taxes_and_charges)
	invoice = frappe.new_doc("Sales Invoice")
	invoice.update(
		{
			"company": bs.company,
			"customer": store.customer,
			"posting_date": bs.posting_date,
			"set_posting_time": 1,
			"due_date": bs.posting_date,
			"is_pos": 1,
			"update_stock": 0,
			"selling_price_list": store.selling_price_list
			or frappe.db.get_single_value("Selling Settings", "selling_price_list"),
			"ignore_pricing_rule": 1,
			"disable_rounded_total": 1,
			"cost_center": store.cost_center,
			"branch": bs.branch,
			"taxes_and_charges": store.taxes_and_charges,
			"remarks": _("LithosPOS sales of store {0} on {1}, from Branch Sales {2}.").format(
				store.name, bs.posting_date, bs.name
			),
		}
	)
	for row in invoice_rows(bs):
		invoice.append("items", {**row, "cost_center": store.cost_center, "branch": bs.branch})

	# The VAT Lithos charged, line by line, rather than 5% of the day's total.
	vat_amount = flt(bs.total_taxes_and_charges, amount_precision())
	if vat_amount:
		invoice.append(
			"taxes",
			{
				"charge_type": "Actual",
				"account_head": vat.account_head,
				"description": vat.description or vat.account_head,
				"tax_amount": vat_amount,
				"cost_center": store.cost_center,
				"branch": bs.branch,
			},
		)
	invoice.append(
		"payments",
		{
			"mode_of_payment": CLEARING,
			"account": clearing_account(bs.company),
			"amount": flt(flt(bs.total, amount_precision()) + vat_amount, amount_precision()),
		},
	)
	return invoice


def check_invoice(invoice, bs):
	digits = amount_precision()
	for label, posted, expected in (
		(_("net total"), invoice.net_total, bs.total),
		(_("VAT"), invoice.total_taxes_and_charges, bs.total_taxes_and_charges),
		(
			_("grand total"),
			invoice.grand_total,
			flt(bs.total, digits) + flt(bs.total_taxes_and_charges, digits),
		),
		(_("amount paid"), invoice.paid_amount, invoice.grand_total),
	):
		if flt(posted, digits) != flt(expected, digits):
			frappe.throw(
				_("Sales Invoice {0} would be {1} against {2} on {3}.").format(
					label, flt(posted, digits), flt(expected, digits), bs.name
				)
			)


def raw_materials(bs):
	"""(raw material -> qty, items with nothing to issue) for one day's sales."""
	from erpnext.manufacturing.doctype.bom.bom import get_bom_items_as_dict

	sold = defaultdict(float)
	for line in bs.items:
		sold[line.item_code] += flt(line.qty)

	suspense = frappe.db.get_single_value("LithosPOS Settings", "suspense_item")
	need, missing = defaultdict(float), []
	for item_code, qty in sold.items():
		# Not cached: a BOM submitted a moment ago must count.
		default_bom, is_stock_item = frappe.db.get_value("Item", item_code, ["default_bom", "is_stock_item"])
		if item_code == suspense:
			missing.append(item_code)
		elif default_bom:
			for raw, row in get_bom_items_as_dict(default_bom, bs.company, qty=qty, fetch_exploded=1).items():
				need[raw] += flt(row.get("qty"))
		elif is_stock_item:
			# Sold as bought, e.g. a bottle of water: the item itself leaves the shelf.
			need[item_code] += qty
		else:
			missing.append(item_code)
	return need, missing


def make_material_issue(bs, store, need):
	entry = frappe.new_doc("Stock Entry")
	entry.update(
		{
			"stock_entry_type": "Material Issue",
			"purpose": "Material Issue",
			"company": bs.company,
			"posting_date": bs.posting_date,
			"posting_time": CONSUMPTION_TIME,
			"set_posting_time": 1,
			"from_warehouse": store.warehouse,
			"branch": bs.branch,
			"remarks": _(
				"Recipe consumption for LithosPOS sales of store {0} on {1}, Branch Sales {2}."
			).format(store.name, bs.posting_date, bs.name),
		}
	)
	digits = get_field_precision(frappe.get_meta("Stock Entry Detail").get_field("qty"))
	for raw, qty in sorted(need.items()):
		if flt(qty, digits) > 0:
			entry.append(
				"items",
				{
					"item_code": raw,
					"qty": flt(qty, digits),
					"s_warehouse": store.warehouse,
					"expense_account": store.consumption_account,
					"cost_center": store.cost_center,
					"branch": bs.branch,
				},
			)
	return entry


def post_consumption(bs, store):
	"""Create and submit the day's Material Issue. Records the outcome on the Branch Sales.

	Never raises: whatever goes wrong is kept on the day, and the sale stands.
	"""
	messages, entry_name = [], None
	frappe.db.savepoint("lithos_consumption")
	try:
		need, missing = raw_materials(bs)
		if missing:
			messages.append(_("Nothing issued for {0}: no default BOM.").format(lithos.shorten(missing)))
		if need:
			entry = make_material_issue(bs, store, need)
			entry.insert(ignore_permissions=True)
			entry_name = entry.name
	except Exception as e:
		frappe.db.rollback(save_point="lithos_consumption")
		return record(bs, PENDING, _("Material Issue not created: {0}").format(error_text(e)), None, [])

	status = NO_BOM if missing else POSTED
	if entry_name:
		frappe.db.savepoint("lithos_consumption_submit")
		try:
			entry.submit()
		except Exception as e:
			frappe.db.rollback(save_point="lithos_consumption_submit")
			status = PENDING
			messages.insert(
				0,
				_("Material Issue {0} waits as a draft: {1}").format(entry_name, error_text(e)),
			)
	return record(bs, status, "\n".join(messages), entry_name, missing)


def record(bs, status, message, entry_name, missing):
	bs.db_set(
		{
			"ledger_status": status,
			"ledger_message": message or None,
			"consumption_entry": entry_name,
			"items_without_bom": ", ".join(sorted(set(missing))) or None,
		},
		update_modified=False,
	)
	return status


def error_text(exc):
	return (strip_html(str(exc)) or exc.__class__.__name__)[:500]


def on_cancel(bs):
	"""Branch Sales on_cancel: reverse the day's Material Issue and Sales Invoice."""
	for doctype, name in (("Stock Entry", bs.consumption_entry), ("Sales Invoice", bs.sales_invoice)):
		if not name or not frappe.db.exists(doctype, name):
			continue
		doc = frappe.get_doc(doctype, name)
		doc.flags.ignore_permissions = True
		if doc.docstatus == 1:
			doc.cancel()
		elif doc.docstatus == 0:
			# Frappe refuses to delete anything still linked, even from a cancelled document.
			bs.db_set(LINK_FIELDS[doctype], None, update_modified=False)
			frappe.delete_doc(doctype, name, ignore_permissions=True)


def rebuild_consumption(bs, store):
	"""Replace the day's Material Issue with one built from the BOMs as they are now."""
	if bs.consumption_entry and frappe.db.exists("Stock Entry", bs.consumption_entry):
		entry = frappe.get_doc("Stock Entry", bs.consumption_entry)
		entry.flags.ignore_permissions = True
		# The submitted Branch Sales links to it, which would otherwise block the cancel.
		entry.flags.ignore_links = True
		if entry.docstatus == 1:
			entry.cancel()
		elif entry.docstatus == 0:
			bs.db_set("consumption_entry", None, update_modified=False)
			frappe.delete_doc("Stock Entry", entry.name, ignore_permissions=True)
	return post_consumption(bs, store)


def gained_bom(bs):
	"""True when an item that had no BOM on this day has one now."""
	items = [item.strip() for item in (bs.items_without_bom or "").split(",") if item.strip()]
	return any(frappe.db.get_value("Item", item, "default_bom") for item in items)


def catch_up(ctx):
	"""Finish the ledger for a store's submitted days, one transaction each.

	Posts days that have no Sales Invoice yet, retries pending consumption, and
	rebuilds days whose missing BOMs have since been created.
	"""
	store = frappe.get_doc("Lithos Store", ctx.target.store_id)
	if not store.ledger_from_date:
		return
	days = frappe.get_all(
		"Branch Sales",
		filters={
			"lithos_store_id": store.name,
			"docstatus": 1,
			"company": store.company,
			"posting_date": (">=", store.ledger_from_date),
		},
		or_filters=[
			["sales_invoice", "is", "not set"],
			["ledger_status", "in", [PENDING, NO_BOM, FAILED]],
		],
		pluck="name",
		order_by="posting_date",
	)
	for name in days:
		if name in ctx.ledger_seen:
			continue
		bs = frappe.get_doc("Branch Sales", name)
		new_invoice = not bs.sales_invoice
		try:
			lithos.lock_store(store.name)
			if new_invoice:
				post_ledger(bs, store)
			elif bs.ledger_status in (PENDING, FAILED) or (bs.ledger_status == NO_BOM and gained_bom(bs)):
				rebuild_consumption(bs, store)
			else:
				continue
			frappe.db.commit()
		except Exception as e:
			frappe.db.rollback()
			frappe.log_error(
				title=f"LithosPOS ledger failed ({store.name} {bs.posting_date})",
				message=frappe.get_traceback(with_context=True),
			)
			frappe.db.set_value(
				"Branch Sales",
				name,
				{"ledger_status": FAILED, "ledger_message": error_text(e)},
				update_modified=False,
			)
			frappe.db.commit()
		count(
			ctx,
			frappe.db.get_value("Branch Sales", name, ["sales_invoice", "ledger_status"], as_dict=True),
			new_invoice,
		)


def count(ctx, bs, new_invoice=True):
	"""Count a day into its run: an invoice only when this run created it."""
	if new_invoice and bs.get("sales_invoice"):
		ctx.counts["invoices_posted"] += 1
	if bs.get("ledger_status") in (PENDING, FAILED):
		ctx.counts["consumption_pending"] += 1


@frappe.whitelist()
def ledger_readiness(store_id):
	"""For the Lithos Store form: what blocks the ledger, and the sold items with nothing to issue."""
	frappe.only_for(lithos.LITHOS_ROLES)
	store = frappe.get_doc("Lithos Store", store_id)
	items = frappe.db.sql(
		"""
		select bsi.item_code, max(bsi.item_name) as item_name, sum(bsi.qty) as qty, sum(bsi.amount) as net
		from `tabBranch Sales Item` bsi
		join `tabBranch Sales` bs on bs.name = bsi.parent
		join `tabItem` i on i.name = bsi.item_code
		where bs.lithos_store_id = %s and bs.docstatus = 1 and bs.posting_date >= curdate() - interval 30 day
			and ifnull(i.default_bom, '') = '' and i.is_stock_item = 0
		group by bsi.item_code
		order by net desc
		""",
		store.name,
		as_dict=True,
	)
	sold = frappe.db.sql(
		"""select count(distinct bsi.item_code) from `tabBranch Sales Item` bsi
		join `tabBranch Sales` bs on bs.name = bsi.parent
		where bs.lithos_store_id = %s and bs.docstatus = 1 and bs.posting_date >= curdate() - interval 30 day""",
		store.name,
	)[0][0]
	return {
		"problems": ledger_problems(store),
		"sold_items": cint(sold),
		"without_bom": [
			{"item_code": i.item_code, "item_name": i.item_name, "qty": flt(i.qty), "net": flt(i.net, 2)}
			for i in items
		],
	}
