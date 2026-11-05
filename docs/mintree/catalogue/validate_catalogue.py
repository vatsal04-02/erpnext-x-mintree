"""Validate the extracted public Mintree catalogue. Offline, deterministic, no invented data.

Reads products.csv / product_variants.csv / product_images.csv from this folder and writes
catalogue_validation.csv and catalogue_validation_summary.md.

"Confirmed" here means ONLY "published on vmintree.in on the extraction date". Nothing in this
file is confirmed by Mintree. A record becomes eligible for a production Item master only after
Mintree supplies the item code (SKU), pack size/UOM, item group and any other attribute listed in
``mintree_to_confirm``. The ``demo_code`` column is a deterministic DEMO identifier for local
demonstration only; it is not an item code and must never be imported into a production site.

Usage:  python3 -I validate_catalogue.py
"""

import csv
import html
import re
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|gm|gms|g|kg|ltr|l)\b", re.I)
BUNDLE_RE = re.compile(r"\b(kit|combo|set|gift|bag|box|bundle|pack of|sample cards?)\b|[&,]|\+", re.I)
REFILL_RE = re.compile(r"\brefill\b", re.I)


def read(name):
	with open(HERE / name, newline="", encoding="utf-8") as f:
		return list(csv.DictReader(f))


def pack_status(title: str, v: dict) -> tuple[str, list[str]]:
	"""Return (status, reasons). Status: PARSED_SINGLE | PARSED_REVIEW | MISSING."""
	if not v["pack_size_value"]:
		return "MISSING", ["no pack size in title"]
	reasons = []
	tokens = SIZE_RE.findall(title)
	if len(tokens) > 1:
		reasons.append("multiple size tokens in title")
	if v["pack_count"]:
		reasons.append(f"pack_count={v['pack_count']} was inferred from title text; verify")
	if re.search(r"\d\s*%", title) and v["pack_count"]:
		reasons.append("pack_count may come from a percentage")
	if re.search(r"gm\s*/\s*ml", title, re.I):
		reasons.append("unit ambiguous (gm/ml)")
	if BUNDLE_RE.search(title):
		reasons.append("title looks like a kit/combo/set: size may not describe a single unit")
	if REFILL_RE.search(title):
		reasons.append("refill pack: confirm whether it is a separate item from the base product")
	return ("PARSED_REVIEW" if reasons else "PARSED_SINGLE"), reasons


