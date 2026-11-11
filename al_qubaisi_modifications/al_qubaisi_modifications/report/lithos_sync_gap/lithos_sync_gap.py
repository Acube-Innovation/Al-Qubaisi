# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

"""Lithos Sync Gap (M8): every live store-day since go-live that is not fully posted.

A day is a gap when it has no submitted Branch Sales (never synced, held, draft,
failed or changed in Lithos), when it still sits in the store's old company, or
when it is due for the ledger and has no Sales Invoice or Material Issue yet. A
closed day ("No sales") is not a gap. Statuses come from the Sync Console's rules.
"""

import frappe
from frappe import _
from frappe.utils import add_days, getdate, today

from al_qubaisi_modifications import lithos, lithos_ledger
from al_qubaisi_modifications.lithos_console import MISSING, day_records, make_cell
from al_qubaisi_modifications.lithos_tower import Scope


def reasons():
	"""(reason, what to do), by the sync status of a day that never posted, or by what a
	posted day still lacks. The reasons double as the report's filter values."""
	return {
		"sync": {
			MISSING: ("Not synced", _("Pull the day again from the Sync Console.")),
			lithos.FAILED: ("Failed", _("Check the error, then pull the day again.")),
			lithos.HELD: ("Held", _("Map its items in the Item Workbench, then Re-post Held.")),
			lithos.DRAFT: ("Draft", _("Submit or delete the draft Branch Sales.")),
			lithos.CHANGED: ("Changed in Lithos", _("Lithos totals changed after posting: review the day.")),
		},
		"other_company": ("Re-post pending", _("Re-post the store into its company from the Lithos Store.")),
		"ledger_due": ("Ledger missing", _("Post Ledger from the Sync Console.")),
		"ledger": {
			lithos_ledger.PENDING: ("Consumption pending", _("Receive the raw materials, then Post Ledger.")),
			lithos_ledger.FAILED: ("Ledger failed", _("Read the ledger message, fix it, then Post Ledger.")),
		},
	}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	scope = Scope(filters.company, None, filters.store)
	rows = get_rows(scope, filters.from_date, filters.to_date)
	if filters.reason:
		rows = [row for row in rows if row.reason == filters.reason]
	return get_columns(), rows, None, None, get_summary(rows)


def get_columns():
	return [
		{"fieldname": "posting_date", "label": _("Date"), "fieldtype": "Date", "width": 105},
		{
			"fieldname": "store",
			"label": _("Store"),
			"fieldtype": "Link",
			"options": "Lithos Store",
			"width": 70,
		},
		{"fieldname": "outlet_name", "label": _("Outlet"), "fieldtype": "Data", "width": 170},
		{
			"fieldname": "company",
			"label": _("Company"),
			"fieldtype": "Link",
			"options": "Company",
			"width": 170,
		},
		{"fieldname": "reason", "label": _("Reason"), "fieldtype": "Data", "width": 140},
		{"fieldname": "detail", "label": _("Detail"), "fieldtype": "Data", "width": 280},
		{"fieldname": "action", "label": _("What to Do"), "fieldtype": "Data", "width": 280},
		{"fieldname": "net", "label": _("Net"), "fieldtype": "Currency", "width": 100},
		{
			"fieldname": "log",
			"label": _("Sync Log"),
			"fieldtype": "Link",
			"options": "Lithos Sync Log",
			"width": 120,
		},
		{
			"fieldname": "branch_sales",
			"label": _("Branch Sales"),
			"fieldtype": "Link",
			"options": "Branch Sales",
			"width": 150,
		},
		{"fieldname": "retry_count", "label": _("Failed Attempts"), "fieldtype": "Int", "width": 90},
	]


def get_rows(scope, from_date=None, to_date=None):
	"""From each store's go-live (or From Date, if later) to yesterday (or To Date, if earlier)."""
	end = getdate(add_days(today(), -1))
	if to_date:
		end = min(end, getdate(to_date))
	spans = {}
	for store in scope.stores:
		if not store.sync_enabled or not store.sync_from_date:
			continue
		start = getdate(store.sync_from_date)
		if from_date:
			start = max(start, getdate(from_date))
		if start <= end:
			spans[store.name] = (store, start)
	if not spans:
		return []

	logs, sales = day_records(list(spans), min(start for _store, start in spans.values()), end)
	table = reasons()
	rows = []
	for store, start in spans.values():
		for day in lithos.days_between(start, end):
			key = (store.name, str(day))
			cell = make_cell(store, day, logs.get(key), sales.get(key))
			gap = gap_reason(cell, table)
			if gap:
				rows.append(
					frappe._dict(
						posting_date=day,
						store=store.name,
						outlet_name=store.outlet_name,
						company=cell.get("company") or store.company,
						reason=gap[0],
						action=gap[1],
						detail=(
							cell.get("hold_reason") or cell.get("ledger_message") or cell.get("message") or ""
						)[:300],
						net=cell.get("net"),
						log=cell.get("log"),
						branch_sales=cell.get("branch_sales"),
						retry_count=cell.get("retry_count"),
					)
				)
	rows.sort(key=lambda row: (row.posting_date, row.store))
	return rows


def gap_reason(cell, table):
	"""(reason, what to do) for a store-day that is a gap, else None."""
	if cell["status"] != lithos.POSTED:
		return table["sync"].get(cell["status"])
	if cell["other_company"]:
		return table["other_company"]
	if cell["ledger_due"]:
		return table["ledger_due"]
	return table["ledger"].get(cell.get("ledger_status"))


def get_summary(rows):
	counts = {}
	for row in rows:
		counts[row.reason] = counts.get(row.reason, 0) + 1
	summary = [
		{
			"label": _("Gap Days"),
			"value": len(rows),
			"datatype": "Int",
			"indicator": "Red" if rows else "Green",
		}
	]
	summary += [
		{"label": _(label), "value": count, "datatype": "Int"}
		for label, count in sorted(counts.items(), key=lambda item: -item[1])
	]
	return summary
