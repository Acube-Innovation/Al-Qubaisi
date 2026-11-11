# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

"""Lithos Control Tower (M8): sync health, sales and outlet stock for one scope.

The page and the four Lithos reports share that scope: the Lithos Stores in the
companies, brands and stores chosen, within the companies the user may see. A User
Permission on Company keeps an outlet user to their own company everywhere here,
since these queries read past the doctype permissions an outlet user lacks.

Lithos sends Qty, Net and Tax per item, so discount, returns, invoice count and
average ticket have no source here (D5 keeps /adsr out).
"""

import frappe
from frappe import _
from frappe.core.doctype.user_permission.user_permission import get_user_permissions
from frappe.utils import add_days, add_to_date, cint, date_diff, flt, getdate, now_datetime, today

from al_qubaisi_modifications import lithos, lithos_ledger
from al_qubaisi_modifications.lithos_console import MISSING, STORE_FIELDS, day_records, make_cell

TOWER_ROLES = (*lithos.LITHOS_ROLES, "Accounts Manager", "Accounts User")
MAX_DAYS = 366
TOP_STORES = 10

# How a sold item's raw materials leave the outlet (M6).
BOM, AS_STOCK, NO_BOM, SUSPENSE = "BOM", "Issued as stock", "No BOM", "Suspense item"


def as_list(value):
	"""A filter value from the page (JSON) or a report (list), as a list."""
	if not value:
		return []
	if isinstance(value, str):
		value = frappe.parse_json(value) if value.startswith("[") else [value]
	return [v for v in value if v]


def allowed_companies():
	"""The companies a User Permission keeps the user to, or None when nothing does."""
	permitted = get_user_permissions().get("Company")
	return sorted({row.get("doc") for row in permitted}) if permitted else None


def is_lithos_user():
	return bool(set(lithos.LITHOS_ROLES) & set(frappe.get_roles()))


class Scope:
	"""The Lithos Stores in view, and what follows from them."""

	def __init__(self, companies=None, brands=None, stores=None):
		self.allowed = allowed_companies()
		companies = as_list(companies)
		if self.allowed is not None:
			companies = [c for c in companies if c in self.allowed] if companies else self.allowed

		filters = {}
		if companies:
			filters["company"] = ("in", companies)
		if as_list(brands):
			filters["brand"] = ("in", as_list(brands))
		if as_list(stores):
			filters["name"] = ("in", as_list(stores))
		self.stores = (
			frappe.get_all(
				"Lithos Store",
				filters=filters,
				fields=[*STORE_FIELDS, "branch", "warehouse"],
				order_by="company, name",
			)
			if companies or self.allowed is None
			else []
		)
		self.by_id = {s.name: s for s in self.stores}
		# Never empty, so "in %(...)s" stays valid SQL and matches nothing.
		self.ids = [s.name for s in self.stores] or [""]
		self.companies = sorted({s.company for s in self.stores}) or [""]
		self.warehouses = sorted({s.warehouse for s in self.stores if s.warehouse}) or [""]

	def params(self, from_date=None, to_date=None, **extra):
		return {
			"ids": self.ids,
			"companies": self.companies,
			"warehouses": self.warehouses,
			"allowed": self.allowed or [""],
			"from_date": from_date,
			"to_date": to_date,
			**extra,
		}

	def sales_condition(self, alias="bs"):
		"""Branch Sales of the stores in view, and only in companies the user may see."""
		condition = f"{alias}.lithos_store_id in %(ids)s"
		if self.allowed is not None:
			condition += f" and {alias}.company in %(allowed)s"
		return condition


def date_range(from_date=None, to_date=None, days=30):
	to_date = getdate(to_date or add_days(today(), -1))
	from_date = getdate(from_date or add_days(to_date, -(days - 1)))
	if from_date > to_date:
		frappe.throw(_("From Date cannot be after To Date."))
	if date_diff(to_date, from_date) >= MAX_DAYS:
		frappe.throw(_("Choose at most {0} days at a time.").format(MAX_DAYS))
	return from_date, to_date


def coverage(item_code, default_bom, is_stock_item, suspense=None):
	"""How a sold item's raw materials leave the outlet, as lithos_ledger.raw_materials decides."""
	if suspense and item_code == suspense:
		return SUSPENSE
	if default_bom:
		return BOM
	return AS_STOCK if cint(is_stock_item) else NO_BOM


@frappe.whitelist()
def get_tower(companies=None, brands=None, stores=None, from_date=None, to_date=None):
	"""Everything the Control Tower shows, for one scope and date range."""
	frappe.only_for(TOWER_ROLES)
	from_date, to_date = date_range(from_date, to_date)
	scope = Scope(companies, brands, stores)
	return {
		"from_date": from_date,
		"to_date": to_date,
		"stores": len(scope.stores),
		"sync": sync_cards(scope, from_date, to_date),
		"amounts": amount_cards(scope, from_date, to_date),
		"material": material_cards(scope, from_date, to_date),
		"charts": charts(scope, from_date, to_date),
	}


