"""Seed a local demo site: public website catalogue + DEMO - NOT REAL manufacturing data.

Runs only on a site whose site_config has ``"fo_demo_site": 1`` (deploy/demo/reset-demo.sh creates
one). It never runs on factory.localhost, test.localhost or a real site.

    bench --site demo.localhost execute factory_ops.demo.seed.seed_demo_site \
        --kwargs '{"extraction_dir": "/path/to/data/mintree_website_extraction"}'

What it does, all idempotent (a second run creates nothing new):

1. Website catalogue (if the extraction folder holds ``erpnext/items.csv``): category Item Groups
   under FG with FG's QC and warehouse settings; an "MRP" price list; Items and Item Prices loaded
   through factory_ops' own **Master Data Import** (the same validated, all-or-nothing path the
   client will use); source URL, published SKU, barcode and image on each item.
2. DEMO masters (``demo/data.py``): suppliers, raw and packaging materials, a bulk item, buying
   prices, inspection templates, a customer, BOMs for the bulk and for one website product, and
   DEMO opening stock (again through Master Data Import).
3. Writes ``private/demo_scenario.json`` naming the items the scenario script uses.

No transaction is created here: purchase, QC, production and sales are run by
``deploy/demo/demo_scenario_http.py`` as the sample users, through the normal screens' API.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
from pathlib import Path

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import add_days, nowdate

from factory_ops.demo import data as D
from factory_ops.setup.sample_company import SAMPLE_ABBR, SAMPLE_COMPANY

COMPANY, ABBR = SAMPLE_COMPANY, SAMPLE_ABBR
IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def wh(name: str) -> str:
	return f"{name} - {ABBR}"


def require_demo_site():
	if not frappe.conf.get("fo_demo_site"):
		frappe.throw(
			"Not a demo site: set fo_demo_site in site_config (deploy/demo/reset-demo.sh does this). "
			"Demo data is never loaded into a development or real site."
		)


def seed_demo_site(extraction_dir: str | None = None) -> dict:
	require_demo_site()
	report: dict = {}
	ensure_demo_fields()
	ensure_category_groups()
	ensure_mrp_price_list()
	catalogue = Path(extraction_dir) / "erpnext" if extraction_dir else None
	if catalogue and (catalogue / "items.csv").exists():
		report["website"] = load_catalogue(catalogue, Path(extraction_dir))
	else:
		report["website"] = "no extraction found: no website products loaded (none are invented)"
	ensure_demo_masters()
	report["opening_stock"] = load_opening_stock()
	fg = choose_demo_product(catalogue)
	if fg:
		report["fg_bom"] = ensure_fg_bom(fg)
	from factory_ops.demo.modules import fg_opening_stock, seed_all

	report["fg_opening_stock"] = fg_opening_stock(COMPANY, fg)
	report["modules"] = seed_all(COMPANY, fg)
	scenario = {
		"company": COMPANY,
		"abbr": ABBR,
		"fg_item": fg,
		"bulk_item": "DEMO-BULK-0001",
		"rm_items": [c for c, v in D.ITEMS.items() if v[1] == "RM"],
		"pm_items": [c for c, v in D.ITEMS.items() if v[1] == "PM"],
		"opening_items": [o[0] for o in D.OPENING_STOCK],
		"suppliers": D.SUPPLIER_KEY,
		"customer": D.CUSTOMER[0],
		"buying_rates": {c: v[6] for c, v in D.ITEMS.items() if v[6]},
	}
	path = Path(frappe.get_site_path("private", "demo_scenario.json"))
	path.write_text(json.dumps(scenario, indent=2))
	os.chmod(path, 0o600)
	report["scenario"] = scenario
	frappe.db.commit()
	print(json.dumps(report, indent=2, default=str))
	return report


# --------------------------------------------------------------------------- website catalogue
def ensure_demo_fields():
	"""Traceability fields on Item, created on the demo site only (not part of the factory_ops app)."""
	create_custom_fields(
		{
			"Item": [
				{"fieldname": "fo_catalogue_section", "fieldtype": "Section Break", "label": "Public website catalogue", "insert_after": "over_billing_allowance"},  # Details tab, before Description
				{"fieldname": "fo_source_url", "fieldtype": "Data", "options": "URL", "label": "Source URL", "read_only": 1, "insert_after": "fo_catalogue_section"},
				{"fieldname": "fo_published_sku", "fieldtype": "Data", "label": "Published SKU", "read_only": 1, "insert_after": "fo_source_url",
				 "description": "Empty when the website publishes none; the item code is then a generated WEB- code."},
				{"fieldname": "fo_pack_size", "fieldtype": "Data", "label": "Pack size (as published)", "read_only": 1, "insert_after": "fo_published_sku"},
				{"fieldname": "fo_catalogue_read_at", "fieldtype": "Data", "label": "Read from the website at (UTC)", "read_only": 1, "insert_after": "fo_pack_size"},
			]
		},
		update=True,
	)  # fmt: skip


def ensure_category_groups():
	"""Website categories as sub-groups of FG, each with FG's QC flags and warehouse defaults.

	factory_ops reads QC flags and default warehouses from an item's own group, so a sub-group
	carries a copy of FG's settings; the RM/PM/BULK/FG checks walk up the tree to FG."""
	fg = frappe.get_doc("Item Group", "FG")
	if not fg.is_group:
		fg.is_group = 1
		fg.save(ignore_permissions=True)
	for name in D.CATEGORY_GROUPS:
		if not frappe.db.exists("Item Group", name):
			frappe.get_doc({"doctype": "Item Group", "item_group_name": name, "parent_item_group": "FG"}).insert(
				ignore_permissions=True
			)
		g = frappe.get_doc("Item Group", name)
		g.fo_qc_required, g.fo_coa_required = fg.fo_qc_required, fg.fo_coa_required
		g.set(
			"item_group_defaults",
			[
				{
					"company": d.company,
					"default_warehouse": d.default_warehouse,
					"fo_production_warehouse": d.get("fo_production_warehouse"),
				}
				for d in fg.item_group_defaults
			],
		)
		g.save(ignore_permissions=True)


