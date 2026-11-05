"""Batch dispatch eligibility: the single source of truth (Milestone 6, DECISIONS D-034).

Every place that decides whether a batch may be sold, reserved, picked or shipped asks this module:

- Available to Sell (report and reservation limit)
- Stock Reservation Entry for a Sales Order (batch pinning)
- Pick List suggestions
- Delivery Note (explicit batches, reserved batches, and batches chosen automatically)
- the dispatch guard G6 (``inventory/guards.validate_shelf_life``)
- the daily shelf-life segregation (a supplementary safeguard only)

**Rule.** A batch is dispatchable on a transaction date when it has an expiry date that is on or
after that date *and* at least ``min_shelf_life_days_for_dispatch`` days remain (a batch with
exactly the minimum left is eligible). Batches without an expiry date are always eligible. The
minimum is read from Factory Ops Settings on every call, so a change applies immediately. With the
minimum at 0 only expired batches are ineligible, which is ERPNext's own behaviour.

**Quantities** follow ERPNext v16's own primitives, never a separate store:

- on-hand per batch and warehouse: ``get_available_batches`` (stock ledger only; a Pick List's
  batch bundle is not a stock movement);
- reserved: Stock Reservation Entries, batch-pinned (``get_reserved_batches_for_sre``) and
  quantity-only (per warehouse);
- picked: ``get_picked_batches`` (Pick Lists not yet completed), less what those Pick Lists have
  already reserved, so a picked-and-reserved quantity is not subtracted twice.
"""

from collections import defaultdict
from dataclasses import dataclass

import frappe
from frappe.utils import cint, date_diff, flt, getdate, nowdate


@dataclass(frozen=True)
class Eligibility:
	eligible: bool
	remaining_days: int | None
	reason: str | None  # None, "expired" or "short_dated"


def min_shelf_life_days() -> int:
	return cint(frappe.db.get_single_value("Factory Ops Settings", "min_shelf_life_days_for_dispatch"))


def check_batch(expiry_date, on_date=None, min_days: int | None = None) -> Eligibility:
	"""Is a batch with this expiry dispatchable on ``on_date`` (default today)?"""
	if not expiry_date:
		return Eligibility(True, None, None)
	remaining = date_diff(getdate(expiry_date), getdate(on_date or nowdate()))
	if remaining < 0:
		return Eligibility(False, remaining, "expired")
	if remaining < (min_shelf_life_days() if min_days is None else min_days):
		return Eligibility(False, remaining, "short_dated")
	return Eligibility(True, remaining, None)


def check_batch_no(batch_no: str, on_date=None, min_days: int | None = None) -> Eligibility:
	return check_batch(frappe.db.get_value("Batch", batch_no, "expiry_date"), on_date, min_days)


# ---------------------------------------------------------------------------
# Quantities
# ---------------------------------------------------------------------------
def _kwargs(company, item_code, warehouse):
	return frappe._dict(
		{
			"company": company,
			"item_code": item_code,
			"warehouse": warehouse,
			"for_stock_levels": True,  # include expired batches, so they can be classified
			"based_on": "Expiry",
		}
	)


def batch_balances(company: str, item_code: str, warehouse: str | None = None) -> list[frappe._dict]:
	"""On-hand quantity per (batch, warehouse) from the stock ledger, with expiry."""
	from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import get_available_batches

	rows = get_available_batches(_kwargs(company, item_code, warehouse))
	return [r for r in rows if flt(r.qty) > 0]


def pinned_reservations(company: str, item_code: str, warehouse: str | None = None) -> dict:
	"""{(batch, warehouse): qty} reserved by batch-pinned Stock Reservation Entries."""
	from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import (
		get_reserved_batches_for_sre,
	)

	out = defaultdict(float)
	for key, row in (get_reserved_batches_for_sre(_kwargs(company, item_code, warehouse)) or {}).items():
		out[key] += abs(flt(row.qty))
	return out


