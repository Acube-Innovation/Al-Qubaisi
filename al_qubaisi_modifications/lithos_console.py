# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

"""Lithos Sync Console (M5): a store x day grid of what each day became, and the runs behind it.

A day's status comes from its Lithos Sync Log. Days posted before logs existed have
none, so their Branch Sales stands in for the log.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, date_diff, flt, getdate, today

from al_qubaisi_modifications import lithos

# Grid statuses on top of the Lithos Sync Log ones.
MISSING, NOT_LIVE, OPEN = "Missing", "Not live", "Open"
MAX_DAYS = 92
# The phase 2 gate: this many days in a row reconciled with no unmapped line.
GATE_DAYS = 7
STREAK_DAYS = 30

STORE_FIELDS = [
	"name",
	"outlet_name",
	"company",
	"brand",
	"sync_enabled",
	"sync_from_date",
	"sync_policy",
	"move_to_company",
	"ledger_from_date",
	"last_synced_date",
	"last_sync_status",
]
LOG_FIELDS = [
	"name",
	"store",
	"posting_date",
	"status",
	"company",
	"branch_sales",
	"lithos_net",
	"erpnext_net",
	"variance",
	"unmapped_lines",
	"lines_without_bom",
	"hold_reason",
	"message",
	"sync_run",
	"retry_count",
	"error_log",
]


def get_stores(company=None, store=None, brand=None):
	filters = {}
	if company:
		filters["company"] = company
	if store:
		filters["name"] = store
	if brand:
		filters["brand"] = brand
	return frappe.get_all("Lithos Store", filters=filters, fields=STORE_FIELDS, order_by="company, name")


def day_records(store_ids, from_date, to_date):
	"""(store, date) -> log, and (store, date) -> live Branch Sales, over a range."""
	dates = ("between", [from_date, to_date])
	logs = {
		(row.store, str(row.posting_date)): row
		for row in frappe.get_all(
			"Lithos Sync Log",
			filters={"store": ("in", store_ids or [""]), "posting_date": dates},
			fields=LOG_FIELDS,
		)
	}
	sales = {
		(row.lithos_store_id, str(row.posting_date)): row
		for row in frappe.get_all(
			"Branch Sales",
			filters={
				"lithos_store_id": ("in", store_ids or [""]),
				"posting_date": dates,
				"docstatus": ("<", 2),
			},
			fields=[
				"name",
				"lithos_store_id",
				"posting_date",
				"docstatus",
				"total",
				"company",
				"sales_invoice",
				"consumption_entry",
				"ledger_status",
				"ledger_message",
			],
		)
	}
	return logs, sales


def make_cell(store, day, log, sale):
	"""What one store-day shows in the grid."""
	if log:
		cell = {key: log.get(key) for key in LOG_FIELDS if key not in ("store", "posting_date")}
		cell["log"] = cell.pop("name")
	elif sale:
		cell = {
			"status": lithos.POSTED if sale.docstatus == 1 else lithos.DRAFT,
			"branch_sales": sale.name,
			"company": sale.company,
			"erpnext_net": sale.total,
			"message": _("Posted before sync logs were kept."),
		}
	elif not store.sync_enabled or not store.sync_from_date or getdate(day) < getdate(store.sync_from_date):
		cell = {"status": NOT_LIVE}
	elif getdate(day) >= getdate(today()):
		cell = {"status": OPEN}
	else:
		cell = {
			"status": MISSING,
			"message": _("The store was live but this day has no Branch Sales and no sync record."),
		}

	if sale:
		cell.update(
			{
				key: sale.get(key)
				for key in ("sales_invoice", "consumption_entry", "ledger_status", "ledger_message")
			}
		)
	# A posted day the ledger should have but does not (M6).
	cell["ledger_due"] = bool(
		sale
		and sale.docstatus == 1
		and store.ledger_from_date
		and getdate(day) >= getdate(store.ledger_from_date)
		and sale.company == store.company
		and not sale.sales_invoice
	)

	company = cell.get("company")
	cell["other_company"] = bool(cell.get("branch_sales") and company and company != store.company)
	cell["net"] = flt(cell.get("erpnext_net") or cell.get("lithos_net"), 2)
	return cell


def is_clean(cell):
	"""Reconciled with no unmapped line, in the store's own company. A closed day counts too."""
	if cell["status"] == lithos.NO_SALES:
		return True
	return (
		cell["status"] == lithos.POSTED
		and not cell["other_company"]
		and not cint(cell.get("unmapped_lines"))
		and not flt(cell.get("variance"), 2)
	)


