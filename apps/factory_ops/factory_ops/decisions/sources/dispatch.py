# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Dispatch and sales decisions: late and due orders, orders blocked by stock or QC, complaints, returns.

"Blocked" uses the one enforced stock rule (available-to-sell: released, in date, unreserved, usable
stock only), so an order held up by QC Hold, expiry or reservation is shown with its real cause. Only
orders already late or due soon are evaluated, and each distinct item/warehouse is evaluated once."""

import frappe
from frappe.utils import add_days, flt, get_datetime

from factory_ops.decisions.context import assigned_user
from factory_ops.decisions.model import CRITICAL, DISPATCH, HIGH, INVENTORY, MEDIUM, OVERDUE, QUALITY, Action, Entry
from factory_ops.decisions.sources import source

OPEN_SO = ["To Deliver and Bill", "To Deliver"]
MAX_STOCK_CHECKS = 60  # distinct (company, item, warehouse) evaluated per refresh
HIGH_PRIORITY_ISSUES = ("high", "urgent", "critical")


@source(
	"sales_order_dispatch",
	DISPATCH,
	("Sales Order",),
	{
		"order_overdue": "Orders past delivery date",
		"order_due_soon": "Orders awaiting dispatch",
		"order_blocked": "Orders blocked by stock or QC restrictions",
	},
)
def sales_orders(ctx):
	window = int(ctx.settings.dispatch_window_days)
	rows = ctx.rows(
		"Sales Order",
		{"docstatus": 1, "status": ["in", OPEN_SO], "delivery_date": ["<=", add_days(ctx.today, window)]},
		["name", "customer", "delivery_date", "base_grand_total", "per_delivered", "owner", "creation", "company", "status", "_assign"],
		order_by="delivery_date asc",
		source="sales_order_dispatch",
	)
	if not rows:
		return []
	lines = frappe.get_all(
		"Sales Order Item",
		filters={"parent": ["in", [r.name for r in rows]]},
		fields=["parent", "item_code", "warehouse", "qty", "delivered_qty"],
		limit_page_length=0,
	)
	from factory_ops.sales_ops.available_to_sell import available_to_sell

	cache: dict[tuple, float] = {}
	company = {r.name: r.company for r in rows}
	blockers: dict[str, list] = {}
	for ln in lines:
		need = flt(ln.qty) - flt(ln.delivered_qty)
		if need <= 0 or not ln.item_code:
			continue
		key = (company[ln.parent], ln.item_code, ln.warehouse)
		if key not in cache:
			if len(cache) >= MAX_STOCK_CHECKS:
				ctx.truncated.append("sales_order_dispatch")
				continue
			cache[key] = flt(available_to_sell(*key))
		if need > cache[key]:
			blockers.setdefault(ln.parent, []).append((ln.item_code, need, cache[key]))
	out = []
	for r in rows:
		due = get_datetime(r.delivery_date).date()
		late = due < ctx.today
		blocked = blockers.get(r.name)
		if blocked:
			shown = "; ".join(f"{i} needs {n:g}, only {a:g} available to sell" for i, n, a in blocked[:3])
			kind, label, issue = "order_blocked", "Orders blocked by stock or QC restrictions", f"{r.customer}: cannot ship — {shown}"
		elif late:
			kind, label = "order_overdue", "Orders past delivery date"
			issue = f"{r.customer}: {(ctx.today - due).days} day(s) past the delivery date, {int(flt(r.per_delivered))}% delivered"
		else:
			kind, label = "order_due_soon", "Orders awaiting dispatch"
			issue = f"{r.customer}: due {r.delivery_date}, {int(flt(r.per_delivered))}% delivered"
		out.append(
			Entry(
				kind=kind,
				category=label,
				department=DISPATCH,
				issue=issue,
				priority=(CRITICAL if blocked and late else HIGH) if (blocked or late) else MEDIUM,
				ref_doctype="Sales Order",
				ref_name=r.name,
				status=r.status,
				state="Blocked" if blocked else (OVERDUE if late else "Pending"),
				since=get_datetime(r.delivery_date) if late else get_datetime(r.creation),
				responsible=assigned_user(r),
				raised_by=r.owner,
				responsible_department="Sales Manager / Dispatch",
				also=(INVENTORY, QUALITY) if blocked else (),
				amount=flt(r.base_grand_total),
				overdue=late,
				blocker=False,
				precedence=5 if blocked else (20 if late else 40),
				action=Action("open", "Open"),
			)
		)
	return out


@source("customer_complaints", DISPATCH, ("Issue",), {"complaint_escalation": "Customer complaints needing escalation"})
def complaints(ctx):
	rows = ctx.rows(
		"Issue",
		{"status": ["in", ["Open", "Replied", "On Hold"]]},
		["name", "subject", "priority", "customer", "creation", "status", "owner", "_assign"],
		source="customer_complaints",
	)
	out = []
	for r in rows:
		level = (r.priority or "").strip().lower()
		if level not in HIGH_PRIORITY_ISSUES:
			continue
		out.append(
			Entry(
				kind="complaint_escalation",
				category="Customer complaints needing escalation",
				department=DISPATCH,
				issue=f"{r.customer or 'Customer'}: {r.subject}",
				priority=CRITICAL if level in ("urgent", "critical") else HIGH,
				ref_doctype="Issue",
				ref_name=r.name,
				status=r.status,
				state="Needs attention",
				since=get_datetime(r.creation),
				responsible=assigned_user(r),
				raised_by=r.owner,
				responsible_department="Sales Manager / QA Manager",
				also=(QUALITY,),
				precedence=10,
				action=Action("open", "Open"),
			)
		)
	return out


@source(
	"customer_returns",
	DISPATCH,
	("Delivery Note",),
	{"return_pending": "Customer returns awaiting processing", "marketplace_return": "Marketplace returns in progress"},
)
def returns(ctx):
	out = []
	for r in ctx.rows(
		"Delivery Note",
		{"docstatus": 0, "is_return": 1},
		["name", "customer", "creation", "owner", "grand_total", "_assign"],
		source="customer_returns",
	):
		out.append(
			Entry(
				kind="return_pending",
				category="Customer returns awaiting processing",
				department=DISPATCH,
				issue=f"{r.customer}: return {r.name} drafted and not processed (returned stock goes to QC, not to usable stock)",
				priority=MEDIUM,
				ref_doctype="Delivery Note",
				ref_name=r.name,
				status="Draft",
				since=get_datetime(r.creation),
				responsible=assigned_user(r),
				raised_by=r.owner,
				responsible_department="Dispatch",
				also=(QUALITY,),
				amount=flt(r.grand_total),
				precedence=10,
				action=Action("open", "Open"),
			)
		)
	if frappe.db.exists("DocType", "Marketplace Return") and ctx.can("Marketplace Return"):
		for r in ctx.rows(
			"Marketplace Return",
			{"status": ["in", ["Requested", "In transit", "Received"]]},
			["name", "status", "creation", "owner", "_assign"],
			source="customer_returns",
		):
			out.append(
				Entry(
					kind="marketplace_return",
					category="Marketplace returns in progress",
					department=DISPATCH,
					issue=f"Marketplace return {r.name}: {r.status}",
					priority=MEDIUM,
					ref_doctype="Marketplace Return",
					ref_name=r.name,
					status=r.status,
					since=get_datetime(r.creation),
					responsible=assigned_user(r),
					raised_by=r.owner,
					responsible_department="Dispatch",
					precedence=10,
					action=Action("open", "Open"),
				)
			)
	return out
