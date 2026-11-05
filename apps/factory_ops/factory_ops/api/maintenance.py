"""Maintenance dashboard figures, computed from records (MINTREE_MAINTENANCE_MODULE.md).

Every figure comes from ERPNext documents the user may read; a section the user may not read is
left out rather than shown as zero. Nothing is estimated.
"""

import frappe
from frappe import _
from frappe.utils import add_days, add_months, flt, get_first_day, getdate, nowdate

UPCOMING_DAYS = 14


@frappe.whitelist(methods=["GET"])
def dashboard(company: str | None = None) -> dict:
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.AuthenticationError)
	today = getdate(nowdate())
	out: dict = {"as_of": str(today), "sections": []}
	asset_filters = {"docstatus": 1}
	if company:
		asset_filters["company"] = company

	if frappe.has_permission("Asset", "read"):
		rows = frappe.get_all(
			"Asset", filters=asset_filters, fields=["fo_operational_status as status", {"COUNT": "*", "as": "n"}], group_by="fo_operational_status"
		)
		out["assets_by_status"] = {(r.status or "Not set"): r.n for r in rows}
		out["sections"].append("assets")

	if frappe.has_permission("Asset Repair", "read"):
		open_rows = frappe.get_all(
			"Asset Repair",
			filters={"docstatus": 0, "repair_status": "Pending", **({"company": company} if company else {})},
			fields=["name", "asset", "asset_name", "failure_date", "fo_severity", "fo_technician"],
			order_by="failure_date asc",
		)
		out["open_breakdowns"] = open_rows
		start = get_first_day(add_months(today, -5))
		closed = frappe.get_all(
			"Asset Repair",
			filters={"docstatus": 1, "completion_date": [">=", start], **({"company": company} if company else {})},
			fields=["completion_date", "fo_downtime_hours", "repair_cost", "consumed_items_cost"],
		)
		months = [get_first_day(add_months(start, i)) for i in range(6)]
		trend = {m.strftime("%Y-%m"): {"month": m.strftime("%Y-%m"), "downtime_hours": 0.0, "cost": 0.0, "closed": 0} for m in months}
		for r in closed:
			key = getdate(r.completion_date).strftime("%Y-%m")
			if key in trend:
				trend[key]["downtime_hours"] += flt(r.fo_downtime_hours)
				trend[key]["cost"] += flt(r.repair_cost) + flt(r.consumed_items_cost)
				trend[key]["closed"] += 1
		out["monthly"] = list(trend.values())
		out["sections"].append("breakdowns")

	if frappe.has_permission("Asset Maintenance Log", "read"):
		logs = frappe.get_all(
			"Asset Maintenance Log",
			filters={"docstatus": 0, "maintenance_status": ["in", ["Planned", "Overdue"]], "due_date": ["<=", add_days(today, UPCOMING_DAYS)]},
			fields=["name", "asset_name", "task_name", "due_date", "maintenance_status", "assign_to_name"],
			order_by="due_date asc",
			limit=50,
		)
		for log in logs:
			if log.maintenance_status == "Planned" and log.due_date and getdate(log.due_date) < today:
				log.maintenance_status = "Overdue"
		out["upcoming_maintenance"] = logs
		out["upcoming_days"] = UPCOMING_DAYS
		out["sections"].append("maintenance")

	if frappe.has_permission("Item", "read") and frappe.has_permission("Bin", "read"):
		out["spares_below_reorder"] = spares_below_reorder()
		out["sections"].append("spares")
	return out


def spares_below_reorder() -> list[dict]:
	"""Spare parts (Item Group "Spare Parts" and below) whose stock is at or below a reorder level set on the item."""
	if not frappe.db.exists("Item Group", "Spare Parts"):
		return []
	lft, rgt = frappe.db.get_value("Item Group", "Spare Parts", ["lft", "rgt"])
	return frappe.db.sql(
		"""
		select ir.parent as item_code, i.item_name, ir.warehouse, ir.warehouse_reorder_level as reorder_level,
			ir.warehouse_reorder_qty as reorder_qty, coalesce(b.actual_qty, 0) as actual_qty
		from `tabItem Reorder` ir
		join `tabItem` i on i.name = ir.parent
		join `tabItem Group` ig on ig.name = i.item_group
		left join `tabBin` b on b.item_code = ir.parent and b.warehouse = ir.warehouse
		where ig.lft >= %s and ig.rgt <= %s and i.disabled = 0
			and coalesce(b.actual_qty, 0) <= ir.warehouse_reorder_level
		order by i.item_name
		""",
		(lft, rgt),
		as_dict=True,
	)


@frappe.whitelist(methods=["POST"])
def register_asset(
	asset_name: str,
	equipment_type: str,
	company: str,
	location: str | None = None,
	department: str | None = None,
	manufacturer: str | None = None,
	model: str | None = None,
	serial_no: str | None = None,
	installation_date: str | None = None,
	warranty_until: str | None = None,
	warranty_details: str | None = None,
	purchase_date: str | None = None,
	purchase_amount: float | None = None,
) -> str:
	"""Register an existing machine as an ERPNext Asset (submitted, no depreciation), with the
	equipment Item and masters it needs. Runs with the user's own Asset permissions."""
	from factory_ops.maintenance.setup import DEFAULT_LOCATION, ensure_maintenance_masters, equipment_item

	if not frappe.has_permission("Asset", "create") or not frappe.has_permission("Asset", "submit"):
		frappe.throw(_("Registering equipment needs permission to create and submit Assets."), frappe.PermissionError)
	if not asset_name or not equipment_type:
		frappe.throw(_("Give the equipment a name and a type."))
	if flt(purchase_amount) <= 0:
		# ERPNext requires the asset's purchase value; it is never assumed here
		frappe.throw(_("Enter the equipment's purchase value (ERPNext requires it for every asset)."))
	ensure_maintenance_masters(company)
	item = equipment_item(equipment_type)
	date = purchase_date or installation_date or nowdate()
	asset = frappe.get_doc(
		{
			"doctype": "Asset",
			"asset_name": asset_name,
			"item_code": item,
			"company": company,
			"location": location or DEFAULT_LOCATION,
			"department": department,
			"asset_type": "Existing Asset",
			"purchase_date": date,
			"available_for_use_date": installation_date or date,
			"net_purchase_amount": flt(purchase_amount),
			"calculate_depreciation": 0,
			"maintenance_required": 1,
			"fo_equipment_type": equipment_type,
			"fo_operational_status": "Active",
			"fo_manufacturer": manufacturer,
			"fo_model": model,
			"fo_serial_no": serial_no,
			"fo_installation_date": installation_date,
			"fo_warranty_until": warranty_until,
			"fo_warranty_details": warranty_details,
		}
	)
	asset.insert()
	asset.submit()
	return asset.name
