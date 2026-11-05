"""Sales and dispatch controls on top of standard ERPNext selling (Milestone 5).

The flow is ERPNext's own: Sales Order → Stock Reservation Entry → Pick List (FEFO) → Delivery
Note (+ Packing Slip) → Sales Invoice → Payment Entry. Credit limits, FEFO batch selection and the
refusal to update stock twice from an invoice linked to a Delivery Note are ERPNext behaviour.
factory_ops adds only what ERPNext does not have (DECISIONS D-026 to D-030):

- **Sales channel** on the Sales Order: B2B, or a manually entered / imported D2C channel. A D2C
  order needs its external order number, and the same number cannot be entered twice per channel
  (duplicate imports are refused).
- **Reservation of sellable stock only**: a Stock Reservation Entry for a Sales Order may reserve
  only stock that is available to sell (released, in a Usable warehouse, not expired, enough shelf
  life left, not already reserved). Reserved batches are checked the same way.
- **One stock path**: stock leaves only through a Delivery Note. A Sales Invoice may not update
  stock unless Factory Ops Settings allows it (off by default); returns are unaffected.
- **Dispatch details**: when Factory Ops Settings requires them, a Delivery Note cannot be
  submitted without the transporter and the LR / AWB number.

QC Hold / Rejected warehouses and expired or short-dated batches are already refused on Sales
Order, Pick List, Delivery Note and Sales Invoice by ``inventory/guards.py`` (G1-G3, G6).
"""

import frappe
from frappe import _
from frappe.utils import cint, date_diff, flt, getdate, nowdate

from factory_ops.constants import D2C_CHANNELS
from factory_ops.inventory.batch_eligibility import (
	allocate,
	available_to_sell,
	check_batch,
	check_batch_no,
	min_shelf_life_days,
)
from factory_ops.inventory.guards import QCGuardError


class DuplicateOrderError(frappe.ValidationError):
	pass


class InsufficientSellableStockError(frappe.ValidationError):
	pass


class SalesStockPathError(frappe.ValidationError):
	pass


class DispatchDetailsError(frappe.ValidationError):
	pass


def _setting(field: str) -> int:
	return cint(frappe.db.get_single_value("Factory Ops Settings", field))


# ---------------------------------------------------------------------------
# Sales Order: channel and external order number
# ---------------------------------------------------------------------------
def validate_sales_channel(doc, method=None):
	if not doc.get("fo_sales_channel"):
		doc.fo_sales_channel = "B2B"
	ref = (doc.get("fo_external_order_id") or "").strip()
	doc.fo_external_order_id = ref or None
	if doc.fo_sales_channel in D2C_CHANNELS and not ref:
		frappe.throw(
			_("Enter the {0} order number (External Order ID) for a {1} order.").format(
				doc.fo_sales_channel, doc.fo_sales_channel
			),
			DuplicateOrderError,
		)
	if not ref:
		return
	duplicate = frappe.db.get_value(
		"Sales Order",
		{
			"fo_sales_channel": doc.fo_sales_channel,
			"fo_external_order_id": ref,
			"docstatus": ("<", 2),
			"name": ("!=", doc.name or ""),
		},
		"name",
	)
	if duplicate:
		frappe.throw(
			_("{0} order {1} has already been entered as Sales Order {2}.").format(
				doc.fo_sales_channel, ref, duplicate
			),
			DuplicateOrderError,
		)


def inherit_sales_channel(doc, method=None):
	"""Delivery Note / Sales Invoice: take channel and external order number from the Sales Order.

	The order's External Order ID is ``no_copy`` (so duplicating an order does not duplicate the
	number), which also stops ERPNext's mapper carrying it to the DN and invoice."""
	so_field = "against_sales_order" if doc.doctype == "Delivery Note" else "sales_order"
	orders = {row.get(so_field) for row in doc.items if row.get(so_field)}
	if len(orders) != 1:
		if not doc.get("fo_sales_channel"):
			doc.fo_sales_channel = "B2B"
		return
	channel, ref = frappe.db.get_value(
		"Sales Order", orders.pop(), ["fo_sales_channel", "fo_external_order_id"]
	)
	doc.fo_sales_channel = channel or "B2B"
	doc.fo_external_order_id = ref


