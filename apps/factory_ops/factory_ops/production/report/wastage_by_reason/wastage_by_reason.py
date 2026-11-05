# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Wastage by Reason: every row of a submitted wastage write-off (Stock Entry Type flagged
"Wastage write-off", D-023) with its quantity, batch and value.

The value is the row's stock-ledger value change, so the total equals the debit to the Wastage
Expense Account in the General Ledger for the same entries (verified by tests). Process loss inside
manufacturing is not wastage: see ERPNext's Process Loss Report.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	rows = get_rows(filters)
	if filters.get("summary"):
		return summary_columns(), summarise(rows)
	return columns(), rows


def columns():
	return [
		{"fieldname": "posting_date", "label": _("Date"), "fieldtype": "Date", "width": 95},
		{
			"fieldname": "stock_entry",
			"label": _("Stock Entry"),
			"fieldtype": "Link",
			"options": "Stock Entry",
			"width": 150,
		},
		{
			"fieldname": "wastage_reason",
			"label": _("Reason"),
			"fieldtype": "Link",
			"options": "Wastage Reason",
			"width": 170,
		},
		{"fieldname": "item_code", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 150},
		{
			"fieldname": "item_group",
			"label": _("Group"),
			"fieldtype": "Link",
			"options": "Item Group",
			"width": 80,
		},
		{"fieldname": "batch_no", "label": _("Batch"), "fieldtype": "Data", "width": 160},
		{
			"fieldname": "warehouse",
			"label": _("From Warehouse"),
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 150,
		},
		{"fieldname": "qty", "label": _("Qty"), "fieldtype": "Float", "width": 80},
		{"fieldname": "uom", "label": _("UOM"), "fieldtype": "Link", "options": "UOM", "width": 60},
		{"fieldname": "value", "label": _("Value"), "fieldtype": "Currency", "width": 110},
	]


def summary_columns():
	return [
		{
			"fieldname": "wastage_reason",
			"label": _("Reason"),
			"fieldtype": "Link",
			"options": "Wastage Reason",
			"width": 200,
		},
		{"fieldname": "entries", "label": _("Rows"), "fieldtype": "Int", "width": 80},
		{"fieldname": "value", "label": _("Value"), "fieldtype": "Currency", "width": 130},
	]


def get_rows(filters):
	conditions = [
		"se.docstatus = 1",
		"t.fo_is_wastage = 1",
		"se.company = %(company)s",
		"se.posting_date between %(from_date)s and %(to_date)s",
	]
	if filters.get("wastage_reason"):
		conditions.append("sed.fo_wastage_reason = %(wastage_reason)s")
	if filters.get("item_code"):
		conditions.append("sed.item_code = %(item_code)s")
	rows = frappe.db.sql(
		f"""
		select se.posting_date, se.name as stock_entry, sed.fo_wastage_reason as wastage_reason,
			sed.item_code, i.item_group, sed.s_warehouse as warehouse, sed.transfer_qty as qty,
			sed.stock_uom as uom, sed.name as detail,
			(select -sum(sle.stock_value_difference) from `tabStock Ledger Entry` sle
				where sle.voucher_detail_no = sed.name and sle.is_cancelled = 0) as value
		from `tabStock Entry Detail` sed
		inner join `tabStock Entry` se on se.name = sed.parent
		inner join `tabStock Entry Type` t on t.name = se.stock_entry_type
		inner join `tabItem` i on i.name = sed.item_code
		where {" and ".join(conditions)}
		order by se.posting_date desc, se.name, sed.idx
		""",
		filters,
		as_dict=True,
	)
	for r in rows:
		r.value = flt(r.value, 2)
		r.batch_no = ", ".join(
			frappe.db.sql_list(
				"""select distinct sbe.batch_no from `tabSerial and Batch Entry` sbe
				inner join `tabSerial and Batch Bundle` sbb on sbb.name = sbe.parent
				where sbb.voucher_detail_no = %s and sbb.is_cancelled = 0 and sbe.batch_no is not null""",
				r.pop("detail"),
			)
		)
	return rows


def summarise(rows):
	by_reason = defaultdict(lambda: {"entries": 0, "value": 0.0})
	for r in rows:
		by_reason[r.wastage_reason]["entries"] += 1
		by_reason[r.wastage_reason]["value"] += flt(r.value)
	return [
		{"wastage_reason": reason, "entries": v["entries"], "value": flt(v["value"], 2)}
		for reason, v in sorted(by_reason.items(), key=lambda kv: -kv[1]["value"])
	]