def main():
	products = read("products.csv")
	variants = {v["product_handle"]: v for v in read("product_variants.csv")}
	images = Counter(i["product_handle"] for i in read("product_images.csv"))
	rows = []
	for p in products:
		v = variants[p["handle"]]
		title = p["title"].strip()
		vendor = html.unescape(p["vendor"]).replace("\xa0", " ").strip()
		vendor_ok = vendor.lower() == "mintree"
		ps, ps_reasons = pack_status(title, v)
		to_confirm = ["item code (SKU): not published"]
		attr_missing = []
		if not p["product_type"]:
			attr_missing.append("product_type")
		if not p["description_text"].strip():
			attr_missing.append("description")
		if not p["tags"]:
			attr_missing.append("tags")
		if images[p["handle"]] == 0:
			attr_missing.append("image")
		if not v["barcode"]:
			attr_missing.append("barcode")
		if not vendor_ok:
			attr_missing.append(f"vendor/brand (site shows {vendor!r})")
		if not v["compare_at_price"]:
			attr_missing.append("mrp (compare-at price)")
		if ps != "PARSED_SINGLE":
			to_confirm.append("pack size / UOM: " + ("; ".join(ps_reasons)))
		if attr_missing:
			to_confirm.append("attributes: " + ", ".join(attr_missing))
		to_confirm.append("item group / category, HSN, shelf life, formulation link: not on website")
		rows.append(
			{
				"demo_code": f"DEMO-WEB-{p['product_id']}",
				"demo_label": "DEMO - NOT REAL",
				"product_id": p["product_id"],
				"handle": p["handle"],
				"title": title,
				"url": p["url"],
				"price_inr": v["price"],
				"mrp_inr": v["compare_at_price"],
				"public_confirmed_fields": "title;url;price"
				+ (";mrp" if v["compare_at_price"] else "")
				+ (";description" if p["description_text"].strip() else "")
				+ (";images" if images[p["handle"]] else ""),
				"sku_published": "yes" if v["sku"] else "no",
				"pack_status": ps,
				"pack_size_parsed": f"{v['pack_size_value']} {v['pack_size_unit']}".strip(),
				"pack_reasons": " | ".join(ps_reasons),
				"missing_attributes": ";".join(attr_missing),
				"looks_like_bundle_heuristic": "yes" if BUNDLE_RE.search(title) else "no",
				"storefront_available": "yes" if v["available_on_storefront"] == "1" else "no",
				"image_count": images[p["handle"]],
				"production_item_master_ready": "NO",
				"mintree_to_confirm": " || ".join(to_confirm),
			}
		)
	rows.sort(key=lambda r: r["handle"])
	with open(HERE / "catalogue_validation.csv", "w", newline="", encoding="utf-8") as f:
		w = csv.DictWriter(f, fieldnames=list(rows[0]))
		w.writeheader()
		w.writerows(rows)

	n = len(rows)
	c = Counter(r["pack_status"] for r in rows)
	attr = Counter(a.split(" ")[0] for r in rows for a in r["missing_attributes"].split(";") if a)
	summary = f"""# Catalogue validation summary

Source: `products.csv`, `product_variants.csv`, `product_images.csv` (public vmintree.in data,
extracted 2026-11-05). Generated by `validate_catalogue.py`; per-record detail in
`catalogue_validation.csv`.

"Confirmed" means only *published on the website*. **Nothing here is confirmed by Mintree.**

| Check | Count (of {n}) |
|---|---:|
| Records with a published SKU | {sum(r['sku_published'] == 'yes' for r in rows)} |
| Pack size missing | {c['MISSING']} |
| Pack size parsed, single unit, no warnings (`PARSED_SINGLE`) | {c['PARSED_SINGLE']} |
| Pack size parsed but needs review (`PARSED_REVIEW`) | {c['PARSED_REVIEW']} |
| Titles that look like a kit / combo / set / gift item (keyword heuristic) | {sum(r['looks_like_bundle_heuristic'] == 'yes' for r in rows)} |
| Missing product type | {attr['product_type']} |
| Missing description | {attr['description']} |
| Missing tags | {attr['tags']} |
| Missing barcode | {attr['barcode']} |
| Missing image | {attr['image']} |
| Vendor shown as something other than "Mintree" (e.g. HTML entity) | {attr['vendor/brand']} |
| Missing MRP (compare-at) | {attr['mrp']} |
| Not available on the storefront | {sum(r['storefront_available'] == 'no' for r in rows)} |
| Records ready for a production Item master | {sum(r['production_item_master_ready'] == 'YES' for r in rows)} |

## Rules
- No production Item master is created from this file. All {n} records need Mintree to supply an
  item code, and every pack size needs confirmation (the parse is from the product title only).
- `PARSED_SINGLE` is the *best* case: one size token, no kit/refill wording, no inferred count. It
  still needs Mintree sign-off before use.
- `demo_code` (`DEMO-WEB-<public product id>`) is for local demonstration only, labelled
  `DEMO - NOT REAL`, deterministic across runs, and unrelated to any Mintree item code.
- Not available from the website, and never filled in here: SKUs, item groups beyond the site's
  own product type, HSN/GST codes, shelf life, formulas, costs, suppliers, stock.

## Decisions Mintree must make
1. Item-code scheme (or supply the codes) for every sellable variant.
2. Pack size and UOM for every record, in particular the {c['MISSING']} with none and the
   {c['PARSED_REVIEW']} flagged for review.
3. Whether kits, combos, gift bags and refill packs are separate Items (with a Product Bundle or
   BOM) or marketing-only listings.
4. Item group / category scheme (the website's collections include internal-looking names).
5. Which records are discontinued, test or duplicates.
"""
	(HERE / "catalogue_validation_summary.md").write_text(summary, encoding="utf-8")
	print(summary.split("## Rules")[0])


if __name__ == "__main__":
	main()
