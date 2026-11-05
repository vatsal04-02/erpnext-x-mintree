# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Audit trail: sensitive DocTypes must record who changed what and when.

Frappe's ``track_changes`` writes a Version record on every change, including submit and cancel.
Almost every document that matters already has it on; this module lists the ones that matter to
Mintree and switches it on, through a Property Setter (the supported customisation, so ERPNext's
own files are untouched), for the few that lack it. A test fails if a DocType on the list stops
tracking.
"""

import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

# QC decisions, approvals, stock and financial postings, production and the people who may approve.
SENSITIVE_DOCTYPES = (
	"QC Release", "QC Deviation", "QC Retest", "Quality Inspection", "Batch",
	"Purchase Approval Limit", "Purchase Order", "Purchase Receipt", "Purchase Invoice", "Material Request",
	"Stock Entry", "Stock Reconciliation", "Stock Reservation Entry", "Delivery Note", "Pick List",
	"Sales Order", "Sales Invoice", "Payment Entry", "Journal Entry",
	"Work Order", "BOM", "Formulation", "Asset Repair", "Factory Ops Settings",
)  # fmt: skip


def untracked() -> list[str]:
	return [d for d in SENSITIVE_DOCTYPES if frappe.db.exists("DocType", d) and not frappe.get_meta(d).track_changes]


def ensure_audit_trails() -> list[str]:
	"""Switch change tracking on where it is off. Returns the DocTypes changed."""
	changed = []
	for doctype in untracked():
		make_property_setter(doctype, None, "track_changes", 1, "Check", for_doctype=True)
		frappe.clear_cache(doctype=doctype)
		changed.append(doctype)
	return changed