def ensure_mrp_price_list():
	if not frappe.db.exists("Price List", D.MRP_PRICE_LIST):
		frappe.get_doc(
			{"doctype": "Price List", "price_list_name": D.MRP_PRICE_LIST, "selling": 1, "currency": "INR", "enabled": 1}
		).insert(ignore_permissions=True)


def read_csv(path: Path) -> tuple[list[str], list[dict]]:
	with open(path, newline="", encoding="utf-8") as f:
		r = csv.DictReader(f)
		return r.fieldnames, list(r)


def master_data_import(import_type: str, header: list[str], rows: list[dict], label: str, posting_date=None) -> str:
	"""Create, validate and run a Master Data Import from rows; returns its final status."""
	if not rows:
		return "nothing new"
	buf = io.StringIO()
	w = csv.DictWriter(buf, fieldnames=header)
	w.writeheader()
	w.writerows(rows)
	file = frappe.get_doc(
		{"doctype": "File", "file_name": f"{label}.csv", "is_private": 1, "content": buf.getvalue().encode()}
	).insert(ignore_permissions=True)
	mdi = frappe.get_doc(
		{"doctype": "Master Data Import", "import_type": import_type, "company": COMPANY, "posting_date": posting_date, "import_file": file.file_url}
	).insert(ignore_permissions=True)
	mdi.validate_file()
	if mdi.status != "Ready to Import":
		problems = "; ".join(f"row {i.row_no} {i.column_name}: {i.message}" for i in mdi.issues if i.severity == "Error")
		frappe.throw(f"{label}: {mdi.summary} {problems[:2000]}")
	status = mdi.start_import()
	if status != "Imported":
		frappe.throw(f"{label}: {mdi.summary}")
	return f"{mdi.name}: {mdi.summary}"


