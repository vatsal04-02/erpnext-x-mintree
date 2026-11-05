"""Synthetic demo data: DEMO - NOT REAL.

Everything in this file is invented for a local demonstration. None of it is Mintree's formula,
supplier, price, QC limit or stock figure, and no publicly listed ingredient declaration was used to
make these BOMs. Every record created from it carries "DEMO" in its code or name and "(NOT REAL)"
in its name or description, so it can never be mistaken for client-confirmed data.
"""

DEMO_TAG = "DEMO - NOT REAL"

SUPPLIER_GROUP = "DEMO Suppliers (NOT REAL)"
SUPPLIERS = {
	"DEMO Supplier - Chemicals (NOT REAL)": "chemicals and actives",
	"DEMO Supplier - Packaging (NOT REAL)": "bottles, caps, labels and cartons",
}
CUSTOMER = ("DEMO Distributor - Nagpur (NOT REAL)", "Distributors", 500_000)

# code: (name, factory group, stock UOM, has expiry, shelf life days, supplier, buying rate INR)
ITEMS = {
	"DEMO-RM-0001": ("DEMO Surfactant Base (NOT REAL)", "RM", "Kg", 1, 730, "chem", 180.0),
	"DEMO-RM-0002": ("DEMO Glycerin (NOT REAL)", "RM", "Kg", 1, 1095, "chem", 120.0),
	"DEMO-RM-0003": ("DEMO Botanical Extract (NOT REAL)", "RM", "Kg", 1, 365, "chem", 950.0),
	"DEMO-RM-0004": ("DEMO Thickener (NOT REAL)", "RM", "Kg", 1, 730, "chem", 400.0),
	"DEMO-RM-0005": ("DEMO Preservative Blend (NOT REAL)", "RM", "Kg", 1, 730, "chem", 1500.0),
	"DEMO-RM-0006": ("DEMO Fragrance (NOT REAL)", "RM", "Kg", 1, 730, "chem", 2200.0),
	"DEMO-RM-0007": ("DEMO Process Water (NOT REAL)", "RM", "Kg", 1, 30, "chem", 2.0),
	"DEMO-PM-0001": ("DEMO Bottle 100 mL PET (NOT REAL)", "PM", "Nos", 0, 0, "pack", 9.0),
	"DEMO-PM-0002": ("DEMO Flip-top Cap (NOT REAL)", "PM", "Nos", 0, 0, "pack", 2.5),
	"DEMO-PM-0003": ("DEMO Front Label (NOT REAL)", "PM", "Nos", 0, 0, "pack", 1.2),
	"DEMO-PM-0004": ("DEMO Mono Carton (NOT REAL)", "PM", "Nos", 0, 0, "pack", 4.0),
	"DEMO-BULK-0001": ("DEMO Bulk for filling (NOT REAL formula)", "BULK", "Kg", 1, 365, None, 0),
}
SUPPLIER_KEY = {"chem": "DEMO Supplier - Chemicals (NOT REAL)", "pack": "DEMO Supplier - Packaging (NOT REAL)"}

# A made-up bulk recipe per 100 kg, 2 % process loss. NOT a Mintree formula.
BULK_BOM = (
	100,
	2,
	[
		("DEMO-RM-0007", 55),
		("DEMO-RM-0001", 25),
		("DEMO-RM-0002", 10),
		("DEMO-RM-0003", 5),
		("DEMO-RM-0004", 3),
		("DEMO-RM-0005", 1),
		("DEMO-RM-0006", 1),
	],
)
# FG BOM per 100 units: bulk = pack size in mL x 100 / 1000 kg (DEMO assumption: density 1 kg/L;
# 0.1 kg per unit when the pack size is unknown), plus one of each pack component.
FG_BOM_UNITS = 100
FG_PACK = ("DEMO-PM-0001", "DEMO-PM-0002", "DEMO-PM-0003", "DEMO-PM-0004")
DEFAULT_BULK_PER_UNIT_KG = 0.1

# Opening stock (DEMO): item, warehouse (without company suffix), qty, valuation rate, batch, days to expiry
OPENING_STOCK = (
	("DEMO-RM-0007", "RM-STORES", 400, 2.0, "DEMO-OPEN-WATER-1", 30),
	("DEMO-PM-0004", "PM-STORES", 300, 4.0, "DEMO-OPEN-CARTON-1", 0),  # no expiry
)

# Inspection templates (DEMO parameters and limits; not Mintree specifications)
QI_PARAMETERS = (
	"DEMO Appearance",
	"DEMO Odour",
	"DEMO pH",
	"DEMO Viscosity (cP)",
	"DEMO Fill volume (mL)",
	"DEMO Print and artwork",
	"DEMO Microbial count (cfu/g)",
)
QI_TEMPLATES = {
	"DEMO RM Incoming (NOT REAL)": [
		("DEMO Appearance", 0, None, None, "Conforms to the supplier COA"),
		("DEMO Odour", 0, None, None, "Characteristic"),
	],
	"DEMO PM Incoming (NOT REAL)": [
		("DEMO Appearance", 0, None, None, "No cracks, leaks or print defects"),
		("DEMO Print and artwork", 0, None, None, "Matches the approved artwork"),
	],
	"DEMO BULK In Process (NOT REAL)": [
		("DEMO Appearance", 0, None, None, "Smooth, homogeneous"),
		("DEMO pH", 1, 5.0, 7.0, None),
		("DEMO Viscosity (cP)", 1, 2000.0, 8000.0, None),
		("DEMO Microbial count (cfu/g)", 1, 0.0, 100.0, None),
	],
	"DEMO FG Outgoing (NOT REAL)": [
		("DEMO Appearance", 0, None, None, "Correct pack, label and batch print"),
		("DEMO Fill volume (mL)", 1, None, None, "Within the declared quantity"),
		("DEMO pH", 1, 5.0, 7.0, None),
	],
}
QI_TEMPLATE_BY_GROUP = {
	"RM": "DEMO RM Incoming (NOT REAL)",
	"PM": "DEMO PM Incoming (NOT REAL)",
	"BULK": "DEMO BULK In Process (NOT REAL)",
	"FG": "DEMO FG Outgoing (NOT REAL)",
}

# Website catalogue categories (all under FG, so every public product is a QC-controlled finished good)
CATEGORY_GROUPS = (
	"Skin Care",
	"Hair Care",
	"Bath & Body",
	"Salon Products",
	"Refills",
	"Gift Sets / Combos",
	"Uncategorised (review)",
)
MRP_PRICE_LIST = "MRP"
