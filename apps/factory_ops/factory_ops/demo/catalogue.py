# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""DEMO - NOT REAL: twelve invented cosmetic products on four invented bulk bases.

Nothing here is a Mintree product, formula, price or supplier term. Every item carries "DEMO" in its code
and "(NOT REAL)" in its name; the recipes are made-up percentages of the generic DEMO materials in
``demo/data.py`` and are not derived from any published ingredient list. Loaded only on a site flagged
``fo_demo_site`` (``demo/seed.require_demo_site``), never on a development or real site.

    bench --site demo.localhost execute factory_ops.demo.catalogue.seed_catalogue
"""

import frappe

from factory_ops.demo import data as D
from factory_ops.demo import seed

TAG = D.DEMO_TAG
UNITS_PER_BOM = 1000  # a finished-goods BOM is per 1,000 units

# bulk code: (name, process loss %, [(raw material, % of the batch)]). Percentages sum to 100.
BULKS = {
	"DEMO-BULK-0001": ("DEMO Cleanser Base (NOT REAL)", 2.0, None),  # BOM already made by demo/data.py
	"DEMO-BULK-0002": (
		"DEMO Cream Base (NOT REAL)",
		3.0,
		[("DEMO-RM-0007", 62), ("DEMO-RM-0002", 15), ("DEMO-RM-0001", 10), ("DEMO-RM-0004", 8),
		 ("DEMO-RM-0003", 3), ("DEMO-RM-0005", 1), ("DEMO-RM-0006", 1)],
	),
	"DEMO-BULK-0003": (
		"DEMO Serum Base (NOT REAL)",
		1.5,
		[("DEMO-RM-0007", 70), ("DEMO-RM-0002", 20), ("DEMO-RM-0003", 6), ("DEMO-RM-0004", 2),
		 ("DEMO-RM-0005", 1), ("DEMO-RM-0006", 1)],
	),
	"DEMO-BULK-0004": (
		"DEMO Conditioning Base (NOT REAL)",
		2.5,
		[("DEMO-RM-0007", 70), ("DEMO-RM-0004", 12), ("DEMO-RM-0001", 8), ("DEMO-RM-0002", 5),
		 ("DEMO-RM-0003", 3), ("DEMO-RM-0005", 1), ("DEMO-RM-0006", 1)],
	),
}  # fmt: skip

# extra packaging beyond demo/data.py (bottle 100 mL, cap, label, carton): code: (name, rate INR)
EXTRA_PM = {
	"DEMO-PM-0005": ("DEMO Bottle 50 mL PET (NOT REAL)", 7.0),
	"DEMO-PM-0006": ("DEMO Bottle 200 mL PET (NOT REAL)", 12.0),
}
BOTTLE = {50: "DEMO-PM-0005", 100: "DEMO-PM-0001", 200: "DEMO-PM-0006"}
CAP, CARTON = "DEMO-PM-0002", "DEMO-PM-0004"
LABEL_RATE = 1.2

# code, name, category (an Item Group under FG), bulk base, pack size mL, selling rate INR (invented)
PRODUCTS = (
	("DEMO-FG-001", "DEMO Gentle Face Wash 100 mL (NOT REAL)", "Skin Care", "DEMO-BULK-0001", 100, 199),
	("DEMO-FG-002", "DEMO Daily Shampoo 200 mL (NOT REAL)", "Hair Care", "DEMO-BULK-0001", 200, 249),
	("DEMO-FG-003", "DEMO Body Wash 200 mL (NOT REAL)", "Bath & Body", "DEMO-BULK-0001", 200, 229),
	("DEMO-FG-004", "DEMO Moisturising Cream 50 mL (NOT REAL)", "Skin Care", "DEMO-BULK-0002", 50, 299),
	("DEMO-FG-005", "DEMO Body Lotion 200 mL (NOT REAL)", "Bath & Body", "DEMO-BULK-0002", 200, 279),
	("DEMO-FG-006", "DEMO Hand Cream 50 mL (NOT REAL)", "Bath & Body", "DEMO-BULK-0002", 50, 179),
	("DEMO-FG-007", "DEMO Face Serum 50 mL (NOT REAL)", "Skin Care", "DEMO-BULK-0003", 50, 499),
	("DEMO-FG-008", "DEMO Hair Serum 100 mL (NOT REAL)", "Hair Care", "DEMO-BULK-0003", 100, 349),
	("DEMO-FG-009", "DEMO Toning Mist 100 mL (NOT REAL)", "Skin Care", "DEMO-BULK-0003", 100, 259),
	("DEMO-FG-010", "DEMO Conditioner 200 mL (NOT REAL)", "Hair Care", "DEMO-BULK-0004", 200, 269),
	("DEMO-FG-011", "DEMO Hair Mask 200 mL (NOT REAL)", "Hair Care", "DEMO-BULK-0004", 200, 399),
	("DEMO-FG-012", "DEMO Leave-in Cream 100 mL (NOT REAL)", "Hair Care", "DEMO-BULK-0004", 100, 329),
)


def label_code(fg_code: str) -> str:
	return "DEMO-PM-L" + fg_code.rsplit("-", 1)[1]  # DEMO-FG-007 -> DEMO-PM-L007


def fg_components(fg_code: str, bulk: str, ml: int) -> list[tuple[str, float]]:
	"""Per 1,000 units: bulk (1 mL = 1 g, a DEMO assumption), bottle, cap, own label, carton."""
	return [
		(bulk, round(ml * UNITS_PER_BOM / 1000, 3)),
		(BOTTLE[ml], UNITS_PER_BOM),
		(CAP, UNITS_PER_BOM),
		(label_code(fg_code), UNITS_PER_BOM),
		(CARTON, UNITS_PER_BOM),
	]


def _item(code, name, group, uom, expiry, shelf_life, purchase, sales, template, **extra):
	if frappe.db.exists("Item", code):
		return
	frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": code,
			"item_name": name,
			"description": f"{name}. {TAG}: synthetic demo item, not a Mintree product or formula.",
			"item_group": group,
			"stock_uom": uom,
			"is_stock_item": 1,
			"is_purchase_item": purchase,
			"is_sales_item": sales,
			"has_expiry_date": expiry,
			"shelf_life_in_days": shelf_life,
			"batch_number_series": f"{code}-.YY.-.#####",
			"quality_inspection_template": template,
			**extra,
		}
	).insert(ignore_permissions=True)


def _price(code: str, price_list: str, rate: float):
	if not frappe.db.exists("Item Price", {"item_code": code, "price_list": price_list}):
		frappe.get_doc(
			{"doctype": "Item Price", "item_code": code, "price_list": price_list, "price_list_rate": rate}
		).insert(ignore_permissions=True)


def seed_catalogue() -> dict:
	"""Idempotent: items, prices and BOMs for the twelve products. Returns what exists."""
	seed.require_demo_site()
	seed.ensure_demo_fields()
	seed.ensure_category_groups()
	seed.ensure_mrp_price_list()
	seed.ensure_demo_masters()  # DEMO suppliers, raw/packaging materials, templates, customer, cleanser BOM
	tpl = D.QI_TEMPLATE_BY_GROUP
	for code, (name, rate) in EXTRA_PM.items():
		_item(code, name, "PM", "Nos", 0, 0, 1, 0, tpl["PM"], supplier_items=[{"supplier": D.SUPPLIER_KEY["pack"]}])
		_price(code, "Standard Buying", rate)
	for code, (name, _loss, _recipe) in BULKS.items():
		_item(code, name, "BULK", "Kg", 1, 365, 0, 0, tpl["BULK"])
	for code, name, category, bulk, ml, price in PRODUCTS:
		label = label_code(code)
		_item(
			label, f"DEMO Front Label - {name.removeprefix('DEMO ')}", "PM", "Nos", 0, 0, 1, 0, tpl["PM"],
			supplier_items=[{"supplier": D.SUPPLIER_KEY["pack"]}],
		)  # fmt: skip
		_price(label, "Standard Buying", LABEL_RATE)
		_item(code, name, category, "Nos", 1, 730, 0, 1, tpl["FG"], fo_pack_size=f"{ml} ml")
		_price(code, D.MRP_PRICE_LIST, price)
	boms = {}
	for code, (_name, loss, recipe) in BULKS.items():
		if recipe:
			assert sum(p for _c, p in recipe) == 100, code
			boms[code] = seed.ensure_bom(code, 100, loss, recipe)
		else:
			boms[code] = seed.ensure_bom(code, *D.BULK_BOM)
	for code, _name, _cat, bulk, ml, _price_ in PRODUCTS:
		boms[code] = seed.ensure_bom(code, UNITS_PER_BOM, 0, fg_components(code, bulk, ml))
	frappe.db.commit()
	return {"products": len(PRODUCTS), "bulks": len(BULKS), "boms": boms}