def load_catalogue(catalogue: Path, extraction: Path) -> dict:
	header, items = read_csv(catalogue / "items.csv")
	code_col = next(h for h in header if h.rstrip("*") == "item_code")
	new_items = [r for r in items if not frappe.db.exists("Item", r[code_col])]
	for brand in {r.get("brand") for r in new_items} - {None, ""}:  # the storefront's vendor name
		if not frappe.db.exists("Brand", brand):
			frappe.get_doc({"doctype": "Brand", "brand": brand}).insert(ignore_permissions=True)
	result = {"items_in_file": len(items), "items_new": len(new_items)}
	result["items_import"] = master_data_import("Items", header, new_items, "website-items")

	pheader, prices = read_csv(catalogue / "item_prices.csv")
	pc = {h.rstrip("*"): h for h in pheader}
	new_prices = [
		p for p in prices
		if not frappe.db.exists("Item Price", {"item_code": p[pc["item_code"]], "price_list": p[pc["price_list"]]})
	]  # fmt: skip
	result["prices_import"] = master_data_import("Item Prices", pheader, new_prices, "website-prices")

	_, sources = read_csv(catalogue / "item_sources.csv")
	images = 0
	for s in sources:
		if not frappe.db.exists("Item", s["item_code"]):
			continue
		item = frappe.get_doc("Item", s["item_code"])
		item.fo_source_url = s["source_url"]
		item.fo_published_sku = s["published_sku"]
		item.fo_pack_size = " ".join(x for x in (s.get("pack_size"), f"pack of {s['pack_count']}" if s.get("pack_count") else "") if x)
		item.fo_catalogue_read_at = s["extracted_at"]
		if s.get("barcode") and not any(b.barcode == s["barcode"] for b in item.barcodes):
			if not frappe.db.exists("Item Barcode", {"barcode": s["barcode"]}):
				item.append("barcodes", {"barcode": s["barcode"]})
		if s.get("image_url") and not item.image:
			item.image = attach_image(item.name, s["image_url"], extraction / "images")
			images += bool(item.image)
		item.flags.ignore_permissions = True
		item.save()
	result["images_attached"] = images
	return result


def attach_image(item_code: str, url: str, folder: Path) -> str:
	"""A downloaded copy (tools/website_extraction/fetch_images.py) if present, else the public URL."""
	ext = Path(url.split("?")[0]).suffix.lower()
	local = folder / (hashlib.sha256(url.encode()).hexdigest()[:20] + (ext if ext in IMAGE_TYPES else ".jpg"))
	if not local.exists():
		return url  # shown only while the demo machine is online
	f = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{re.sub(r'[^A-Za-z0-9-]+', '-', item_code)}{local.suffix}",
			"attached_to_doctype": "Item",
			"attached_to_name": item_code,
			"attached_to_field": "image",
			"is_private": 0,
			"content": local.read_bytes(),
		}
	).insert(ignore_permissions=True)
	return f.file_url


def choose_demo_product(catalogue: Path | None) -> str | None:
	"""The website product the scenario makes: the first imported Skin Care item with a pack size in mL."""
	if not catalogue or not (catalogue / "item_sources.csv").exists():
		return None
	_, sources = read_csv(catalogue / "item_sources.csv")
	present = [s for s in sources if frappe.db.exists("Item", s["item_code"])]
	for pref in (
		lambda s: s["item_group"] == "Skin Care" and s["pack_size"].endswith("ml"),
		lambda s: s["pack_size"].endswith("ml"),
		lambda s: True,
	):
		match = next((s for s in present if pref(s)), None)
		if match:
			return match["item_code"]
	return None


