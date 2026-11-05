# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Dispatch Register: every submitted Delivery Note with its dispatch record (transporter, LR /
AWB, vehicle, packages from Packing Slips), the batches shipped and its billing status."""

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{
			"fieldname": "delivery_note",
			"label": _("Delivery Note"),
			"fieldtype": "Link",
			"options": "Delivery Note",
			"width": 150,
		},
		{"fieldname": "posting_date", "label": _("Date"), "fieldtype": "Date", "width": 95},
		{
			"fieldname": "customer",
			"label": _("Customer"),
			"fieldtype": "Link",
			"options": "Customer",
			"width": 170,
		},
		{"fieldname": "sales_channel", "label": _("Channel"), "fieldtype": "Data", "width": 110},
		{"fieldname": "external_order_id", "label": _("External Order"), "fieldtype": "Data", "width": 120},
		{"fieldname": "transporter", "label": _("Transporter"), "fieldtype": "Data", "width": 140},
		{"fieldname": "lr_no", "label": _("LR / AWB"), "fieldtype": "Data", "width": 110},
		{"fieldname": "vehicle_no", "label": _("Vehicle"), "fieldtype": "Data", "width": 100},
		{"fieldname": "packages", "label": _("Packages"), "fieldtype": "Int", "width": 80},
		{"fieldname": "total_qty", "label": _("Qty"), "fieldtype": "Float", "width": 80},
		{"fieldname": "batches", "label": _("Batches"), "fieldtype": "Data", "width": 200},
		{"fieldname": "grand_total", "label": _("Value"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "per_billed", "label": _("% Billed"), "fieldtype": "Percent", "width": 80},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 100},
	]


def get_data(filters):
	conditions = [
		"dn.docstatus = 1",
		"dn.is_return = 0",
		"dn.company = %(company)s",
		"dn.posting_date between %(from_date)s and %(to_date)s",
	]
	if filters.get("customer"):
		conditions.append("dn.customer = %(customer)s")
	if filters.get("sales_channel"):
		conditions.append("dn.fo_sales_channel = %(sales_channel)s")
	rows = frappe.db.sql(
		f"""
		select dn.name as delivery_note, dn.posting_date, dn.customer, dn.fo_sales_channel as sales_channel,
			dn.fo_external_order_id as external_order_id,
			coalesce(nullif(dn.transporter_name, ''), dn.transporter) as transporter, dn.lr_no, dn.vehicle_no,
			dn.total_qty, dn.grand_total, dn.per_billed, dn.status
		from `tabDelivery Note` dn
		where {" and ".join(conditions)}
		order by dn.posting_date desc, dn.name desc
		""",
		filters,
		as_dict=True,
	)
	for row in rows:
		row.packages = frappe.db.count("Packing Slip", {"delivery_note": row.delivery_note, "docstatus": 1})
		row.batches = ", ".join(
			frappe.db.sql_list(
				"""select distinct sbe.batch_no from `tabSerial and Batch Entry` sbe
				inner join `tabSerial and Batch Bundle` sbb on sbb.name = sbe.parent
				where sbb.voucher_type = 'Delivery Note' and sbb.voucher_no = %s and sbb.is_cancelled = 0
				and sbe.batch_no is not null""",
				row.delivery_note,
			)
		)
	return rows
