"""Create the factory warehouse tree for a company (design doc section 3.1)."""

import frappe

from factory_ops.constants import RELEASE_MAP, WAREHOUSE_TREE, WH_TYPE_REJECTED, WIP_WAREHOUSE


def wh_name(name: str, company: str) -> str:
	abbr = frappe.get_cached_value("Company", company, "abbr")
	return f"{name} - {abbr}"


def ensure_warehouse_tree(company: str) -> dict[str, str]:
	"""Create missing warehouses; return {short name: full warehouse name}. Idempotent."""
	root = frappe.db.get_value(
		"Warehouse", {"company": company, "is_group": 1, "parent_warehouse": ("in", ("", None))}, "name"
	)
	created: dict[str, str] = {}
	for short, parent, wh_type, is_group in WAREHOUSE_TREE:
		full = wh_name(short, company)
		if not frappe.db.exists("Warehouse", full):
			doc = frappe.get_doc(
				{
					"doctype": "Warehouse",
					"warehouse_name": short,
					"company": company,
					"is_group": is_group,
					"parent_warehouse": wh_name(parent, company) if parent else root,
					"warehouse_type": wh_type,
					"is_rejected_warehouse": 1 if wh_type == WH_TYPE_REJECTED else 0,
				}
			)
			doc.insert(ignore_permissions=True)
		created[short] = full

	for hold, usable in RELEASE_MAP.items():
		frappe.db.set_value("Warehouse", created[hold], "fo_release_warehouse", created[usable])
	# seed the company's WIP warehouse once; afterwards it is edited on the Company
	if not frappe.db.get_value("Company", company, "fo_wip_warehouse"):
		frappe.db.set_value("Company", company, "fo_wip_warehouse", created[WIP_WAREHOUSE])
	return created
