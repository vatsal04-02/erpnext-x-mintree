"""DEMO - NOT REAL records for the R&D, Maintenance, Marketplace and Marketing modules.

Called by `seed.seed_demo_site` on a site marked `fo_demo_site` only. Every record is flagged and
labelled DEMO; nothing here is a Mintree formula, machine, order, campaign or figure. Idempotent:
each block checks for its own records first. Figures are deterministic (no randomness), so a
rebuilt demo shows the same numbers.
"""

import frappe
from frappe.utils import add_days, add_months, flt, get_first_day, getdate, nowdate

from factory_ops.demo import data as D

RND_LABEL = "DEMO - NOT REAL - NOT FOR COMMERCIAL PRODUCTION"


def seed_all(company: str, fg_item: str | None) -> dict:
	return {
		"rnd": seed_rnd(),
		"maintenance": seed_maintenance(company),
		"marketplace": seed_marketplace(company, fg_item),
		"marketing": seed_marketing(fg_item),
	}


# --------------------------------------------------------------------------- R&D
def seed_rnd() -> str:
	if frappe.db.exists("Research Project", {"is_demo": 1}):
		return "exists"
	bulk = "DEMO-BULK-0002"
	if not frappe.db.exists("Item", bulk):
		frappe.get_doc(
			{"doctype": "Item", "item_code": bulk, "item_name": "DEMO R&D trial bulk (NOT REAL formula)", "item_group": "BULK",
			 "stock_uom": "Kg", "is_stock_item": 1, "has_expiry_date": 1, "shelf_life_in_days": 365,
			 "description": RND_LABEL}
		).insert(ignore_permissions=True)  # fmt: skip
	if not frappe.db.exists("Ingredient Category", "DEMO Base"):
		for cat in ("DEMO Base", "DEMO Active", "DEMO Preservative", "DEMO Fragrance"):
			frappe.get_doc({"doctype": "Ingredient Category", "category_name": cat, "description": RND_LABEL}).insert(ignore_permissions=True)
		for code, cat in (("DEMO-RM-0007", "DEMO Base"), ("DEMO-RM-0001", "DEMO Base"), ("DEMO-RM-0002", "DEMO Base"),
			("DEMO-RM-0003", "DEMO Active"), ("DEMO-RM-0005", "DEMO Preservative"), ("DEMO-RM-0006", "DEMO Fragrance")):  # fmt: skip
			frappe.db.set_value("Item", code, "fo_ingredient_category", cat)
	project = frappe.get_doc(
		{"doctype": "Research Project", "title": "Lighter gel base", "is_demo": 1, "status": "Proposed",
		 "objective": "Explore a lighter gel base with a lower thickener level. Synthetic project for the demo.",
		 "target_product": "Gel cleanser (demo)", "start_date": add_days(nowdate(), -60), "target_date": add_days(nowdate(), 60)}
	).insert(ignore_permissions=True)  # fmt: skip
	project.status = "In Progress"
	project.save(ignore_permissions=True)
	rows_v1 = [("DEMO-RM-0007", 56, 1, "Base"), ("DEMO-RM-0001", 24, 1, "Cleansing"), ("DEMO-RM-0002", 10, 0, "Humectant"),
		("DEMO-RM-0003", 5, 0, "Botanical"), ("DEMO-RM-0004", 3, 0, "Thickener"), ("DEMO-RM-0005", 1, 1, "Preservation"),
		("DEMO-RM-0006", 1, 0, "Fragrance")]  # fmt: skip
	v1 = frappe.get_doc(
		{"doctype": "Formulation", "formulation_name": "Light gel base", "formulation_code": "DEMO-LGB", "is_demo": 1,
		 "project": project.name, "product_item": bulk, "batch_size_kg": 100,
		 "ingredients": [{"item_code": c, "percentage": p, "required": r, "function": fn} for c, p, r, fn in rows_v1]}
	).insert(ignore_permissions=True)  # fmt: skip
	v1.apply_action("send_for_review")
	v1.reload()
	v1.apply_action("start_trial")
	trial = frappe.get_doc(
		{"doctype": "Formulation Trial", "title": "1 kg lab batch", "formulation": v1.name, "record_type": "Trial Batch",
		 "trial_date": add_days(nowdate(), -30), "batch_size_kg": 1, "trial_batch_ref": "DEMO-TB-001", "outcome": "Pass",
		 "objective": "Check appearance and viscosity of the lighter base.",
		 "observations": "Clear gel, no separation after 24 h at room temperature (synthetic observation).",
		 "conclusion": "Proceed to stability."}
	).insert(ignore_permissions=True)  # fmt: skip
	trial.submit()
	v1.reload()
	v1.apply_action("approve", "Approved for the demo (synthetic)")
	frappe.get_doc(
		{"doctype": "Stability Study", "title": "Accelerated 40/75", "formulation": v1.name, "trial": trial.name,
		 "storage_condition": "40°C / 75% RH", "start_date": add_days(nowdate(), -28), "duration_days": 90, "status": "In Progress",
		 "acceptance_criteria": "Synthetic criteria for the demo: no separation; pH and viscosity within the R&D target.",
		 "readings": [
			{"interval": "Initial", "reading_date": add_days(nowdate(), -28), "appearance": "Clear gel", "ph": 5.8, "viscosity_cp": 4200, "within_spec": "Yes"},
			{"interval": "Week 2", "reading_date": add_days(nowdate(), -14), "appearance": "Clear gel", "ph": 5.7, "viscosity_cp": 4050, "within_spec": "Yes"},
			{"interval": "Week 4", "reading_date": nowdate(), "appearance": "Slight haze", "ph": 5.6, "viscosity_cp": 3900, "within_spec": "Yes",
			 "observation": "Haze noted; watch at week 8 (synthetic)."},
		 ]}
	).insert(ignore_permissions=True)  # fmt: skip
	v2 = frappe.get_doc("Formulation", v1.new_version())
	for r in v2.ingredients:
		if r.item_code == "DEMO-RM-0004":
			r.percentage = 2.5
		if r.item_code == "DEMO-RM-0007":
			r.percentage = 56.5
	v2.change_note = "Thickener 3 % -> 2.5 % for a lighter feel (synthetic)."
	v2.save(ignore_permissions=True)
	return f"{project.name}, {v1.name} (approved), {v2.name} (draft v2), {trial.name}"


