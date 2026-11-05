"""Server-side QC guards (design doc section 3.3).

Every ERPNext route that can move, consume, reserve, pick or plan stock is checked here,
in ``validate`` and again in ``before_submit``, so the rules hold for Desk, the REST API,
data import and server scripts alike. UI restrictions are never relied on.

G1  Nothing leaves a QC Hold warehouse except through a QC Release.
G2  Nothing leaves a Rejected warehouse except scrap (with a reason), return to the
    supplier, or a QC Retest ("Send for Re-test", QA Manager) back into QC Hold.
G3  QC Hold and Rejected stock cannot be reserved, picked or planned for use.
G6  Expired stock, and stock below the minimum remaining shelf life, cannot be dispatched.
+   Items flagged "QC Release Required" must be received or produced into a QC Hold warehouse,
    and sales returns of them must come back into QC Hold.
G4/G5 (segregation and roles) live on the QC Release itself.
"""

import frappe
from frappe import _
from frappe.utils import cint, date_diff, getdate

from factory_ops.inventory.stock_queries import (
	get_row_batches,
	is_qc_hold,
	is_rejected,
	is_restricted,
	usable_group,
)

RELEASE_FLAG = "fo_qc_release_in_progress"
RETEST_FLAG = "fo_qc_retest_in_progress"


class QCGuardError(frappe.ValidationError):
	pass


def _throw(msg: str):
	frappe.throw(msg, exc=QCGuardError, title=_("Blocked by QC control"))


def _qc_required(item_code: str | None) -> bool:
	return bool(item_code) and bool(frappe.get_cached_value("Item", item_code, "fo_qc_required"))


def _is_release_in_progress(doc) -> bool:
	"""True only while QC Release server code is submitting its own Stock Entry.

	``frappe.flags`` is process-local and cannot be set through the API, so a client
	setting ``fo_qc_release`` on a Stock Entry does not unlock anything.
	"""
	active = frappe.flags.get(RELEASE_FLAG)
	return bool(active) and doc.get("fo_qc_release") == active


def _is_retest_in_progress(doc) -> bool:
	"""True only while QC Retest server code is submitting its own Stock Entry."""
	active = frappe.flags.get(RETEST_FLAG)
	return bool(active) and doc.get("fo_qc_retest") == active


def _submitting(doc) -> bool:
	return cint(doc.docstatus) == 1


# ---------------------------------------------------------------------------
# Stock Entry
# ---------------------------------------------------------------------------
INWARD_PURPOSES = ("Material Receipt", "Manufacture", "Repack", "Receive from Customer")


def validate_stock_entry(doc, method=None):
	releasing = _is_release_in_progress(doc)

	if doc.get("fo_qc_release") and _submitting(doc) and not releasing:
		_throw(
			_(
				"Stock Entry {0} belongs to QC Release {1} and can only be submitted from that QC Release."
			).format(doc.name, doc.fo_qc_release)
		)

	for row in doc.items:
		src, tgt = row.s_warehouse, row.t_warehouse

		# G1: QC Hold exit
		if src and is_qc_hold(src):
			if releasing:
				pass
			elif doc.get("fo_qc_release") and not _submitting(doc):
				pass  # draft prepared by a QC Release; submission is checked above
			else:
				_throw(
					_(
						"Row #{0}: {1} is in QC Hold warehouse {2}. Stock can only leave QC Hold through a submitted QC Release."
					).format(row.idx, row.item_code, src)
				)

		# G2: Rejected exit
		if src and is_rejected(src) and not releasing:
			_validate_rejected_exit(doc, row)

		# Inward: QC items must land in QC Hold
		if tgt and _qc_required(row.item_code) and not is_qc_hold(tgt) and not is_rejected(tgt):
			is_output = doc.purpose in ("Manufacture", "Repack") and (
				row.get("is_finished_item") or row.get("is_secondary_item") or doc.purpose == "Repack"
			)
			if (doc.purpose in INWARD_PURPOSES and not src) or is_output:
				_throw(
					_(
						"Row #{0}: {1} requires QC release. Receive or produce it into a QC Hold warehouse, not {2}."
					).format(row.idx, row.item_code, tgt)
				)


def _validate_rejected_exit(doc, row):
	if _is_retest_in_progress(doc) and row.t_warehouse and is_qc_hold(row.t_warehouse):
		return
	if doc.purpose == "Material Issue":
		if not (doc.get("fo_disposal_reason") or "").strip():
			_throw(
				_(
					"Row #{0}: enter a Disposal Reason to issue (scrap) stock from Rejected warehouse {1}."
				).format(row.idx, row.s_warehouse)
			)
		return
	if doc.get("fo_qc_retest") and not _submitting(doc):
		return  # draft prepared by a QC Retest; submission is checked by the flag above
	_throw(
		_(
			"Row #{0}: stock in Rejected warehouse {1} can only be scrapped (Material Issue with a reason), returned to the supplier, or sent for re-test by a QA Manager (QC Retest)."
		).format(row.idx, row.s_warehouse)
	)


