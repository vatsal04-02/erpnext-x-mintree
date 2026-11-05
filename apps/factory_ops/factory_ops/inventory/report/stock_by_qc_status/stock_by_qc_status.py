# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Batch-level stock split by warehouse type (QC Hold / Usable / Rejected ...).

Quantities come from submitted Serial and Batch Entries, i.e. the batch side of the stock
ledger; nothing is stored separately.
"""

import frappe
from frappe import _
from frappe.utils import date_diff, flt, nowdate

from factory_ops.inventory.stock_queries import LEDGER_BUNDLE


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"fieldname": "item_code", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 150},
		{"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 180},
		{
			"fieldname": "item_group",
			"label": _("Group"),
			"fieldtype": "Link",
			"options": "Item Group",
			"width": 80,
		},
		{"fieldname": "batch_no", "label": _("Batch"), "fieldtype": "Link", "options": "Batch", "width": 160},
		{"fieldname": "supplier_batch_no", "label": _("Supplier Batch"), "fieldtype": "Data", "width": 120},
		{
			"fieldname": "warehouse",
			"label": _("Warehouse"),
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 160,
		},
		{"fieldname": "warehouse_type", "label": _("Warehouse Type"), "fieldtype": "Data", "width": 110},
		{"fieldname": "qty", "label": _("Qty"), "fieldtype": "Float", "width": 90},
		{"fieldname": "uom", "label": _("UOM"), "fieldtype": "Link", "options": "UOM", "width": 60},
		{"fieldname": "qc_status", "label": _("Batch QC Status"), "fieldtype": "Data", "width": 120},
		{"fieldname": "expiry_date", "label": _("Expiry"), "fieldtype": "Date", "width": 100},
		{"fieldname": "days_to_expiry", "label": _("Days to Expiry"), "fieldtype": "Int", "width": 100},
		{
			"fieldname": "dispatch_eligibility",
			"label": _("Dispatch Eligibility"),
			"fieldtype": "Data",
			"width": 170,
		},
	]


def get_data(filters):
	conditions = [
		"sbb.docstatus = 1",
		"sbb.is_cancelled = 0",
		LEDGER_BUNDLE,
		"sbe.batch_no is not null",
		"w.company = %(company)s",
	]
	values = {"company": filters.company}
	for key, column in (("item_code", "sbb.item_code"), ("warehouse_type", "w.warehouse_type")):
		if filters.get(key):
			conditions.append(f"{column} = %({key})s")
			values[key] = filters.get(key)
	if filters.item_group:
		conditions.append("i.item_group = %(item_group)s")
		values["item_group"] = filters.item_group
	rows = frappe.db.sql(
		f"""
		select sbb.item_code, i.item_name, i.item_group, i.stock_uom as uom, sbe.batch_no, sbe.warehouse,
			w.warehouse_type, sum(sbe.qty) as qty,
			b.fo_supplier_batch_no as supplier_batch_no, b.fo_qc_status as qc_status, b.expiry_date
		from `tabSerial and Batch Entry` sbe
		inner join `tabSerial and Batch Bundle` sbb on sbb.name = sbe.parent
		inner join `tabWarehouse` w on w.name = sbe.warehouse
		inner join `tabItem` i on i.name = sbb.item_code
		inner join `tabBatch` b on b.name = sbe.batch_no
		where {" and ".join(conditions)}
		group by sbb.item_code, sbe.batch_no, sbe.warehouse
		having abs(sum(sbe.qty)) > 0.0000001
		order by sbb.item_code, b.expiry_date, sbe.batch_no
		""",
		values,
		as_dict=True,
	)
	from factory_ops.inventory.batch_eligibility import check_batch, min_shelf_life_days

	today = nowdate()
	min_days = min_shelf_life_days()
	labels = {None: _("Eligible"), "expired": _("Expired"), "short_dated": _("Below minimum shelf life")}
	for r in rows:
		r.qty = flt(r.qty, 3)
		r.days_to_expiry = date_diff(r.expiry_date, today) if r.expiry_date else None
		# the same rule Available to Sell, reservations, Pick Lists and Delivery Notes use (D-034)
		r.dispatch_eligibility = labels[check_batch(r.expiry_date, today, min_days).reason]
	return rows