def sync_cards(scope, from_date, to_date):
	yesterday = getdate(add_days(today(), -1))
	live = [
		s
		for s in scope.stores
		if s.sync_enabled and s.sync_from_date and getdate(s.sync_from_date) <= yesterday
	]

	logs, sales = day_records(scope.ids, yesterday, yesterday)
	synced = sum(
		1
		for s in live
		if make_cell(s, yesterday, logs.get((s.name, str(yesterday))), sales.get((s.name, str(yesterday))))[
			"status"
		]
		in (lithos.POSTED, lithos.NO_SALES)
	)

	logs, sales = day_records(scope.ids, from_date, to_date)
	counts = {lithos.HELD: 0, lithos.FAILED: 0, MISSING: 0}
	for store in scope.stores:
		for day in lithos.days_between(from_date, to_date):
			key = (store.name, str(day))
			status = make_cell(store, day, logs.get(key), sales.get(key))["status"]
			if status in counts:
				counts[status] += 1

	return {
		"live": len(live),
		"synced_yesterday": synced,
		"failed_runs": failed_runs(scope),
		"last_success": frappe.db.sql(
			"""select max(last_attempt) from `tabLithos Sync Log`
			where store in %(ids)s and status in %(statuses)s""",
			{"ids": scope.ids, "statuses": (lithos.POSTED, lithos.NO_SALES)},
		)[0][0],
		"furthest_behind": min(
			(s for s in live if s.last_synced_date),
			key=lambda s: getdate(s.last_synced_date),
			default=None,
		),
		"held": counts[lithos.HELD],
		"failed": counts[lithos.FAILED],
		"missing": counts[MISSING],
		# Map rows cover every store, and only Lithos users can act on them.
		"unmapped_items": frappe.db.count("Lithos Item Map", {"status": lithos.MAP_UNMAPPED})
		if is_lithos_user()
		else None,
		"items_without_bom": len(items_without_bom(scope, from_date, to_date)),
	}


def failed_runs(scope):
	"""Runs in the last 24 hours with a failed store in view."""
	runs = frappe.get_all(
		"Lithos Sync Run",
		filters={"creation": (">=", add_to_date(now_datetime(), hours=-24))},
		or_filters={"status": "Failed", "stores_failed": (">", 0)},
		fields=["name", "store_ids"],
	)
	in_view = set(scope.ids)
	return sum(1 for run in runs if in_view & {s.strip() for s in (run.store_ids or "").split(",")})


def items_without_bom(scope, from_date, to_date):
	"""Items sold in view that leave no raw material behind: no BOM and not a stock item."""
	return frappe.db.sql_list(
		f"""
		select distinct bsi.item_code
		from `tabBranch Sales Item` bsi
		join `tabBranch Sales` bs on bs.name = bsi.parent
		join `tabItem` i on i.name = bsi.item_code
		where bs.docstatus = 1 and {scope.sales_condition()}
			and bs.posting_date between %(from_date)s and %(to_date)s
			and ifnull(i.default_bom, '') = '' and i.is_stock_item = 0
		""",
		scope.params(from_date, to_date),
	)


def amount_cards(scope, from_date, to_date):
	params = scope.params(from_date, to_date)
	sales = frappe.db.sql(
		f"""
		select count(*) as days, ifnull(sum(bs.total), 0) as net,
			ifnull(sum(bs.total_taxes_and_charges), 0) as vat
		from `tabBranch Sales` bs
		where bs.docstatus = 1 and {scope.sales_condition()}
			and bs.posting_date between %(from_date)s and %(to_date)s
		""",
		params,
		as_dict=True,
	)[0]
	logs = frappe.db.sql(
		"""
		select ifnull(sum(void_total), 0) as voids, ifnull(sum(variance), 0) as variance,
			sum(round(variance, 2) != 0) as variance_days
		from `tabLithos Sync Log`
		where store in %(ids)s and posting_date between %(from_date)s and %(to_date)s
		""",
		params,
		as_dict=True,
	)[0]
	return {
		"days": sales.days,
		"net": flt(sales.net, 2),
		"vat": flt(sales.vat, 2),
		"gross": flt(flt(sales.net) + flt(sales.vat), 2),
		"voids": flt(logs.voids, 2),
		"variance": flt(logs.variance, 2),
		"variance_days": cint(logs.variance_days),
	}