# --------------------------------------------------------------------------- Maintenance
EQUIPMENT = (
	("DEMO Mixing vessel 500 L (NOT REAL)", "Machine", "DEMO Process Engineering", "MV-500", 1_200_000),
	("DEMO Tube filling line 1 (NOT REAL)", "Filling Machine", "DEMO Packmatic", "TF-60", 2_500_000),
	("DEMO Labelling machine (NOT REAL)", "Packaging Equipment", "DEMO Labeltech", "LB-200", 650_000),
	("DEMO Precision balance 6 kg (NOT REAL)", "Weighing Equipment", "DEMO Scales", "PB-6K", 85_000),
	("DEMO pH meter (NOT REAL)", "Laboratory Equipment", "DEMO LabInstruments", "PH-10", 45_000),
	("DEMO Purified water plant (NOT REAL)", "Utility", "DEMO Water Systems", "PW-1000", 900_000),
)
SPARE = "DEMO-SPARE-0001"


def seed_maintenance(company: str) -> str:
	from factory_ops.api.maintenance import register_asset
	from factory_ops.maintenance.setup import ensure_maintenance_masters

	if frappe.db.exists("Asset", {"fo_is_demo": 1}):
		return "exists"
	ensure_maintenance_masters(company)
	assets = []
	for i, (name, kind, make, model, value) in enumerate(EQUIPMENT):
		a = register_asset(
			asset_name=name, equipment_type=kind, company=company, manufacturer=make, model=model, serial_no=f"DEMO-SN-{i + 1:03d}",
			installation_date=str(add_months(getdate(nowdate()), -18 + i)), warranty_until=str(add_months(getdate(nowdate()), 6 + i)),
			warranty_details="DEMO warranty terms (NOT REAL)", purchase_amount=value,
		)  # fmt: skip
		frappe.db.set_value("Asset", a, "fo_is_demo", 1)
		assets.append(a)
	team = "DEMO Maintenance Team"
	if not frappe.db.exists("Asset Maintenance Team", team):
		frappe.get_doc(
			{"doctype": "Asset Maintenance Team", "maintenance_team_name": team, "company": company,
			 "maintenance_manager": "sample.maintmanager@sample.invalid",
			 "maintenance_team_members": [{"team_member": "sample.maintenance@sample.invalid", "maintenance_role": "Maintenance User"}]}
		).insert(ignore_permissions=True)  # fmt: skip
	for a, tasks in (
		(assets[0], [("Check agitator seals", "Monthly", -25), ("Clean-in-place verification", "Weekly", -5)]),
		(assets[1], [("Lubricate crimping jaws", "Weekly", -6), ("Replace heater element gaskets", "Quarterly", -80)]),
		(assets[3], [("Calibrate with reference weights", "Monthly", -27)]),
		(assets[4], [("Two-point buffer calibration", "Weekly", -2)]),
	):
		frappe.get_doc(
			{"doctype": "Asset Maintenance", "asset_name": a, "company": company, "maintenance_team": team,
			 "asset_maintenance_tasks": [
				{"maintenance_task": t, "maintenance_type": "Calibration" if "alibrat" in t else "Preventive Maintenance",
				 "maintenance_status": "Planned", "periodicity": p, "start_date": add_days(nowdate(), d),
				 "assign_to": "sample.maintenance@sample.invalid"} for t, p, d in tasks]}
		).insert(ignore_permissions=True)  # fmt: skip
	if not frappe.db.exists("Item", SPARE):
		frappe.get_doc(
			{"doctype": "Item", "item_code": SPARE, "item_name": "DEMO Filling nozzle O-ring kit (NOT REAL)", "item_group": "Spare Parts",
			 "stock_uom": "Nos", "is_stock_item": 1, "valuation_rate": 1200,
			 "reorder_levels": [{"warehouse": f"Stores - {frappe.db.get_value('Company', company, 'abbr')}", "warehouse_reorder_level": 4,
				"warehouse_reorder_qty": 10, "material_request_type": "Purchase"}]}
		).insert(ignore_permissions=True)  # fmt: skip
	stores = f"Stores - {frappe.db.get_value('Company', company, 'abbr')}"
	se = frappe.get_doc(
		{"doctype": "Stock Entry", "purpose": "Material Receipt", "stock_entry_type": "Material Receipt", "company": company,
		 "remarks": "DEMO opening stock of a spare part (NOT REAL)",
		 "items": [{"item_code": SPARE, "qty": 6, "t_warehouse": stores, "basic_rate": 1200}]}
	).insert(ignore_permissions=True)  # fmt: skip
	se.submit()
	last_month = get_first_day(add_months(getdate(nowdate()), -1))
	closed = frappe.get_doc(
		{"doctype": "Asset Repair", "asset": assets[1], "company": company, "failure_date": f"{add_days(last_month, 9)} 07:40:00",
		 "completion_date": f"{add_days(last_month, 9)} 13:10:00", "repair_status": "Completed", "fo_severity": "High",
		 "description": "Nozzle 3 dripping between fills (DEMO - NOT REAL).", "actions_performed": "Replaced nozzle O-rings, re-timed valve.",
		 "fo_root_cause": "Worn O-rings past their change interval (synthetic).", "fo_reported_by": "sample.production@sample.invalid",
		 "fo_technician": "sample.maintenance@sample.invalid", "repair_cost": 1500,
		 "stock_items": [{"item_code": SPARE, "warehouse": stores, "consumed_quantity": "3"}]}
	).insert(ignore_permissions=True)  # fmt: skip
	closed.submit()
	frappe.get_doc(
		{"doctype": "Asset Repair", "asset": assets[0], "company": company, "failure_date": f"{nowdate()} 06:15:00", "repair_status": "Pending",
		 "fo_severity": "Critical", "description": "Agitator motor tripping on start (DEMO - NOT REAL).",
		 "fo_reported_by": "sample.production@sample.invalid", "fo_technician": "sample.maintenance@sample.invalid"}
	).insert(ignore_permissions=True)  # fmt: skip
	frappe.db.set_value("Asset", assets[2], "fo_operational_status", "Idle")
	return f"{len(assets)} assets, 4 schedules, 1 open + 1 closed breakdown, spare {SPARE}"