# --------------------------------------------------------------------------- DEMO masters
def ensure_demo_masters():
	if not frappe.db.exists("Supplier Group", D.SUPPLIER_GROUP):
		frappe.get_doc(
			{"doctype": "Supplier Group", "supplier_group_name": D.SUPPLIER_GROUP, "parent_supplier_group": "All Supplier Groups"}
		).insert(ignore_permissions=True)
	for name in D.SUPPLIERS:
		if not frappe.db.exists("Supplier", name):
			frappe.get_doc({"doctype": "Supplier", "supplier_name": name, "supplier_group": D.SUPPLIER_GROUP, "country": "India"}).insert(
				ignore_permissions=True
			)
	for param in D.QI_PARAMETERS:
		if not frappe.db.exists("Quality Inspection Parameter", param):
			frappe.get_doc({"doctype": "Quality Inspection Parameter", "parameter": param}).insert(ignore_permissions=True)
	for name, params in D.QI_TEMPLATES.items():
		if frappe.db.exists("Quality Inspection Template", name):
			continue
		tpl = frappe.get_doc({"doctype": "Quality Inspection Template", "quality_inspection_template_name": name})
		for param, numeric, lo, hi, text in params:
			tpl.append(
				"item_quality_inspection_parameter",
				{"specification": param, "numeric": numeric, "min_value": lo, "max_value": hi, "value": text},
			)
		tpl.insert(ignore_permissions=True)
	for code, (name, group, uom, has_expiry, shelf_life, supplier, rate) in D.ITEMS.items():
		if not frappe.db.exists("Item", code):
			item = frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": code,
					"item_name": name,
					"description": f"{name}. {D.DEMO_TAG}: synthetic demo material, not a Mintree item or formula.",
					"item_group": group,
					"stock_uom": uom,
					"is_stock_item": 1,
					"is_purchase_item": group in ("RM", "PM"),
					"is_sales_item": 0,
					"has_expiry_date": has_expiry,
					"shelf_life_in_days": shelf_life,
					"batch_number_series": f"{code}-.YY.-.#####",
					"quality_inspection_template": D.QI_TEMPLATE_BY_GROUP[group],
				}
			)
			if supplier:
				item.append("supplier_items", {"supplier": D.SUPPLIER_KEY[supplier]})
			item.insert(ignore_permissions=True)
		if rate and not frappe.db.exists("Item Price", {"item_code": code, "price_list": "Standard Buying"}):
			frappe.get_doc({"doctype": "Item Price", "item_code": code, "price_list": "Standard Buying", "price_list_rate": rate}).insert(
				ignore_permissions=True
			)
	# website products are finished goods: give them the DEMO outgoing template where they have none
	for code in frappe.get_all("Item", filters={"fo_source_url": ("is", "set"), "quality_inspection_template": ("is", "not set")}, pluck="name"):
		frappe.db.set_value("Item", code, "quality_inspection_template", D.QI_TEMPLATE_BY_GROUP["FG"])
	name, group, limit = D.CUSTOMER
	if not frappe.db.exists("Customer", name):
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": name,
				"customer_type": "Company",
				"customer_group": group,
				"territory": "India" if frappe.db.exists("Territory", "India") else "All Territories",
				"credit_limits": [{"company": COMPANY, "credit_limit": limit}],
			}
		).insert(ignore_permissions=True)
	ensure_bom("DEMO-BULK-0001", *D.BULK_BOM)


def ensure_bom(item: str, quantity: float, loss: float, components: list[tuple[str, float]]) -> str:
	existing = frappe.db.get_value("BOM", {"item": item, "company": COMPANY, "is_default": 1, "docstatus": 1}, "name")
	if existing:
		return existing
	bom = frappe.get_doc(
		{
			"doctype": "BOM",
			"item": item,
			"company": COMPANY,
			"quantity": quantity,
			"process_loss_percentage": loss,
			"rm_cost_as_per": "Price List",
			"buying_price_list": "Standard Buying",
			"is_active": 1,
			"is_default": 1,
			"remarks": f"{D.DEMO_TAG}: synthetic demo BOM. Not Mintree's formula and not derived from any published ingredient list.",
			"items": [
				{"item_code": code, "qty": qty, "bom_no": frappe.db.get_value("BOM", {"item": code, "is_default": 1, "docstatus": 1}, "name")}
				for code, qty in components
			],
		}
	)
	bom.insert(ignore_permissions=True)
	bom.submit()
	return bom.name


def ensure_fg_bom(fg: str) -> str:
	pack = frappe.db.get_value("Item", fg, "fo_pack_size") or ""
	m = re.match(r"(\d+(?:\.\d+)?)\s*ml", pack)
	per_unit = float(m.group(1)) / 1000 if m else D.DEFAULT_BULK_PER_UNIT_KG
	units = D.FG_BOM_UNITS
	components = [("DEMO-BULK-0001", round(per_unit * units, 3))] + [(pm, units) for pm in D.FG_PACK]
	return ensure_bom(fg, units, 0, components)


def load_opening_stock() -> str:
	from factory_ops.master_data.importer import SPECS

	header = [c.name + ("*" if c.required else "") for c in SPECS["Opening Stock"].columns]
	rows = []
	for item, warehouse, qty, rate, batch, days in D.OPENING_STOCK:
		if frappe.db.get_value("Bin", {"item_code": item, "actual_qty": (">", 0)}) or frappe.db.exists(
			"Stock Ledger Entry", {"item_code": item, "is_cancelled": 0}
		):
			continue
		row = {h: "" for h in header}
		row.update({"item_code*": item, "warehouse*": wh(warehouse), "qty*": qty, "valuation_rate*": rate})
		if batch:
			row.update({"batch_no": batch, "manufacturing_date": add_days(nowdate(), -1)})
		if days:
			row["expiry_date"] = add_days(nowdate(), days)
		rows.append(row)
	return master_data_import("Opening Stock", header, rows, "demo-opening-stock", posting_date=nowdate())
