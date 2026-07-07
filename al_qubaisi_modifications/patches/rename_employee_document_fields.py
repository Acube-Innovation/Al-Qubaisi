import frappe
from frappe.utils import getdate


def execute():
	"""Migrate data after renaming table_oque -> documents and creation_date -> issue_date."""
	if not frappe.db.table_exists("Employee Documents Detail"):
		return

	frappe.db.sql(
		"""update `tabEmployee Documents Detail`
		set parentfield = 'documents'
		where parentfield = 'table_oque'"""
	)

	if frappe.db.has_column("Employee Documents Detail", "creation_date"):
		rows = frappe.db.sql(
			"""select name, creation_date from `tabEmployee Documents Detail`
			where ifnull(creation_date, '') != '' and issue_date is null"""
		)
		for name, value in rows:
			try:
				issue_date = getdate(value)
			except Exception:
				continue
			frappe.db.set_value(
				"Employee Documents Detail", name, "issue_date", issue_date, update_modified=False
			)
