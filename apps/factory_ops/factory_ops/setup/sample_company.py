"""SAMPLE company and master data for development and UAT.

Every name here is a labelled placeholder (brief section 2): no real Mintree product, supplier,
price, quality limit or tax registration. Replace through the master-data import templates when
the client confirms real data.

- ``setup_dev_site()``: runs ERPNext's own setup wizard (company + April-March financial year)
  on an empty development site, then ``setup_company_masters``.
- ``setup_company_masters(company)``: idempotent; also used by the automated tests against
  ERPNext's ``_Test Company``.
"""

import frappe
from frappe.utils import getdate, nowdate

from factory_ops.constants import RELEASE_MAP
from factory_ops.setup.install import apply_setup
from factory_ops.setup.warehouses import ensure_warehouse_tree, wh_name

SAMPLE_COMPANY = "SAMPLE Mintree Manufacturing"
SAMPLE_ABBR = "SMM"

FACTORY_GROUP = "Factory Items"
# group: (QC release required, supplier COA required, default receiving / output warehouse)
ITEM_GROUPS = {
	"RM": (1, 1, "RM-QC-HOLD"),
	"PM": (1, 0, "PM-QC-HOLD"),
	"BULK": (1, 0, "BULK-QC-HOLD"),
	"FG": (1, 0, "FG-QC-HOLD"),
}

SUPPLIER_GROUP = "SAMPLE Suppliers"
SUPPLIERS = ("SAMPLE Supplier - Actives", "SAMPLE Supplier - Packaging")
# Customer groups for the sales channels (Milestone 5)
CUSTOMER_GROUPS = ("Distributors", "B2B Customers", "D2C Consumers")
# name: (customer group, credit limit per company or 0 for none). SAMPLE values, not client terms.
CUSTOMERS = {
	"SAMPLE Customer - Distributor": ("Distributors", 500_000),
	"SAMPLE Customer - Retailer": ("B2B Customers", 25_000),
	"SAMPLE D2C - Website Orders": ("D2C Consumers", 0),
}
SELLING_PRICES = {"SAMPLE-FG-0001": 250}  # Standard Selling, INR per unit (SAMPLE)

# Quality masters (Milestone 6). SAMPLE parameters and limits only: the real specifications are
# client input (OPEN_ISSUES). Template per factory group; SAMPLE items get their group's template.
QI_PARAMETERS = (
	"SAMPLE Appearance",
	"SAMPLE Identification (IR)",
	"SAMPLE Assay (%)",
	"SAMPLE pH",
	"SAMPLE Viscosity (cP)",
	"SAMPLE Fill volume (mL)",
	"SAMPLE Print and artwork",
	"SAMPLE Microbial count (cfu/g)",
)
# template: [(parameter, numeric, min, max, text specification)]
QI_TEMPLATES = {
	"SAMPLE RM Incoming": [
		("SAMPLE Appearance", 0, None, None, "Conforms to the supplier COA"),
		("SAMPLE Identification (IR)", 0, None, None, "Matches the reference spectrum"),
		("SAMPLE Assay (%)", 1, 98.0, 102.0, None),
	],
	"SAMPLE PM Incoming": [
		("SAMPLE Appearance", 0, None, None, "No defects"),
		("SAMPLE Print and artwork", 0, None, None, "Matches the approved artwork"),
	],
	"SAMPLE BULK In Process": [
		("SAMPLE Appearance", 0, None, None, "Clear, colourless gel"),
		("SAMPLE pH", 1, 5.0, 6.5, None),
		("SAMPLE Viscosity (cP)", 1, 3000.0, 6000.0, None),
		("SAMPLE Microbial count (cfu/g)", 1, 0.0, 100.0, None),
	],
	"SAMPLE FG Outgoing": [
		("SAMPLE Appearance", 0, None, None, "Correct pack, label and batch print"),
		("SAMPLE Fill volume (mL)", 1, 29.5, 31.0, None),
		("SAMPLE pH", 1, 5.0, 6.5, None),
	],
}
QI_TEMPLATE_BY_GROUP = {
	"RM": "SAMPLE RM Incoming",
	"PM": "SAMPLE PM Incoming",
	"BULK": "SAMPLE BULK In Process",
	"FG": "SAMPLE FG Outgoing",
}
BRANDS = ("SAMPLE Brand",)
# Workstations and operations for later routing (Phase 4); not attached to the SAMPLE BOMs.
WORKSTATIONS = ("SAMPLE Mixing Vessel 500 L", "SAMPLE Filling Line 1")
OPERATIONS = {
	"SAMPLE Mixing": "SAMPLE Mixing Vessel 500 L",
	"SAMPLE Filling and Packing": "SAMPLE Filling Line 1",
}