def material_cards(scope, from_date, to_date):
	params = scope.params(from_date, to_date)
	stock_in = frappe.db.sql(
		"""
		select ifnull(sum(pii.stock_qty), 0) as qty, ifnull(sum(pii.base_net_amount), 0) as value,
			count(distinct pi.name) as documents
		from `tabPurchase Invoice Item` pii
		join `tabPurchase Invoice` pi on pi.name = pii.parent
		where pi.docstatus = 1 and pi.is_internal_supplier = 1 and pi.update_stock = 1
			and pi.company in %(companies)s and pii.warehouse in %(warehouses)s
			and pi.posting_date between %(from_date)s and %(to_date)s
		""",
		params,
		as_dict=True,
	)[0]
	stock_out = frappe.db.sql(
		f"""
		select ifnull(sum(sed.transfer_qty), 0) as qty, ifnull(sum(sed.amount), 0) as value,
			count(distinct se.name) as documents
		from `tabStock Entry Detail` sed
		join `tabStock Entry` se on se.name = sed.parent
		join `tabBranch Sales` bs on bs.consumption_entry = se.name
		where se.docstatus = 1 and bs.docstatus = 1 and {scope.sales_condition()}
			and bs.posting_date between %(from_date)s and %(to_date)s
		""",
		params,
		as_dict=True,
	)[0]
	# Supplied by the group and not yet counted in: the outlet's Purchase Invoice is a
	# draft, or was never created.
	not_received = frappe.db.sql(
		"""
		select count(*) as documents, ifnull(sum(si.base_grand_total), 0) as value
		from `tabSales Invoice` si
		where si.docstatus = 1 and si.is_return = 0 and si.is_internal_customer = 1
			and si.represents_company in %(companies)s
			and not exists (
				select 1 from `tabPurchase Invoice` pi
				where pi.inter_company_invoice_reference = si.name and pi.docstatus = 1
			)
		""",
		params,
		as_dict=True,
	)[0]
	# Of the raw materials the outlets used or are waiting to use, those with none left.
	at_zero = frappe.db.sql(
		f"""
		select count(*)
		from (
			select distinct sed.s_warehouse, sed.item_code
			from `tabStock Entry Detail` sed
			join `tabStock Entry` se on se.name = sed.parent
			join `tabBranch Sales` bs on bs.consumption_entry = se.name
			where se.docstatus < 2 and bs.docstatus = 1 and {scope.sales_condition()}
				and bs.posting_date between %(from_date)s and %(to_date)s
		) used
		left join `tabBin` bin on bin.warehouse = used.s_warehouse and bin.item_code = used.item_code
		where ifnull(bin.actual_qty, 0) <= 0
		""",
		params,
	)[0][0]
	pending = frappe.db.sql(
		f"""
		select count(*) from `tabBranch Sales` bs
		where bs.docstatus = 1 and bs.ledger_status = %(pending)s and {scope.sales_condition()}
			and bs.posting_date between %(from_date)s and %(to_date)s
		""",
		{**params, "pending": lithos_ledger.PENDING},
	)[0][0]
	return {
		"ledger_stores": sum(1 for s in scope.stores if s.ledger_from_date),
		"stock_in": stock_in,
		"stock_out": stock_out,
		"not_received": not_received,
		"at_zero": at_zero,
		"consumption_pending": pending,
	}


def charts(scope, from_date, to_date):
	params = scope.params(from_date, to_date)
	days = [str(day) for day in lithos.days_between(from_date, to_date)]
	index = {day: i for i, day in enumerate(days)}

	by_company = {}
	for row in frappe.db.sql(
		f"""
		select bs.posting_date, bs.company, sum(bs.total) as net
		from `tabBranch Sales` bs
		where bs.docstatus = 1 and {scope.sales_condition()}
			and bs.posting_date between %(from_date)s and %(to_date)s
		group by bs.posting_date, bs.company
		""",
		params,
		as_dict=True,
	):
		values = by_company.setdefault(row.company, [0.0] * len(days))
		values[index[str(row.posting_date)]] = flt(row.net, 2)

	top = frappe.db.sql(
		f"""
		select bs.lithos_store_id as store, sum(bs.total) as net, count(*) as days
		from `tabBranch Sales` bs
		where bs.docstatus = 1 and {scope.sales_condition()}
			and bs.posting_date between %(from_date)s and %(to_date)s
		group by bs.lithos_store_id
		order by net desc
		limit %(limit)s
		""",
		{**params, "limit": TOP_STORES},
		as_dict=True,
	)
	for row in top:
		store = scope.by_id.get(row.store) or {}
		row.update({"outlet_name": store.get("outlet_name"), "company": store.get("company")})

	variance = [0.0] * len(days)
	for row in frappe.db.sql(
		"""
		select posting_date, sum(variance) as variance
		from `tabLithos Sync Log`
		where store in %(ids)s and posting_date between %(from_date)s and %(to_date)s
		group by posting_date
		""",
		params,
		as_dict=True,
	):
		variance[index[str(row.posting_date)]] = flt(row.variance, 2)

	return {
		"days": days,
		"by_company": [
			{"company": company, "values": values, "total": flt(sum(values), 2)}
			for company, values in sorted(by_company.items(), key=lambda item: -sum(item[1]))
		],
		"top_stores": top,
		"variance": variance,
	}


@frappe.whitelist()
def store_options(txt=None, companies=None):
	"""Stores for the filters, within the companies the user may see."""
	frappe.only_for(TOWER_ROLES)
	txt = (txt or "").lower()
	return [
		{"value": s.name, "description": f"{s.outlet_name or ''} · {s.company}"}
		for s in Scope(companies).stores
		if txt in s.name.lower() or txt in (s.outlet_name or "").lower()
	][:20]


@frappe.whitelist()
def brand_options(txt=None):
	frappe.only_for(TOWER_ROLES)
	txt = (txt or "").lower()
	return sorted({s.brand for s in Scope().stores if s.brand and txt in s.brand.lower()})[:20]
