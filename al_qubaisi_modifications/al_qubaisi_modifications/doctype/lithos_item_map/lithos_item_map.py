# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from al_qubaisi_modifications.lithos import collapse, normalise

UNMAPPED = "Unmapped"
MANUAL = "Manual"
IGNORED = "Ignored"


def scope_key(store, match_key):
	"""One row per Lithos name per scope; a blank store is the all-stores scope."""
	return f"{store or '*'}::{match_key}"


class LithosItemMap(Document):
	@property
	def default_bom(self):
		# Read live rather than stored: a BOM is usually submitted after the map row exists.
		return frappe.db.get_value("Item", self.item_code, "default_bom") if self.item_code else None

	def validate(self):
		self.lithos_item_name = collapse(self.lithos_item_name)
		self.barcode = (self.barcode or "").strip()
		self.match_key = normalise(self.lithos_item_name)
		self.scope_key = scope_key(self.store, self.match_key)
		self.set_status()
		self.validate_duplicate()

	def set_status(self):
		# The sync and the workbook import set their own status; anyone else editing the
		# item is making a manual decision. Choosing Ignored is one too, with or without
		# an item.
		ignoring = self.status == IGNORED and (self.is_new() or self.has_value_changed("status"))
		if not (self.flags.keep_status or ignoring) and (
			self.is_new() or self.has_value_changed("item_code")
		):
			self.status = MANUAL if self.item_code else UNMAPPED

		if not self.item_code and self.status != IGNORED:
			self.status = UNMAPPED

		if self.status in (MANUAL, IGNORED) and (
			self.has_value_changed("item_code") or self.has_value_changed("status")
		):
			self.mapped_by = frappe.session.user
			self.mapped_on = now_datetime()

	def validate_duplicate(self):
		existing = frappe.db.get_value(
			"Lithos Item Map", {"scope_key": self.scope_key, "name": ("!=", self.name)}
		)
		if existing:
			frappe.throw(
				_("{0} is already mapped for {1} in {2}.").format(
					frappe.bold(self.lithos_item_name),
					_("store {0}").format(self.store) if self.store else _("all stores"),
					frappe.get_desk_link("Lithos Item Map", existing),
				)
			)
