# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Production decisions: orders awaiting approval, late or overdue orders, blockers, yield.

One Work Order produces at most one entry: when it is both blocked and overdue, the blocker (the cause)
is shown and flagged overdue; precedence resolves the rest."""

import frappe
from frappe.utils import flt, get_datetime

from factory_ops.decisions.context import assigned_user
from factory_ops.decisions.model import (
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
from factory_ops.decisions.sources.procurement import CLOSED_WO

WO_FIELDS = ["name", "production_item", "item_name", "status", "planned_start_date", "planned_end_date", "qty", "produced_qty", "creation", "owner", "company", "_assign"]


@source("work_order_approval", PRODUCTION, ("Work Order",), {"wo_approval": "Production orders awaiting approval"})
def orders_awaiting_approval(ctx):
	rows = ctx.rows("Work Order", {"docstatus": 0}, WO_FIELDS, source="work_order_approval")
	can_submit = ctx.can("Work Order", "submit")
	return [
		Entry(
			kind="wo_approval",
			category="Production orders awaiting approval",
			department=PRODUCTION,
			issue=f"{r.item_name or r.production_item}: order for {flt(r.qty):g} awaiting approval",
			priority=MEDIUM,
			ref_doctype="Work Order",
			ref_name=r.name,
			status="Draft",
			since=get_datetime(r.creation),
			responsible=assigned_user({"_assign": r.get("_assign"), "owner": ""}),
			raised_by=r.owner,
			responsible_department="Manufacturing Manager",
			precedence=10,
			action=Action("submit", "Approve", can_submit, "" if can_submit else "You do not have permission to approve Work Orders."),
		)
		for r in rows
	]


@source(
	"work_order_delays",
	PRODUCTION,
	("Work Order",),
	{"wo_overdue": "Overdue Work Orders", "wo_late_start": "Production delays (not started on time)"},
)
def late_orders(ctx):
	rows = ctx.rows(
		"Work Order",
		{"docstatus": 1, "status": ["not in", CLOSED_WO]},
		WO_FIELDS,
		order_by="planned_end_date asc",
		source="work_order_delays",
	)
	out = []
	for r in rows:
		end = get_datetime(r.planned_end_date).date() if r.planned_end_date else None
		start = get_datetime(r.planned_start_date).date() if r.planned_start_date else None
		if end and end < ctx.today:
			late_days, kind, since = (ctx.today - end).days, "wo_overdue", get_datetime(r.planned_end_date)
			issue = f"{r.item_name or r.production_item}: {late_days} day(s) past its planned end ({flt(r.produced_qty):g} of {flt(r.qty):g} made)"
			category = "Overdue Work Orders"
		elif r.status == "Not Started" and start and start < ctx.today:
			late_days, kind, since = (ctx.today - start).days, "wo_late_start", get_datetime(r.planned_start_date)
			issue = f"{r.item_name or r.production_item}: not started, {late_days} day(s) past its planned start"
			category = "Production delays (not started on time)"
		else:
			continue
		out.append(
			Entry(
				kind=kind,
				category=category,
				department=PRODUCTION,
				issue=issue,
				priority=HIGH,
				ref_doctype="Work Order",
				ref_name=r.name,
				status=r.status,
				state=OVERDUE,
				since=since,
				responsible=assigned_user(r),
				raised_by=r.owner,
				responsible_department="Manufacturing Manager",
				overdue=True,
				precedence=20,
				action=Action("open", "Open"),
			)
		)
	return out


@source("work_order_blocked", PRODUCTION, ("Work Order", "Bin"), {"wo_blocked": "Material shortages blocking production"})
def blocked_orders(ctx):
	"""Open Work Orders with a component whose remaining need exceeds the stock at its source warehouse."""
	company_sql, params = ctx.company_sql("wo")
	rows = frappe.db.sql(
		f"""select wo.name, wo.item_name, wo.production_item, wo.status, wo.planned_end_date, wo.creation, wo.owner,
			wo._assign, woi.item_code, woi.required_qty - woi.transferred_qty as need, coalesce(b.actual_qty, 0) as have
		from `tabWork Order` wo
		join `tabWork Order Item` woi on woi.parent = wo.name
		left join `tabBin` b on b.item_code = woi.item_code and b.warehouse = woi.source_warehouse
		where wo.docstatus = 1 and wo.status not in %(closed)s and woi.source_warehouse is not null
			and woi.source_warehouse != '' and woi.required_qty - woi.transferred_qty > coalesce(b.actual_qty, 0) {company_sql}
		order by wo.creation asc""",
		{"closed": CLOSED_WO, **params},
		as_dict=True,
	)
	by_order: dict[str, list] = {}
	for r in rows:
		by_order.setdefault(r.name, []).append(r)
	allowed = {  # honour row-level permission: only orders the viewer may read
		n for n in frappe.get_list("Work Order", filters={"name": ["in", list(by_order)]}, pluck="name", limit_page_length=0)
	} if by_order else set()
	out = []
	for name, parts in by_order.items():
		if name not in allowed:
			continue
		head = parts[0]
		overdue = bool(head.planned_end_date and get_datetime(head.planned_end_date).date() < ctx.today)
		shown = "; ".join(f"{p.item_code} needs {flt(p.need):g}, has {flt(p.have):g}" for p in parts[:3])
		more = f" (+{len(parts) - 3} more)" if len(parts) > 3 else ""
		out.append(
			Entry(
				kind="wo_blocked",
				category="Material shortages blocking production",
				department=PRODUCTION,
				issue=f"{head.item_name or head.production_item}: blocked, short of {len(parts)} material(s): {shown}{more}",
				priority=CRITICAL if overdue else HIGH,
				ref_doctype="Work Order",
				ref_name=name,
				status=head.status,
				state="Blocked",
				since=get_datetime(head.creation),
				responsible=assigned_user(head),
				raised_by=head.owner,
				responsible_department="Manufacturing Manager",
				also=(PROCUREMENT, INVENTORY),
				overdue=overdue,
				blocker=True,
				precedence=5,
				action=Action("open", "Open"),
			)
		)
	return out[: ctx.limit]


@source("yield_deviation", PRODUCTION, ("Work Order",), {"yield_deviation": "Yield deviations and unreconciled orders"})
def yield_deviations(ctx):
	"""Reuses the Milestone A reconciliation (3 queries for all orders): a yield below the configured
	minimum, or a finished order that left material unaccounted for in WIP. No minimum configured: no check."""
	from factory_ops.production.reconciliation import compute

	rows = ctx.rows(
		"Work Order",
		{"docstatus": 1, "status": ["in", ["In Process", "Completed", "Closed"]]},
		["name", "modified", "owner", "_assign"],
		order_by="modified desc",
		source="yield_deviation",
	)
	if not rows:
		return []
	info = {r.name: r for r in rows}
	out = []
	for rec in compute(list(info)):
		low = bool(rec["below_min_yield"])
		unreconciled = rec["reconciliation"] == "Unreconciled"
		if not (low or unreconciled):
			continue
		row = info[rec["work_order"]]
		out.append(
			Entry(
				kind="yield_deviation",
				category="Yield deviations and unreconciled orders",
				department=PRODUCTION,
				issue=(
					f"{rec['item_name'] or rec['production_item']}: yield {rec['yield_pct']}% is below the minimum {rec['min_yield_pct']}%"
					if low
					else f"{rec['item_name'] or rec['production_item']}: closed with material unaccounted for in WIP"
				),
				priority=MEDIUM,
				ref_doctype="Work Order",
				ref_name=rec["work_order"],
				status=rec["status"],
				state="Needs attention",
				since=get_datetime(row.modified),
				responsible=assigned_user(row),
				responsible_department="Manufacturing Manager",
				also=(INVENTORY,) if unreconciled else (),
				precedence=40,
				action=Action("open", "Open"),
			)
		)
	return out
