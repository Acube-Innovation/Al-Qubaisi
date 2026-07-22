# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class LithosPOSSettings(Document):
	def validate(self):
		# Trailing slashes would double up when the store id and endpoint are appended.
		self.base_url = (self.base_url or "").strip().rstrip("/")