def qty_reservations(company: str, item_code: str, warehouse: str | None = None) -> dict:
	"""{warehouse: qty} reserved by quantity-only Stock Reservation Entries (no batch pinned)."""
	values = {"company": company, "item_code": item_code, "warehouse": warehouse}
	rows = frappe.db.sql(
		f"""select warehouse, sum(reserved_qty - delivered_qty) as qty
		from `tabStock Reservation Entry`
		where docstatus = 1 and company = %(company)s and item_code = %(item_code)s
			and status not in ('Delivered', 'Cancelled') and reservation_based_on = 'Qty'
			{"and warehouse = %(warehouse)s" if warehouse else ""}
		group by warehouse""",
		values,
		as_dict=True,
	)
	return {r.warehouse: max(flt(r.qty), 0) for r in rows}


def picked_unreserved(company: str, item_code: str, warehouse: str | None = None) -> dict:
	"""{(batch, warehouse): qty} picked on open Pick Lists and not yet reserved from them."""
	from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import get_picked_batches

	picked = get_picked_batches(_kwargs(company, item_code, warehouse)) or {}
	values = {"company": company, "item_code": item_code, "warehouse": warehouse}
	reserved_from_pick = defaultdict(float)
	for r in frappe.db.sql(
		f"""select sbe.batch_no, sre.warehouse, sum(sbe.qty - sbe.delivered_qty) as qty
		from `tabStock Reservation Entry` sre
		inner join `tabSerial and Batch Entry` sbe on sbe.parent = sre.name
		where sre.docstatus = 1 and sre.company = %(company)s and sre.item_code = %(item_code)s
			and sre.from_voucher_type = 'Pick List' and sre.status not in ('Delivered', 'Cancelled')
			{"and sre.warehouse = %(warehouse)s" if warehouse else ""}
		group by sbe.batch_no, sre.warehouse""",
		values,
		as_dict=True,
	):
		reserved_from_pick[(r.batch_no, r.warehouse)] += flt(r.qty)
	out = defaultdict(float)
	for key, row in picked.items():
		remaining = abs(flt(row.qty)) - reserved_from_pick.get(key, 0)
		if remaining > 0:
			out[key] += remaining
	return out


def eligible_batches(
	company: str,
	item_code: str,
	warehouse: str | None = None,
	on_date=None,
	usable_only: bool = True,
	ignore_reserved_for: str | None = None,
) -> list[frappe._dict]:
	"""Dispatchable batches with the quantity still free to sell, FEFO (earliest expiry first).

	``qty`` = on hand - batch-pinned reservations - picked-but-unreserved. Quantity-only
	reservations are per warehouse, not per batch; ``available_to_sell`` subtracts them.
	``ignore_reserved_for``: a Sales Order whose own pinned reservations count as free (used when
	choosing batches for that order's own reservation or delivery)."""
	from factory_ops.inventory.stock_queries import is_restricted

	min_days = min_shelf_life_days()
	pinned = pinned_reservations(company, item_code, warehouse)
	if ignore_reserved_for:
		for key, qty in _pinned_for_order(ignore_reserved_for, item_code).items():
			pinned[key] = max(pinned.get(key, 0) - qty, 0)
	picked = picked_unreserved(company, item_code, warehouse)
	out = []
	for b in batch_balances(company, item_code, warehouse):
		if usable_only and (is_restricted(b.warehouse) or not _is_usable(b.warehouse)):
			continue
		if not check_batch(b.expiry_date, on_date, min_days).eligible:
			continue
		free = (
			flt(b.qty) - pinned.get((b.batch_no, b.warehouse), 0) - picked.get((b.batch_no, b.warehouse), 0)
		)
		if free > 0:
			out.append(
				frappe._dict(batch_no=b.batch_no, warehouse=b.warehouse, qty=free, expiry_date=b.expiry_date)
			)
	return sorted(
		out, key=lambda r: (getdate(r.expiry_date) if r.expiry_date else getdate("9999-12-31"), r.batch_no)
	)