# --------------------------------------------------------------------------- Marketplaces
SHARES = {"Amazon": 30, "Flipkart": 25, "Myntra": 15, "Nykaa": 15}
CHANNEL_CODE = {"Amazon": "AMZ", "Flipkart": "FK", "Myntra": "MYN", "Nykaa": "NYK"}


def seed_marketplace(company: str, fg_item: str | None) -> str:
	from factory_ops.marketplace import service
	from factory_ops.marketplace.setup import ensure_marketplace_channels

	ensure_marketplace_channels()
	customer = "DEMO Marketplace Buyers (NOT REAL)"
	if not frappe.db.exists("Customer", customer):
		frappe.get_doc(
			{"doctype": "Customer", "customer_name": customer, "customer_type": "Individual", "customer_group": "D2C Consumers",
			 "territory": "India" if frappe.db.exists("Territory", "India") else "All Territories"}
		).insert(ignore_permissions=True)  # fmt: skip
	for ch, pct in SHARES.items():
		doc = frappe.get_doc("Marketplace Channel", ch)
		doc.update({"allocation_percent": pct, "safety_stock": 2, "default_customer": customer})
		doc.save(ignore_permissions=True)
	if not fg_item:
		return "channel shares set; no website product on the site, so no listings or orders (none invented)"
	if frappe.db.exists("Marketplace Order", {"is_demo": 1}):
		return "exists"
	rate = flt(frappe.db.get_value("Item Price", {"item_code": fg_item, "price_list": "Standard Selling"}, "price_list_rate")) or None
	for ch, code in CHANNEL_CODE.items():
		if not frappe.db.exists("Marketplace Listing", {"channel": ch, "item_code": fg_item}):
			frappe.get_doc(
				{"doctype": "Marketplace Listing", "channel": ch, "item_code": fg_item, "marketplace_sku": f"DEMO-{code}-{fg_item}"[:140],
				 "listing_id": f"DEMO-{code}-LISTING-1", "channel_price": rate, "is_demo": 1}
			).insert(ignore_permissions=True)  # fmt: skip
	plan = [  # channel, n, status, qty
		("Amazon", 1, "New", 2), ("Amazon", 2, "Shipped", 1), ("Amazon", 3, "Delivered", 3), ("Amazon", 4, "Returned", 1),
		("Flipkart", 1, "New", 1), ("Flipkart", 2, "Accepted", 2), ("Flipkart", 3, "Cancelled", 1),
		("Myntra", 1, "New", 1), ("Myntra", 2, "Delivered", 2),
		("Nykaa", 1, "Accepted", 2), ("Nykaa", 2, "Delivered", 1),
	]  # fmt: skip
	made = 0
	for i, (ch, n, status, qty) in enumerate(plan):
		code = CHANNEL_CODE[ch]
		name, created = service.ingest_order(
			ch,
			{"external_order_id": f"DEMO-{code}-{n:04d}", "order_date": f"{add_days(nowdate(), -(i % 9))} 1{i % 10}:15:00", "order_status": status,
			 "payment_status": "Cash on delivery" if i % 3 == 0 else "Prepaid", "customer_name": f"DEMO buyer {i + 1} (masked)",
			 "ship_to_city": ("Nagpur", "Pune", "Mumbai", "Bengaluru", "Delhi")[i % 5],
			 "items": [{"marketplace_sku": f"DEMO-{code}-{fg_item}"[:140], "qty": qty, "rate": rate}]},
			source="Demo", is_demo=1,
		)  # fmt: skip
		made += created
		if status == "Returned" and created:
			frappe.get_doc(
				{"doctype": "Marketplace Return", "channel": ch, "marketplace_order": name, "external_return_id": f"DEMO-{code}-RET-0001",
				 "reason": "Damaged in transit", "status": "Requested", "is_demo": 1}
			).insert(ignore_permissions=True)  # fmt: skip
	if not frappe.db.exists("Marketplace Settlement", {"is_demo": 1}):
		gross = flt(rate) * 5
		frappe.get_doc(
			{"doctype": "Marketplace Settlement", "channel": "Amazon", "settlement_id": "DEMO-AMZ-STL-0001", "is_demo": 1,
			 "period_from": add_days(nowdate(), -14), "period_to": nowdate(), "payout_date": nowdate(), "gross_sales": gross,
			 "commission": round(gross * 0.12, 2), "fees": 120, "shipping_fees": 160, "tax_withheld": round(gross * 0.01, 2)}
		).insert(ignore_permissions=True)  # fmt: skip
	service.refresh_published_quantities(company)
	return f"listings on 4 channels for {fg_item}; {made} DEMO orders; 1 return; 1 settlement"