# code: (name, group, stock uom, has_expiry_date, shelf life days)
ITEMS = {
	"SAMPLE-RM-0001": ("SAMPLE Niacinamide", "RM", "Kg", 1, 730),
	"SAMPLE-RM-0002": ("SAMPLE Glycerin", "RM", "Kg", 1, 1095),
	"SAMPLE-PM-0001": ("SAMPLE Bottle 30 mL amber glass", "PM", "Nos", 0, 0),
	"SAMPLE-BULK-0001": ("SAMPLE Bulk serum base", "BULK", "Kg", 1, 365),
	"SAMPLE-FG-0001": ("SAMPLE Serum 30 mL", "FG", "Nos", 1, 730),
}

UOM_CONVERSIONS = (("Kg", "Gram", 1000), ("Litre", "Millilitre", 1000))

# SAMPLE two-stage production (placeholder quantities, not a real formula):
# item: (BOM quantity, process loss %, [(component, qty per BOM quantity)])
BOMS = {
	"SAMPLE-BULK-0001": (100, 2, [("SAMPLE-RM-0001", 5), ("SAMPLE-RM-0002", 95)]),
	"SAMPLE-FG-0001": (1000, 0, [("SAMPLE-BULK-0001", 30), ("SAMPLE-PM-0001", 1000)]),
}

# Starter wastage reason codes; the client edits the list in Desk.
WASTAGE_REASONS = {
	"Spillage": "Material spilled or lost during weighing, charging or transfer.",
	"Line purge / changeover": "Material used to purge or prime a line at changeover.",
	"Damaged in handling": "Containers or components damaged in stores or on the line.",
	"Process failure": "Material spoiled by a failed process step.",
	"Retained / test sample": "Material taken for retained samples or testing.",
}

MANUFACTURING_SETTINGS = {
	# consume what was actually transferred to WIP (less returns), not the BOM figure
	"backflush_raw_materials_based_on": "Material Transferred for Manufacture",
	"overproduction_percentage_for_work_order": 0,
}


def financial_year_for(date=None) -> tuple[str, str]:
	"""Indian financial year (April to March) containing ``date``."""
	d = getdate(date or nowdate())
	start_year = d.year if d.month >= 4 else d.year - 1
	return f"{start_year}-04-01", f"{start_year + 1}-03-31"


def setup_dev_site(company: str = SAMPLE_COMPANY, abbr: str = SAMPLE_ABBR) -> str:
	"""Run on an empty development site: bench --site <site> execute factory_ops.setup.sample_company.setup_dev_site"""
	if not frappe.is_setup_complete():
		from frappe.desk.page.setup_wizard.setup_wizard import setup_complete

		fy_start, fy_end = financial_year_for()
		result = setup_complete(
			{
				"language": "English",
				"country": "India",
				"timezone": "Asia/Kolkata",
				"currency": "INR",
				"full_name": "SAMPLE Administrator",
				"email": "admin@sample.invalid",
				"company_name": company,
				"company_abbr": abbr,
				"chart_of_accounts": "Standard",
				"fy_start_date": fy_start,
				"fy_end_date": fy_end,
				"setup_demo": 0,
			}
		)
		if (result or {}).get("status") != "ok":
			frappe.throw(f"Setup wizard did not complete: {result}")
	ensure_next_financial_year()
	setup_company_masters(company)
	configure_stock_settings()
	configure_manufacturing_settings()
	configure_selling_settings()
	frappe.db.commit()
	return company