def before_cancel_stock_entry(doc, method=None):
	if doc.get("fo_qc_retest") and not _is_retest_in_progress(doc):
		_throw(
			_("Stock Entry {0} was created by QC Retest {1}. Cancel the QC Retest instead.").format(
				doc.name, doc.fo_qc_retest
			)
		)
	if doc.get("fo_qc_release") and not _is_release_in_progress(doc):
		_throw(
			_("Stock Entry {0} was created by QC Release {1}. Cancel the QC Release instead.").format(
				doc.name, doc.fo_qc_release
			)
		)


# ---------------------------------------------------------------------------
# Purchase Receipt / Purchase Invoice (update stock)
# ---------------------------------------------------------------------------
def validate_purchase_receipt(doc, method=None):
	if doc.doctype == "Purchase Invoice" and not cint(doc.get("update_stock")):
		return
	if cint(doc.get("is_return")):
		return  # returns to the supplier may leave from any warehouse, including Rejected
	if doc.doctype == "Purchase Invoice":
		# QC items must come in through a Purchase Receipt, where supplier batch, dates and
		# COA are captured. A stock-updating invoice would bypass that.
		for row in doc.items:
			if _qc_required(row.item_code):
				_throw(
					_(
						"Row #{0}: {1} requires QC release; receive it with a Purchase Receipt, then bill it with a Purchase Invoice without stock update."
					).format(row.idx, row.item_code)
				)
		return
	for row in doc.items:
		if not _qc_required(row.item_code):
			continue
		if row.get("qty") and not is_qc_hold(row.warehouse):
			_throw(
				_(
					"Row #{0}: {1} requires QC release. Set the accepted warehouse to a QC Hold warehouse (it is {2})."
				).format(row.idx, row.item_code, row.warehouse or "-")
			)
		if row.get("rejected_qty") and not is_rejected(row.get("rejected_warehouse")):
			_throw(
				_("Row #{0}: rejected quantity of {1} must go to a Rejected warehouse.").format(
					row.idx, row.item_code
				)
			)


# ---------------------------------------------------------------------------
# Delivery Note / Sales Invoice (update stock)
# ---------------------------------------------------------------------------
def validate_outward_sales_doc(doc, method=None):
	if doc.doctype == "Sales Invoice" and not cint(doc.get("update_stock")):
		return
	if cint(doc.get("is_return")):
		for row in doc.items:
			if _qc_required(row.item_code) and row.warehouse and not is_qc_hold(row.warehouse):
				_throw(
					_("Row #{0}: returned {1} must come back into a QC Hold warehouse, not {2}.").format(
						row.idx, row.item_code, row.warehouse
					)
				)
		return
	for row in doc.items:
		if is_restricted(row.warehouse):
			_throw(
				_("Row #{0}: {1} cannot be dispatched from {2} (QC Hold or Rejected stock).").format(
					row.idx, row.item_code, row.warehouse
				)
			)
	validate_shelf_life(doc)


def validate_shelf_life(doc, method=None):
	"""G6: no expired batch, and at least the configured remaining shelf life."""
	if cint(doc.get("is_return")):
		return
	if doc.doctype == "Sales Invoice" and not cint(doc.get("update_stock")):
		return
	from factory_ops.inventory.batch_eligibility import check_batch, min_shelf_life_days

	min_days = min_shelf_life_days()
	posting = getdate(doc.get("posting_date"))  # Pick Lists have no posting date: today
	rows = doc.get("items") if doc.doctype != "Pick List" else doc.get("locations")
	submitted_batches: dict[str, list[str]] = {}
	if cint(doc.docstatus) == 1 and doc.doctype != "Pick List":
		# After submit the bundles exist, but their names are not on the in-memory rows when
		# ERPNext auto-picked the batches (FEFO) during submit: read them from the database.
		for r in frappe.db.sql(
			"""select sbb.voucher_detail_no, sbe.batch_no
			from `tabSerial and Batch Entry` sbe
			inner join `tabSerial and Batch Bundle` sbb on sbb.name = sbe.parent
			where sbb.voucher_type = %s and sbb.voucher_no = %s and sbe.batch_no is not null""",
			(doc.doctype, doc.name),
			as_dict=True,
		):
			submitted_batches.setdefault(r.voucher_detail_no, []).append(r.batch_no)
	for row in rows or []:
		for batch in dict.fromkeys(get_row_batches(row) + submitted_batches.get(row.name, [])):
			expiry = frappe.db.get_value("Batch", batch, "expiry_date")
			result = check_batch(expiry, posting, min_days)
			remaining = result.remaining_days
			if result.reason == "expired":
				_throw(_("Row #{0}: batch {1} expired on {2}.").format(row.idx, batch, expiry))
			if result.reason == "short_dated":
				_throw(
					_(
						"Row #{0}: batch {1} has {2} days of shelf life left; at least {3} days are required for dispatch."
					).format(row.idx, batch, remaining, min_days)
				)


