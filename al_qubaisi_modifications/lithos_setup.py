# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

"""Load the three Lithos input workbooks into ERPNext.

* ``01_Outlet_Company_Import_Sheet.xlsx``: outlet companies with their UAE VAT
  Settings, inter-company parties, the walk-in customer and the Lithos Store records.
* ``02_Item_Master_and_BOM_Template.xlsx``: Lithos Item Map rows, item barcodes and
  BOMs.
* ``03_Outlet_Account_Template.xlsx``: the outlet accounts and the Lithos Clearing
  mode of payment.

Dry run first. It reads everything and writes nothing::

	bench --site <site> execute al_qubaisi_modifications.lithos_setup.run \\
		--kwargs "{'folder': '/path/to/lithos_inputs'}"

Add ``'dry_run': 0`` to apply. ``'companies': [...]`` limits the company steps to the
named outlets. Without it, only outlets that are ready are taken: not yet in ERPNext,
with a TRN, at least one Store ID, and no possible existing company left to rule out.
An existing ERPNext company is only touched when it is named, because which existing
companies become outlets is still an open question.

Every step creates what is missing and reports what it left alone, so a workbook can
be corrected and the import simply run again.
"""

import os
from collections import Counter

import frappe
import openpyxl
from frappe import _
from frappe.utils import cint, flt, getdate, strip_html

from al_qubaisi_modifications.api import resolve_branch_customer
from al_qubaisi_modifications.lithos import collapse, normalise

MOTHER_COMPANY = "Al Qubaisi Group"
CLEARING = "Lithos Clearing"
DUE_FROM_OUTLETS = "Due from Outlet Companies"
DUE_TO_MOTHER = "Due to Al Qubaisi Group"
CONSUMPTION = "Recipe Consumption"
SALES_TAX_TEMPLATE = "UAE VAT 5%"
VAT_ACCOUNT = "VAT 5%"
# One customer for every outlet's till sales; the invoices are paid on the spot (D13).
WALK_IN = "Lithos Walk-in Customer"
# ERPNext only allows an inter-company invoice on a price list that is both buying and
# selling. Supplies to outlets are priced at cost (D12), so the list holds no prices.
INTER_COMPANY_PRICE_LIST = "Inter-company"

FILES = {
	"companies": "01_Outlet_Company_Import_Sheet.xlsx",
	"items": "02_Item_Master_and_BOM_Template.xlsx",
	"accounts": "03_Outlet_Account_Template.xlsx",
}

# Outcomes that are only counted, never listed line by line.
QUIET = {"Exists", "Out of scope"}
MAX_LISTED = 10


def text(value):
	"""A cell as trimmed text. Whole floats lose their ``.0`` (Store ID 141.0 -> 141)."""
	if value is None:
		return ""
	if isinstance(value, float) and value.is_integer():
		value = int(value)
	return collapse(str(value))


def read_sheet(path, sheet):
	"""Every non-blank row of a sheet, as dicts keyed by the header row.

	Formula cells are read as the value the spreadsheet last calculated, so a
	workbook must be saved from Excel or LibreOffice, not written by a script.
	"""
	workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
	try:
		rows = workbook[sheet].iter_rows(values_only=True)
		header = [text(cell) for cell in next(rows)]
		return [
			frappe._dict(zip(header, row, strict=False)) for row in rows if any(text(cell) for cell in row)
		]
	finally:
		workbook.close()


@frappe.whitelist()
def run(folder, companies=None, dry_run=1):
	"""Import the three workbooks in ``folder``. See the module docstring."""
	frappe.only_for("System Manager")
	if isinstance(companies, str):
		companies = frappe.parse_json(companies) if companies.startswith("[") else [companies]

	importer = Importer(folder, companies or [], cint(dry_run))
	importer.run()
	importer.print_report()
	return importer.summary()


