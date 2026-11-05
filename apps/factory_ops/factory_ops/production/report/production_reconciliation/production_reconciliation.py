# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Production Reconciliation: planned vs produced, yield, consumption variance and WIP balance per
Work Order (or per component with "Show components"). Definitions: ``factory_ops.production.reconciliation``.
"""

import frappe
from frappe import _

from factory_ops.production.reconciliation import list_reconciliations


def execute(filters=None):
	filters = frappe._dict(filters or {})
	data = list_reconciliations(filters)
	if filters.get("only_problems"):
		data = [d for d in data if d["reconciliation"] == "Unreconciled" or d["below_min_yield"]]
	if filters.get("detail"):
		return detail_columns(), detail_rows(data)
	return summary_columns(), summary_rows(data)


def summary_rows(data):
	return [{k: v for k, v in d.items() if k != "items"} for d in data]


def detail_rows(data):
	rows = []
	for d in data:
		head = {k: d[k] for k in ("work_order", "production_item", "status", "reconciliation", "yield_pct")}
		for line in d["items"]:
			rows.append({**head, **line})
	return rows


def summary_columns():
	return [
		{"fieldname": "work_order", "label": _("Work Order"), "fieldtype": "Link", "options": "Work Order", "width": 140},
		{"fieldname": "production_item", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 150},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 100},
		{"fieldname": "planned_qty", "label": _("Planned"), "fieldtype": "Float", "width": 90},
		{"fieldname": "produced_qty", "label": _("Produced"), "fieldtype": "Float", "width": 90},
		{"fieldname": "process_loss_qty", "label": _("Process loss"), "fieldtype": "Float", "width": 95},
		{"fieldname": "completion_pct", "label": _("Completion %"), "fieldtype": "Percent", "width": 100},
		{"fieldname": "yield_pct", "label": _("Yield %"), "fieldtype": "Percent", "width": 85},
		{"fieldname": "below_min_yield", "label": _("Below min yield"), "fieldtype": "Check", "width": 100},
		{"fieldname": "reconciliation", "label": _("Reconciliation"), "fieldtype": "Data", "width": 115},
		{"fieldname": "wastage_value", "label": _("Wastage value"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "sales_order", "label": _("Sales Order"), "fieldtype": "Link", "options": "Sales Order", "width": 130},
	]


def detail_columns():
	return [
		{"fieldname": "work_order", "label": _("Work Order"), "fieldtype": "Link", "options": "Work Order", "width": 140},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 90},
		{"fieldname": "item_code", "label": _("Component"), "fieldtype": "Link", "options": "Item", "width": 150},
		{"fieldname": "uom", "label": _("UOM"), "fieldtype": "Link", "options": "UOM", "width": 60},
		{"fieldname": "required_qty", "label": _("Required"), "fieldtype": "Float", "width": 90},
		{"fieldname": "standard_qty", "label": _("Standard for output"), "fieldtype": "Float", "width": 120},
		{"fieldname": "transferred_qty", "label": _("Transferred"), "fieldtype": "Float", "width": 95},
		{"fieldname": "consumed_qty", "label": _("Consumed"), "fieldtype": "Float", "width": 90},
		{"fieldname": "returned_qty", "label": _("Returned"), "fieldtype": "Float", "width": 85},
		{"fieldname": "wastage_qty", "label": _("Wastage"), "fieldtype": "Float", "width": 85},
		{"fieldname": "variance_qty", "label": _("Variance"), "fieldtype": "Float", "width": 90},
		{"fieldname": "variance_pct", "label": _("Variance %"), "fieldtype": "Percent", "width": 90},
		{"fieldname": "balance_qty", "label": _("WIP balance"), "fieldtype": "Float", "width": 95},
		{"fieldname": "reconciliation", "label": _("Reconciliation"), "fieldtype": "Data", "width": 110},
	]
