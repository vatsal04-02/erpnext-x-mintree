# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Batch Genealogy: trace a batch back to its receipts and forward to where it went.

All logic is in ``factory_ops.production.genealogy`` (read-only, permission-masked); see its
docstring for the attribution rule and what cannot be traced.
"""

import frappe
from frappe import _

from factory_ops.production.genealogy import trace_back, trace_forward


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("batch_no"):
		frappe.throw(_("Choose a batch."))
	direction = filters.get("direction") or "Both"
	depth = max(1, min(int(filters.get("max_depth") or 10), 25))
	rows, truncated = [], False
	if direction in ("Backward", "Both"):
		res = trace_back(filters.batch_no, depth)
		rows += _label(res["rows"], _("Came from"))
		truncated |= res["truncated"]
	if direction in ("Forward", "Both"):
		res = trace_forward(filters.batch_no, depth)
		rows += _label(res["rows"], _("Went to"))
		truncated |= res["truncated"]
	message = _("Result truncated: raise the depth or narrow the batch.") if truncated else None
	return columns(), rows, message


def _label(rows: list[dict], section: str) -> list[dict]:
	for r in rows:
		r["section"] = section
	return rows


def columns():
	return [
		{"fieldname": "section", "label": _("Direction"), "fieldtype": "Data", "width": 90},
		{"fieldname": "level", "label": _("Level"), "fieldtype": "Int", "width": 55},
		{"fieldname": "batch_no", "label": _("Batch"), "fieldtype": "Link", "options": "Batch", "width": 150},
		{"fieldname": "item_code", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 150},
		{"fieldname": "qty", "label": _("Qty"), "fieldtype": "Float", "width": 90},
		{"fieldname": "event", "label": _("Event"), "fieldtype": "Data", "width": 160},
		{
			"fieldname": "voucher_no",
			"label": _("Document"),
			"fieldtype": "Dynamic Link",
			"options": "voucher_type",
			"width": 160,
		},
		{"fieldname": "voucher_type", "label": _("Document type"), "fieldtype": "Data", "hidden": 1},
		{"fieldname": "posting_date", "label": _("Date"), "fieldtype": "Date", "width": 95},
		{"fieldname": "party", "label": _("Supplier / Customer"), "fieldtype": "Data", "width": 160},
		{"fieldname": "supplier_batch_no", "label": _("Supplier batch"), "fieldtype": "Data", "width": 120},
		{
			"fieldname": "work_order",
			"label": _("Work Order"),
			"fieldtype": "Link",
			"options": "Work Order",
			"width": 130,
		},
		{"fieldname": "manufacturing_date", "label": _("Mfg"), "fieldtype": "Date", "width": 90},
		{"fieldname": "expiry_date", "label": _("Expiry"), "fieldtype": "Date", "width": 90},
		{"fieldname": "qc_status", "label": _("QC status"), "fieldtype": "Data", "width": 90},
	]
