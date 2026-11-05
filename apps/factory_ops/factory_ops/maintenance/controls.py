"""Maintenance on ERPNext's own Asset, Asset Maintenance, Asset Maintenance Log and Asset Repair
(MINTREE_MAINTENANCE_MODULE.md). factory_ops adds only what the factory needs on top:

- equipment details and an operating status on Asset (custom fields);
- breakdown severity, reporter, technician, root cause, downtime in hours and a closure approval
  on Asset Repair;
- the next due date of each preventive task, computed with ERPNext's own function (ERPNext fills it
  only in the browser form, so schedules made through the API had none);
- the asset's operating status follows its open breakdowns.

Spare parts are ordinary stock Items: an Asset Repair's consumed items are issued from stock by
ERPNext itself (a Material Issue Stock Entry, posted with the closing user's rights).
"""

import frappe
from frappe import _
from frappe.utils import flt, get_datetime, now_datetime, time_diff_in_hours

ROLE_MAINT_MANAGER = "Maintenance Manager"
ROLE_MAINT_USER = "Maintenance User"
OPEN_REPAIR = {"docstatus": 0, "repair_status": "Pending"}


def set_next_due_dates(doc, method=None):
	"""Asset Maintenance: every planned task gets its next due date (ERPNext's calculation)."""
	from erpnext.assets.doctype.asset_maintenance.asset_maintenance import calculate_next_due_date

	for task in doc.get("asset_maintenance_tasks") or []:
		if task.maintenance_status == "Planned" and not task.next_due_date and task.start_date and task.periodicity:
			task.next_due_date = calculate_next_due_date(
				periodicity=task.periodicity,
				start_date=task.start_date,
				end_date=task.end_date,
				last_completion_date=task.last_completion_date,
			)


def fill_spare_rates(doc, method=None):
	"""Asset Repair (before_validate): each consumed spare part's rate from its current stock
	valuation, so ERPNext's own cost total and stock issue carry a value (ERPNext fills the rate
	only in the browser form)."""
	for row in doc.get("stock_items") or []:
		if row.item_code and row.warehouse and not flt(row.valuation_rate):
			row.valuation_rate = flt(
				frappe.db.get_value("Bin", {"item_code": row.item_code, "warehouse": row.warehouse}, "valuation_rate")
			) or flt(frappe.db.get_value("Item", row.item_code, "valuation_rate"))


def validate_repair(doc, method=None):
	"""Asset Repair (breakdown): severity and reporter; downtime in hours from failure to completion."""
	if not doc.get("fo_severity"):
		frappe.throw(_("Set the breakdown severity."))
	if not doc.get("fo_reported_by"):
		doc.fo_reported_by = frappe.session.user
	if doc.completion_date and doc.failure_date:
		# ERPNext refuses a completion before the failure with its own message
		doc.fo_downtime_hours = flt(time_diff_in_hours(doc.completion_date, doc.failure_date), 2)
	else:
		doc.fo_downtime_hours = 0
	if doc.repair_status == "Completed":
		check_ready_to_close(doc)


def check_ready_to_close(doc):
	"""A repair marked Completed carries its resolution, root cause, technician and completion time."""
	missing = [
		label
		for field, label in (
			("completion_date", _("completion time")),
			("actions_performed", _("resolution (actions performed)")),
			("fo_root_cause", _("root cause")),
			("fo_technician", _("technician")),
		)
		if not doc.get(field)
	]
	if missing:
		frappe.throw(_("Before closing, record the {0}.").format(", ".join(missing)), title=_("Breakdown not ready to close"))


def before_submit_repair(doc, method=None):
	"""Submitting a completed repair is the closure approval, by a Maintenance Manager only."""
	roles = set(frappe.get_roles())
	if not roles & {ROLE_MAINT_MANAGER, "System Manager"}:
		frappe.throw(_("Only a Maintenance Manager may approve the closure of a breakdown."), frappe.PermissionError)
	check_ready_to_close(doc)
	doc.fo_closure_approved_by = frappe.session.user
	doc.fo_closure_approved_on = now_datetime()


def after_repair_change(doc, method=None):
	"""The asset is in Breakdown while any breakdown on it is open; Active again once all are closed."""
	if not doc.asset:
		return
	current = frappe.db.get_value("Asset", doc.asset, "fo_operational_status")
	if current == "Retired":
		return
	open_count = frappe.db.count("Asset Repair", {"asset": doc.asset, **OPEN_REPAIR})
	if open_count:
		new = "Breakdown"
	elif current in ("Breakdown", None, ""):
		new = "Active"
	else:
		new = current
	if new != current:
		frappe.db.set_value("Asset", doc.asset, "fo_operational_status", new)
