# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Plant maintenance decisions: breakdowns (with their downtime), closures awaiting approval, overdue
preventive maintenance. Built on ERPNext's Asset Repair and Asset Maintenance Log."""

from frappe.utils import get_datetime

from factory_ops.decisions.context import assigned_user
from factory_ops.decisions.model import CRITICAL, HIGH, LOW, MAINTENANCE, MEDIUM, OVERDUE, PRODUCTION, Action, Entry
from factory_ops.decisions.priority import age_hours
from factory_ops.decisions.sources import source

SEVERITY_PRIORITY = {"Critical": CRITICAL, "High": HIGH, "Medium": MEDIUM, "Low": LOW}


@source(
	"breakdowns",
	MAINTENANCE,
	("Asset Repair",),
	{
		"breakdown": "Machine breakdowns",
		"repair_closure": "Breakdown closures awaiting approval",
	},
)
def breakdowns(ctx):
	rows = ctx.rows(
		"Asset Repair",
		{"docstatus": 0, "repair_status": ["in", ["Pending", "Completed"]]},
		["name", "asset", "asset_name", "failure_date", "repair_status", "fo_severity", "fo_technician", "owner", "creation", "description", "_assign"],
		order_by="failure_date asc",
		source="breakdowns",
	)
	can_close = ctx.can("Asset Repair", "submit")
	out = []
	for r in rows:
		since = get_datetime(r.failure_date or r.creation)
		if r.repair_status == "Completed":  # repaired; the manager's closure approval is what is missing
			out.append(
				Entry(
					kind="repair_closure",
					category="Breakdown closures awaiting approval",
					department=MAINTENANCE,
					issue=f"{r.asset_name or r.asset}: repair finished, closure awaiting approval",
					priority=MEDIUM,
					ref_doctype="Asset Repair",
					ref_name=r.name,
					status="Completed (draft)",
					since=since,
					responsible=r.fo_technician or assigned_user(r),
					raised_by=r.owner,
					responsible_department="Maintenance Manager",
					precedence=10,
					action=Action("submit", "Approve closure", can_close, "" if can_close else "Closure approval is limited to the Maintenance Manager."),
				)
			)
			continue
		severity = r.fo_severity or ""
		hours = age_hours(since, ctx.now) or 0.0
		critical = severity == "Critical"
		out.append(
			Entry(
				kind="breakdown",
				category="Machine breakdowns",
				department=MAINTENANCE,
				issue=f"{r.asset_name or r.asset}: {severity or 'unrated'} breakdown, down for {int(hours)} hour(s)"
				+ (f" — {(r.description or '')[:80]}" if r.description else ""),
				priority=SEVERITY_PRIORITY.get(severity, MEDIUM),
				ref_doctype="Asset Repair",
				ref_name=r.name,
				status="Breakdown open",
				state="Needs attention",
				since=since,
				responsible=r.fo_technician or assigned_user(r),
				raised_by=r.owner,
				responsible_department="Maintenance Manager",
				also=(PRODUCTION,) if critical else (),
				precedence=5,
				action=Action("open", "Open"),
			)
		)
	return out


@source("preventive_maintenance", MAINTENANCE, ("Asset Maintenance Log",), {"pm_overdue": "Overdue preventive maintenance"})
def overdue_preventive_maintenance(ctx):
	rows = ctx.rows(
		"Asset Maintenance Log",
		{"docstatus": 0, "maintenance_status": ["in", ["Planned", "Overdue"]], "due_date": ["<", ctx.today]},
		["name", "asset_name", "task_name", "due_date", "assign_to_name", "owner", "creation", "_assign"],
		order_by="due_date asc",
		source="preventive_maintenance",
	)
	out = []
	for r in rows:
		days = (ctx.today - get_datetime(r.due_date).date()).days
		out.append(
			Entry(
				kind="pm_overdue",
				category="Overdue preventive maintenance",
				department=MAINTENANCE,
				issue=f"{r.asset_name}: {r.task_name} is {days} day(s) overdue",
				priority=HIGH,
				ref_doctype="Asset Maintenance Log",
				ref_name=r.name,
				status="Overdue",
				state=OVERDUE,
				since=get_datetime(r.due_date),
				responsible=assigned_user(r),
				raised_by=r.owner,
				responsible_department=r.assign_to_name or "Maintenance Manager",
				overdue=True,
				precedence=20,
				action=Action("open", "Open"),
			)
		)
	return out