# ---------------------------------------------------------------------------
# Delivery Note: batches already pinned by a reservation must still be dispatchable
# ---------------------------------------------------------------------------
def validate_reserved_batches_dispatchable(doc, method=None):
	"""A Delivery Note against a reserved order ships the reserved batches. If one of them has
	expired or dropped below the minimum shelf life since it was reserved, refuse here with a
	clear message (ERPNext would otherwise fail later, inside its reservation update)."""
	if cint(doc.get("is_return")):
		return
	min_days = min_shelf_life_days()
	for row in doc.items:
		if not row.get("so_detail") or row.get("batch_no"):
			continue  # explicitly chosen batches are checked by the dispatch guard (G6)
		for b in frappe.db.sql(
			"""select sbe.batch_no, b.expiry_date
			from `tabSerial and Batch Entry` sbe
			inner join `tabStock Reservation Entry` sre on sre.name = sbe.parent
			inner join `tabBatch` b on b.name = sbe.batch_no
			where sbe.parenttype = 'Stock Reservation Entry' and sre.docstatus = 1
				and sre.voucher_detail_no = %s and sre.warehouse = %s
				and sre.status not in ('Delivered', 'Cancelled') and sbe.qty > sbe.delivered_qty""",
			(row.so_detail, row.warehouse),
			as_dict=True,
		):
			result = check_batch(b.expiry_date, doc.get("posting_date") or nowdate(), min_days)
			remaining = result.remaining_days
			if not result.eligible:
				frappe.throw(
					_(
						"Row #{0}: batch {1}, reserved for this order, has {2} days of shelf life left; at least {3} are required for dispatch. Cancel the reservation and reserve again so another batch is chosen."
					).format(row.idx, b.batch_no, remaining, min_days),
					exc=QCGuardError,
					title=_("Blocked by QC control"),
				)


# ---------------------------------------------------------------------------
# Stock Reservation Entry: only stock that is available to sell
# ---------------------------------------------------------------------------
def validate_reservation_is_sellable(doc, method=None):
	"""before_submit, after the QC guard has refused QC Hold / Rejected warehouses.

	- Reserved from a Pick List: the batches were chosen by the Pick List under the same
	  eligibility rule; check them, do not re-count the picked quantity.
	- Reserved from the Sales Order: ERPNext pins batches FEFO without knowing the minimum shelf
	  life. Ineligible pins are replaced by eligible batches (FEFO); if eligible free stock is
	  short, the reservation is refused. The quantity may never exceed available to sell.
	"""
	if doc.voucher_type != "Sales Order":
		return
	pinned = [r for r in doc.get("sb_entries") or [] if r.get("batch_no")]
	ineligible = [r for r in pinned if not check_batch_no(r.batch_no).eligible]
	if doc.get("from_voucher_type") == "Pick List":
		if ineligible:
			frappe.throw(
				_(
					"Batch {0} cannot be reserved: it is expired or below the minimum shelf life for dispatch."
				).format(ineligible[0].batch_no),
				InsufficientSellableStockError,
			)
		return
	available = available_to_sell(doc.company, doc.item_code, doc.warehouse)
	if flt(doc.reserved_qty) > flt(available) + 1e-9:
		frappe.throw(
			_(
				"Only {0} of {1} is available to sell in {2} (released, in date and not already reserved); cannot reserve {3}."
			).format(flt(available), doc.item_code, doc.warehouse, flt(doc.reserved_qty)),
			InsufficientSellableStockError,
		)
	if ineligible:
		allocation = allocate(doc.company, doc.item_code, doc.warehouse, flt(doc.reserved_qty))
		if flt(sum(q for _, q in allocation), 6) < flt(doc.reserved_qty, 6):
			frappe.throw(
				_("Not enough stock of {0} with enough shelf life left in {1} to reserve {2}.").format(
					doc.item_code, doc.warehouse, flt(doc.reserved_qty)
				),
				InsufficientSellableStockError,
			)
		doc.set("sb_entries", [])
		for batch_no, qty in allocation:
			doc.append("sb_entries", {"batch_no": batch_no, "qty": qty, "warehouse": doc.warehouse})


# ---------------------------------------------------------------------------
# Pick List: suggest only eligible batches
# ---------------------------------------------------------------------------
def reallocate_pick_list(doc, method=None):
	"""before_save, right after ERPNext computed the suggested locations (FEFO, expired skipped).
	Rows whose batch is not dispatchable are replaced by eligible batches from the same
	warehouse, FEFO; quantities already suggested in this Pick List are not offered twice.
	Manually picked lists are left as entered (and checked by the dispatch guard)."""
	if cint(doc.get("pick_manually")) or not doc.get("locations"):
		return
	taken: dict = {}
	keep, redo = [], []
	for row in doc.locations:
		if row.get("batch_no") and not check_batch_no(row.batch_no).eligible:
			redo.append(row)
		else:
			keep.append(row)
			if row.get("batch_no"):
				key = (row.batch_no, row.warehouse)
				taken[key] = taken.get(key, 0) + flt(row.stock_qty or row.qty)
	if not redo:
		return
	rows = list(keep)
	for row in redo:
		need = flt(row.stock_qty or row.qty)
		for batch_no, qty in allocate(doc.company, row.item_code, row.warehouse, need, taken=taken):
			new = frappe.copy_doc(row, ignore_no_copy=False)
			new.batch_no = batch_no
			new.serial_and_batch_bundle = None
			new.use_serial_batch_fields = 1
			factor = flt(row.conversion_factor) or 1
			new.stock_qty = qty
			new.qty = qty / factor
			new.picked_qty = new.stock_qty if flt(row.picked_qty) else row.picked_qty
			rows.append(new)
	doc.set("locations", [])
	for i, row in enumerate(rows, start=1):
		row.idx = i
		doc.append("locations", row)


