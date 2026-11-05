# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Quality Control decisions.

QC approval stays in the QC documents: an entry here names the batch or the draft QC Release and links
to it. The Decision Centre never releases, rejects or approves a batch; only QC Officer and QA Manager
can, in the QC Release form, through the existing guards and segregation of duties."""

import frappe
from frappe.utils import get_datetime

from factory_ops.decisions import settings
from factory_ops.decisions.context import assigned_user
from factory_ops.decisions.groups import factory_groups_of
from factory_ops.decisions.model import (
	HIGH,
	INVENTORY,
	MEDIUM,
	OVERDUE,
	PRODUCTION,
	QUALITY,
	Action,
	Entry,
)
from factory_ops.decisions.priority import age_hours
from factory_ops.decisions.sources import source

KIND_BY_GROUP = {"RM": "qc_incoming", "PM": "qc_incoming", "BULK": "qc_bulk", "FG": "qc_fg"}
LABELS = {
	"qc_incoming": "Incoming QC awaiting inspection or decision",
	"qc_bulk": "Bulk QC awaiting approval",
	"qc_fg": "Finished goods QC awaiting approval",
}
QC_KINDS = tuple(LABELS)  # the "QC approvals pending" family


@source("qc_held_batches", QUALITY, ("Batch",), {**LABELS, "qc_rejected": "Rejected batches"})
def held_batches(ctx):
	try:
		batches = ctx.rows(
			"Batch",
			{"disabled": 0, "batch_qty": [">", 0], "fo_qc_status": ["in", ["Hold", "Partly Released"]]},
			["name", "item", "item_name", "creation", "batch_qty", "fo_qc_status", "expiry_date"],
			source="qc_held_batches",
		)
	except frappe.PermissionError:  # fo_qc_status is a permission-level-1 field: no level-1 read, no QC view
		ctx.skipped.append("qc_held_batches")
		return []
	if not batches:
		return []
	drafts = {}
	if ctx.can("QC Release"):
		for r in ctx.rows(
			"QC Release",
			{"docstatus": 0, "batch_no": ["in", [b.name for b in batches]]},
			["name", "batch_no", "decision", "owner", "creation", "_assign"],
			source="qc_held_batches",
		):
			drafts[r.batch_no] = r
	groups = factory_groups_of(b.item for b in batches)
	sla = settings.qc_sla_hours()
	may_decide = ctx.can("QC Release", "submit")
	out = []
	for b in batches:
		group = groups.get(b.item)
		kind = KIND_BY_GROUP.get(group, "qc_incoming")
		since = get_datetime(b.creation)
		hours = age_hours(since, ctx.now) or 0.0
		target = sla.get(group or "RM", 0)
		late = bool(target and hours > target)
		draft = drafts.get(b.name)
		stage = f"decision drafted ({draft.decision})" if draft else "awaiting inspection"
		out.append(
			Entry(
				kind=kind,
				category=LABELS[kind],
				department=QUALITY,
				issue=f"{b.item_name or b.item} batch {b.name}: {stage}, {b.batch_qty:g} on hold"
				+ (f", {int(hours - target)} h past the {target} h QC target" if late else ""),
				priority=HIGH if late else MEDIUM,
				ref_doctype="QC Release" if draft else "Batch",
				ref_name=draft.name if draft else b.name,
				status=b.fo_qc_status,
				state=OVERDUE if late else "Pending",
				since=since,
				responsible=assigned_user(draft) if draft else "",
				responsible_department="QC Officer / QA Manager",
				also=(INVENTORY, PRODUCTION) if kind == "qc_bulk" else (INVENTORY,),
				overdue=late,
				precedence=10,
				action=Action(
					"open",
					"Open QC Release" if draft else "Open batch",
					True,
					"" if may_decide else "QC approval is limited to QC Officer and QA Manager.",
				),
			)
		)
	rejected = ctx.rows(
		"Batch",
		{"disabled": 0, "batch_qty": [">", 0], "fo_qc_status": "Rejected"},
		["name", "item", "item_name", "creation", "batch_qty", "fo_qc_status"],
		source="qc_held_batches",
	)
	for b in rejected:
		out.append(
			Entry(
				kind="qc_rejected",
				category="Rejected batches",
				department=QUALITY,
				issue=f"{b.item_name or b.item} batch {b.name}: rejected, {b.batch_qty:g} still on hand awaiting a disposal decision",
				priority=MEDIUM,
				ref_doctype="Batch",
				ref_name=b.name,
				status=b.fo_qc_status,
				since=get_datetime(b.creation),
				responsible_department="QA Manager",
				also=(INVENTORY,),
				precedence=20,
				action=Action("open", "Open batch"),
			)
		)
	return out


@source("qc_deviations", QUALITY, ("QC Deviation",), {"qc_deviation": "Open quality deviations"})
def open_deviations(ctx):
	rows = ctx.rows(
		"QC Deviation",
		{"docstatus": 0},
		["name", "item_code", "item_name", "batch_no", "owner", "creation", "description", "_assign"],
		source="qc_deviations",
	)
	can_approve = ctx.can("QC Deviation", "submit")
	return [
		Entry(
			kind="qc_deviation",
			category="Open quality deviations",
			department=QUALITY,
			issue=f"{r.item_name or r.item_code or ''} {('batch ' + r.batch_no) if r.batch_no else ''}: deviation awaiting approval"
			+ (f" — {(r.description or '')[:80]}" if r.description else ""),
			priority=MEDIUM,
			ref_doctype="QC Deviation",
			ref_name=r.name,
			status="Draft",
			since=get_datetime(r.creation),
			responsible=assigned_user(r),
			raised_by=r.owner,
			responsible_department="QA Manager",
			precedence=10,
			action=Action(
				"submit", "Approve deviation", can_approve, "" if can_approve else "Deviation approval is limited to the QA Manager."
			),
		)
		for r in rows
	]


@source("capa_overdue", QUALITY, ("Quality Action",), {"capa_overdue": "CAPA actions overdue"})
def overdue_corrective_actions(ctx):
	rows = ctx.rows(
		"Quality Action Resolution",
		{"status": "Open", "completion_by": ["<", ctx.today]},
		["name", "parent", "problem", "responsible", "completion_by"],
		order_by="completion_by asc",
		parent_doctype="Quality Action",
		source="capa_overdue",
	)
	seen, out = set(), []
	for r in rows:  # one entry per Quality Action: its oldest overdue step
		if r.parent in seen:
			continue
		seen.add(r.parent)
		days = (ctx.today - get_datetime(r.completion_by).date()).days
		out.append(
			Entry(
				kind="capa_overdue",
				category="CAPA actions overdue",
				department=QUALITY,
				issue=f"Corrective/preventive action {r.parent}: {days} day(s) past its completion date"
				+ (f" — {(r.problem or '')[:80]}" if r.problem else ""),
				priority=HIGH,
				ref_doctype="Quality Action",
				ref_name=r.parent,
				status="Open",
				state=OVERDUE,
				since=get_datetime(r.completion_by),
				responsible=r.responsible or "",
				responsible_department="QA Manager",
				overdue=True,
				precedence=10,
				action=Action("open", "Open"),
			)
		)
	return out
