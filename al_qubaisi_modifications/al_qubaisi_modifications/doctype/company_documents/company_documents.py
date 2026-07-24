# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, getdate, nowdate

# Statuses set manually by the user; the system never overwrites these
MANUAL_STATUSES = ("Renewal In Progress", "Not Applicable")

# Used when a Company Documents record has no Notify Users configured
FALLBACK_ROLE = "System Manager"


class CompanyDocuments(Document):
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
		"Company Document Type",
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
		from `tabCompany Documents Detail` child
		left join `tabCompany Document Type` dt on dt.name = child.document_type
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
				"Company Documents Detail", row.name, "status", new_status, update_modified=False
			)


def send_expiry_digest():
	"""Daily scheduler task: email a digest of expired / expiring company documents.

	Recipients are configured per record via the Notify Users table; records that leave
	it empty fall back to every enabled System Manager. Records sharing the same
	recipient list are bundled into a single email.
	"""
	rows = frappe.db.sql(
		"""
		select parent.name as company_documents, parent.company,
			child.document_type, child.reference_no, child.issuing_authority,
			child.expiry_date, child.status
		from `tabCompany Documents Detail` child
		inner join `tabCompany Documents` parent on parent.name = child.parent
		inner join `tabCompany Document Type` dt on dt.name = child.document_type
		where dt.has_expiry = 1
			and child.expiry_date is not null
			and child.expiry_date <= date_add(curdate(), interval coalesce(dt.reminder_days, 30) day)
			and ifnull(child.status, '') != 'Not Applicable'
			-- skip expired rows that have already been renewed
			-- (a newer un-expired row of the same type exists)
			and not (
				child.expiry_date < curdate()
				and exists (
					select 1 from `tabCompany Documents Detail` renewed
					where renewed.parent = child.parent
						and renewed.document_type = child.document_type
						and renewed.name != child.name
						and renewed.expiry_date >= curdate()
				)
			)
		order by child.expiry_date asc, parent.company asc
		""",
		as_dict=True,
	)

	if not rows:
		return

	recipients_map = get_recipients_map({row.company_documents for row in rows})

	# bundle records that notify exactly the same people into one email
	batches = {}
	for row in rows:
		recipients = recipients_map.get(row.company_documents)
		if not recipients:
			continue
		batches.setdefault(recipients, []).append(row)

	for recipients, batch in batches.items():
		frappe.sendmail(
			recipients=list(recipients),
			subject=_("Company Documents Expiring — {0}").format(frappe.utils.formatdate(nowdate())),
			message=build_digest_html(batch),
		)


def get_recipients_map(company_document_names):
	"""Return {company documents name: tuple of emails}, falling back to System Managers."""
	rows = frappe.db.sql(
		"""
		select n.parent, u.email
		from `tabCompany Documents Notify User` n
		inner join `tabUser` u on u.name = n.user
		where n.parenttype = 'Company Documents'
			and n.parent in %(names)s
			and u.enabled = 1
			and ifnull(u.email, '') != ''
		""",
		{"names": list(company_document_names)},
		as_dict=True,
	)

	configured = {}
	for row in rows:
		configured.setdefault(row.parent, []).append(row.email)

	fallback = None
	recipients_map = {}
	for name in company_document_names:
		emails = configured.get(name)
		if not emails:
			if fallback is None:
				fallback = get_fallback_recipients()
			emails = fallback
		recipients_map[name] = tuple(sorted(set(emails)))
	return recipients_map


def get_fallback_recipients():
	return frappe.db.sql_list(
		"""
		select distinct u.email
		from `tabUser` u
		inner join `tabHas Role` r on r.parent = u.name
		where r.role = %s
			and u.enabled = 1
			and u.user_type = 'System User'
			and u.email is not null
		""",
		(FALLBACK_ROLE,),
	)


def build_digest_html(rows):
	today = getdate(nowdate())
	expired = [r for r in rows if getdate(r.expiry_date) < today]
	expiring = [r for r in rows if getdate(r.expiry_date) >= today]

	def table(items):
		body = "".join(
			"""<tr>
				<td>{company}</td><td>{document_type}</td><td>{reference_no}</td>
				<td>{issuing_authority}</td><td>{expiry_date}</td>
			</tr>""".format(
				company=r.company or "",
				document_type=r.document_type,
				reference_no=r.reference_no or "",
				issuing_authority=r.issuing_authority or "",
				expiry_date=frappe.utils.formatdate(r.expiry_date),
			)
			for r in items
		)
		return """<table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse;">
			<tr><th>{0}</th><th>{1}</th><th>{2}</th><th>{3}</th><th>{4}</th></tr>{5}
		</table>""".format(
			_("Company"), _("Document"), _("Reference No"),
			_("Issuing Authority"), _("Expiry Date"), body,
		)

	sections = []
	if expired:
		sections.append("<h3>{0} ({1})</h3>{2}".format(_("Expired"), len(expired), table(expired)))
	if expiring:
		sections.append(
			"<h3>{0} ({1})</h3>{2}".format(_("Expiring Soon"), len(expiring), table(expiring))
		)
	return "".join(sections)