# ---------------------------------------------------------------------------
# Delivery Note: choose eligible batches when ERPNext would choose them itself
# ---------------------------------------------------------------------------
def assign_eligible_batches(doc, method=None):
	"""before_validate of a draft Delivery Note, only when a minimum shelf life is set (with 0,
	ERPNext's own FEFO already skips expired batches and nothing changes).

	Rows of batch items with no batch, no bundle and no reservation behind them are given eligible
	batches FEFO (a row is split when it needs more than one batch). Batches entered by a user are
	left alone and checked by the dispatch guard; reserved rows ship their reserved batches."""
	if cint(doc.docstatus) != 0 or cint(doc.get("is_return")) or not min_shelf_life_days():
		return
	taken: dict = {}
	rows = []
	changed = False
	for row in doc.items:
		if (
			row.get("batch_no")
			or row.get("serial_and_batch_bundle")
			or not row.get("warehouse")
			or not cint(frappe.get_cached_value("Item", row.item_code, "has_batch_no"))
			or (row.get("so_detail") and _has_reservation(row.so_detail))
		):
			rows.append(row)
			continue
		allocation = allocate(
			doc.company,
			row.item_code,
			row.warehouse,
			flt(row.stock_qty or row.qty),
			on_date=doc.posting_date,
			taken=taken,
		)
		if flt(sum(q for _, q in allocation), 6) < flt(row.stock_qty or row.qty, 6):
			# not enough eligible stock: never shrink a quantity silently; ERPNext / the dispatch
			# guard refuse the document with a reason
			for batch_no, qty in allocation:
				taken[(batch_no, row.warehouse)] -= qty
			rows.append(row)
			continue
		changed = True
		factor = flt(row.conversion_factor) or 1
		for i, (batch_no, qty) in enumerate(allocation):
			new = row if i == 0 else frappe.copy_doc(row, ignore_no_copy=False)
			new.batch_no = batch_no
			new.use_serial_batch_fields = 1
			new.qty = qty / factor
			new.stock_qty = qty
			rows.append(new)
	if changed:
		doc.set("items", [])
		for i, row in enumerate(rows, start=1):
			row.idx = i
			doc.append("items", row)


def _has_reservation(so_detail: str) -> bool:
	return bool(
		frappe.db.exists(
			"Stock Reservation Entry",
			{
				"voucher_detail_no": so_detail,
				"docstatus": 1,
				"status": ("not in", ("Delivered", "Cancelled")),
			},
		)
	)


# ---------------------------------------------------------------------------
# Sales Invoice: stock leaves only through a Delivery Note
# ---------------------------------------------------------------------------
def validate_invoice_stock_path(doc, method=None):
	if not cint(doc.get("update_stock")) or cint(doc.get("is_return")) or cint(doc.get("is_pos")):
		return
	if _setting("allow_sales_invoice_stock_update"):
		return
	for row in doc.items:
		if frappe.get_cached_value("Item", row.item_code, "is_stock_item"):
			frappe.throw(
				_(
					"Row #{0}: {1} is a stock item. Dispatch it with a Delivery Note and invoice it from the Delivery Note, so stock is deducted once."
				).format(row.idx, row.item_code),
				SalesStockPathError,
			)


# ---------------------------------------------------------------------------
# Delivery Note: dispatch details
# ---------------------------------------------------------------------------
def validate_dispatch_details(doc, method=None):
	if cint(doc.get("is_return")) or not _setting("require_dispatch_details"):
		return
	missing = []
	if not (doc.get("transporter") or (doc.get("transporter_name") or "").strip()):
		missing.append(_("Transporter"))
	if not (doc.get("lr_no") or "").strip():
		missing.append(_("Transport Receipt No (LR / AWB)"))
	if missing:
		frappe.throw(
			_("Enter the dispatch details before submitting: {0}.").format(", ".join(missing)),
			DispatchDetailsError,
		)
