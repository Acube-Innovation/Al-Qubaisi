# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, getdate, nowdate

# Statuses set manually by the user; the system never overwrites these
MANUAL_STATUSES = ("Renewal In Progress", "Not Applicable")


class EmployeeDocuments(Document):
	def validate(self):
		self.validate_documents()
		self.set_statuses()

	def validate_documents(self):
		seen_types = set()
		for row in self.documents:
			settings = get_document_type_settings(row.document_type)

			if settings.has_expiry and not row.expiry_date:
				frappe.throw(
					_("Row #{0}: Expiry Date is mandatory for document type {1}").format(
						row.idx, frappe.bold(row.document_type)
					)
				)

			if row.issue_date and row.expiry_date and getdate(row.expiry_date) <= getdate(row.issue_date):
				frappe.throw(
					_("Row #{0}: Expiry Date must be after Issue Date").format(row.idx)
				)

			if settings.requires_attachment and not row.attachments:
				frappe.msgprint(
					_("Row #{0}: {1} usually requires an attachment").format(
						row.idx, frappe.bold(row.document_type)
					),
					indicator="orange",
					alert=True,
				)

			# renewals legitimately repeat a document type (old expired row + new row),
			# so only warn when two un-expired rows share the same type
			if not is_row_expired(row):
				if row.document_type in seen_types:
					frappe.msgprint(
						_("Row #{0}: {1} has more than one active (non-expired) entry").format(
							row.idx, frappe.bold(row.document_type)
						),
						indicator="orange",
						alert=True,
					)
				seen_types.add(row.document_type)

	def set_statuses(self):
		for row in self.documents:
			if row.status in MANUAL_STATUSES:
				continue
			settings = get_document_type_settings(row.document_type)
			row.status = get_expiry_status(row.expiry_date, settings.reminder_days)


def is_row_expired(row):
	if row.status == "Expired":
		return True
	if not row.expiry_date:
		return False
	return getdate(row.expiry_date) < getdate(nowdate())


def get_document_type_settings(document_type):
	if not document_type:
		return frappe._dict(has_expiry=0, reminder_days=30, requires_attachment=0)
	settings = frappe.get_cached_value(
		"Employee Document Type",
		document_type,
		["has_expiry", "reminder_days", "requires_attachment"],
		as_dict=True,
	) or frappe._dict()
	settings.reminder_days = settings.get("reminder_days") or 30
	return settings


def get_expiry_status(expiry_date, reminder_days=30):
	if not expiry_date:
		return "Active"
	expiry_date = getdate(expiry_date)
	today = getdate(nowdate())
	if expiry_date < today:
		return "Expired"
	if expiry_date <= add_days(today, reminder_days or 30):
		return "Expiring Soon"
	return "Active"


def update_document_statuses():
	"""Daily scheduler task: keep child row statuses in sync with expiry dates."""
	rows = frappe.db.sql(
		"""
		select child.name, child.status, child.expiry_date,
			coalesce(dt.reminder_days, 30) as reminder_days
		from `tabEmployee Documents Detail` child
		left join `tabEmployee Document Type` dt on dt.name = child.document_type
		where child.expiry_date is not null
			and ifnull(child.status, '') not in ({manual})
		""".format(manual=", ".join(["%s"] * len(MANUAL_STATUSES))),
		MANUAL_STATUSES,
		as_dict=True,
	)

	for row in rows:
		new_status = get_expiry_status(row.expiry_date, row.reminder_days)
		if new_status != row.status:
			frappe.db.set_value(
				"Employee Documents Detail", row.name, "status", new_status, update_modified=False
			)


def send_expiry_digest():
	"""Daily scheduler task: email HR a digest of expired / expiring documents."""
	recipients = get_hr_recipients()
	if not recipients:
		return

	rows = frappe.db.sql(
		"""
		select parent.employee, parent.employee_name, parent.company, parent.department,
			child.document_type, child.reference_no, child.expiry_date, child.status
		from `tabEmployee Documents Detail` child
		inner join `tabEmployee Documents` parent on parent.name = child.parent
		inner join `tabEmployee Document Type` dt on dt.name = child.document_type
		where dt.has_expiry = 1
			and child.expiry_date is not null
			and child.expiry_date <= date_add(curdate(), interval coalesce(dt.reminder_days, 30) day)
			and ifnull(child.status, '') != 'Not Applicable'
			and ifnull(parent.employee_status, 'Active') = 'Active'
			-- skip expired rows that have already been renewed
			-- (a newer un-expired row of the same type exists)
			and not (
				child.expiry_date < curdate()
				and exists (
					select 1 from `tabEmployee Documents Detail` renewed
					where renewed.parent = child.parent
						and renewed.document_type = child.document_type
						and renewed.name != child.name
						and renewed.expiry_date >= curdate()
				)
			)
		order by child.expiry_date asc, parent.employee_name asc
		""",
		as_dict=True,
	)

	if not rows:
		return

	frappe.sendmail(
		recipients=recipients,
		subject=_("Employee Documents Expiring — {0}").format(frappe.utils.formatdate(nowdate())),
		message=build_digest_html(rows),
	)


def get_hr_recipients():
	return frappe.db.sql_list(
		"""
		select distinct u.email
		from `tabUser` u
		inner join `tabHas Role` r on r.parent = u.name
		where r.role = 'HR Manager'
			and u.enabled = 1
			and u.user_type = 'System User'
			and u.email is not null
		"""
	)


def build_digest_html(rows):
	today = getdate(nowdate())
	expired = [r for r in rows if getdate(r.expiry_date) < today]
	expiring = [r for r in rows if getdate(r.expiry_date) >= today]

	def table(items):
		body = "".join(
			"""<tr>
				<td>{employee}</td><td>{employee_name}</td><td>{department}</td>
				<td>{document_type}</td><td>{reference_no}</td><td>{expiry_date}</td>
			</tr>""".format(
				employee=r.employee,
				employee_name=r.employee_name or "",
				department=r.department or "",
				document_type=r.document_type,
				reference_no=r.reference_no or "",
				expiry_date=frappe.utils.formatdate(r.expiry_date),
			)
			for r in items
		)
		return """<table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse;">
			<tr><th>{0}</th><th>{1}</th><th>{2}</th><th>{3}</th><th>{4}</th><th>{5}</th></tr>{6}
		</table>""".format(
			_("Employee"), _("Name"), _("Department"),
			_("Document"), _("Reference No"), _("Expiry Date"), body,
		)

	sections = []
	if expired:
		sections.append("<h3>{0} ({1})</h3>{2}".format(_("Expired"), len(expired), table(expired)))
	if expiring:
		sections.append(
			"<h3>{0} ({1})</h3>{2}".format(_("Expiring Soon"), len(expiring), table(expiring))
		)
	return "".join(sections)
