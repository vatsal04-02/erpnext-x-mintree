# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Available to Sell: sellable stock minus active reservations, for sales items.

Computed by ``factory_ops.sales_ops.available_to_sell.stock_positions``, the same function the
reservation check uses.
"""

import frappe
from frappe import _
from frappe.utils import flt

from factory_ops.sales_ops.available_to_sell import min_shelf_life_days, stock_positions

QTY_FIELDS = ("sellable", "reserved", "picked", "available", "short_dated", "expired", "qc_hold", "rejected")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(filters), get_data(filters)


def get_columns(filters):
	columns = [
		{"fieldname": "item_code", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 150},
		{"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 180},
	]
	if filters.get("by_warehouse"):
		columns.append(
			{
				"fieldname": "warehouse",
				"label": _("Warehouse"),
				"fieldtype": "Link",
				"options": "Warehouse",
				"width": 160,
			}
		)
	labels = {
		"sellable": _("Sellable"),
		"reserved": _("Reserved"),
		"picked": _("Picked, not reserved"),
		"available": _("Available to Sell"),
		"short_dated": _("Short-dated (< {0} days)").format(min_shelf_life_days()),
		"expired": _("Expired"),
		"qc_hold": _("In QC Hold"),
		"rejected": _("Rejected"),
	}
	columns += [{"fieldname": f, "label": labels[f], "fieldtype": "Float", "width": 110} for f in QTY_FIELDS]
	columns.append(
		{"fieldname": "stock_uom", "label": _("UOM"), "fieldtype": "Link", "options": "UOM", "width": 60}
	)
	return columns


def get_data(filters):
	positions = stock_positions(filters.company, filters.get("item_code"))
	sales_items = set(
		frappe.get_all(
			"Item",
			filters={
				"is_sales_item": 1,
				**({"item_group": filters.item_group} if filters.get("item_group") else {}),
			},
			pluck="name",
		)
	)
	rows: dict = {}
	for (item_code, warehouse), pos in positions.items():
		if item_code not in sales_items:
			continue
		key = (item_code, warehouse) if filters.get("by_warehouse") else (item_code,)
		row = rows.setdefault(
			key, {"item_code": item_code, "warehouse": warehouse, **{f: 0.0 for f in QTY_FIELDS}}
		)
		for field in QTY_FIELDS:
			row[field] += flt(pos[field])
	for row in rows.values():
		row["item_name"], row["stock_uom"] = frappe.get_cached_value(
			"Item", row["item_code"], ["item_name", "stock_uom"]
		)
	return sorted(rows.values(), key=lambda r: (r["item_code"], r.get("warehouse") or ""))
