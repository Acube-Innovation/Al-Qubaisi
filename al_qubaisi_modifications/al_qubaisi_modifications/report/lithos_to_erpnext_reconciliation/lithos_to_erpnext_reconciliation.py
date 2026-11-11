# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

"""Lithos to ERPNext Reconciliation (M8): each store-day, from the till to the clearing account.

Sync variance is Lithos net less the submitted Branch Sales and the ignored lines.
Ledger variance is the Branch Sales gross less what its Sales Invoice debited to
Lithos Clearing; a day due for the ledger without an invoice is short by its gross.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate

from al_qubaisi_modifications import lithos, lithos_ledger
from al_qubaisi_modifications.lithos_tower import Scope, date_range


def execute(filters=None):
	filters = frappe._dict(filters or {})
	from_date, to_date = date_range(filters.from_date, filters.to_date)
	scope = Scope(filters.company, None, filters.store)
	rows = get_rows(scope, from_date, to_date)
	if filters.only_variances:
		rows = [row for row in rows if row.issues]
	return get_columns(), rows, None, None, get_summary(rows)


def get_columns():
	def column(fieldname, label, fieldtype="Currency", width=110, **extra):
		return {"fieldname": fieldname, "label": label, "fieldtype": fieldtype, "width": width, **extra}

	return [
		column("posting_date", _("Date"), "Date", 105),
		column("store", _("Store"), "Link", 70, options="Lithos Store"),
		column("outlet_name", _("Outlet"), "Data", 170),
		column("company", _("Company"), "Link", 170, options="Company"),
		column("sync_status", _("Sync Status"), "Data", 95),
		column("lithos_net", _("Lithos Net")),
		column("lithos_vat", _("Lithos VAT"), width=95),
		column("voids", _("Voids"), width=90),
		column("ignored", _("Ignored Lines"), width=95),
		column("branch_sales", _("Branch Sales"), "Link", 150, options="Branch Sales"),
		column("erpnext_net", _("ERPNext Net")),
		column("erpnext_vat", _("ERPNext VAT"), width=95),
		column("sync_variance", _("Sync Variance")),
		column("ledger_status", _("Ledger"), "Data", 140),
		column("sales_invoice", _("Sales Invoice"), "Link", 150, options="Sales Invoice"),
		column("invoice_net", _("Invoice Net")),
		column("clearing", _("Clearing Debit")),
		column("ledger_variance", _("Ledger Variance")),
		column("issues", _("Issues"), "Data", 260),
	]


def get_rows(scope, from_date, to_date):
	dates = ("between", [from_date, to_date])
	logs = {
		(log.store, str(log.posting_date)): log
		for log in frappe.get_all(
			"Lithos Sync Log",
			filters={"store": ("in", scope.ids), "posting_date": dates},
			fields=[
				"store",
				"posting_date",
				"status",
				"company",
				"lithos_net",
				"lithos_tax",
				"void_total",
				"ignored_amount",
			],
		)
	}
	sales_filters = {"lithos_store_id": ("in", scope.ids), "posting_date": dates, "docstatus": ("<", 2)}
	if scope.allowed is not None:
		sales_filters["company"] = ("in", scope.allowed)
	sales = {
		(bs.lithos_store_id, str(bs.posting_date)): bs
		for bs in frappe.get_all(
			"Branch Sales",
			filters=sales_filters,
			fields=[
				"name",
				"lithos_store_id",
				"posting_date",
				"docstatus",
				"company",
				"total",
				"total_taxes_and_charges",
				"sales_invoice",
				"ledger_status",
			],
		)
	}
	invoices = invoice_totals([bs.sales_invoice for bs in sales.values() if bs.sales_invoice])

	rows = []
	for key in sorted(set(logs) | set(sales), key=lambda key: (key[1], key[0])):
		store = scope.by_id.get(key[0])
		if store:
			rows.append(make_row(store, key[1], logs.get(key), sales.get(key), invoices))
	return rows


def invoice_totals(names):
	"""Sales Invoice -> its net and what it debited to Lithos Clearing."""
	if not names:
		return {}
	invoices = {
		si.name: si
		for si in frappe.get_all(
			"Sales Invoice",
			filters={"name": ("in", names), "docstatus": 1},
			fields=["name", "company", "net_total"],
		)
	}
	accounts = {
		company: lithos_ledger.clearing_account(company)
		for company in {si.company for si in invoices.values()}
	}
	for si in invoices.values():
		si.clearing = 0.0
	for row in frappe.db.sql(
		"""
		select voucher_no, account, sum(debit - credit) as amount
		from `tabGL Entry`
		where voucher_type = 'Sales Invoice' and voucher_no in %(names)s and is_cancelled = 0
		group by voucher_no, account
		""",
		{"names": list(invoices) or [""]},
		as_dict=True,
	):
		si = invoices[row.voucher_no]
		if row.account == accounts.get(si.company):
			si.clearing = flt(row.amount, 2)
	return invoices


def make_row(store, day, log, bs, invoices):
	posted = bs if bs and bs.docstatus == 1 else None
	row = frappe._dict(
		posting_date=getdate(day),
		store=store.name,
		outlet_name=store.outlet_name,
		company=(bs and bs.company) or (log and log.company) or store.company,
		branch_sales=bs and bs.name,
		erpnext_net=flt(posted.total, 2) if posted else 0.0,
		erpnext_vat=flt(posted.total_taxes_and_charges, 2) if posted else 0.0,
		ledger_status=posted and posted.ledger_status,
	)
	issues = []

	if log:
		row.update(
			sync_status=log.status,
			lithos_net=flt(log.lithos_net, 2),
			lithos_vat=flt(log.lithos_tax, 2),
			voids=flt(log.void_total, 2),
			ignored=flt(log.ignored_amount, 2),
		)
		row.sync_variance = flt(row.lithos_net - row.erpnext_net - row.ignored, 2)
		if log.status not in (lithos.POSTED, lithos.NO_SALES):
			issues.append(_("Not posted ({0})").format(_(log.status)))
		elif row.sync_variance:
			issues.append(_("Sync variance"))
	else:
		# Posted before sync logs were kept: nothing from Lithos to compare against.
		row.sync_status = lithos.POSTED if posted else lithos.DRAFT
		issues.append(_("No sync log"))

	if posted and posted.company != store.company:
		issues.append(_("In {0}: re-post pending").format(posted.company))

	due = bool(
		posted
		and store.ledger_from_date
		and getdate(day) >= getdate(store.ledger_from_date)
		and posted.company == store.company
	)
	gross = flt(row.erpnext_net + row.erpnext_vat, 2)
	invoice = posted and invoices.get(posted.sales_invoice)
	if invoice:
		row.update(
			sales_invoice=invoice.name,
			invoice_net=flt(invoice.net_total, 2),
			clearing=invoice.clearing,
			ledger_variance=flt(gross - invoice.clearing, 2),
		)
		if row.ledger_variance:
			issues.append(_("Ledger variance"))
	elif due:
		row.update(ledger_status=_("Missing"), ledger_variance=gross)
		issues.append(_("Ledger missing"))
	if row.ledger_status in (lithos_ledger.PENDING, lithos_ledger.FAILED):
		issues.append(_(row.ledger_status))

	row.issues = ", ".join(issues)
	return row


def get_summary(rows):
	def total(field):
		return flt(sum(flt(row.get(field)) for row in rows), 2)

	variance_days = sum(1 for row in rows if row.issues)
	summary = [
		{"label": _("Lithos Net"), "value": total("lithos_net"), "datatype": "Currency"},
		{"label": _("ERPNext Net"), "value": total("erpnext_net"), "datatype": "Currency"},
		{
			"label": _("Sync Variance"),
			"value": total("sync_variance"),
			"datatype": "Currency",
			"indicator": "Red" if total("sync_variance") else "Green",
		},
		{
			"label": _("Days with an Issue"),
			"value": variance_days,
			"datatype": "Int",
			"indicator": "Red" if variance_days else "Green",
		},
	]
	if any(row.sales_invoice or row.ledger_variance for row in rows):
		summary += [
			{"label": _("Clearing Debit"), "value": total("clearing"), "datatype": "Currency"},
			{
				"label": _("Ledger Variance"),
				"value": total("ledger_variance"),
				"datatype": "Currency",
				"indicator": "Red" if total("ledger_variance") else "Green",
			},
		]
	return summary
