"""Figures for workspace number cards that ERPNext has no standard card for (Milestone 6, D-037).

Every figure is read straight from ERPNext's ledgers and documents, never stored or estimated:

- stock value by factory group and in Rejected warehouses: ``tabBin.stock_value`` (the stock
  ledger's running value per item and warehouse, the same source as ERPNext's "Total Stock Value"
  card and Stock Balance report);
- wastage value: the outgoing value of submitted "Wastage" Stock Entries.

Tests reconcile each figure with ERPNext's own reports or the GL.
"""

import frappe
from frappe.utils import add_days, flt, nowdate

from factory_ops.constants import WH_TYPE_REJECTED


def _company(filters) -> str | None:
	return frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)


def stock_value(
	company: str | None = None, factory_group: str | None = None, warehouse_type: str | None = None
) -> float:
	"""Σ Bin.stock_value for the company, optionally for one factory group (with its sub-groups)
	or one warehouse type."""
	conditions, values = ["w.company = %(company)s"], {"company": company}
	if factory_group:
		lft, rgt = frappe.db.get_value("Item Group", factory_group, ["lft", "rgt"])
		conditions.append("ig.lft >= %(lft)s and ig.rgt <= %(rgt)s")
		values.update(lft=lft, rgt=rgt)
	if warehouse_type:
		conditions.append("w.warehouse_type = %(wh_type)s")
		values["wh_type"] = warehouse_type
	return flt(
		frappe.db.sql(
			f"""select coalesce(sum(bin.stock_value), 0)
			from `tabBin` bin
			inner join `tabWarehouse` w on w.name = bin.warehouse
			inner join `tabItem` i on i.name = bin.item_code
			inner join `tabItem Group` ig on ig.name = i.item_group
			where {" and ".join(conditions)}""",
			values,
		)[0][0],
		2,
	)


def wastage_value(company: str | None = None, days: int = 30) -> float:
	return flt(
		frappe.db.sql(
			"""select coalesce(sum(se.total_outgoing_value), 0) from `tabStock Entry` se
			inner join `tabStock Entry Type` t on t.name = se.stock_entry_type
			where se.docstatus = 1 and t.fo_is_wastage = 1 and se.company = %s and se.posting_date >= %s""",
			(company, add_days(nowdate(), -days)),
		)[0][0],
		2,
	)


def _card(value: float) -> dict:
	return {"value": value, "fieldtype": "Currency"}


def _guard(doctype: str = "Bin"):
	frappe.has_permission(doctype, "read", throw=True)


@frappe.whitelist()
def rm_stock_value(filters=None):
	_guard()
	return _card(stock_value(_company(filters), "RM"))


@frappe.whitelist()
def pm_stock_value(filters=None):
	_guard()
	return _card(stock_value(_company(filters), "PM"))


@frappe.whitelist()
def bulk_stock_value(filters=None):
	_guard()
	return _card(stock_value(_company(filters), "BULK"))


@frappe.whitelist()
def fg_stock_value(filters=None):
	_guard()
	return _card(stock_value(_company(filters), "FG"))


@frappe.whitelist()
def rejected_stock_value(filters=None):
	_guard()
	return _card(stock_value(_company(filters), warehouse_type=WH_TYPE_REJECTED))


@frappe.whitelist()
def wastage_value_30_days(filters=None):
	_guard("Stock Entry")
	return _card(wastage_value(_company(filters)))


# --------------------------------------------------------------------------- maintenance
MAINTENANCE_DUE_DAYS = 14  # the same window as the maintenance dashboard API


def _count(value: int) -> int:
	"""A plain integer on purpose. Frappe fills every Number Card's ``currency`` with the site default,
	and the widget formats a card with a currency as money even when its method returns
	``{"fieldtype": "Int"}``, so a count showed as "₹ 0.00". A bare number is displayed as it is."""
	return int(value)


@frappe.whitelist()
def maintenance_tasks_due(filters=None):
	"""Planned or overdue preventive-maintenance tasks due within two weeks (or already late).
	Uses ``get_list``, so it counts only what the user may read."""
	_guard("Asset Maintenance Log")
	names = frappe.get_list(
		"Asset Maintenance Log",
		filters={
			"docstatus": 0,
			"maintenance_status": ["in", ["Planned", "Overdue"]],
			"due_date": ["<=", add_days(nowdate(), MAINTENANCE_DUE_DAYS)],
		},
		pluck="name",
		limit_page_length=0,
	)
	return _count(len(names))


@frappe.whitelist()
def spares_below_reorder_count(filters=None):
	"""Spare parts at or below the reorder level set on the item (api.maintenance.spares_below_reorder)."""
	_guard("Item")
	_guard("Bin")
	from factory_ops.api.maintenance import spares_below_reorder

	return _count(len(spares_below_reorder()))


# --------------------------------------------------------------------------- procurement, stores, sales
EXPIRY_WINDOW_DAYS = 30


@frappe.whitelist()
def purchase_orders_overdue(filters=None):
	"""Submitted Purchase Orders still to receive whose required-by date has passed."""
	_guard("Purchase Order")
	names = frappe.get_list(
		"Purchase Order",
		filters={
			"docstatus": 1,
			"status": ["in", ["To Receive and Bill", "To Receive"]],
			"schedule_date": ["<", nowdate()],
		},
		pluck="name",
		limit_page_length=0,
	)
	return _count(len(names))


@frappe.whitelist()
def sales_orders_past_delivery_date(filters=None):
	"""Submitted Sales Orders still to deliver whose delivery date has passed."""
	_guard("Sales Order")
	names = frappe.get_list(
		"Sales Order",
		filters={
			"docstatus": 1,
			"status": ["in", ["To Deliver and Bill", "To Deliver"]],
			"delivery_date": ["<", nowdate()],
		},
		pluck="name",
		limit_page_length=0,
	)
	return _count(len(names))


@frappe.whitelist()
def batches_expiring_soon(filters=None):
	"""Batches that still hold stock and expire within 30 days (not already expired)."""
	_guard("Batch")
	names = frappe.get_list(
		"Batch",
		filters={
			"disabled": 0,
			"batch_qty": [">", 0],
			"expiry_date": ["between", [nowdate(), add_days(nowdate(), EXPIRY_WINDOW_DAYS)]],
		},
		pluck="name",
		limit_page_length=0,
	)
	return _count(len(names))


def items_below_reorder() -> list[dict]:
	"""Enabled stock items whose quantity in a warehouse is at or below the reorder level set on the item
	for that warehouse. Reads Item Reorder and Bin only; nothing is estimated."""
	return frappe.db.sql(
		"""
		select ir.parent as item_code, ir.warehouse, ir.warehouse_reorder_level as reorder_level,
			coalesce(b.actual_qty, 0) as actual_qty
		from `tabItem Reorder` ir
		join `tabItem` i on i.name = ir.parent
		left join `tabBin` b on b.item_code = ir.parent and b.warehouse = ir.warehouse
		where i.disabled = 0 and i.is_stock_item = 1 and ir.warehouse_reorder_level > 0
			and coalesce(b.actual_qty, 0) <= ir.warehouse_reorder_level
		""",
		as_dict=True,
	)


@frappe.whitelist()
def items_below_reorder_count(filters=None):
	_guard("Item")
	_guard("Bin")
	return _count(len(items_below_reorder()))