STOCK_SETTINGS = {
	# A fresh v16 site has batch tracking switched off; batch-tracked items cannot be received
	# until it is on (ERPNext's test bootstrap turns it on, so only a real site shows this).
	"enable_serial_and_batch_no_for_item": 1,
	"allow_negative_stock": 0,
	"allow_negative_stock_for_batch": 0,
	"pick_serial_and_batch_based_on": "Expiry",  # FEFO
	"valuation_method": "Moving Average",
	"enable_stock_reservation": 1,
	"action_if_quality_inspection_is_not_submitted": "Stop",
	"action_if_quality_inspection_is_rejected": "Stop",
}


def configure_stock_settings(skip: tuple[str, ...] = ()):
	"""Stock Settings for the factory (also listed in docs/DATABASE_AND_WORKFLOW_DESIGN.md)."""
	for field, value in STOCK_SETTINGS.items():
		if field not in skip:
			frappe.db.set_single_value("Stock Settings", field, value)


def configure_manufacturing_settings():
	for field, value in MANUFACTURING_SETTINGS.items():
		frappe.db.set_single_value("Manufacturing Settings", field, value)


def ensure_next_financial_year():
	start, end = financial_year_for()
	next_start = f"{int(start[:4]) + 1}-04-01"
	next_end = f"{int(end[:4]) + 1}-03-31"
	for s, e in ((start, end), (next_start, next_end)):
		if not frappe.db.exists("Fiscal Year", {"year_start_date": s}):
			frappe.get_doc(
				{
					"doctype": "Fiscal Year",
					"year": f"{s[:4]}-{e[2:4]}",
					"year_start_date": s,
					"year_end_date": e,
				}
			).insert(ignore_permissions=True)


def setup_company_masters(company: str) -> dict:
	apply_setup()
	warehouses = ensure_warehouse_tree(company)
	ensure_uoms()
	ensure_item_groups(company)
	ensure_suppliers()
	ensure_customers(company)
	ensure_items()
	ensure_selling_prices()
	ensure_quality_masters()
	ensure_manufacturing_masters()
	ensure_wastage_reasons()
	ensure_wastage_account(company)
	ensure_boms(company)
	from factory_ops.maintenance.setup import ensure_maintenance_masters

	ensure_maintenance_masters(company)
	return warehouses


def ensure_wastage_reasons():
	for reason, description in WASTAGE_REASONS.items():
		if not frappe.db.exists("Wastage Reason", reason):
			frappe.get_doc(
				{"doctype": "Wastage Reason", "wastage_reason": reason, "description": description}
			).insert(ignore_permissions=True)


