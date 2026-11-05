# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""RM / PM Requirements: what open Work Orders still need, against stock that production can use.

ERPNext's projected-quantity reports count stock in QC Hold as available. Here it is a separate
column, because held material may still be rejected, and only **usable** stock covers a need:

    shortfall = max(open WO need + reorder level - usable - in QC Hold - on order - requested, 0)

All figures in the item's stock UOM, read from Work Orders, Bins, Purchase Orders, Material
Requests and Item reorder levels; nothing is stored or estimated.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt

QTY = ("required", "usable", "qc_hold", "on_order", "requested", "reorder_level", "shortfall")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return columns(), get_data(filters)


def columns():
	labels = {
		"required": _("Needed for open Work Orders"),
		"usable": _("Usable stock"),
		"qc_hold": _("In QC Hold"),
		"on_order": _("On order (PO)"),
		"requested": _("Requested (MR)"),
		"reorder_level": _("Reorder level"),
		"shortfall": _("Shortfall"),
	}
	return [
		{"fieldname": "item_code", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 150},
		{"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 180},
		{"fieldname": "factory_group", "label": _("Group"), "fieldtype": "Data", "width": 60},
		{"fieldname": "stock_uom", "label": _("UOM"), "fieldtype": "Link", "options": "UOM", "width": 60},
		*({"fieldname": f, "label": labels[f], "fieldtype": "Float", "width": 120} for f in QTY),
	]


def _items(group):
	groups = (group,) if group else ("RM", "PM")
	out = {}
	for g in groups:
		lft, rgt = frappe.db.get_value("Item Group", g, ["lft", "rgt"]) or (None, None)
		if lft is None:
			continue
		for i in frappe.db.sql(
			"""select i.name, i.item_name, i.stock_uom from `tabItem` i
			inner join `tabItem Group` ig on ig.name = i.item_group
			where ig.lft >= %s and ig.rgt <= %s and i.disabled = 0 and i.is_stock_item = 1""",
			(lft, rgt),
			as_dict=True,
		):
			out[i.name] = {
				"item_code": i.name,
				"item_name": i.item_name,
				"stock_uom": i.stock_uom,
				"factory_group": g,
			}
	return out


def get_data(filters):
	items = _items(filters.get("factory_group"))
	if not items:
		return []
	names = tuple(items)
	co = filters.company
	q = defaultdict(lambda: defaultdict(float))
	for r in frappe.db.sql(
		"""select woi.item_code, sum(greatest(woi.required_qty - woi.transferred_qty, 0)) as qty
		from `tabWork Order Item` woi inner join `tabWork Order` wo on wo.name = woi.parent
		where wo.docstatus = 1 and wo.company = %s and wo.status not in ('Completed', 'Stopped', 'Closed', 'Cancelled')
			and woi.item_code in %s group by woi.item_code""",
		(co, names),
		as_dict=True,
	):
		q[r.item_code]["required"] = flt(r.qty)
	for r in frappe.db.sql(
		"""select bin.item_code, w.warehouse_type, sum(bin.actual_qty) as qty from `tabBin` bin
		inner join `tabWarehouse` w on w.name = bin.warehouse
		where w.company = %s and bin.item_code in %s and w.warehouse_type in ('Usable', 'QC Hold')
		group by bin.item_code, w.warehouse_type""",
		(co, names),
		as_dict=True,
	):
		q[r.item_code]["usable" if r.warehouse_type == "Usable" else "qc_hold"] += flt(r.qty)
	for r in frappe.db.sql(
		"""select poi.item_code, sum(greatest(poi.qty - poi.received_qty, 0) * poi.conversion_factor) as qty
		from `tabPurchase Order Item` poi inner join `tabPurchase Order` po on po.name = poi.parent
		where po.docstatus = 1 and po.company = %s and po.status not in ('Closed', 'Completed', 'On Hold')
			and poi.item_code in %s group by poi.item_code""",
		(co, names),
		as_dict=True,
	):
		q[r.item_code]["on_order"] = flt(r.qty)
	for r in frappe.db.sql(
		"""select mri.item_code, sum(greatest(mri.stock_qty - mri.ordered_qty, 0)) as qty
		from `tabMaterial Request Item` mri inner join `tabMaterial Request` mr on mr.name = mri.parent
		where mr.docstatus = 1 and mr.company = %s and mr.material_request_type = 'Purchase'
			and mr.status not in ('Stopped', 'Ordered', 'Cancelled', 'Received') and mri.item_code in %s
		group by mri.item_code""",
		(co, names),
		as_dict=True,
	):
		q[r.item_code]["requested"] = flt(r.qty)
	for r in frappe.db.sql(
		"""select ir.parent as item_code, sum(ir.warehouse_reorder_level) as qty from `tabItem Reorder` ir
		inner join `tabWarehouse` w on w.name = ir.warehouse
		where w.company = %s and ir.parent in %s group by ir.parent""",
		(co, names),
		as_dict=True,
	):
		q[r.item_code]["reorder_level"] = flt(r.qty)
	rows = []
	for code, info in items.items():
		v = q[code]
		supply = v["usable"] + v["qc_hold"] + v["on_order"] + v["requested"]
		v["shortfall"] = max(v["required"] + v["reorder_level"] - supply, 0)
		if not any(v[f] for f in QTY):
			continue  # nothing needed, held, ordered or planned
		if filters.get("only_shortfall") and not v["shortfall"]:
			continue
		rows.append({**info, **{f: flt(v[f], 3) for f in QTY}})
	return sorted(rows, key=lambda r: (-r["shortfall"], r["item_code"]))
