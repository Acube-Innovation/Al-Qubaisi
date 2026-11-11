import frappe

# A Single only stores the values it was saved with, so fields added later read as
# blank (or 0) until set. These are the defaults the fields declare.
DEFAULTS = {
	"default_sync_policy": "Hold",
	"hold_without_bom": 0,
	"look_back_days": 3,
	"max_retries": 3,
}


def execute():
	# get_single_value casts a missing Int to 0, so ask tabSingles what is really stored.
	stored = set(
		frappe.db.sql_list("select field from `tabSingles` where doctype = %s", "LithosPOS Settings")
	)
	for fieldname, value in DEFAULTS.items():
		if fieldname not in stored:
			frappe.db.set_single_value("LithosPOS Settings", fieldname, value)