def recent(stores):
	"""Each store's clean days in a row counting back from yesterday, and yesterday's cell.

	Read on their own, whatever range the grid shows.
	"""
	yesterday = getdate(add_days(today(), -1))
	start = getdate(add_days(yesterday, -(STREAK_DAYS - 1)))
	logs, sales = day_records([s.name for s in stores], start, yesterday)
	streak, last = {}, {}
	for store in stores:
		run, day = 0, yesterday
		while day >= start:
			cell = make_cell(store, day, logs.get((store.name, str(day))), sales.get((store.name, str(day))))
			if day == yesterday:
				last[store.name] = cell
			if not is_clean(cell):
				break
			run += 1
			day = add_days(day, -1)
		streak[store.name] = run
	return streak, last


@frappe.whitelist()
def get_console(company=None, store=None, brand=None, from_date=None, to_date=None):
	"""The grid, its summary cards and the latest runs."""
	frappe.only_for(lithos.LITHOS_ROLES)
	to_date = getdate(to_date or add_days(today(), -1))
	from_date = getdate(from_date or add_days(to_date, -13))
	if from_date > to_date:
		frappe.throw(_("From Date cannot be after To Date."))
	if date_diff(to_date, from_date) >= MAX_DAYS:
		frappe.throw(_("Show at most {0} days at a time.").format(MAX_DAYS))

	stores = get_stores(company, store, brand)
	logs, sales = day_records([s.name for s in stores], from_date, to_date)
	days = [str(day) for day in lithos.days_between(from_date, to_date)]
	streak, yesterday = recent(stores)

	totals = frappe._dict(
		{status: 0 for status in (lithos.HELD, lithos.FAILED, lithos.CHANGED, lithos.DRAFT, MISSING)}
	)
	synced_yesterday = ledger_pending = ledger_due = 0
	for s in stores:
		s.cells = {day: make_cell(s, day, logs.get((s.name, day)), sales.get((s.name, day))) for day in days}
		s.streak = streak[s.name]
		s.gate = s.streak >= GATE_DAYS
		s.pending_repost = bool(s.move_to_company) or any(c["other_company"] for c in s.cells.values())
		for cell in s.cells.values():
			if cell["status"] in totals:
				totals[cell["status"]] += 1
			ledger_pending += cell.get("ledger_status") in ("Consumption pending", "Failed")
			ledger_due += cell["ledger_due"]
		synced_yesterday += s.sync_enabled and yesterday[s.name]["status"] in (lithos.POSTED, lithos.NO_SALES)

	live = [s for s in stores if s.sync_enabled]
	return {
		"days": days,
		"stores": stores,
		"summary": {
			"stores": len(stores),
			"live": len(live),
			"synced_yesterday": synced_yesterday,
			"gate": sum(1 for s in live if s.gate),
			"gate_days": GATE_DAYS,
			"held": totals[lithos.HELD],
			"failed": totals[lithos.FAILED],
			"changed": totals[lithos.CHANGED],
			"draft": totals[lithos.DRAFT],
			"missing": totals[MISSING],
			"unmapped_items": frappe.db.count("Lithos Item Map", {"status": lithos.MAP_UNMAPPED}),
			"pending_repost": sum(1 for s in stores if s.pending_repost),
			"ledger_stores": sum(1 for s in stores if s.ledger_from_date),
			"consumption_pending": ledger_pending,
			"ledger_due": ledger_due,
		},
		"runs": frappe.get_all(
			"Lithos Sync Run",
			fields=[
				"name",
				"trigger",
				"status",
				"from_date",
				"to_date",
				"store_count",
				"stores_done",
				"days_posted",
				"days_held",
				"days_failed",
				"requested_by",
				"creation",
				"finished_at",
			],
			order_by="creation desc",
			limit=8,
		),
	}


