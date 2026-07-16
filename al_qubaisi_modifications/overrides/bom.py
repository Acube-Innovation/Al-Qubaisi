import frappe

from erpnext.manufacturing.doctype.bom.bom import BOM


class CustomBOM(BOM):
	def autoname(self):
		# Al-Qubaisi: name the BOM using the item name instead of the item code.
		# ignore amended documents while calculating current index

		name_source = self.item_name or self.item

		search_key = f"{self.doctype}-{name_source}%"
		existing_boms = frappe.get_all(
			"BOM", filters={"name": search_key, "amended_from": ["is", "not set"]}, pluck="name"
		)

		index = self.get_index_for_bom(existing_boms)

		prefix = self.doctype
		suffix = "%.3i" % index  # convert index to string (1 -> "001")
		bom_name = f"{prefix}-{name_source}-{suffix}"

		if len(bom_name) <= 140:
			name = bom_name
		else:
			# since max characters for name is 140, remove enough characters from the
			# item name to fit the prefix, suffix and the separators
			truncated_length = 140 - (len(prefix) + len(suffix) + 2)
			truncated_item_name = name_source[:truncated_length]
			# if a partial word is found after truncate, remove the extra characters
			truncated_item_name = truncated_item_name.rsplit(" ", 1)[0]
			name = f"{prefix}-{truncated_item_name}-{suffix}"

		if frappe.db.exists("BOM", name):
			existing_boms = frappe.get_all(
				"BOM", filters={"name": ("like", search_key), "amended_from": ["is", "not set"]}, pluck="name"
			)

			index = self.get_index_for_bom(existing_boms)
			suffix = "%.3i" % index
			name = f"{prefix}-{name_source}-{suffix}"

		self.name = name
