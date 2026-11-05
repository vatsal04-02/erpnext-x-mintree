# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Production reconciliation and yield for Work Orders.

Read-only and deterministic: every figure is derived from ERPNext's own Work Order, Work Order Item
and Stock Entry records, plus wastage write-offs linked through ``Stock Entry.fo_work_order``. No
AI, no estimates, nothing stored, so it can never disagree with the stock ledger.

Definitions (per Work Order; quantities in the production item's / component's stock UOM):

- ``processed_qty``  = ``produced_qty`` + ``process_loss_qty``
  (what has gone through Manufacture entries).
- ``completion_pct`` = ``processed_qty`` / ``planned_qty`` x 100.
- ``yield_pct``      = ``produced_qty`` / ``processed_qty`` x 100 (blank until something is processed).
- per component (ERPNext stores ``Work Order Item.consumed_qty`` as consumption **plus** returned
  quantity, see ``work_order.py`` ``update_consumed_qty_for_required_items``; the figures here use
  true consumption = stored ``consumed_qty`` - ``returned_qty``):
  ``standard_qty``   = ``required_qty`` x ``processed_qty`` / ``planned_qty``
  ``variance_qty``   = true consumption - ``standard_qty`` (positive = used more than the BOM allows)
  ``balance_qty``    = ``transferred_qty`` - stored ``consumed_qty`` - wastage written off from the
  Work Order's WIP warehouse. What should still be sitting in WIP; zero when reconciled.
  Not computed (blank) for orders that skip the transfer step, because nothing passes through WIP.
- ``reconciliation``: ``Open`` while the order is not Completed/Closed; for a Completed or Closed
  order, ``Reconciled`` if every balance is zero (to quantity precision), else ``Unreconciled``.
- ``below_min_yield``: only when Factory Ops Settings has a minimum yield; blank setting = no check.

Not covered: by-products and scrap items of a BOM, and operation/labour time (ERPNext's own Job
Card and Process Loss reports cover those).
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt

CLOSED_STATUSES = ("Completed", "Closed")


def _qty_precision() -> int:
	return int(frappe.db.get_default("float_precision") or 3)


def _min_yield() -> float | None:
	value = frappe.db.get_single_value("Factory Ops Settings", "min_yield_percent")
	return flt(value) if value else None


def _wastage(work_orders: list[str]) -> dict[tuple[str, str], dict]:
	"""Submitted wastage write-offs linked to the orders, per (work order, item)."""
	if not work_orders:
		return {}
	entries = frappe.get_all(
		"Stock Entry",
		filters={"fo_work_order": ("in", work_orders), "docstatus": 1},
		fields=["name", "fo_work_order"],
	)
	if not entries:
		return {}
	owner = {e.name: e.fo_work_order for e in entries}
	wip = {w.name: w.wip_warehouse for w in frappe.get_all("Work Order", filters={"name": ("in", work_orders)}, fields=["name", "wip_warehouse"])}
	out: dict[tuple[str, str], dict] = defaultdict(lambda: {"qty": 0.0, "from_wip_qty": 0.0, "value": 0.0})
	for row in frappe.get_all(
		"Stock Entry Detail",
		filters={"parent": ("in", list(owner))},
		fields=["parent", "item_code", "qty", "s_warehouse", "basic_amount"],
	):
		wo = owner[row.parent]
		cell = out[(wo, row.item_code)]
		cell["qty"] += flt(row.qty)
		cell["value"] += flt(row.basic_amount)
		if row.s_warehouse and row.s_warehouse == wip.get(wo):
			cell["from_wip_qty"] += flt(row.qty)
	return out