def scope(stores=None, companies=None):
	"""Store ids for a Quick Sync: the stores chosen, else the enabled stores of the companies chosen."""
	stores = frappe.parse_json(stores) if isinstance(stores, str) else stores
	companies = frappe.parse_json(companies) if isinstance(companies, str) else companies
	if stores:
		return stores
	if companies:
		ids = frappe.get_all(
			"Lithos Store", filters={"company": ("in", companies), "sync_enabled": 1}, pluck="name"
		)
		if not ids:
			frappe.throw(_("None of the chosen companies has a store with sync enabled."))
		return ids
	frappe.throw(_("Choose at least one store or company."))


@frappe.whitelist()
def quick_sync(from_date, to_date, stores=None, companies=None):
	"""Pull a range now for chosen stores or companies. Returns the run's name."""
	frappe.only_for(lithos.LITHOS_ROLES)
	if not lithos.get_settings():
		frappe.throw(_("LithosPOS Settings is disabled."))
	return lithos.start_run(lithos.QUICK, scope(stores, companies), from_date, to_date)


def logs_in_view(statuses, company=None, store=None, brand=None, from_date=None, to_date=None, **filters):
	ids = [s.name for s in get_stores(company, store, brand)]
	filters.update({"store": ("in", ids or [""]), "status": ("in", statuses)})
	if from_date and to_date:
		filters["posting_date"] = ("between", [getdate(from_date), getdate(to_date)])
	return frappe.get_all("Lithos Sync Log", filters=filters, fields=["store", "posting_date"])


def start_for(trigger, rows, mode=lithos.PULL):
	dates = [getdate(row.posting_date) for row in rows]
	return lithos.start_run(trigger, sorted({row.store for row in rows}), min(dates), max(dates), mode=mode)


@frappe.whitelist()
def retry_failed(company=None, store=None, brand=None, from_date=None, to_date=None):
	"""Pull the failed days in the console's filter again."""
	frappe.only_for(lithos.LITHOS_ROLES)
	rows = logs_in_view([lithos.FAILED], company, store, brand, from_date, to_date)
	if not rows:
		frappe.throw(_("No failed day in this view."))
	return start_for(lithos.RETRY, rows)


@frappe.whitelist()
def repost_held(company=None, store=None, brand=None, from_date=None, to_date=None):
	"""Re-post held days (and days on the suspense item) from their logs, after mapping their items."""
	frappe.only_for(lithos.LITHOS_ROLES)
	rows = logs_in_view([lithos.HELD], company, store, brand, from_date, to_date)
	if frappe.db.get_single_value("LithosPOS Settings", "suspense_item"):
		rows += logs_in_view(
			[lithos.POSTED], company, store, brand, from_date, to_date, unmapped_lines=(">", 0)
		)
	if not rows:
		frappe.throw(_("No held day in this view."))
	return start_for(lithos.REPOST_HELD, rows, mode=lithos.FROM_LOGS)


@frappe.whitelist()
def post_ledger(company=None, store=None, brand=None):
	"""Finish the ledger for the stores in view: missing invoices, pending consumption, new BOMs."""
	frappe.only_for(lithos.LITHOS_ROLES)
	stores = [s for s in get_stores(company, store, brand) if s.ledger_from_date]
	if not stores:
		frappe.throw(_("No store in this view has a Ledger From Date."))
	return lithos.start_run(
		lithos.LEDGER,
		[s.name for s in stores],
		min(getdate(s.ledger_from_date) for s in stores),
		add_days(today(), -1),
		mode=lithos.POST_LEDGER,
	)