# --------------------------------------------------------------------------- Marketing
def seed_marketing(fg_item: str | None) -> str:
	if frappe.db.exists("Marketing Campaign", {"is_demo": 1}):
		return "exists"
	product = frappe.db.get_value("Item", fg_item, "item_name") if fg_item else "demo product"
	camps = []
	for name, platform, objective, status, start, budget in (
		("Monsoon care launch", "Meta (Facebook + Instagram)", "Leads", "Live", -42, 150000),
		("Salon partner outreach", "Facebook", "Leads", "Completed", -90, 60000),
		("Festive gifting", "Instagram", "Sales", "Awaiting Approval", 20, 200000),
	):
		c = frappe.get_doc(
			{"doctype": "Marketing Campaign", "campaign_name": name, "platform": platform, "objective": objective, "is_demo": 1,
			 "start_date": add_days(nowdate(), start), "end_date": add_days(nowdate(), start + 60), "budget": budget,
			 "owner_user": "sample.marketing@sample.invalid", "products": product,
			 "target_audience": "Synthetic audience for the demo (NOT REAL).", "brief": "DEMO - NOT REAL brief.",
			 "submitted_by": "sample.marketingexec@sample.invalid",
			 "approved_by": "sample.marketing@sample.invalid" if status in ("Live", "Completed") else None}
		)  # fmt: skip
		c.status = status
		c.flags.fo_transition = True
		c.insert(ignore_permissions=True)
		camps.append(c)
	for i, (title, kind, platform, days, status) in enumerate((
		("Routine reel", "Reel / video", "Instagram", -3, "Published"), ("Ingredient carousel", "Carousel", "Instagram", 2, "Scheduled"),
		("Lead form ad", "Ad creative", "Meta (Facebook + Instagram)", 5, "Approved"), ("Salon story", "Story", "Instagram", 9, "In Review"),
		("Gift box shoot", "Image post", "Instagram", 16, "In Production"), ("Festive email", "Email", "Other", 20, "Idea"),
	)):  # fmt: skip
		ct = frappe.get_doc(
			{"doctype": "Marketing Content", "title": title, "campaign": camps[0 if i < 4 else 2].name, "content_type": kind, "platform": platform,
			 "planned_date": add_days(nowdate(), days), "assigned_to": "sample.marketingexec@sample.invalid", "is_demo": 1,
			 "caption": "DEMO caption (NOT REAL).", "published_url": "https://example.invalid/demo-post" if status == "Published" else None}
		)  # fmt: skip
		ct.status = status
		ct.flags.fo_transition = True
		ct.insert(ignore_permissions=True)
	# weekly DEMO figures: deterministic shapes, labelled with their source
	for c, weeks, base in ((camps[0], 6, 14000), (camps[1], 8, 7000)):
		start = getdate(c.start_date)
		for w in range(weeks):
			spend = base + 900 * (w % 3)
			impressions = int(spend * 32)
			clicks = int(impressions * (0.011 + 0.001 * (w % 2)))
			leads = int(clicks * 0.06)
			frappe.get_doc(
				{"doctype": "Campaign Performance", "campaign": c.name, "source": "DEMO - NOT REAL", "period_start": add_days(start, 7 * w),
				 "period_end": add_days(start, 7 * w + 6), "spend": spend, "impressions": impressions, "reach": int(impressions * 0.6),
				 "clicks": clicks, "leads": leads, "notes": "Synthetic figures for the demo; not Mintree advertising results."}
			).insert(ignore_permissions=True)  # fmt: skip
	return "3 campaigns, 6 content items, 14 weeks of DEMO figures"


