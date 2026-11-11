# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

from frappe.model.document import Document
from frappe.utils import getdate


def log_name(store, posting_date):
	"""One log per store per day, so a retry or a re-post updates the same record."""
	return f"{store}-{getdate(posting_date)}"


class LithosSyncLog(Document):
	def autoname(self):
		self.name = log_name(self.store, self.posting_date)
