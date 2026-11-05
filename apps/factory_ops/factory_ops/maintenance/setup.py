"""Masters the maintenance module needs on a company: a location, asset categories with the
company's fixed-asset accounts, an equipment Item Group and a Spare Parts Item Group (not QC
controlled). Idempotent; names are generic factory terms, not client data."""

import frappe

EQUIPMENT_GROUP = "Factory Equipment"
SPARES_GROUP = "Spare Parts"
DEFAULT_LOCATION = "Factory"
# category: fixed asset account name (standard Indian chart of accounts)
CATEGORIES = {
	"Plant and Machinery": "Plants and Machineries",
	"Laboratory Equipment": "Plants and Machineries",
	"Utilities": "Plants and Machineries",
}
CATEGORY_BY_TYPE = {"Laboratory Equipment": "Laboratory Equipment", "Utility": "Utilities"}


def category_for(equipment_type: str | None) -> str:
	return CATEGORY_BY_TYPE.get(equipment_type or "", "Plant and Machinery")


def ensure_maintenance_masters(company: str) -> None:
	abbr = frappe.db.get_value("Company", company, "abbr")
	for group in (EQUIPMENT_GROUP, SPARES_GROUP):
		if not frappe.db.exists("Item Group", group):
			frappe.get_doc({"doctype": "Item Group", "item_group_name": group, "parent_item_group": "All Item Groups"}).insert(
				ignore_permissions=True
			)
	if not frappe.db.exists("Location", DEFAULT_LOCATION):
		frappe.get_doc({"doctype": "Location", "location_name": DEFAULT_LOCATION}).insert(ignore_permissions=True)
	for category, account in CATEGORIES.items():
		fixed = f"{account} - {abbr}"
		if not frappe.db.exists("Account", fixed):
			fixed = frappe.db.get_value("Account", {"company": company, "account_type": "Fixed Asset", "is_group": 0}, "name")
		accounts = {
			"company_name": company,
			"fixed_asset_account": fixed,
			"accumulated_depreciation_account": frappe.db.get_value(
				"Account", {"company": company, "account_type": "Accumulated Depreciation", "is_group": 0}, "name"
			),
			"depreciation_expense_account": frappe.db.get_value(
				"Account", {"company": company, "account_type": "Depreciation", "is_group": 0}, "name"
			),
		}
		if not frappe.db.exists("Asset Category", category):
			frappe.get_doc({"doctype": "Asset Category", "asset_category_name": category, "accounts": [accounts]}).insert(
				ignore_permissions=True
			)
		else:
			doc = frappe.get_doc("Asset Category", category)
			if not any(r.company_name == company for r in doc.accounts):
				doc.append("accounts", accounts)
				doc.save(ignore_permissions=True)


def equipment_item(equipment_type: str | None) -> str:
	"""One non-stock fixed-asset Item per equipment type (ERPNext needs an Item behind every Asset)."""
	kind = equipment_type or "Other"
	code = f"EQUIPMENT-{kind.upper().replace(' ', '-')}"
	if not frappe.db.exists("Item", code):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": code,
				"item_name": f"Factory equipment: {kind}",
				"item_group": EQUIPMENT_GROUP,
				"stock_uom": "Nos",
				"is_stock_item": 0,
				"is_fixed_asset": 1,
				"asset_category": category_for(equipment_type),
			}
		).insert(ignore_permissions=True)
	return code
