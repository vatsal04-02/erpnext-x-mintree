"""Item rules driven by the item group (RM, PM, BULK, FG)."""

import frappe


def apply_item_group_controls(doc, method=None):
	"""Stock items in a QC-controlled group are batch-tracked and need QC release."""
	if not doc.item_group or not doc.is_stock_item:
		return
	qc, coa = frappe.get_cached_value("Item Group", doc.item_group, ["fo_qc_required", "fo_coa_required"])
	if qc:
		doc.fo_qc_required = 1
		doc.has_batch_no = 1
		doc.create_new_batch = 1  # production output gets a batch automatically
	if coa:
		doc.fo_coa_required = 1