class Importer:
	def __init__(self, folder, companies, dry_run):
		self.paths = {key: os.path.join(folder, name) for key, name in FILES.items()}
		missing = [path for path in self.paths.values() if not os.path.exists(path)]
		if missing:
			frappe.throw(_("Workbook not found: {0}").format(", ".join(missing)))

		self.named = [collapse(name) for name in companies]
		self.dry_run = dry_run
		self.lines = []
		# Company -> abbr for companies this run creates (or, in a dry run, would).
		self.planned = {}
		# (doctype, name) a dry run would have created, so later steps can rely on them.
		self.would = set()

	def run(self):
		self.company_rows = read_sheet(self.paths["companies"], "Companies")
		self.store_rows = read_sheet(self.paths["companies"], "Store Mapping")
		self.outlets = self.select_outlets()

		self.create_companies()
		self.create_accounts()
		self.setup_clearing_mode_of_payment()
		self.create_internal_customers()
		self.update_internal_supplier()
		self.setup_inter_company_price_list()
		self.setup_vat_settings()
		self.setup_walk_in_customer()
		self.import_stores()

		self.import_item_maps()
		self.import_item_actions()
		self.import_boms()

	# ---------- bookkeeping ----------

	def log(self, step, key, outcome, detail=""):
		self.lines.append((step, str(key), outcome, detail))

	def exists(self, doctype, name):
		return bool(name) and (bool(frappe.db.exists(doctype, name)) or (doctype, name) in self.would)

	def abbr(self, company):
		return frappe.db.get_value("Company", company, "abbr") or self.planned.get(company)

	def is_planned(self, company):
		"""True for a company this dry run would create: its standard records are assumed."""
		return self.dry_run and company in self.planned and not frappe.db.exists("Company", company)

	def write(self, step, key, outcome, detail, action):
		"""Run one unit of work in its own transaction, so a bad row cannot undo the others."""
		if self.dry_run:
			self.log(step, key, f"Would {outcome.lower()}", detail)
			return True
		try:
			action()
			frappe.db.commit()
		except Exception as e:
			frappe.db.rollback()
			self.log(step, key, "Error", strip_html(str(e)) or repr(e))
			return False
		self.log(step, key, {"Create": "Created", "Update": "Updated"}.get(outcome, outcome), detail)
		return True

	def create(self, step, values, key=None, detail="", submit=False, keep_status=False):
		key = key or values.get("name")

		def insert():
			doc = frappe.get_doc(values)
			doc.flags.keep_status = keep_status
			doc.insert(ignore_permissions=True)
			if submit:
				doc.submit()

		done = self.write(step, key, "Create", detail, insert)
		if done and self.dry_run:
			self.would.add((values["doctype"], key))
		return done

	def save(self, step, key, doc, detail="", outcome="Update"):
		return self.write(step, key, outcome, detail, lambda: doc.save(ignore_permissions=True))

	# ---------- 01: companies, parties, stores ----------

	def select_outlets(self):
		known = {text(row.get("Company")) for row in self.company_rows}
		for name in self.named:
			if name not in known:
				self.log("Scope", name, "Error", "Not in the Companies sheet.")

		with_store = {text(row.get("Outlet Company")) for row in self.store_rows if text(row.get("Store ID"))}
		outlets = []
		for row in self.company_rows:
			name = text(row.get("Company"))
			if not name:
				continue
			if self.named:
				if name in self.named:
					outlets.append(row)
				continue

			if frappe.db.exists("Company", name):
				reason = "Already an ERPNext company. Name it in 'companies' to include it (open question)."
			elif not text(row.get("Tax ID")):
				reason = "TRN needed."
			elif name not in with_store:
				reason = "Store ID needed."
			elif text(row.get("Possible Existing Company")):
				reason = (
					f"May be the existing company {text(row.get('Possible Existing Company'))}. "
					"Confirm, clear that column and re-run."
				)
			else:
				outlets.append(row)
				continue
			self.log("Scope", name, "Not ready", reason)

		return outlets

	def outlet_names(self):
		return [text(row.get("Company")) for row in self.outlets]

	def create_companies(self):
		for row in self.outlets:
			name = text(row.get("Company"))
			if frappe.db.exists("Company", name):
				self.log("Company", name, "Exists")
				continue

			abbr = text(row.get("Abbr"))
			taken = frappe.db.get_value("Company", {"abbr": abbr}) or next(
				(company for company, planned in self.planned.items() if planned == abbr), None
			)
			if not abbr or taken:
				self.log(
					"Company",
					name,
					"Error",
					f"Abbr {abbr!r} is already used by {taken}." if abbr else "Abbr is blank.",
				)
				continue

			based_on = text(row.get("Create Chart Of Accounts Based On")) or "Standard Template"
			chart = text(row.get("Chart Of Accounts Template"))
			values = {
				"doctype": "Company",
				"company_name": name,
				"abbr": abbr,
				"default_currency": text(row.get("Default Currency")) or "AED",
				"country": text(row.get("Country")) or "United Arab Emirates",
				"tax_id": text(row.get("Tax ID")),
				"phone_no": text(row.get("Phone No")),
				"email": text(row.get("Email")),
				"website": text(row.get("Website")),
				"create_chart_of_accounts_based_on": based_on,
			}
			if based_on == "Existing Company":
				values["existing_company"] = chart
			else:
				values["chart_of_accounts"] = chart or "Standard"

			self.planned[name] = abbr
			detail = "" if values["tax_id"] else "No TRN yet: add it on the company before go-live."
			if not self.create("Company", values, key=name, detail=detail):
				del self.planned[name]

	def create_accounts(self):
		template = [
			row
			for row in read_sheet(self.paths["accounts"], "Outlet Account Template")
			if text(row.get("Action")) == "Create"
		]
		for company in self.outlet_names():
			abbr = self.abbr(company)
			if not abbr:
				self.log("Account", company, "Skipped", "Company not created.")
				continue
			for row in template:
				self.create_account(
					company,
					abbr,
					text(row.get("Account Name")),
					text(row.get("Parent Account (standard chart)")),
					text(row.get("Account Type")),
				)

		if not self.outlets:
			return
		# Al Qubaisi Group's side of the inter-company flow. Its names are written in
		# full in the workbook, so the abbr is stripped back off.
		mother_abbr = self.abbr(MOTHER_COMPANY)
		suffix = f" - {mother_abbr}"
		for row in read_sheet(self.paths["accounts"], "Al Qubaisi Group"):
			if text(row.get("Action")) != "Create":
				continue
			self.create_account(
				MOTHER_COMPANY,
				mother_abbr,
				text(row.get("Account")).removesuffix(suffix),
				text(row.get("Parent Account")).removesuffix(suffix),
				text(row.get("Account Type")),
			)

	def create_account(self, company, abbr, account_name, parent_name, account_type):
		name, parent = f"{account_name} - {abbr}", f"{parent_name} - {abbr}"
		if self.exists("Account", name):
			self.log("Account", name, "Exists")
			return
		if not (self.exists("Account", parent) or self.is_planned(company)):
			self.log("Account", name, "Error", f"Parent account {parent} not found.")
			return
		self.create(
			"Account",
			{
				"doctype": "Account",
				"account_name": account_name,
				"parent_account": parent,
				"company": company,
				"account_type": account_type,
				"is_group": 0,
			},
			key=name,
		)

	def setup_clearing_mode_of_payment(self):
		"""One mode of payment, Lithos Clearing, with each outlet's clearing account (D13)."""
		if not self.outlets:
			return
		if frappe.db.exists("Mode of Payment", CLEARING):
			mode = frappe.get_doc("Mode of Payment", CLEARING)
		else:
			# new_doc, not get_doc(dict): only new_doc marks the document as new for is_new().
			mode = frappe.new_doc("Mode of Payment")
			mode.update({"mode_of_payment": CLEARING, "type": "General", "enabled": 1})

		have = {row.company for row in mode.accounts}
		added = []
		for company in self.outlet_names():
			account = f"{CLEARING} - {self.abbr(company)}"
			if company in have:
				continue
			if not self.exists("Account", account):
				self.log(
					"Mode of Payment", f"{CLEARING} / {company}", "Skipped", f"Account {account} not created."
				)
				continue
			mode.append("accounts", {"company": company, "default_account": account})
			added.append(company)

		if not added:
			self.log("Mode of Payment", CLEARING, "Exists")
			return
		self.save(
			"Mode of Payment",
			CLEARING,
			mode,
			f"Accounts for {', '.join(added)}",
			"Create" if mode.is_new() else "Update",
		)

	def create_internal_customers(self):
		"""Each outlet is an internal customer of Al Qubaisi Group, for supplies at cost (D12)."""
		receivable = f"{DUE_FROM_OUTLETS} - {self.abbr(MOTHER_COMPANY)}"
		group = leaf(
			"Customer Group", frappe.db.get_single_value("Selling Settings", "customer_group"), "Commercial"
		)
		territory = leaf(
			"Territory", frappe.db.get_single_value("Selling Settings", "territory"), "United Arab Emirates"
		)

		for company in self.outlet_names():
			if not self.abbr(company):
				continue
			existing = frappe.db.get_value(
				"Customer", {"is_internal_customer": 1, "represents_company": company}
			)
			if existing:
				self.log("Internal Customer", existing, "Exists")
				continue
			if frappe.db.exists("Customer", company):
				self.log(
					"Internal Customer",
					company,
					"Error",
					"A customer with this name exists but is not internal. Make it internal or rename it.",
				)
				continue
			self.create(
				"Internal Customer",
				{
					"doctype": "Customer",
					"customer_name": company,
					"customer_type": "Company",
					"customer_group": group,
					"territory": territory,
					"is_internal_customer": 1,
					"represents_company": company,
					"companies": [{"company": MOTHER_COMPANY}],
					"accounts": (
						[{"company": MOTHER_COMPANY, "account": receivable}]
						if self.exists("Account", receivable)
						else []
					),
				},
				key=company,
			)

	def update_internal_supplier(self):
		"""Al Qubaisi Group is one internal supplier, allowed for every outlet company."""
		if not self.outlets:
			return
		name = frappe.db.get_value(
			"Supplier", {"is_internal_supplier": 1, "represents_company": MOTHER_COMPANY}
		)
		if name:
			supplier = frappe.get_doc("Supplier", name)
		elif frappe.db.exists("Supplier", MOTHER_COMPANY):
			self.log(
				"Internal Supplier",
				MOTHER_COMPANY,
				"Error",
				"A supplier with this name exists but is not internal. Make it internal or rename it.",
			)
			return
		else:
			supplier = frappe.new_doc("Supplier")
			supplier.update(
				{
					"supplier_name": MOTHER_COMPANY,
					"supplier_type": "Company",
					"supplier_group": leaf(
						"Supplier Group",
						frappe.db.get_single_value("Buying Settings", "supplier_group"),
						"Local",
					),
					"country": "United Arab Emirates",
					"is_internal_supplier": 1,
					"represents_company": MOTHER_COMPANY,
				}
			)

		allowed = {row.company for row in supplier.companies}
		with_account = {row.company for row in supplier.accounts}
		added = []
		for company in self.outlet_names():
			abbr = self.abbr(company)
			if not abbr:
				continue
			payable = f"{DUE_TO_MOTHER} - {abbr}"
			if company not in allowed:
				supplier.append("companies", {"company": company})
				added.append(company)
			if company not in with_account and self.exists("Account", payable):
				supplier.append("accounts", {"company": company, "account": payable})
				if company not in added:
					added.append(company)

		# Supplies arrive as inter-company invoices, never against a Purchase Order or
		# Receipt, so the global "Purchase Order Required" must not apply to them.
		waived = []
		for fieldname in (
			"allow_purchase_invoice_creation_without_purchase_order",
			"allow_purchase_invoice_creation_without_purchase_receipt",
		):
			if not supplier.get(fieldname):
				supplier.set(fieldname, 1)
				waived.append(fieldname)

		if not (added or waived):
			self.log("Internal Supplier", supplier.name, "Exists")
			return
		details = [f"Allowed for {', '.join(added)}."] if added else []
		if waived:
			details.append("Purchase Invoices without a Purchase Order or Receipt allowed.")
		self.save(
			"Internal Supplier",
			MOTHER_COMPANY,
			supplier,
			" ".join(details),
			"Create" if supplier.is_new() else "Update",
		)

	def setup_inter_company_price_list(self):
		if self.exists("Price List", INTER_COMPANY_PRICE_LIST):
			self.log("Price List", INTER_COMPANY_PRICE_LIST, "Exists")
		else:
			self.create(
				"Price List",
				{
					"doctype": "Price List",
					"price_list_name": INTER_COMPANY_PRICE_LIST,
					"currency": frappe.db.get_value("Company", MOTHER_COMPANY, "default_currency"),
					"buying": 1,
					"selling": 1,
					"enabled": 1,
				},
				key=INTER_COMPANY_PRICE_LIST,
			)
		for company in self.outlet_names():
			customer = frappe.db.get_value(
				"Customer", {"is_internal_customer": 1, "represents_company": company}
			)
			if not customer:
				continue
			if frappe.db.get_value("Customer", customer, "default_price_list") == INTER_COMPANY_PRICE_LIST:
				self.log("Price List", customer, "Exists")
				continue
			doc = frappe.get_doc("Customer", customer)
			doc.default_price_list = INTER_COMPANY_PRICE_LIST
			self.save("Price List", customer, doc, f"Default price list {INTER_COMPANY_PRICE_LIST}.")

	def setup_vat_settings(self):
		"""UAE VAT Settings per outlet company, so its VAT 201 return finds the VAT account."""
		for company in self.outlet_names():
			abbr = self.abbr(company)
			if not abbr:
				continue
			if frappe.db.exists("UAE VAT Settings", company):
				self.log("VAT Settings", company, "Exists")
				continue
			account = f"{VAT_ACCOUNT} - {abbr}"
			if not self.exists("Account", account) and not self.is_planned(company):
				self.log("VAT Settings", company, "Error", f"Account {account} not found.")
				continue
			self.create(
				"VAT Settings",
				{
					"doctype": "UAE VAT Settings",
					"company": company,
					"uae_vat_accounts": [{"account": account}],
				},
				key=company,
			)

	def setup_walk_in_customer(self):
		if self.exists("Customer", WALK_IN):
			self.log("Walk-in Customer", WALK_IN, "Exists")
			return
		self.create(
			"Walk-in Customer",
			{
				"doctype": "Customer",
				"customer_name": WALK_IN,
				"customer_type": "Individual",
				"customer_group": leaf("Customer Group", "Individual"),
				"territory": leaf("Territory", "United Arab Emirates"),
			},
			key=WALK_IN,
		)

	def import_stores(self):
		in_scope = set(self.outlet_names())
		price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list")

		for row in self.store_rows:
			store_id, company = text(row.get("Store ID")), text(row.get("Outlet Company"))
			if company not in in_scope:
				self.log("Lithos Store", store_id or company, "Out of scope")
				continue
			if not store_id:
				self.log("Lithos Store", company, "Skipped", "Store ID needed.")
				continue
			abbr = self.abbr(company)
			if not abbr:
				self.log("Lithos Store", store_id, "Skipped", f"Company {company} not created.")
				continue

			branch = text(row.get("Branch (reporting, D11)"))
			branch = branch if frappe.db.exists("Branch", branch) else None
			go_live = getdate(row.get("Sync From Date")) if row.get("Sync From Date") else None
			wants_sync = text(row.get("Sync Enabled")) == "Yes"
			values = {
				"terminal_id": text(row.get("Terminal ID")),
				"outlet_name": company,
				"company": company,
				"branch": branch,
				"warehouse": self.first_of(
					company, "Warehouse", text(row.get("Outlet Warehouse")), f"Stores - {abbr}"
				),
				"cost_center": self.first_of(
					company, "Cost Center", text(row.get("Cost Center")), f"Main - {abbr}"
				),
				"consumption_account": self.first_of(company, "Account", f"{CONSUMPTION} - {abbr}"),
				"taxes_and_charges": self.first_of(
					company, "Sales Taxes and Charges Template", f"{SALES_TAX_TEMPLATE} - {abbr}"
				),
				"selling_price_list": price_list,
				"customer": WALK_IN if self.exists("Customer", WALK_IN) else resolve_branch_customer(branch),
				"sync_from_date": go_live,
				# Go-live is the switch: a store only syncs once it has a start date.
				"sync_enabled": 1 if wants_sync and go_live else 0,
			}
			notes = []
			if not branch:
				notes.append("No Branch: create one or pick it on the store.")
			if wants_sync and not go_live:
				notes.append("Sync stays off until a Sync From Date (go-live) is set.")

			current, moving_to = frappe.db.get_value(
				"Lithos Store", store_id, ["company", "move_to_company"]
			) or (None, None)
			if current and current != company:
				# Live in another company: the Re-post button on the store moves it (D3).
				store = frappe.get_doc("Lithos Store", store_id)
				details = []
				if moving_to != company:
					store.move_to_company = company
					details.append(
						f"Posts into {current} until its history is re-posted into {company} (D3)."
					)
				if self.use_walk_in(store):
					details.append(f"Customer: {WALK_IN}.")
				if details:
					self.save("Lithos Store", store_id, store, " ".join(details))
				else:
					self.log("Lithos Store", store_id, "Exists")
				continue
			if current:
				# Fill gaps only: anything already set was set on purpose in ERPNext.
				store = frappe.get_doc("Lithos Store", store_id)
				gaps = {k: v for k, v in values.items() if v and not store.get(k) and k != "sync_enabled"}
				if self.use_walk_in(store):
					gaps["customer"] = WALK_IN
				if not gaps:
					self.log("Lithos Store", store_id, "Exists")
					continue
				store.update(gaps)
				self.save("Lithos Store", store_id, store, f"Filled {', '.join(gaps)}")
				continue

			self.create(
				"Lithos Store",
				{"doctype": "Lithos Store", "store_id": store_id, **values},
				key=store_id,
				detail=" ".join(notes),
			)

	def use_walk_in(self, store):
		"""Swap a branch customer for the walk-in one: a branch customer is the outlet as a
		buyer from Al Qubaisi Group, not the people at its till. True if it changed."""
		if not self.exists("Customer", WALK_IN) or store.customer == WALK_IN:
			return False
		if store.customer and not frappe.db.get_value("Customer", store.customer, "custom_is_branch"):
			return False
		store.customer = WALK_IN
		return True

	def first_of(self, company, doctype, *names):
		"""The first of ``names`` that exists. In a dry run a new company's defaults count."""
		for name in names:
			if name and self.exists(doctype, name):
				return name
		return names[-1] if self.is_planned(company) else None

	# ---------- 02: item map, barcodes, BOMs ----------

	def import_item_maps(self):
		"""Lithos Aliases -> all-stores Lithos Item Map rows.

		A row the client has not judged ("Mapping Correct?" and "Correct Item Code"
		both blank) is only added, never used to overwrite ERPNext.
		"""
		existing = {
			row.match_key: row
			for row in frappe.get_all(
				"Lithos Item Map",
				filters={"store": ("is", "not set")},
				fields=["name", "match_key", "item_code", "status"],
			)
		}
		seen = {}
		for row in read_sheet(self.paths["items"], "Lithos Aliases"):
			lithos_name = text(row.get("Lithos Item Name"))[:140]
			if not lithos_name:
				continue
			key = normalise(lithos_name)
			if key in seen:
				self.log(
					"Item Map",
					lithos_name,
					"Skipped",
					f"Same name as {seen[key]!r} once case and spaces are ignored.",
				)
				continue
			seen[key] = lithos_name

			mapped, correct = text(row.get("Mapped To Item Code")), text(row.get("Correct Item Code"))
			verdict = text(row.get("Mapping Correct?"))
			if correct:
				item, status = correct, "Manual"
			elif verdict == "Yes":
				item, status = mapped, "Manual"
			elif verdict == "No":
				item, status = None, "Unmapped"
			else:
				item, status = mapped, guessed_status(row, lithos_name)

			if item and not frappe.db.exists("Item", item):
				self.log("Item Map", lithos_name, "Error", f"Item {item} not found.")
				continue

			current = existing.get(key)
			if current:
				judged = bool(correct or verdict)
				if not judged or (current.item_code == item and current.status == status):
					self.log("Item Map", lithos_name, "Exists")
					continue
				doc = frappe.get_doc("Lithos Item Map", current.name)
				doc.update({"item_code": item, "status": status})
				doc.flags.keep_status = True
				self.save("Item Map", lithos_name, doc, f"{status}: {item or 'no item'}")
				continue

			self.create(
				"Item Map",
				{
					"doctype": "Lithos Item Map",
					"lithos_item_name": lithos_name,
					"item_code": item,
					"status": status,
					"first_sold": getdate(row.get("First Sold")) if row.get("First Sold") else None,
					"last_sold": getdate(row.get("Last Sold")) if row.get("Last Sold") else None,
				},
				key=lithos_name,
				keep_status=True,
			)

	def import_item_actions(self):
		"""Sellable Items: the final barcode, and the Merge or Disable chosen for each item.

		A merge moves the item's Lithos names to the target and disables a POS- item
		the sync created (see lithos_workbench.merge). Posted days keep the old item
		until they are re-posted. Rename has no new-name column, so it is reported.
		"""
		for row in read_sheet(self.paths["items"], "Sellable Items"):
			item = text(row.get("Item Code"))
			if not item:
				continue
			barcode = text(row.get("Final Barcode"))
			if barcode:
				self.set_barcode(item, barcode)

			action, target = text(row.get("Action")), text(row.get("Merge Into Item Code"))
			if action == "Merge into another item":
				self.merge_item(item, target)
			elif action == "Disable":
				self.disable_item(item)
			elif action == "Rename":
				self.log(
					"Item Action",
					item,
					"Skipped",
					"Rename: change the Item Name on the item; the sheet has no new name.",
				)

	def set_barcode(self, item, barcode):
		owner = frappe.db.get_value("Item Barcode", {"barcode": barcode, "parenttype": "Item"}, "parent")
		if owner == item:
			self.log("Barcode", f"{item} {barcode}", "Exists")
			return
		if owner:
			self.log("Barcode", f"{item} {barcode}", "Error", f"Barcode already on item {owner}.")
			return
		if not frappe.db.exists("Item", item):
			self.log("Barcode", f"{item} {barcode}", "Error", f"Item {item} not found.")
			return
		doc = frappe.get_doc("Item", item)
		doc.append("barcodes", {"barcode": barcode})
		self.write("Barcode", f"{item} {barcode}", "Create", "", lambda: doc.save(ignore_permissions=True))

	def merge_item(self, item, target):
		from al_qubaisi_modifications.lithos_workbench import merge

		if not target or not frappe.db.exists("Item", target):
			self.log("Item Action", item, "Error", f"Merge target {target or '(blank)'} not found.")
			return
		names = frappe.get_all("Lithos Item Map", filters={"item_code": item}, pluck="name")
		disabled = frappe.db.get_value("Item", item, "disabled")
		if not names and disabled:
			self.log("Item Action", item, "Exists", f"Merged into {target} earlier.")
			return
		self.write(
			"Item Action",
			item,
			"Update",
			f"Merge into {target}: {len(names)} map row(s) move.",
			lambda: merge(item, target),
		)

	def disable_item(self, item):
		if not frappe.db.exists("Item", item):
			self.log("Item Action", item, "Error", f"Item {item} not found.")
			return
		if frappe.db.get_value("Item", item, "disabled"):
			self.log("Item Action", item, "Exists", "Already disabled.")
			return
		if frappe.db.exists("Lithos Item Map", {"item_code": item, "status": ("!=", "Ignored")}):
			# Its Lithos names would stop resolving and hold their days.
			self.log(
				"Item Action", item, "Error", "Lithos names still map to it: choose Merge, or map them first."
			)
			return
		doc = frappe.get_doc("Item", item)
		doc.disabled = 1
		self.save("Item Action", item, doc, "Disabled.")

	def import_boms(self):
		"""BOM Template rows with a raw material and a quantity, one BOM per finished item.

		BOMs belong to Al Qubaisi Group, which owns the recipes. The consumption entry
		reads only the BOM's lines, so one BOM per item serves every outlet company.
		"""
		boms = {}
		for row in read_sheet(self.paths["items"], "BOM Template"):
			finished, raw = text(row.get("Finished Item Code")), text(row.get("Raw Material Item Code"))
			if not (finished and raw):
				# The template is pre-filled with every finished item; most wait for the client.
				continue
			bom = boms.setdefault(
				finished, {"quantity": flt(row.get("BOM Quantity")) or 1, "items": [], "errors": []}
			)

			qty = flt(row.get("Raw Material Qty"))
			stock_uom = frappe.db.get_value("Item", raw, "stock_uom")
			uom = text(row.get("Raw Material UOM")) or stock_uom
			if not stock_uom:
				bom["errors"].append(f"{raw} not found")
			elif qty <= 0:
				bom["errors"].append(f"{raw} has no quantity")
			elif uom != stock_uom and not frappe.db.exists(
				"UOM Conversion Detail", {"parent": raw, "parenttype": "Item", "uom": uom}
			):
				bom["errors"].append(f"{raw} has no conversion from {uom} to {stock_uom}")
			else:
				bom["items"].append({"item_code": raw, "qty": qty, "uom": uom})

		if not boms:
			self.log("BOM", "BOM Template", "Skipped", "No row has a raw material and quantity yet.")
			return

		for finished, bom in boms.items():
			if not frappe.db.exists("Item", finished):
				self.log("BOM", finished, "Error", "Finished item not found.")
				continue
			if bom["errors"]:
				self.log("BOM", finished, "Error", "; ".join(bom["errors"]))
				continue
			default = frappe.db.get_value("Item", finished, "default_bom")
			if default:
				self.log("BOM", finished, "Exists", f"Default BOM {default}: change it in ERPNext.")
				continue
			self.create(
				"BOM",
				{
					"doctype": "BOM",
					"item": finished,
					"company": MOTHER_COMPANY,
					"quantity": bom["quantity"],
					"is_active": 1,
					"is_default": 1,
					"rm_cost_as_per": "Valuation Rate",
					"items": bom["items"],
				},
				key=finished,
				detail=f"{len(bom['items'])} raw material(s)",
				submit=True,
			)

	# ---------- report ----------

	def summary(self):
		counts = {}
		for step, _key, outcome, _detail in self.lines:
			counts.setdefault(step, Counter())[outcome] += 1
		return {"dry_run": bool(self.dry_run), "counts": {step: dict(c) for step, c in counts.items()}}

	def print_report(self):
		print("\nLithos input import:", "DRY RUN, nothing written" if self.dry_run else "APPLIED")
		for step, counts in self.summary()["counts"].items():
			print(f"  {step:<18} " + ", ".join(f"{n} {outcome}" for outcome, n in counts.items()))
		print()
		# Errors are always listed in full; anything else is capped so 257 identical
		# "Created" lines do not bury them.
		shown = Counter()
		for step, key, outcome, detail in self.lines:
			if outcome in QUIET:
				continue
			shown[step, outcome] += 1
			if outcome == "Error" or shown[step, outcome] <= MAX_LISTED:
				print(f"  [{step}] {key}: {outcome}" + (f". {detail}" if detail else ""))
			elif shown[step, outcome] == MAX_LISTED + 1:
				print(f"  [{step}] ... more {outcome} lines, see the counts above")


def guessed_status(row, lithos_name):
	"""How the sync originally matched an alias the client has not judged yet."""
	if text(row.get("Origin")).startswith("Auto-created"):
		return "Auto-created"
	same_name = normalise(lithos_name) == normalise(text(row.get("ERPNext Item Name")))
	return "Matched by name" if same_name else "Matched by barcode"


def leaf(doctype, *candidates):
	"""The first candidate that is a usable (non-group) tree node, else any leaf."""
	for name in candidates:
		if name and frappe.db.get_value(doctype, name, "is_group") == 0:
			return name
	return frappe.db.get_value(doctype, {"is_group": 0})
