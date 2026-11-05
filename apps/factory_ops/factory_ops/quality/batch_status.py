"""Derived batch QC status (Batch.fo_qc_status).

The warehouse a batch sits in is the record of truth; this field is a convenience for lists,
labels and the stock reconciliation guard. It is recomputed whenever a QC Release or an
inward document is submitted or cancelled, and checked nightly.
"""

import frappe

from factory_ops.constants import (
	BATCH_HOLD,
	BATCH_PARTLY_RELEASED,
	BATCH_REJECTED,
	BATCH_RELEASED,
	DECISION_REJECT,
	RELEASE_DECISIONS,
)
from factory_ops.inventory.stock_queries import batch_qty_in_qc_hold


def compute_status(batch_no: str) -> str:
	hold_qty = batch_qty_in_qc_hold(batch_no)
	decisions = []
	if frappe.db.exists("DocType", "QC Release"):
		decisions = frappe.get_all(
			"QC Release", filters={"batch_no": batch_no, "docstatus": 1}, pluck="decision"
		)
	released = any(d in RELEASE_DECISIONS for d in decisions)
	rejected = DECISION_REJECT in decisions
	if hold_qty > 0:
		return BATCH_PARTLY_RELEASED if released else BATCH_HOLD
	if released:
		return BATCH_RELEASED
	if rejected:
		return BATCH_REJECTED
	return ""


def recompute(batch_no: str) -> str:
	status = compute_status(batch_no)
	if frappe.db.get_value("Batch", batch_no, "fo_qc_status") != status:
		frappe.db.set_value("Batch", batch_no, "fo_qc_status", status, update_modified=False)
	return status


def batches_for_voucher(voucher_type: str, voucher_no: str) -> list[str]:
	"""Batches moved by a voucher, read from the database (bundle names are assigned during
	submit and are not reliably present on the in-memory document)."""
	return frappe.db.sql_list(
		"""select distinct sbe.batch_no
		from `tabSerial and Batch Entry` sbe
		inner join `tabSerial and Batch Bundle` sbb on sbb.name = sbe.parent
		where sbb.voucher_type = %s and sbb.voucher_no = %s and sbe.batch_no is not null""",
		(voucher_type, voucher_no),
	)


def recompute_for_doc(doc) -> None:
	batches = batches_for_voucher(doc.doctype, doc.name)
	for row in doc.get("items") or []:
		if row.get("batch_no"):
			batches.append(row.batch_no)
	for batch in dict.fromkeys(batches):
		recompute(batch)


def nightly_consistency_check() -> int:
	"""Recompute every batch that has a QC status or QC Hold stock. Returns the number corrected."""
	candidates = set(frappe.get_all("Batch", filters={"fo_qc_status": ("is", "set")}, pluck="name"))
	candidates.update(
		frappe.db.sql_list(
			"""select distinct sbe.batch_no from `tabSerial and Batch Entry` sbe
			inner join `tabWarehouse` w on w.name = sbe.warehouse
			where w.warehouse_type = 'QC Hold' and sbe.is_cancelled = 0 and sbe.batch_no is not null"""
		)
	)
	corrected = 0
	for batch in candidates:
		before = frappe.db.get_value("Batch", batch, "fo_qc_status") or ""
		if recompute(batch) != before:
			corrected += 1
	if corrected:
		frappe.log_error(
			title="QC batch status corrected",
			message=f"Nightly check corrected {corrected} batch QC status value(s).",
		)
	return corrected
