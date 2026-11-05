# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""QC Queue: every batch with stock in a QC Hold warehouse, longest-waiting first."""

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime, time_diff_in_hours

from factory_ops.inventory.stock_queries import LEDGER_BUNDLE

SLA_FIELD_BY_PREFIX = (
	("RM-", "qc_sla_hours_rm"),
	("PM-", "qc_sla_hours_pm"),
	("BULK-", "qc_sla_hours_bulk"),
	("FG-", "qc_sla_hours_fg"),
	("RETURNS-", "qc_sla_hours_fg"),
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"fieldname": "batch_no", "label": _("Batch"), "fieldtype": "Link", "options": "Batch", "width": 140},
		{"fieldname": "item_code", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 160},
		{"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 180},
		{
			"fieldname": "warehouse",
			"label": _("QC Hold Warehouse"),
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 150,
		},
		{"fieldname": "qty", "label": _("Qty in Hold"), "fieldtype": "Float", "width": 100},
		{"fieldname": "uom", "label": _("UOM"), "fieldtype": "Link", "options": "UOM", "width": 60},
		{"fieldname": "received_on", "label": _("Received"), "fieldtype": "Datetime", "width": 150},
		{
			"fieldname": "age_hours",
			"label": _("Waiting (h)"),
			"fieldtype": "Float",
			"precision": 1,
			"width": 90,
		},
		{"fieldname": "sla_hours", "label": _("SLA (h)"), "fieldtype": "Int", "width": 70},
		{"fieldname": "overdue", "label": _("Overdue"), "fieldtype": "Check", "width": 70},
		{"fieldname": "expiry_date", "label": _("Expiry"), "fieldtype": "Date", "width": 100},
		{
			"fieldname": "origin",
			"label": _("Received / Produced By"),
			"fieldtype": "Dynamic Link",
			"options": "origin_type",
			"width": 150,
		},
		{"fieldname": "origin_type", "label": _("Origin Type"), "fieldtype": "Data", "hidden": 1},
		{
			"fieldname": "open_release",
			"label": _("Open QC Release"),
			"fieldtype": "Link",
			"options": "QC Release",
			"width": 130,
		},
	]


def get_data(filters):
	conditions = ["w.warehouse_type = 'QC Hold'", "sbb.docstatus = 1", "sbb.is_cancelled = 0", LEDGER_BUNDLE]
	values = {}
	if filters.company:
		conditions.append("w.company = %(company)s")
		values["company"] = filters.company
	if filters.warehouse:
		conditions.append("sbe.warehouse = %(warehouse)s")
		values["warehouse"] = filters.warehouse

	rows = frappe.db.sql(
		f"""
		select sbe.batch_no, sbe.warehouse, sbb.item_code, sum(sbe.qty) as qty,
			min(case when sbb.type_of_transaction = 'Inward' then sbb.posting_datetime end) as received_on
		from `tabSerial and Batch Entry` sbe
		inner join `tabSerial and Batch Bundle` sbb on sbb.name = sbe.parent
		inner join `tabWarehouse` w on w.name = sbe.warehouse
		where {" and ".join(conditions)} and sbe.batch_no is not null
		group by sbe.batch_no, sbe.warehouse, sbb.item_code
		having sum(sbe.qty) > 0
		""",
		values,
		as_dict=True,
	)

	settings = frappe.get_cached_doc("Factory Ops Settings")
	now = now_datetime()
	data = []
	for r in rows:
		item = frappe.get_cached_value("Item", r.item_code, ["item_name", "stock_uom"], as_dict=True)
		wh_name = frappe.get_cached_value("Warehouse", r.warehouse, "warehouse_name") or ""
		sla = next((cint(settings.get(f)) for p, f in SLA_FIELD_BY_PREFIX if wh_name.startswith(p)), 0)
		age = flt(time_diff_in_hours(now, r.received_on), 1) if r.received_on else 0
		origin = (
			frappe.db.get_value(
				"Serial and Batch Bundle",
				{
					"type_of_transaction": "Inward",
					"docstatus": 1,
					"is_cancelled": 0,
					"name": (
						"in",
						frappe.get_all("Serial and Batch Entry", {"batch_no": r.batch_no}, pluck="parent"),
					),
				},
				["voucher_type", "voucher_no"],
				order_by="posting_datetime asc",
				as_dict=True,
			)
			or {}
		)
		row = {
			**r,
			"item_name": item.item_name,
			"uom": item.stock_uom,
			"age_hours": age,
			"sla_hours": sla,
			"overdue": 1 if sla and age > sla else 0,
			"expiry_date": frappe.db.get_value("Batch", r.batch_no, "expiry_date"),
			"origin_type": origin.get("voucher_type"),
			"origin": origin.get("voucher_no"),
			"open_release": frappe.db.get_value(
				"QC Release",
				{"batch_no": r.batch_no, "source_warehouse": r.warehouse, "docstatus": 0},
				"name",
			),
		}
		if filters.overdue_only and not row["overdue"]:
			continue
		data.append(row)
	data.sort(key=lambda d: (-d["overdue"], -d["age_hours"]))
	return data
