# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Procurement decisions: approvals, supplier delays, material shortages.

Purchase Order approval is *submitting* it. The approval rules (factory_ops.purchase.approval) are the
authority; this module only reads them to say whether the viewer may approve, never to decide."""

import frappe
from frappe.utils import flt, get_datetime

from factory_ops.constants import (
	WH_TYPE_JOB_WORK,
	WH_TYPE_NOT_FOR_DISPATCH,
	WH_TYPE_QC_HOLD,
	WH_TYPE_REJECTED,
	WH_TYPE_RETAINED,
)
from factory_ops.decisions.context import assigned_user
from factory_ops.decisions.model import (
	ACCOUNTS,
	CRITICAL,
	HIGH,
	INVENTORY,
	MEDIUM,
	OVERDUE,
	PRODUCTION,
	PROCUREMENT,
	Action,
	Entry,
)
from factory_ops.decisions.sources import source
from factory_ops.purchase.approval import approval_enforced

CLOSED_WO = ("Draft", "Completed", "Stopped", "Closed", "Cancelled")
RESTRICTED_WAREHOUSE_TYPES = (WH_TYPE_QC_HOLD, WH_TYPE_REJECTED, WH_TYPE_RETAINED, WH_TYPE_NOT_FOR_DISPATCH, WH_TYPE_JOB_WORK)


def _limits() -> list[dict]:
	"""Every enabled approval limit: one query. Used only to say who *could* approve."""
	return frappe.get_all(
		"Purchase Approval Limit", filters={"enabled": 1}, fields=["user", "company", "approval_limit"]
	)


def _best_limit(limits: list[dict], company: str) -> float | None:
	values = [flt(x.approval_limit) for x in limits if not x.company or x.company == company]
	return max(values) if values else None


def _my_limit(limits: list[dict], user: str, company: str) -> float | None:
	for x in limits:
		if x.user == user and (not x.company or x.company == company):
			return flt(x.approval_limit)
	return None


@source(
	"purchase_order_approval",
	PROCUREMENT,
	("Purchase Order",),
	{
		"po_approval": "Purchase Orders awaiting approval",
		"po_over_limit": "Purchase Orders above every approval limit",
	},
)
def purchase_orders_awaiting_approval(ctx):
	rows = ctx.rows(
		"Purchase Order",
		{"docstatus": 0},
		["name", "supplier", "base_grand_total", "owner", "creation", "company", "status", "_assign"],
		source="purchase_order_approval",
	)
	if not rows:
		return []
	limits = _limits()
	enforced = approval_enforced()
	can_submit = ctx.can("Purchase Order", "submit")
	out = []
	for r in rows:
		total = flt(r.base_grand_total)
		best = _best_limit(limits, r.company)
		over = enforced and (best is None or total > best)
		action = _approval_action(ctx, r, total, enforced, can_submit, limits)
		out.append(
			Entry(
				kind="po_over_limit" if over else "po_approval",
				category="Purchase Orders above every approval limit" if over else "Purchase Orders awaiting approval",
				department=PROCUREMENT,
				issue=(
					f"{r.supplier}: {total:,.2f} is above every approval limit; nobody can approve it as it stands"
					if over
					else f"{r.supplier}: Purchase Order of {total:,.2f} awaiting approval"
				),
				priority=HIGH if over else MEDIUM,
				ref_doctype="Purchase Order",
				ref_name=r.name,
				status=r.status or "Draft",
				since=get_datetime(r.creation),
				responsible=assigned_user({"_assign": r.get("_assign"), "owner": ""}),
				responsible_department="Purchase Manager (approver)",
				raised_by=r.owner,
				amount=total,
				precedence=10,
				action=action,
			)
		)
	return out


def _approval_action(ctx, r, total, enforced, can_submit, limits) -> Action:
	label = "Approve"
	if not can_submit:
		return Action("approve_po", label, False, "You do not have permission to approve Purchase Orders.")
	if enforced:
		if r.owner == ctx.user:
			return Action("approve_po", label, False, "You raised this order; another approver must approve it.")
		mine = _my_limit(limits, ctx.user, r.company)
		if mine is None:
			return Action("approve_po", label, False, "You have no purchase approval limit.")
		if total > mine:
			return Action("approve_po", label, False, f"Above your approval limit of {mine:,.2f}.")
	return Action("approve_po", label)


@source("purchase_request_review", PROCUREMENT, ("Material Request",), {"pr_review": "Purchase requests awaiting review"})
def purchase_requests(ctx):
	rows = ctx.rows(
		"Material Request",
		{"docstatus": 0, "material_request_type": "Purchase"},
		["name", "owner", "creation", "status", "schedule_date", "_assign"],
		source="purchase_request_review",
	)
	return [
		Entry(
			kind="pr_review",
			category="Purchase requests awaiting review",
			department=PROCUREMENT,
			issue=f"Purchase request {r.name} awaiting review"
			+ (f", needed by {r.schedule_date}" if r.schedule_date else ""),
			priority=MEDIUM,
			ref_doctype="Material Request",
			ref_name=r.name,
			status=r.status or "Draft",
			since=get_datetime(r.creation),
			responsible=assigned_user(r),
			raised_by=r.owner,
			responsible_department="Purchase Manager",
			action=Action("open", "Open"),
		)
		for r in rows
	]


@source("supplier_delay", PROCUREMENT, ("Purchase Order",), {"supplier_delay": "Supplier delays"})
def supplier_delays(ctx):
	rows = ctx.rows(
		"Purchase Order",
		{
			"docstatus": 1,
			"status": ["in", ["To Receive and Bill", "To Receive"]],
			"schedule_date": ["<", ctx.today],
		},
		["name", "supplier", "schedule_date", "owner", "creation", "status", "per_received", "base_grand_total", "_assign"],
		order_by="schedule_date asc",
		source="supplier_delay",
	)
	out = []
	for r in rows:
		days = (ctx.today - get_datetime(r.schedule_date).date()).days
		out.append(
			Entry(
				kind="supplier_delay",
				category="Supplier delays",
				department=PROCUREMENT,
				issue=f"{r.supplier}: {days} day(s) late, {int(flt(r.per_received))}% received",
				priority=HIGH,
				ref_doctype="Purchase Order",
				ref_name=r.name,
				status=r.status,
				state=OVERDUE,
				since=get_datetime(r.schedule_date),
				responsible=assigned_user(r),
				raised_by=r.owner,
				responsible_department="Purchase Manager",
				amount=flt(r.base_grand_total),
				overdue=True,
				precedence=20,
				action=Action("open", "Open"),
			)
		)
	return out


@source(
	"material_shortage",
	PROCUREMENT,
	("Work Order", "Bin", "Purchase Order", "Item"),
	{"material_shortage": "Material shortages needing a purchasing decision"},
)
def material_shortages(ctx):
	"""Items the open Work Orders still need, beyond usable stock and what is already on order.

	Three aggregate queries (needed, in stock, on order), never one per item. Nothing on hand and
	nothing coming is Critical; a shortfall that an order would cover is High."""
	company_sql, params = ctx.company_sql("wo")
	needed = frappe.db.sql(
		f"""select woi.item_code, i.item_name, sum(greatest(woi.required_qty - woi.transferred_qty, 0)) as need,
			min(wo.creation) as since
		from `tabWork Order Item` woi
		join `tabWork Order` wo on wo.name = woi.parent
		join `tabItem` i on i.name = woi.item_code
		where wo.docstatus = 1 and wo.status not in %(closed)s {company_sql}
		group by woi.item_code, i.item_name
		having need > 0""",
		{"closed": CLOSED_WO, **params},
		as_dict=True,
	)
	if not needed:
		return []
	items = tuple(n.item_code for n in needed)
	stock = {
		r.item_code: flt(r.qty)
		for r in frappe.db.sql(
			"""select b.item_code, sum(b.actual_qty) as qty from `tabBin` b
			join `tabWarehouse` w on w.name = b.warehouse
			where b.item_code in %(items)s and (w.warehouse_type is null or w.warehouse_type not in %(restricted)s)
			group by b.item_code""",
			{"items": items, "restricted": RESTRICTED_WAREHOUSE_TYPES},
			as_dict=True,
		)
	}
	ordered = {
		r.item_code: flt(r.qty)
		for r in frappe.db.sql(
			"""select poi.item_code, sum(greatest(poi.qty - poi.received_qty, 0)) as qty
			from `tabPurchase Order Item` poi join `tabPurchase Order` po on po.name = poi.parent
			where poi.item_code in %(items)s and po.docstatus = 1
				and po.status not in ('Closed', 'Completed', 'Cancelled', 'On Hold')
			group by poi.item_code""",
			{"items": items},
			as_dict=True,
		)
	}
	out = []
	for n in needed:
		have, coming = stock.get(n.item_code, 0.0), ordered.get(n.item_code, 0.0)
		short = flt(n.need) - have - coming
		if short <= 0:
			continue
		nothing_coming = have <= 0 and coming <= 0
		out.append(
			Entry(
				kind="material_shortage",
				category="Material shortages needing a purchasing decision",
				department=PROCUREMENT,
				issue=(
					f"{n.item_name or n.item_code}: short by {short:,.3f} (needed {flt(n.need):,.3f}, "
					f"in stock {have:,.3f}, on order {coming:,.3f})"
				),
				priority=CRITICAL if nothing_coming else HIGH,
				ref_doctype="Item",
				ref_name=n.item_code,
				status="Short",
				state="Needs attention",
				since=get_datetime(n.since),
				responsible_department="Purchase Manager",
				also=(INVENTORY, PRODUCTION),
				blocker=False,
				precedence=30,
				action=Action("open", "Open"),
			)
		)
	return out[: ctx.limit]