def fg_opening_stock(company: str, fg_item: str | None) -> str:
	"""DEMO opening stock of the website product in FG-STORES (released), so Available to Sell has rows
	before the scenario runs. Through Master Data Import, like the other opening stock."""
	from factory_ops.demo.seed import ABBR, master_data_import
	from factory_ops.master_data.importer import SPECS

	if not fg_item or frappe.db.exists("Stock Ledger Entry", {"item_code": fg_item, "is_cancelled": 0}):
		return "skipped"
	header = [c.name + ("*" if c.required else "") for c in SPECS["Opening Stock"].columns]
	row = {h: "" for h in header}
	batch = "DEMO-OPEN-FG-1"
	hold = f"FG-QC-HOLD - {ABBR}"
	row.update({"item_code*": fg_item, "warehouse*": hold, "qty*": 120, "valuation_rate*": 95,
		"batch_no": batch, "manufacturing_date": add_days(nowdate(), -20), "expiry_date": add_days(nowdate(), 700)})  # fmt: skip
	result = master_data_import("Opening Stock", header, [row], "demo-fg-opening-stock", posting_date=nowdate())
	# released through the real QC path (inspection accepted, QC Release) by the SAMPLE QC officer
	return f"{result}; {release_batch(company, fg_item, batch, hold)}"


def release_batch(company: str, item: str, batch: str, hold: str) -> str:
	from factory_ops.quality.doctype.qc_release.qc_release import create_quality_inspection

	user = frappe.session.user
	frappe.set_user("sample.qc@sample.invalid")
	try:
		rel = frappe.get_doc(
			{"doctype": "QC Release", "company": company, "item_code": item, "batch_no": batch, "source_warehouse": hold, "decision": "Release"}
		).insert()
		qi = frappe.get_doc("Quality Inspection", create_quality_inspection(rel.name, "Outgoing"))
		qi.manual_inspection = 1
		qi.status = "Accepted"
		qi.remarks = "DEMO inspection (NOT REAL results)"
		for r in qi.get("readings") or []:
			r.status = "Accepted"
		qi.save()
		qi.submit()
		rel.reload()
		rel.submit()
		return f"{batch} released by {frappe.session.user} ({rel.name})"
	finally:
		frappe.set_user(user)
