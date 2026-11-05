# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Inventory and stores decisions: reorder levels, expiry, stock discrepancies.

QC-held stock and production-critical shortages are not repeated here: they are one entry each (under
Quality Control, Procurement and Production) that also lists Inventory & Stores in ``also``."""

from datetime import timedelta

import frappe
from frappe.utils import add_days, flt, get_datetime

from factory_ops.decisions.groups import factory_groups_of
from factory_ops.decisions.model import HIGH, INVENTORY, MEDIUM, Action, Entry
from factory_ops.decisions.sources import source


@source("below_reorder", INVENTORY, ("Item", "Bin"), {"below_reorder": "Items below reorder level"})
def items_below_reorder(ctx):
	from factory_ops.setup.dashboard_data import items_below_reorder as rows_below

	rows = rows_below()
	if not rows:
		return []
	by_item: dict[str, list] = {}
	for r in rows:
		by_item.setdefault(r.item_code, []).append(r)
	names = {
		i.name: i.item_name
		for i in frappe.get_all("Item", filters={"name": ["in", list(by_item)]}, fields=["name", "item_name"], limit_page_length=0)
	}
	# honour row-level permission on Item
	allowed = set(frappe.get_list("Item", filters={"name": ["in", list(by_item)]}, pluck="name", limit_page_length=0))
	out = []
	for code, parts in by_item.items():
		if code not in allowed:
			continue
		empty = any(flt(p.actual_qty) <= 0 for p in parts)
		where = "; ".join(f"{p.warehouse}: {flt(p.actual_qty):g} (level {flt(p.reorder_level):g})" for p in parts[:3])
		out.append(
			Entry(
				kind="below_reorder",
				category="Items below reorder level",
				department=INVENTORY,
				issue=f"{names.get(code) or code}: at or below its reorder level — {where}",
				priority=HIGH if empty else MEDIUM,
				ref_doctype="Item",
				ref_name=code,
				status="Below reorder level",
				state="Needs attention",
				since=None,
				responsible_department="Stock Manager",
				also=("Procurement",),
				precedence=40,
				action=Action("open", "Open"),
			)
		)
	return out[: ctx.limit]


@source(
	"expiring_batches",
	INVENTORY,
	("Batch",),
	{"expiry_materials": "Raw materials nearing expiry", "expiry_fg": "Finished goods nearing expiry"},
)
def expiring_batches(ctx):
	window = int(ctx.settings.expiry_window_days)
	rows = ctx.rows(
		"Batch",
		{"disabled": 0, "batch_qty": [">", 0], "expiry_date": ["between", [ctx.today, add_days(ctx.today, window)]]},
		["name", "item", "item_name", "expiry_date", "batch_qty", "creation"],
		order_by="expiry_date asc",
		source="expiring_batches",
	)
	if not rows:
		return []
	groups = factory_groups_of(r.item for r in rows)
	out = []
	for r in rows:
		days_left = (get_datetime(r.expiry_date).date() - ctx.today).days
		finished = groups.get(r.item) == "FG"
		kind = "expiry_fg" if finished else "expiry_materials"
		out.append(
			Entry(
				kind=kind,
				category="Finished goods nearing expiry" if finished else "Raw materials nearing expiry",
				department=INVENTORY,
				issue=f"{r.item_name or r.item} batch {r.name}: {flt(r.batch_qty):g} expire in {days_left} day(s) ({r.expiry_date})",
				priority=HIGH if days_left <= window / 4 else MEDIUM,  # the closest quarter of the configured window
				ref_doctype="Batch",
				ref_name=r.name,
				status="Expiring",
				state="Needs attention",
				since=get_datetime(r.expiry_date) - timedelta(days=window),  # when it entered the window
				responsible_department="Stock Manager",
				also=("Dispatch & Sales",) if finished else ("Production",),
				precedence=30,
				action=Action("open", "Open batch"),
			)
		)
	return out


@source(
	"stock_discrepancies",
	INVENTORY,
	("Bin",),
	{"negative_stock": "Negative stock to correct", "stock_reco_draft": "Stock reconciliations awaiting review"},
)
def stock_discrepancies(ctx):
	out = []
	for b in ctx.rows(
		"Bin",
		{"actual_qty": ["<", 0]},
		["name", "item_code", "warehouse", "actual_qty", "modified"],
		order_by="modified asc",
		source="stock_discrepancies",
	):
		out.append(
			Entry(
				kind="negative_stock",
				category="Negative stock to correct",
				department=INVENTORY,
				issue=f"{b.item_code} in {b.warehouse}: {flt(b.actual_qty):g} (negative stock needs a reconciliation)",
				priority=HIGH,
				ref_doctype="Bin",
				ref_name=b.name,
				status="Negative",
				state="Needs attention",
				since=get_datetime(b.modified),
				responsible_department="Stock Manager",
				precedence=20,
				action=Action("open", "Open"),
			)
		)
	if ctx.can("Stock Reconciliation"):
		can_submit = ctx.can("Stock Reconciliation", "submit")
		for r in ctx.rows(
			"Stock Reconciliation",
			{"docstatus": 0},
			["name", "owner", "creation", "purpose", "_assign"],
			source="stock_discrepancies",
		):
			out.append(
				Entry(
					kind="stock_reco_draft",
					category="Stock reconciliations awaiting review",
					department=INVENTORY,
					issue=f"{r.purpose or 'Stock Reconciliation'} {r.name} drafted and not yet applied",
					priority=MEDIUM,
					ref_doctype="Stock Reconciliation",
					ref_name=r.name,
					status="Draft",
					since=get_datetime(r.creation),
					responsible=(frappe.parse_json(r._assign)[0] if r.get("_assign") and frappe.parse_json(r._assign) else ""),
					raised_by=r.owner,
					responsible_department="Stock Manager",
					precedence=10,
					action=Action("submit", "Apply", can_submit, "" if can_submit else "You do not have permission to apply stock reconciliations."),
				)
			)
	return out
