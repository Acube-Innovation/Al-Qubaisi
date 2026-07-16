# Copyright (c) 2026, Acube Innoivations Pvt Limited and contributors
# For license information, please see license.txt

"""One-time patch: add a "Branch Operations" card to the standard Accounting workspace.

Runs once during `bench migrate` (Frappe Cloud runs this on deploy) and is recorded in
the Patch Log, so it does not re-run. The card groups the branch-facing objects:
Delivery Note (branch purchases), Branch Sales (custom doctype) and the Branch
Transaction report.
"""

import json

import frappe

WORKSPACE = "Accounting"
CARD_LABEL = "Branch Operations"

LINKS = [
	{"label": "Delivery Note", "link_type": "DocType", "link_to": "Delivery Note", "is_query_report": 0},
	{"label": "Branch Sales", "link_type": "DocType", "link_to": "Branch Sales", "is_query_report": 0},
	{
		"label": "Branch Transaction",
		"link_type": "Report",
		"link_to": "Branch Transaction",
		"is_query_report": 1,
		"report_ref_doctype": "Branch Sales",
	},
]


def execute():
	if not frappe.db.exists("Workspace", WORKSPACE):
		return

	doc = frappe.get_doc("Workspace", WORKSPACE)

	# Idempotent: drop any prior copy of the card (links + content block) first.
	_remove_existing_card(doc)

	# Append the Card Break followed by its Link rows (kept contiguous).
	idx = (doc.links[-1].idx if doc.links else 0) + 1
	doc.append(
		"links",
		{"type": "Card Break", "label": CARD_LABEL, "link_count": len(LINKS), "idx": idx},
	)
	for link in LINKS:
		idx += 1
		row = {
			"type": "Link",
			"label": link["label"],
			"link_type": link["link_type"],
			"link_to": link["link_to"],
			"is_query_report": link.get("is_query_report", 0),
			"idx": idx,
		}
		if link.get("report_ref_doctype"):
			row["report_ref_doctype"] = link["report_ref_doctype"]
		doc.append("links", row)

	# Mirror the card block into the content JSON so it renders.
	content = json.loads(doc.content or "[]")
	if not _content_has_card(content):
		content.append(
			{
				"id": frappe.generate_hash(length=10),
				"type": "card",
				"data": {"card_name": CARD_LABEL, "col": 4},
			}
		)
	doc.content = json.dumps(content)

	doc.save(ignore_permissions=True)
	frappe.db.commit()


def _remove_existing_card(doc):
	"""Remove the Card Break with our label, its Link rows, and the content block."""
	kept = []
	skipping = False
	for link in doc.links:
		if link.type == "Card Break":
			skipping = link.label == CARD_LABEL
			if skipping:
				continue
		if skipping and link.type == "Link":
			continue
		kept.append(link)
	doc.links = kept

	content = [
		block
		for block in json.loads(doc.content or "[]")
		if not (block.get("type") == "card" and block.get("data", {}).get("card_name") == CARD_LABEL)
	]
	doc.content = json.dumps(content)


def _content_has_card(content):
	return any(
		block.get("type") == "card" and block.get("data", {}).get("card_name") == CARD_LABEL
		for block in content
	)