# ---------------------------------------------------------------------------
# Pick List, Stock Reservation Entry, Work Order, Material Request, Subcontracting
# ---------------------------------------------------------------------------
def default_pick_list_warehouse(doc, method=None):
	"""Without a parent warehouse ERPNext suggests locations from every warehouse, QC Hold
	included. Default the search to the company's USABLE group so suggestions are pickable."""
	if not doc.get("parent_warehouse") and doc.get("company"):
		doc.parent_warehouse = usable_group(doc.company)


def validate_pick_list(doc, method=None):
	# ERPNext recomputes suggested locations in before_save unless the list is picked manually,
	# so at the validate stage only manually picked locations are final. before_save and
	# before_submit always check.
	if method == "validate" and not cint(doc.get("pick_manually")):
		return
	for row in doc.get("locations") or []:
		if is_restricted(row.warehouse):
			_throw(
				_("Row #{0}: cannot pick {1} from {2} (QC Hold or Rejected stock).").format(
					row.idx, row.item_code, row.warehouse
				)
			)
	if doc.get("purpose") == "Delivery":
		validate_shelf_life(doc)


def validate_sales_order(doc, method=None):
	if is_restricted(doc.get("set_warehouse")):
		_throw(
			_("Sales Orders cannot be fulfilled from {0} (QC Hold or Rejected).").format(doc.set_warehouse)
		)
	for row in doc.items:
		if is_restricted(row.get("warehouse")):
			_throw(
				_("Row #{0}: {1} cannot be sold from {2} (QC Hold or Rejected stock).").format(
					row.idx, row.item_code, row.warehouse
				)
			)


def validate_stock_reservation(doc, method=None):
	if is_restricted(doc.warehouse):
		_throw(
			_("Stock in {0} (QC Hold or Rejected) cannot be reserved for {1} {2}.").format(
				doc.warehouse, doc.voucher_type, doc.voucher_no
			)
		)


def validate_work_order(doc, method=None):
	for field in ("source_warehouse", "wip_warehouse"):
		if is_restricted(doc.get(field)):
			_throw(
				_("{0} {1} is a QC Hold or Rejected warehouse; production cannot draw from it.").format(
					doc.meta.get_label(field), doc.get(field)
				)
			)
	for row in doc.get("required_items") or []:
		if is_restricted(row.get("source_warehouse")):
			_throw(
				_("Row #{0}: {1} cannot be consumed from {2} (QC Hold or Rejected stock).").format(
					row.idx, row.item_code, row.source_warehouse
				)
			)
	if _qc_required(doc.production_item) and doc.get("fg_warehouse") and not is_qc_hold(doc.fg_warehouse):
		_throw(
			_("{0} requires QC release; set the Target Warehouse to a QC Hold warehouse (it is {1}).").format(
				doc.production_item, doc.fg_warehouse
			)
		)


def validate_material_request(doc, method=None):
	if doc.material_request_type not in ("Material Transfer", "Material Issue", "Customer Provided"):
		return
	if is_restricted(doc.get("set_from_warehouse")):
		_throw(
			_("Material cannot be requested from {0} (QC Hold or Rejected).").format(doc.set_from_warehouse)
		)
	for row in doc.items:
		if is_restricted(row.get("from_warehouse")):
			_throw(
				_("Row #{0}: material cannot be requested from {1} (QC Hold or Rejected).").format(
					row.idx, row.from_warehouse
				)
			)


def validate_subcontracting_order(doc, method=None):
	if is_restricted(doc.get("set_reserve_warehouse")):
		_throw(
			_("Supplied items cannot be reserved in {0} (QC Hold or Rejected).").format(
				doc.set_reserve_warehouse
			)
		)
	for row in doc.get("supplied_items") or []:
		if is_restricted(row.get("reserve_warehouse")):
			_throw(
				_("Row #{0}: {1} cannot be reserved in {2} (QC Hold or Rejected).").format(
					row.idx, row.rm_item_code, row.reserve_warehouse
				)
			)


def validate_stock_reconciliation(doc, method=None):
	"""A held or rejected batch must not appear in a usable warehouse through a count adjustment."""
	for row in doc.items:
		if not row.get("batch_no") and not row.get("serial_and_batch_bundle"):
			continue
		if is_restricted(row.warehouse):
			continue
		for batch in get_row_batches(row):
			status = frappe.db.get_value("Batch", batch, "fo_qc_status")
			if status in ("Hold", "Rejected"):
				_throw(
					_(
						"Row #{0}: batch {1} is {2} in QC; it cannot be counted into usable warehouse {3}."
					).format(row.idx, batch, status, row.warehouse)
				)


# ---------------------------------------------------------------------------
# Batch QC status bookkeeping
# ---------------------------------------------------------------------------
def on_inward_submit(doc, method=None):
	from factory_ops.quality.batch_status import recompute_for_doc

	recompute_for_doc(doc)


def on_inward_cancel(doc, method=None):
	from factory_ops.quality.batch_status import recompute_for_doc

	recompute_for_doc(doc)
