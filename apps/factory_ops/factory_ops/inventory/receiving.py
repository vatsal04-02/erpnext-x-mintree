"""Goods receipt (Purchase Receipt) for QC-controlled items.

- QC items land in the item group's default warehouse (a QC Hold warehouse) automatically, and
  rejected quantity defaults to the company's Rejected warehouse.
- Supplier batch number, manufacturing and expiry dates are mandatory for QC items with batch
  and expiry tracking, and the supplier's COA where the item requires it.
- On submit, one Batch per accepted row is created from those supplier details before ERPNext
  builds the batch bundle, so the batch carries the supplier's dates from the start.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from factory_ops.constants import WH_TYPE_REJECTED
from factory_ops.inventory.stock_queries import is_qc_hold


class ReceiptDataError(frappe.ValidationError):
	pass


def _item(item_code):
	return frappe.get_cached_value(
		"Item",
		item_code,
		["fo_qc_required", "fo_coa_required", "has_batch_no", "has_expiry_date", "item_group"],
		as_dict=True,
	)


def _hold_default(item_code: str, company: str) -> str | None:
	"""Default warehouse from the item, else its item group, if it is a QC Hold warehouse."""
	group = frappe.get_cached_value("Item", item_code, "item_group")
	for parent, parenttype in ((item_code, "Item"), (group, "Item Group")):
		wh = frappe.db.get_value(
			"Item Default",
			{"parent": parent, "parenttype": parenttype, "company": company},
			"default_warehouse",
		)
		if wh and is_qc_hold(wh):
			return wh
	return None


def set_receipt_defaults(doc, method=None):
	if cint(doc.get("is_return")):
		return
	rejected = frappe.db.get_value(
		"Warehouse", {"company": doc.company, "warehouse_type": WH_TYPE_REJECTED, "is_group": 0}, "name"
	)
	for row in doc.items:
		item = _item(row.item_code)
		if not item or not item.fo_qc_required:
			continue
		if not row.warehouse:
			row.warehouse = _hold_default(row.item_code, doc.company)
		if flt(row.get("rejected_qty")) and not row.get("rejected_warehouse") and rejected:
			row.rejected_warehouse = rejected


def validate_receipt_data(doc, method=None):
	"""Supplier batch, dates and COA. Dates are checked on save; completeness on submit."""
	if cint(doc.get("is_return")):
		return
	submitting = cint(doc.docstatus) == 1
	for row in doc.items:
		item = _item(row.item_code)
		if not item or not item.fo_qc_required or not item.has_batch_no:
			continue
		if row.fo_mfg_date and getdate(row.fo_mfg_date) > getdate(nowdate()):
			frappe.throw(
				_("Row #{0}: manufacturing date is in the future.").format(row.idx), ReceiptDataError
			)
		if row.fo_mfg_date and row.fo_expiry_date and getdate(row.fo_expiry_date) <= getdate(row.fo_mfg_date):
			frappe.throw(
				_("Row #{0}: expiry date must be after the manufacturing date.").format(row.idx),
				ReceiptDataError,
			)
		if not submitting:
			continue
		missing = []
		if not (row.fo_supplier_batch_no or "").strip():
			missing.append(_("Supplier Batch No"))
		if not row.fo_mfg_date:
			missing.append(_("Manufacturing Date"))
		if item.has_expiry_date and not row.fo_expiry_date:
			missing.append(_("Expiry Date"))
		if item.fo_coa_required and not row.fo_coa:
			missing.append(_("Certificate of Analysis"))
		if missing:
			frappe.throw(
				_("Row #{0} ({1}): enter {2} before submitting.").format(
					row.idx, row.item_code, ", ".join(missing)
				),
				ReceiptDataError,
			)


def create_supplier_batches(doc, method=None):
	"""before_submit: create the batch from supplier details and point the row at it."""
	if cint(doc.get("is_return")):
		return
	for row in doc.items:
		item = _item(row.item_code)
		if not item or not item.fo_qc_required or not item.has_batch_no:
			continue
		if row.get("batch_no") or row.get("serial_and_batch_bundle"):
			continue
		batch = frappe.get_doc(
			{
				"doctype": "Batch",
				"item": row.item_code,
				"supplier": doc.supplier,
				"fo_supplier_batch_no": row.fo_supplier_batch_no,
				"manufacturing_date": row.fo_mfg_date,
				"expiry_date": row.fo_expiry_date,
				"fo_coa": row.fo_coa,
				"reference_doctype": doc.doctype,
				"reference_name": doc.name,
			}
		).insert()
		row.batch_no = batch.name
		row.use_serial_batch_fields = 1