def compute(work_orders: list[str]) -> list[dict]:
	"""Reconciliation for the given Work Orders (permission is the caller's responsibility)."""
	if not work_orders:
		return []
	precision = _qty_precision()
	min_yield = _min_yield()
	orders = frappe.get_all(
		"Work Order",
		filters={"name": ("in", work_orders), "docstatus": ("!=", 2)},
		fields=[
			"name", "company", "production_item", "item_name", "status", "qty", "produced_qty",
			"process_loss_qty", "skip_transfer", "wip_warehouse", "sales_order", "bom_no",
		],
	)
	items = defaultdict(list)
	for r in frappe.get_all(
		"Work Order Item",
		filters={"parent": ("in", [o.name for o in orders])},
		fields=["parent", "item_code", "item_name", "required_qty", "transferred_qty", "consumed_qty", "returned_qty", "stock_uom"],
		order_by="idx",
	):
		items[r.parent].append(r)
	wastage = _wastage([o.name for o in orders])

	result = []
	for o in orders:
		planned, produced, loss = flt(o.qty), flt(o.produced_qty), flt(o.process_loss_qty)
		processed = produced + loss
		yield_pct = flt(produced / processed * 100, 2) if processed else None
		completion = flt(processed / planned * 100, 2) if planned else None
		lines, unreconciled = [], False
		for r in items[o.name]:
			standard = flt(flt(r.required_qty) * processed / planned, precision) if planned else 0.0
			returned = flt(r.returned_qty)
			consumed = flt(flt(r.consumed_qty) - returned, precision)  # ERPNext's field includes returns
			w = wastage.get((o.name, r.item_code), {"qty": 0.0, "from_wip_qty": 0.0, "value": 0.0})
			if o.skip_transfer:
				balance = None
			else:
				balance = flt(flt(r.transferred_qty) - flt(r.consumed_qty) - w["from_wip_qty"], precision)
				unreconciled |= bool(balance)
			variance = flt(consumed - standard, precision)
			lines.append(
				{
					"item_code": r.item_code,
					"item_name": r.item_name,
					"uom": r.stock_uom,
					"required_qty": flt(r.required_qty),
					"transferred_qty": flt(r.transferred_qty),
					"consumed_qty": consumed,
					"returned_qty": returned,
					"wastage_qty": flt(w["qty"], precision),
					"wastage_value": flt(w["value"], 2),
					"standard_qty": standard,
					"variance_qty": variance,
					"variance_pct": flt(variance / standard * 100, 2) if standard else None,
					"balance_qty": balance,
				}
			)
		if o.status in CLOSED_STATUSES:
			state = "Unreconciled" if unreconciled else "Reconciled"
		else:
			state = "Open"
		result.append(
			{
				"work_order": o.name,
				"company": o.company,
				"production_item": o.production_item,
				"item_name": o.item_name,
				"status": o.status,
				"sales_order": o.sales_order,
				"planned_qty": planned,
				"produced_qty": produced,
				"process_loss_qty": loss,
				"processed_qty": flt(processed, precision),
				"completion_pct": completion,
				"yield_pct": yield_pct,
				"min_yield_pct": min_yield,
				"below_min_yield": bool(min_yield is not None and yield_pct is not None and yield_pct < min_yield),
				"reconciliation": state,
				"wastage_value": flt(sum(line["wastage_value"] for line in lines), 2),
				"items": lines,
			}
		)
	return result


@frappe.whitelist()
def reconcile(work_order: str) -> dict:
	"""One Work Order's reconciliation. Requires read access to the Work Order."""
	frappe.has_permission("Work Order", "read", doc=work_order, throw=True)
	data = compute([work_order])
	if not data:
		frappe.throw(_("Work Order {0} was not found or is cancelled.").format(work_order))
	return data[0]


def list_reconciliations(filters: dict, limit: int = 500) -> list[dict]:
	"""Reconciliation of the Work Orders the user may read, newest first."""
	conditions = {"docstatus": 1}
	for key in ("company", "production_item", "status"):
		if filters.get(key):
			conditions[key] = filters[key]
	if filters.get("work_order"):
		conditions["name"] = filters["work_order"]
	if filters.get("from_date"):
		conditions["creation"] = (">=", filters["from_date"])
	if filters.get("to_date"):
		conditions["creation"] = ("between", [filters.get("from_date") or "1900-01-01", f"{filters['to_date']} 23:59:59"])
	names = frappe.get_list("Work Order", filters=conditions, pluck="name", order_by="creation desc", limit_page_length=limit)
	return compute(names)