def _pinned_for_order(sales_order: str, item_code: str) -> dict:
	out = defaultdict(float)
	for r in frappe.db.sql(
		"""select sbe.batch_no, sre.warehouse, sum(sbe.qty - sbe.delivered_qty) as qty
		from `tabStock Reservation Entry` sre
		inner join `tabSerial and Batch Entry` sbe on sbe.parent = sre.name
		where sre.docstatus = 1 and sre.voucher_type = 'Sales Order' and sre.voucher_no = %s
			and sre.item_code = %s and sre.status not in ('Delivered', 'Cancelled')
		group by sbe.batch_no, sre.warehouse""",
		(sales_order, item_code),
		as_dict=True,
	):
		out[(r.batch_no, r.warehouse)] += flt(r.qty)
	return out


def _is_usable(warehouse: str) -> bool:
	from factory_ops.constants import WH_TYPE_USABLE

	return frappe.get_cached_value("Warehouse", warehouse, "warehouse_type") == WH_TYPE_USABLE


def available_to_sell(company: str, item_code: str, warehouse: str | None = None, on_date=None) -> float:
	"""Eligible free batch stock minus quantity-only reservations (never below zero per warehouse).
	Items without batches: Usable on-hand minus reservations."""
	if not cint(frappe.get_cached_value("Item", item_code, "has_batch_no")):
		return _available_non_batch(company, item_code, warehouse)
	by_wh = defaultdict(float)
	for b in eligible_batches(company, item_code, warehouse, on_date):
		by_wh[b.warehouse] += b.qty
	reserved = qty_reservations(company, item_code, warehouse)
	total = 0.0
	for wh in set(by_wh) | set(reserved):
		total += max(by_wh.get(wh, 0) - reserved.get(wh, 0), 0)
	return flt(total, 6)


def _available_non_batch(company, item_code, warehouse):
	from factory_ops.constants import WH_TYPE_USABLE

	values = {"company": company, "item_code": item_code, "warehouse": warehouse, "usable": WH_TYPE_USABLE}
	wh_cond = "and bin.warehouse = %(warehouse)s" if warehouse else ""
	on_hand = frappe.db.sql(
		f"""select bin.warehouse, bin.actual_qty from `tabBin` bin
		inner join `tabWarehouse` w on w.name = bin.warehouse
		where w.company = %(company)s and w.warehouse_type = %(usable)s and bin.item_code = %(item_code)s {wh_cond}""",
		values,
		as_dict=True,
	)
	sre = frappe.db.sql(
		f"""select warehouse, sum(reserved_qty - delivered_qty) as qty from `tabStock Reservation Entry`
		where docstatus = 1 and company = %(company)s and item_code = %(item_code)s
			and status not in ('Delivered', 'Cancelled') {"and warehouse = %(warehouse)s" if warehouse else ""}
		group by warehouse""",
		values,
		as_dict=True,
	)
	reserved = {r.warehouse: flt(r.qty) for r in sre}
	return flt(sum(max(flt(r.actual_qty) - reserved.get(r.warehouse, 0), 0) for r in on_hand), 6)


def allocate(
	company: str, item_code: str, warehouse: str, qty: float, on_date=None, taken: dict | None = None, **kw
):
	"""FEFO allocation of ``qty`` from eligible free batches in ``warehouse``.
	``taken``: {(batch, warehouse): qty} already allocated in the same document (updated in place).
	Returns [(batch_no, qty)]; the total may be less than ``qty`` when stock is short."""
	taken = taken if taken is not None else {}
	out, need = [], flt(qty)
	for b in eligible_batches(company, item_code, warehouse, on_date, **kw):
		if need <= 0:
			break
		free = b.qty - taken.get((b.batch_no, b.warehouse), 0)
		if free <= 0:
			continue
		use = min(free, need)
		out.append((b.batch_no, use))
		taken[(b.batch_no, b.warehouse)] = taken.get((b.batch_no, b.warehouse), 0) + use
		need -= use
	return out