def ensure_wastage_account(company: str) -> str | None:
	"""A 'Production Wastage' expense account under Stock Expenses, set on the Company once."""
	if frappe.db.get_value("Company", company, "fo_wastage_account"):
		return frappe.db.get_value("Company", company, "fo_wastage_account")
	parent = frappe.db.get_value(
		"Account", {"company": company, "account_name": "Stock Expenses", "is_group": 1}, "name"
	)
	if not parent:
		return None
	account = frappe.db.get_value("Account", {"company": company, "account_name": "Production Wastage"})
	if not account:
		account = (
			frappe.get_doc(
				{
					"doctype": "Account",
					"account_name": "Production Wastage",
					"parent_account": parent,
					"company": company,
					"root_type": "Expense",
					"report_type": "Profit and Loss",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
	frappe.db.set_value("Company", company, "fo_wastage_account", account)
	return account


def ensure_boms(company: str) -> dict[str, str]:
	"""Submitted default BOM per SAMPLE made item and company, bulk first. Idempotent."""
	boms = {}
	for item, (quantity, loss, components) in BOMS.items():
		existing = frappe.db.get_value(
			"BOM", {"item": item, "company": company, "is_default": 1, "docstatus": 1}, "name"
		)
		if not existing:
			bom = frappe.get_doc(
				{
					"doctype": "BOM",
					"item": item,
					"company": company,
					"quantity": quantity,
					"process_loss_percentage": loss,
					"rm_cost_as_per": "Valuation Rate",
					"is_active": 1,
					"is_default": 1,
					"items": [
						{"item_code": code, "qty": qty, "bom_no": boms.get(code)} for code, qty in components
					],
				}
			)
			bom.insert(ignore_permissions=True)
			bom.submit()
			existing = bom.name
		boms[item] = existing
	return boms


def ensure_customers(company: str | None = None):
	"""Customer groups, SAMPLE customers and, when a company is given, their credit limit there."""
	for group in CUSTOMER_GROUPS:
		if not frappe.db.exists("Customer Group", group):
			frappe.get_doc(
				{
					"doctype": "Customer Group",
					"customer_group_name": group,
					"parent_customer_group": "All Customer Groups",
				}
			).insert(ignore_permissions=True)
	for name, (group, limit) in CUSTOMERS.items():
		if not frappe.db.exists("Customer", name):
			frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": name,
					"customer_type": "Individual" if group == "D2C Consumers" else "Company",
					"territory": "India" if frappe.db.exists("Territory", "India") else "All Territories",
					"customer_group": group,
				}
			).insert(ignore_permissions=True)
		doc = frappe.get_doc("Customer", name)
		changed = doc.customer_group != group
		doc.customer_group = group
		if company and limit and not any(r.company == company for r in doc.credit_limits):
			doc.append("credit_limits", {"company": company, "credit_limit": limit})
			changed = True
		if changed:
			doc.save(ignore_permissions=True)


def ensure_quality_masters():
	"""SAMPLE inspection parameters and templates; each SAMPLE item gets its group's template."""
	for param in QI_PARAMETERS:
		if not frappe.db.exists("Quality Inspection Parameter", param):
			frappe.get_doc({"doctype": "Quality Inspection Parameter", "parameter": param}).insert(
				ignore_permissions=True
			)
	for name, params in QI_TEMPLATES.items():
		if frappe.db.exists("Quality Inspection Template", name):
			continue
		tpl = frappe.get_doc(
			{"doctype": "Quality Inspection Template", "quality_inspection_template_name": name}
		)
		for param, numeric, lo, hi, text in params:
			tpl.append(
				"item_quality_inspection_parameter",
				{"specification": param, "numeric": numeric, "min_value": lo, "max_value": hi, "value": text},
			)
		tpl.insert(ignore_permissions=True)
	for code, (_name, group, *_rest) in ITEMS.items():
		if not frappe.db.get_value("Item", code, "quality_inspection_template"):
			frappe.db.set_value("Item", code, "quality_inspection_template", QI_TEMPLATE_BY_GROUP[group])
	for brand in BRANDS:
		if not frappe.db.exists("Brand", brand):
			frappe.get_doc({"doctype": "Brand", "brand": brand}).insert(ignore_permissions=True)


def ensure_manufacturing_masters():
	for ws in WORKSTATIONS:
		if not frappe.db.exists("Workstation", ws):
			frappe.get_doc({"doctype": "Workstation", "workstation_name": ws}).insert(ignore_permissions=True)
	for op, ws in OPERATIONS.items():
		if not frappe.db.exists("Operation", op):
			frappe.get_doc({"doctype": "Operation", "name": op, "workstation": ws}).insert(
				ignore_permissions=True
			)


def ensure_selling_prices():
	price_list = "Standard Selling"
	if not frappe.db.exists("Price List", price_list):
		return
	for item, rate in SELLING_PRICES.items():
		if not frappe.db.exists("Item Price", {"item_code": item, "price_list": price_list}):
			frappe.get_doc(
				{
					"doctype": "Item Price",
					"item_code": item,
					"price_list": price_list,
					"price_list_rate": rate,
				}
			).insert(ignore_permissions=True)


def configure_selling_settings():
	"""Development / real-site selling rules (not applied by the test setup; tests set what they need):
	a Delivery Note needs a Sales Order and a Sales Invoice needs a Delivery Note, so every sale
	follows SO -> DN -> SI and stock moves once; a Sales Manager may submit an order that exceeds
	the customer's credit limit (ERPNext's credit controller; the role must be able to submit Sales
	Orders, which Accounts Manager cannot); dispatch details are required on Delivery Notes."""
	frappe.db.set_single_value("Selling Settings", "so_required", "Yes")
	frappe.db.set_single_value("Selling Settings", "dn_required", "Yes")
	frappe.db.set_single_value("Accounts Settings", "credit_controller", "Sales Manager")
	frappe.db.set_single_value("Factory Ops Settings", "require_dispatch_details", 1)


def ensure_uoms():
	for uom in ("Kg", "Gram", "Litre", "Millilitre", "Nos"):
		if not frappe.db.exists("UOM", uom):
			frappe.get_doc({"doctype": "UOM", "uom_name": uom}).insert(ignore_permissions=True)
	frappe.db.set_value("UOM", "Nos", "must_be_whole_number", 1)
	for from_uom, to_uom, value in UOM_CONVERSIONS:
		if not frappe.db.exists("UOM Conversion Factor", {"from_uom": from_uom, "to_uom": to_uom}):
			category = frappe.db.get_value("UOM Conversion Factor", {"from_uom": from_uom}, "category")
			frappe.get_doc(
				{
					"doctype": "UOM Conversion Factor",
					"category": category or "Mass",
					"from_uom": from_uom,
					"to_uom": to_uom,
					"value": value,
				}
			).insert(ignore_permissions=True)


def ensure_item_groups(company: str):
	if not frappe.db.exists("Item Group", FACTORY_GROUP):
		frappe.get_doc(
			{
				"doctype": "Item Group",
				"item_group_name": FACTORY_GROUP,
				"is_group": 1,
				"parent_item_group": "All Item Groups",
			}
		).insert(ignore_permissions=True)
	for group, (qc, coa, warehouse) in ITEM_GROUPS.items():
		if not frappe.db.exists("Item Group", group):
			frappe.get_doc(
				{"doctype": "Item Group", "item_group_name": group, "parent_item_group": FACTORY_GROUP}
			).insert(ignore_permissions=True)
		doc = frappe.get_doc("Item Group", group)
		doc.fo_qc_required = qc
		doc.fo_coa_required = coa
		default = next((d for d in doc.item_group_defaults if d.company == company), None)
		if not default:
			default = doc.append("item_group_defaults", {"company": company})
		default.default_warehouse = wh_name(warehouse, company)
		# production draws the group's items from the usable store (seeded once; editable)
		if not default.get("fo_production_warehouse"):
			default.fo_production_warehouse = wh_name(RELEASE_MAP[warehouse], company)
		doc.save(ignore_permissions=True)


def ensure_suppliers():
	if not frappe.db.exists("Supplier Group", SUPPLIER_GROUP):
		frappe.get_doc(
			{
				"doctype": "Supplier Group",
				"supplier_group_name": SUPPLIER_GROUP,
				"parent_supplier_group": "All Supplier Groups",
			}
		).insert(ignore_permissions=True)
	for name in SUPPLIERS:
		if not frappe.db.exists("Supplier", name):
			frappe.get_doc(
				{
					"doctype": "Supplier",
					"supplier_name": name,
					"supplier_group": SUPPLIER_GROUP,
					"country": "India",
				}
			).insert(ignore_permissions=True)


def ensure_items():
	for code, (name, group, uom, has_expiry, shelf_life) in ITEMS.items():
		if frappe.db.exists("Item", code):
			continue
		item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": code,
				"item_name": name,
				"description": f"{name}. SAMPLE placeholder, not a real Mintree item.",
				"item_group": group,
				"stock_uom": uom,
				"is_stock_item": 1,
				"is_purchase_item": group in ("RM", "PM"),
				"is_sales_item": group == "FG",
				"has_expiry_date": has_expiry,
				"shelf_life_in_days": shelf_life,
				"batch_number_series": f"{code}-.YY.-.#####",
			}
		)
		for from_uom, to_uom, value in UOM_CONVERSIONS:
			if from_uom == uom:
				item.append("uoms", {"uom": to_uom, "conversion_factor": 1 / value})
		item.insert(ignore_permissions=True)
