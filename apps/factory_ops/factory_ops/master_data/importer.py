"""Master data import: validate a whole file first, then import it all-or-nothing (D-035).

ERPNext's Data Import checks mandatory fields and links but none of Mintree's rules, and it imports
row by row, so a file can end up half imported. This importer:

1. parses a CSV or XLSX file whose columns are defined once here (``SPECS``), the same definition
   that generates the downloadable templates, so a template cannot drift from its rules;
2. **validates every row** without writing anything and reports each problem as row / column /
   value / reason;
3. **imports only a file with no errors**, inside one savepoint: if any record fails while being
   created by ERPNext, everything is rolled back and the failing row is reported. Records are made
   with ERPNext's own documents and the importing user's own permissions.

Duplicates are never merged or overwritten: a key repeated inside the file, or already present in
the system, is an error. Changes to existing records are made in Desk, where they are audited.
"""

import csv
import io
import re
from collections import defaultdict
from dataclasses import dataclass, field

import frappe
from frappe import _
from frappe.utils import getdate

FACTORY_GROUPS = ("RM", "PM", "BULK", "FG")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class Column:
	name: str
	required: bool = False
	kind: str = "text"  # text, int, number, date, check, choice
	help: str = ""
	choices: tuple = ()
	example: tuple = ()  # one value per example row


@dataclass
class Spec:
	key: str
	label: str
	doctype: str
	columns: list
	rules: list = field(default_factory=list)  # validation rules, in plain words, for the template
	duplicates: str = ""
	needs_company: bool = False
	needs_posting_date: bool = False


@dataclass
class Issue:
	row: int  # 0 = file level; otherwise the spreadsheet row number (header is row 1)
	column: str
	value: str
	message: str
	severity: str = "Error"  # Error blocks the import; Warning does not


# ---------------------------------------------------------------------------
# Specifications (one per import type)
# ---------------------------------------------------------------------------
SPECS = {
	"Items": Spec(
		"Items",
		"Items and products (RM, PM, BULK, FG)",
		"Item",
		[
			Column("item_code", True, help="Unique code, max 140 characters", example=("RM-0101", "FG-0201")),
			Column("item_name", True, example=("Niacinamide (example)", "Serum 30 mL (example)")),
			Column(
				"item_group",
				True,
				help="An existing Item Group; factory items must sit under RM, PM, BULK or FG",
				example=("RM", "FG"),
			),
			Column(
				"stock_uom", True, help="An existing UOM (e.g. Kg, Gram, Litre, Nos)", example=("Kg", "Nos")
			),
			Column("description", example=("", "")),
			Column("brand", help="An existing Brand", example=("", "")),
			Column(
				"has_expiry_date", kind="check", help="1 = batches carry an expiry date", example=("1", "1")
			),
			Column(
				"shelf_life_in_days",
				kind="int",
				help="Required when has_expiry_date is 1; > 0",
				example=("730", "730"),
			),
			Column(
				"purchase_uom", help="An existing UOM; needs purchase_conversion_factor", example=("", "")
			),
			Column(
				"purchase_conversion_factor",
				kind="number",
				help="Stock units in one purchase unit; > 0",
				example=("", ""),
			),
			Column(
				"quality_inspection_template",
				help="An existing Quality Inspection Template",
				example=("", ""),
			),
			Column("default_supplier", help="An existing Supplier", example=("", "")),
			Column("supplier_part_no", example=("", "")),
			Column("lead_time_days", kind="int", help=">= 0", example=("14", "")),
			Column("reorder_level", kind="number", help=">= 0; give with reorder_qty", example=("50", "")),
			Column("reorder_qty", kind="number", help="> 0; give with reorder_level", example=("100", "")),
			Column(
				"reorder_warehouse",
				help="Usable warehouse for the reorder level; default: the group's production store",
				example=("", ""),
			),
			Column("valuation_rate", kind="number", help=">= 0", example=("1200", "")),
			Column("is_sales_item", kind="check", help="Default: 1 for FG, else 0", example=("", "")),
			Column(
				"is_purchase_item", kind="check", help="Default: 1 for RM and PM, else 0", example=("", "")
			),
		],
		rules=[
			"Items in RM, PM, BULK and FG are batch-tracked and need QC release (set by the item group).",
			"A Nos (whole-number) stock UOM is required for counted items such as FG units.",
			"has_expiry_date = 1 needs shelf_life_in_days > 0.",
			"purchase_uom different from stock_uom needs purchase_conversion_factor > 0.",
			"reorder_level and reorder_qty are given together.",
		],
		duplicates="item_code must not repeat in the file and must not exist already. Existing items are changed in Desk.",
	),
	"Suppliers": Spec(
		"Suppliers",
		"Suppliers",
		"Supplier",
		[
			Column("supplier_name", True, example=("Example Actives Pvt Ltd",)),
			Column("supplier_group", True, help="An existing Supplier Group", example=("SAMPLE Suppliers",)),
			Column(
				"supplier_type",
				kind="choice",
				choices=("Company", "Individual", "Partnership"),
				help="Default Company",
				example=("Company",),
			),
			Column("country", help="An existing Country; default India", example=("India",)),
			Column(
				"tax_id",
				help="Tax / GSTIN as registered; stored, not validated (GST not configured)",
				example=("",),
			),
			Column("email_id", help="A valid e-mail address", example=("orders@example.com",)),
			Column("mobile_no", example=("",)),
			Column("default_currency", help="An existing Currency", example=("INR",)),
			Column("payment_terms", help="An existing Payment Terms Template", example=("",)),
		],
		duplicates="supplier_name (case-insensitive) and tax_id must not repeat in the file or exist already.",
	),
	"Customers": Spec(
		"Customers",
		"Customers and distributors",
		"Customer",
		[
			Column("customer_name", True, example=("Example Distributor LLP",)),
			Column("customer_group", True, help="An existing Customer Group", example=("Distributors",)),
			Column("territory", True, help="An existing Territory", example=("India",)),
			Column(
				"customer_type",
				kind="choice",
				choices=("Company", "Individual", "Partnership"),
				help="Default Company",
				example=("Company",),
			),
			Column("credit_limit", kind="number", help=">= 0, for the selected company", example=("500000",)),
			Column(
				"default_price_list", help="An existing selling Price List", example=("Standard Selling",)
			),
			Column("payment_terms", help="An existing Payment Terms Template", example=("",)),
			Column("tax_id", help="Stored, not validated (GST not configured)", example=("",)),
			Column("email_id", help="A valid e-mail address", example=("accounts@example.com",)),
			Column("mobile_no", example=("",)),
		],
		duplicates="customer_name (case-insensitive) and tax_id must not repeat in the file or exist already.",
		needs_company=True,
	),
	"BOMs": Spec(
		"BOMs",
		"Bills of materials (one row per component)",
		"BOM",
		[
			Column(
				"bom_item",
				True,
				help="The BULK or FG item made",
				example=("BULK-0101", "BULK-0101", "FG-0201", "FG-0201"),
			),
			Column(
				"bom_quantity",
				True,
				kind="number",
				help="Quantity the BOM makes, in the item's stock UOM; same on every row of the BOM",
				example=("100", "100", "1000", "1000"),
			),
			Column(
				"process_loss_percentage",
				kind="number",
				help="0 to < 100",
				example=("2", "2", "0", "0"),
			),
			Column("component_item", True, example=("RM-0101", "RM-0102", "BULK-0101", "PM-0301")),
			Column(
				"component_qty",
				True,
				kind="number",
				help="> 0, in the component's stock UOM",
				example=("5", "95", "30", "1000"),
			),
		],
		rules=[
			"bom_item must be in BULK or FG; components must be stock items, not the bom_item itself, once per BOM.",
			"Two-stage production: an FG BOM consumes BULK and PM; a BULK BOM consumes RM (other mixes are warned about).",
			"A BULK component needs an active default BOM, existing or earlier in the same file.",
			"The BOMs are submitted and made the item's default.",
		],
		duplicates="An item that already has a submitted default BOM is refused; make new versions in Desk.",
		needs_company=True,
	),
	"Opening Stock": Spec(
		"Opening Stock",
		"Opening stock with batches",
		"Stock Reconciliation",
		[
			Column("item_code", True, example=("RM-0101", "FG-0201")),
			Column(
				"warehouse",
				True,
				help="A Usable or QC Hold warehouse of the company",
				example=("RM-STORES - SMM", "FG-QC-HOLD - SMM"),
			),
			Column("qty", True, kind="number", help="> 0; whole number for Nos", example=("25", "500")),
			Column("valuation_rate", True, kind="number", help="> 0, per stock UOM", example=("1200", "180")),
			Column("batch_no", help="Required for batch items", example=("LOT-OPEN-001", "FG-OPEN-001")),
			Column(
				"manufacturing_date",
				kind="date",
				help="YYYY-MM-DD; not after the posting date",
				example=("2026-06-01", "2026-09-15"),
			),
			Column(
				"expiry_date",
				kind="date",
				help="Required for items with expiry; in the future",
				example=("2028-05-31", "2028-09-14"),
			),
			Column("supplier_batch_no", example=("SUP-LOT-77", "")),
		],
		rules=[
			"Only Usable and QC Hold warehouses: stock counted into QC Hold awaits a QC Release; Rejected and other types are refused.",
			"A new batch is created with its dates; an existing batch must belong to the item and match its expiry.",
			"Expired batches are refused.",
			"Posted as one Stock Reconciliation (purpose Opening Stock) on the chosen posting date.",
		],
		duplicates="item_code + warehouse + batch_no must not repeat; an item with stock already in the company is refused (opening stock is entered once).",
		needs_company=True,
		needs_posting_date=True,
	),
	"Item Prices": Spec(
		"Item Prices",
		"Price lists and item prices",
		"Item Price",
		[
			Column("item_code", True, example=("FG-0201",)),
			Column("price_list", True, help="An existing, enabled Price List", example=("Standard Selling",)),
			Column("rate", True, kind="number", help="> 0, in the price list currency", example=("250",)),
			Column(
				"uom", help="The item's stock UOM or one of its conversions; default stock UOM", example=("",)
			),
			Column("valid_from", kind="date", help="YYYY-MM-DD; default today", example=("",)),
		],
		duplicates="item_code + price_list + uom + valid_from must not repeat or exist already; change existing prices in Desk.",
	),
}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def normalise_header(text: str) -> str:
	text = (text or "").strip().lower().replace("*", "").replace("(required)", "").strip()
	return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def read_rows(content: bytes | str, filename: str) -> tuple[list[str], list[dict]]:
	"""Header names and data rows (each with its spreadsheet row number under ``__row``).
	Frappe's File.get_content() returns text for a CSV and bytes for an XLSX: both are accepted."""
	if isinstance(content, str):
		content = content.encode("utf-8")
	if filename.lower().endswith(".xlsx"):
		from openpyxl import load_workbook

		wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
		ws = wb["Data"] if "Data" in wb.sheetnames else wb.worksheets[0]
		matrix = [[("" if v is None else str(v)) for v in row] for row in ws.iter_rows(values_only=True)]
	else:
		text = content.decode("utf-8-sig")
		matrix = list(csv.reader(io.StringIO(text)))
	if not matrix:
		return [], []
	header = [normalise_header(h) for h in matrix[0]]
	rows = []
	for idx, raw in enumerate(matrix[1:], start=2):
		values = [(v or "").strip() for v in raw]
		if not any(values):
			continue  # blank lines are ignored
		row = {h: (values[i] if i < len(values) else "") for i, h in enumerate(header) if h}
		row["__row"] = idx
		rows.append(row)
	return header, rows


def _parse_date(value: str):
	for pattern in (r"^\d{4}-\d{2}-\d{2}$", r"^\d{2}-\d{2}-\d{4}$", r"^\d{2}/\d{2}/\d{4}$"):
		if re.match(pattern, value):
			if pattern.startswith(r"^\d{4}"):
				return getdate(value)
			d, m, y = re.split(r"[-/]", value)
			return getdate(f"{y}-{m}-{d}")
	# spreadsheets may hand over a datetime string
	if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", value):
		return getdate(value[:10])
	raise ValueError


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
class Context:
	def __init__(self, spec: Spec, company: str | None, posting_date):
		self.spec = spec
		self.company = company
		self.posting_date = getdate(posting_date) if posting_date else None
		self.issues: list[Issue] = []

	def error(self, row, column, value, message):
		self.issues.append(
			Issue(row["__row"] if isinstance(row, dict) else row, column, str(value or ""), message)
		)

	def warn(self, row, column, value, message):
		self.issues.append(
			Issue(
				row["__row"] if isinstance(row, dict) else row, column, str(value or ""), message, "Warning"
			)
		)

	def exists(self, doctype, name) -> bool:
		return bool(name) and bool(frappe.db.exists(doctype, name))


def validate(
	spec_key: str, content: bytes, filename: str, company=None, posting_date=None
) -> tuple[list[dict], list[Issue]]:
	spec = SPECS[spec_key]
	ctx = Context(spec, company, posting_date)
	if spec.needs_company and not (company and frappe.db.exists("Company", company)):
		ctx.error(0, "company", company, _("Select the company the records belong to."))
	if spec.needs_posting_date and not posting_date:
		ctx.error(0, "posting_date", "", _("Select the posting date of the opening stock."))
	if not frappe.has_permission(spec.doctype, "create"):
		ctx.error(0, "", "", _("You are not permitted to create {0} records.").format(spec.doctype))
	try:
		header, rows = read_rows(content, filename)
	except Exception as e:
		ctx.error(0, "", filename, _("The file cannot be read as CSV or XLSX: {0}").format(e))
		return [], ctx.issues
	known = {c.name for c in spec.columns}
	for h in header:
		if h and h not in known:
			ctx.error(1, h, h, _("Unknown column. Use the template's column names."))
	for c in spec.columns:
		if c.required and c.name not in header:
			ctx.error(1, c.name, "", _("Required column is missing."))
	if not rows:
		ctx.error(0, "", "", _("The file has no data rows."))
	if any(i.severity == "Error" and i.row in (0, 1) for i in ctx.issues):
		return rows, ctx.issues
	for row in rows:
		_check_types(ctx, row)
	VALIDATORS[spec_key](ctx, rows)
	return rows, sorted(ctx.issues, key=lambda i: (i.row, i.column))


def _check_types(ctx: Context, row: dict):
	for c in ctx.spec.columns:
		value = row.get(c.name, "")
		if not value:
			if c.required:
				ctx.error(row, c.name, value, _("Required value is empty."))
			continue
		try:
			if c.kind == "int":
				if not re.match(r"^-?\d+$", value):
					raise ValueError
				row[c.name] = int(value)
			elif c.kind == "number":
				row[c.name] = float(value.replace(",", ""))
			elif c.kind == "date":
				row[c.name] = _parse_date(value)
			elif c.kind == "check":
				if value not in ("0", "1"):
					raise ValueError
				row[c.name] = int(value)
			elif c.kind == "choice" and value not in c.choices:
				ctx.error(row, c.name, value, _("Must be one of: {0}.").format(", ".join(c.choices)))
		except (ValueError, frappe.ValidationError):  # e.g. 31-02-2026: Frappe raises its own error
			expected = {
				"int": "a whole number",
				"number": "a number",
				"date": "a date (YYYY-MM-DD)",
				"check": "0 or 1",
			}
			ctx.error(row, c.name, value, _("Must be {0}.").format(expected[c.kind]))
			row[c.name] = None


def _duplicates_in_file(ctx: Context, rows, key_fn, column, label):
	seen = defaultdict(list)
	for row in rows:
		key = key_fn(row)
		if key:
			seen[key].append(row)
	for group in seen.values():
		if len(group) > 1:
			lines = ", ".join(str(r["__row"]) for r in group)
			for row in group:
				ctx.error(
					row,
					column,
					row.get(column),
					_("{0} appears more than once in the file (rows {1}).").format(label, lines),
				)


def _factory_group(item_group: str) -> str | None:
	"""The factory group (RM/PM/BULK/FG) an item group belongs to, walking up the tree."""
	group = item_group
	for _i in range(10):
		if not group:
			return None
		if group in FACTORY_GROUPS:
			return group
		group = frappe.db.get_value("Item Group", group, "parent_item_group")
	return None


def _validate_items(ctx: Context, rows):
	_duplicates_in_file(ctx, rows, lambda r: (r.get("item_code") or "").lower(), "item_code", _("Item code"))
	for row in rows:
		code = row.get("item_code")
		if code:
			if len(code) > 140:
				ctx.error(row, "item_code", code, _("At most 140 characters."))
			if frappe.db.exists("Item", code):
				ctx.error(row, "item_code", code, _("Item already exists; change it in Desk."))
		group = row.get("item_group")
		if group:
			if not frappe.db.exists("Item Group", group):
				ctx.error(row, "item_group", group, _("Item Group does not exist."))
			elif frappe.db.get_value("Item Group", group, "is_group"):
				ctx.error(row, "item_group", group, _("Choose a leaf Item Group, not a group of groups."))
			elif not _factory_group(group):
				ctx.warn(
					row,
					"item_group",
					group,
					_("Not under RM, PM, BULK or FG: the item will not be QC-controlled."),
				)
		for col in ("stock_uom", "purchase_uom"):
			if row.get(col) and not frappe.db.exists("UOM", row[col]):
				ctx.error(row, col, row[col], _("UOM does not exist."))
		if row.get("purchase_uom") and row.get("purchase_uom") != row.get("stock_uom"):
			if not row.get("purchase_conversion_factor") or row["purchase_conversion_factor"] <= 0:
				ctx.error(
					row,
					"purchase_conversion_factor",
					row.get("purchase_conversion_factor"),
					_("Required and > 0 when the purchase UOM differs from the stock UOM."),
				)
		if row.get("has_expiry_date") == 1 and not (row.get("shelf_life_in_days") or 0) > 0:
			ctx.error(
				row,
				"shelf_life_in_days",
				row.get("shelf_life_in_days"),
				_("Required and > 0 when has_expiry_date is 1."),
			)
		if (
			row.get("shelf_life_in_days") is not None
			and isinstance(row.get("shelf_life_in_days"), int)
			and row["shelf_life_in_days"] < 0
		):
			ctx.error(row, "shelf_life_in_days", row["shelf_life_in_days"], _("Must be > 0."))
		for col, ref in (
			("brand", "Brand"),
			("quality_inspection_template", "Quality Inspection Template"),
			("default_supplier", "Supplier"),
		):
			if row.get(col) and not frappe.db.exists(ref, row[col]):
				ctx.error(row, col, row[col], _("{0} does not exist.").format(ref))
		for col in ("lead_time_days", "valuation_rate", "reorder_level"):
			if isinstance(row.get(col), (int, float)) and row[col] < 0:
				ctx.error(row, col, row[col], _("Must not be negative."))
		if bool(row.get("reorder_level") not in (None, "")) != bool(row.get("reorder_qty") not in (None, "")):
			ctx.error(
				row, "reorder_qty", row.get("reorder_qty"), _("Give reorder_level and reorder_qty together.")
			)
		if isinstance(row.get("reorder_qty"), float) and row["reorder_qty"] <= 0:
			ctx.error(row, "reorder_qty", row["reorder_qty"], _("Must be > 0."))
		wh = row.get("reorder_warehouse")
		if wh:
			if not frappe.db.exists("Warehouse", wh):
				ctx.error(row, "reorder_warehouse", wh, _("Warehouse does not exist."))
			elif frappe.get_cached_value("Warehouse", wh, "warehouse_type") != "Usable":
				ctx.error(row, "reorder_warehouse", wh, _("Must be a Usable warehouse."))
		if row.get("reorder_level") not in (None, "") and not wh:
			ctx.warn(
				row,
				"reorder_warehouse",
				"",
				_("No reorder warehouse: the group's production store is used for every company."),
			)


def _validate_parties(ctx: Context, rows, name_col: str, doctype: str, group_col: str, group_doctype: str):
	_duplicates_in_file(ctx, rows, lambda r: (r.get(name_col) or "").strip().lower(), name_col, _("Name"))
	_duplicates_in_file(ctx, rows, lambda r: (r.get("tax_id") or "").strip().upper(), "tax_id", _("Tax ID"))
	for row in rows:
		name = row.get(name_col)
		if name and frappe.db.sql(
			f"select name from `tab{doctype}` where lower({name_col}) = %s or lower(name) = %s",
			(name.lower(), name.lower()),
		):
			ctx.error(row, name_col, name, _("{0} already exists; change it in Desk.").format(doctype))
		if row.get("tax_id") and frappe.db.exists(doctype, {"tax_id": row["tax_id"]}):
			ctx.error(row, "tax_id", row["tax_id"], _("Another {0} already has this tax ID.").format(doctype))
		if row.get(group_col) and not frappe.db.exists(group_doctype, row[group_col]):
			ctx.error(row, group_col, row[group_col], _("{0} does not exist.").format(group_doctype))
		elif row.get(group_col) and frappe.db.get_value(group_doctype, row[group_col], "is_group"):
			ctx.error(row, group_col, row[group_col], _("Choose a leaf group, not a group of groups."))
		if row.get("email_id") and not EMAIL.match(row["email_id"]):
			ctx.error(row, "email_id", row["email_id"], _("Not a valid e-mail address."))
		for col, ref in (
			("country", "Country"),
			("default_currency", "Currency"),
			("payment_terms", "Payment Terms Template"),
			("territory", "Territory"),
		):
			if row.get(col) and not frappe.db.exists(ref, row[col]):
				ctx.error(row, col, row[col], _("{0} does not exist.").format(ref))


def _validate_suppliers(ctx, rows):
	_validate_parties(ctx, rows, "supplier_name", "Supplier", "supplier_group", "Supplier Group")


def _validate_customers(ctx, rows):
	_validate_parties(ctx, rows, "customer_name", "Customer", "customer_group", "Customer Group")
	for row in rows:
		if isinstance(row.get("credit_limit"), float) and row["credit_limit"] < 0:
			ctx.error(row, "credit_limit", row["credit_limit"], _("Must not be negative."))
		pl = row.get("default_price_list")
		if pl and not frappe.db.exists("Price List", {"name": pl, "selling": 1}):
			ctx.error(row, "default_price_list", pl, _("Selling Price List does not exist."))


def _validate_boms(ctx: Context, rows):
	boms = defaultdict(list)
	for row in rows:
		if row.get("bom_item"):
			boms[row["bom_item"]].append(row)
	order = {item: i for i, item in enumerate(boms)}  # BOMs are built in order of first appearance
	for item, group in boms.items():
		first = group[0]
		if not frappe.db.exists("Item", item):
			for r in group:
				ctx.error(r, "bom_item", item, _("Item does not exist."))
			continue
		fgroup = _factory_group(frappe.db.get_value("Item", item, "item_group"))
		if fgroup not in ("BULK", "FG"):
			ctx.error(
				first,
				"bom_item",
				item,
				_("Only BULK and FG items are made with a BOM here (this item is in {0}).").format(
					fgroup or "-"
				),
			)
		if ctx.company and frappe.db.exists(
			"BOM", {"item": item, "company": ctx.company, "is_default": 1, "docstatus": 1}
		):
			ctx.error(first, "bom_item", item, _("Already has a default BOM; make a new version in Desk."))
		quantities = {r.get("bom_quantity") for r in group if r.get("bom_quantity") is not None}
		if len(quantities) > 1:
			for r in group:
				ctx.error(
					r,
					"bom_quantity",
					r.get("bom_quantity"),
					_("bom_quantity differs between rows of the same BOM."),
				)
		elif quantities and next(iter(quantities)) <= 0:
			ctx.error(first, "bom_quantity", first.get("bom_quantity"), _("Must be > 0."))
		losses = {r.get("process_loss_percentage") or 0 for r in group}
		if len(losses) > 1:
			ctx.error(
				first, "process_loss_percentage", "", _("Process loss differs between rows of the same BOM.")
			)
		elif not 0 <= next(iter(losses)) < 100:
			ctx.error(first, "process_loss_percentage", next(iter(losses)), _("Must be from 0 to below 100."))
		seen = set()
		for r in group:
			comp = r.get("component_item")
			if not comp:
				continue
			if comp == item:
				ctx.error(r, "component_item", comp, _("A BOM cannot consume the item it makes."))
				continue
			if comp in seen:
				ctx.error(r, "component_item", comp, _("Component listed twice in the same BOM."))
			seen.add(comp)
			if not frappe.db.exists("Item", comp):
				ctx.error(r, "component_item", comp, _("Item does not exist."))
				continue
			if not frappe.db.get_value("Item", comp, "is_stock_item"):
				ctx.error(r, "component_item", comp, _("Must be a stock item."))
			if isinstance(r.get("component_qty"), float) and r["component_qty"] <= 0:
				ctx.error(r, "component_qty", r["component_qty"], _("Must be > 0."))
			cgroup = _factory_group(frappe.db.get_value("Item", comp, "item_group"))
			if fgroup == "FG" and cgroup not in ("BULK", "PM"):
				ctx.warn(
					r,
					"component_item",
					comp,
					_(
						"An FG BOM normally consumes BULK and PM (two-stage production); this component is {0}."
					).format(cgroup or "-"),
				)
			if fgroup == "BULK" and cgroup not in ("RM", "BULK"):
				ctx.warn(
					r,
					"component_item",
					comp,
					_("A BULK BOM normally consumes RM; this component is {0}.").format(cgroup or "-"),
				)
			made_earlier = comp in order and order[comp] < order[item]
			if cgroup == "BULK" and not made_earlier and ctx.company:
				if not frappe.db.exists(
					"BOM",
					{"item": comp, "company": ctx.company, "is_default": 1, "is_active": 1, "docstatus": 1},
				):
					ctx.error(
						r,
						"component_item",
						comp,
						_(
							"This BULK component has no active default BOM; add its BOM to the file (before this one) or create it first."
						),
					)


def _validate_opening(ctx: Context, rows):
	_duplicates_in_file(
		ctx,
		rows,
		lambda r: (r.get("item_code"), r.get("warehouse"), r.get("batch_no") or "")
		if r.get("item_code")
		else None,
		"item_code",
		_("Item + warehouse + batch"),
	)
	batch_expiry = {}
	for row in rows:
		item = row.get("item_code")
		if not item:
			continue
		if not frappe.db.exists("Item", item):
			ctx.error(row, "item_code", item, _("Item does not exist."))
			continue
		meta = frappe.db.get_value(
			"Item", item, ["is_stock_item", "has_batch_no", "has_expiry_date", "stock_uom"], as_dict=True
		)
		if not meta.is_stock_item:
			ctx.error(row, "item_code", item, _("Not a stock item."))
		if ctx.company and frappe.db.exists(
			"Stock Ledger Entry", {"item_code": item, "company": ctx.company, "is_cancelled": 0}
		):
			ctx.error(
				row,
				"item_code",
				item,
				_("This item already has stock in the company; opening stock is entered once."),
			)
		wh = row.get("warehouse")
		if wh:
			w = frappe.db.get_value("Warehouse", wh, ["company", "is_group", "warehouse_type"], as_dict=True)
			if not w:
				ctx.error(row, "warehouse", wh, _("Warehouse does not exist."))
			elif ctx.company and w.company != ctx.company:
				ctx.error(row, "warehouse", wh, _("Warehouse belongs to another company."))
			elif w.is_group:
				ctx.error(row, "warehouse", wh, _("Choose a warehouse, not a group."))
			elif w.warehouse_type not in ("Usable", "QC Hold"):
				ctx.error(
					row,
					"warehouse",
					wh,
					_("Opening stock goes to a Usable or QC Hold warehouse (this is {0}).").format(
						w.warehouse_type or _("untyped")
					),
				)
		qty = row.get("qty")
		if isinstance(qty, float):
			if qty <= 0:
				ctx.error(row, "qty", qty, _("Must be > 0."))
			elif frappe.db.get_value("UOM", meta.stock_uom, "must_be_whole_number") and qty != int(qty):
				ctx.error(row, "qty", qty, _("{0} must be a whole number.").format(meta.stock_uom))
		if isinstance(row.get("valuation_rate"), float) and row["valuation_rate"] <= 0:
			ctx.error(row, "valuation_rate", row["valuation_rate"], _("Must be > 0."))
		batch = row.get("batch_no")
		if meta.has_batch_no and not batch:
			ctx.error(row, "batch_no", "", _("Required: the item is batch-tracked."))
		if batch and not meta.has_batch_no:
			ctx.error(row, "batch_no", batch, _("The item is not batch-tracked."))
		mfg, exp = row.get("manufacturing_date"), row.get("expiry_date")
		if (
			meta.has_batch_no
			and meta.has_expiry_date
			and not exp
			and not (batch and frappe.db.get_value("Batch", batch, "expiry_date"))
		):
			ctx.error(row, "expiry_date", "", _("Required: the item has an expiry date."))
		if exp and ctx.posting_date and exp < ctx.posting_date:
			ctx.error(row, "expiry_date", exp, _("The batch is expired on the posting date."))
		if mfg and exp and mfg >= exp:
			ctx.error(row, "manufacturing_date", mfg, _("Must be before the expiry date."))
		if mfg and ctx.posting_date and mfg > ctx.posting_date:
			ctx.error(row, "manufacturing_date", mfg, _("Must not be after the posting date."))
		if batch:
			existing = frappe.db.get_value("Batch", batch, ["item", "expiry_date"], as_dict=True)
			if existing and existing.item != item:
				ctx.error(
					row, "batch_no", batch, _("Batch exists for another item ({0}).").format(existing.item)
				)
			elif existing and exp and existing.expiry_date and getdate(existing.expiry_date) != exp:
				ctx.error(
					row, "expiry_date", exp, _("Batch exists with expiry {0}.").format(existing.expiry_date)
				)
			key = (item, batch)
			if key in batch_expiry and batch_expiry[key] != exp:
				ctx.error(row, "expiry_date", exp, _("The same batch has another expiry date in the file."))
			batch_expiry.setdefault(key, exp)


def _validate_prices(ctx: Context, rows):
	_duplicates_in_file(
		ctx,
		rows,
		lambda r: (
			r.get("item_code"),
			r.get("price_list"),
			r.get("uom") or "",
			str(r.get("valid_from") or ""),
		)
		if r.get("item_code")
		else None,
		"item_code",
		_("Item + price list + UOM + valid from"),
	)
	for row in rows:
		item, pl = row.get("item_code"), row.get("price_list")
		if item and not frappe.db.exists("Item", item):
			ctx.error(row, "item_code", item, _("Item does not exist."))
			continue
		if pl:
			info = frappe.db.get_value("Price List", pl, ["enabled"], as_dict=True)
			if not info:
				ctx.error(row, "price_list", pl, _("Price List does not exist."))
				continue
			if not info.enabled:
				ctx.error(row, "price_list", pl, _("Price List is disabled."))
		if isinstance(row.get("rate"), float) and row["rate"] <= 0:
			ctx.error(row, "rate", row["rate"], _("Must be > 0."))
		uom = row.get("uom")
		if uom and item:
			stock_uom = frappe.db.get_value("Item", item, "stock_uom")
			if uom != stock_uom and not frappe.db.exists(
				"UOM Conversion Detail", {"parent": item, "uom": uom}
			):
				ctx.error(row, "uom", uom, _("Not the item's stock UOM and no conversion is defined."))
		# ERPNext dates a price from today when valid_from is blank and refuses overlapping prices
		# for the same item, list and UOM: check the same here, before anything is imported
		if (
			item
			and pl
			and frappe.db.sql(
				"""select name from `tabItem Price` where item_code=%s and price_list=%s and uom=%s
				and (valid_upto is null or valid_upto >= %s)""",
				(
					item,
					pl,
					uom or frappe.db.get_value("Item", item, "stock_uom"),
					row.get("valid_from") or getdate(),
				),
			)
		):
			ctx.error(
				row,
				"item_code",
				item,
				_("A price for this item, price list and UOM already exists; change it in Desk."),
			)


VALIDATORS = {
	"Items": _validate_items,
	"Suppliers": _validate_suppliers,
	"Customers": _validate_customers,
	"BOMs": _validate_boms,
	"Opening Stock": _validate_opening,
	"Item Prices": _validate_prices,
}


# ---------------------------------------------------------------------------
# Import (only after a clean validation; all-or-nothing)
# ---------------------------------------------------------------------------
class ImportFailed(frappe.ValidationError):
	pass


def run(spec_key: str, rows: list[dict], company=None, posting_date=None) -> list[tuple[str, str]]:
	"""Create every record; on any failure roll back all of them and raise ImportFailed naming the row."""
	frappe.db.savepoint("fo_master_data_import")
	current = {"row": 0}
	try:
		created = BUILDERS[spec_key](rows, company, posting_date, current)
	except Exception as e:
		frappe.db.rollback(save_point="fo_master_data_import")
		message = frappe.utils.strip_html(str(e)) or e.__class__.__name__
		raise ImportFailed(
			_("Row {0}: {1}. Nothing was imported.").format(current["row"] or "-", message)
		) from e
	return created


def _build_items(rows, company, posting_date, current):
	created = []
	for row in rows:
		current["row"] = row["__row"]
		fgroup = _factory_group(row["item_group"])
		item = frappe.new_doc("Item")
		item.update(
			{
				"item_code": row["item_code"],
				"item_name": row["item_name"],
				"item_group": row["item_group"],
				"stock_uom": row["stock_uom"],
				"description": row.get("description") or row["item_name"],
				"brand": row.get("brand") or None,
				"is_stock_item": 1,
				"has_expiry_date": row.get("has_expiry_date") or 0,
				"shelf_life_in_days": row.get("shelf_life_in_days") or 0,
				"batch_number_series": f"{row['item_code']}-.YY.-.#####",
				"quality_inspection_template": row.get("quality_inspection_template") or None,
				"lead_time_days": row.get("lead_time_days") or 0,
				"valuation_rate": row.get("valuation_rate") or 0,
				"is_sales_item": row["is_sales_item"]
				if row.get("is_sales_item") is not None and row.get("is_sales_item") != ""
				else int(fgroup == "FG"),
				"is_purchase_item": row["is_purchase_item"]
				if row.get("is_purchase_item") is not None and row.get("is_purchase_item") != ""
				else int(fgroup in ("RM", "PM")),
			}
		)
		if row.get("purchase_uom") and row["purchase_uom"] != row["stock_uom"]:
			item.purchase_uom = row["purchase_uom"]
			item.append(
				"uoms", {"uom": row["purchase_uom"], "conversion_factor": row["purchase_conversion_factor"]}
			)
		if row.get("default_supplier"):
			item.append(
				"supplier_items",
				{"supplier": row["default_supplier"], "supplier_part_no": row.get("supplier_part_no")},
			)
		if row.get("reorder_level") not in (None, ""):
			warehouses = [row["reorder_warehouse"]] if row.get("reorder_warehouse") else []
			if not warehouses and fgroup:
				for co in [company] if company else frappe.get_all("Company", pluck="name"):
					group_store = frappe.db.get_value(
						"Item Default",
						{"parenttype": "Item Group", "parent": fgroup, "company": co},
						"fo_production_warehouse",
					)
					if group_store:
						warehouses.append(group_store)
			for wh in warehouses:
				item.append(
					"reorder_levels",
					{
						"warehouse": wh,
						"warehouse_reorder_level": row["reorder_level"],
						"warehouse_reorder_qty": row["reorder_qty"],
						"material_request_type": "Purchase",
					},
				)
		item.insert()
		created.append(("Item", item.name))
	return created


def _build_parties(doctype, name_col, group_col, rows, company, current):
	created = []
	for row in rows:
		current["row"] = row["__row"]
		doc = frappe.new_doc(doctype)
		values = {
			name_col: row[name_col],
			group_col: row[group_col],
			"tax_id": row.get("tax_id") or None,
			"email_id": row.get("email_id") or None,
			"mobile_no": row.get("mobile_no") or None,
			"payment_terms": row.get("payment_terms") or None,
		}
		if doctype == "Supplier":
			values.update(
				{
					"supplier_type": row.get("supplier_type") or "Company",
					"country": row.get("country") or "India",
					"default_currency": row.get("default_currency") or None,
				}
			)
		else:
			values.update(
				{
					"customer_type": row.get("customer_type") or "Company",
					"territory": row["territory"],
					"default_price_list": row.get("default_price_list") or None,
				}
			)
			if row.get("credit_limit"):
				doc.append("credit_limits", {"company": company, "credit_limit": row["credit_limit"]})
		doc.update({k: v for k, v in values.items() if v is not None})
		doc.insert()
		created.append((doctype, doc.name))
	return created


def _build_suppliers(rows, company, posting_date, current):
	return _build_parties("Supplier", "supplier_name", "supplier_group", rows, company, current)


def _build_customers(rows, company, posting_date, current):
	return _build_parties("Customer", "customer_name", "customer_group", rows, company, current)


def _build_boms(rows, company, posting_date, current):
	boms = defaultdict(list)
	for row in rows:
		boms[row["bom_item"]].append(row)
	created, made = [], {}
	for item, group in boms.items():
		current["row"] = group[0]["__row"]
		bom = frappe.new_doc("BOM")
		bom.update(
			{
				"item": item,
				"company": company,
				"quantity": group[0]["bom_quantity"],
				"process_loss_percentage": group[0].get("process_loss_percentage") or 0,
				"rm_cost_as_per": "Valuation Rate",
				"is_active": 1,
				"is_default": 1,
			}
		)
		for r in group:
			sub = made.get(r["component_item"]) or frappe.db.get_value(
				"BOM",
				{"item": r["component_item"], "company": company, "is_default": 1, "docstatus": 1},
				"name",
			)
			bom.append("items", {"item_code": r["component_item"], "qty": r["component_qty"], "bom_no": sub})
		bom.insert()
		bom.submit()
		made[item] = bom.name
		created.append(("BOM", bom.name))
	return created


def _build_opening(rows, company, posting_date, current):
	from factory_ops.quality.batch_status import recompute

	created = []
	batches = []
	for row in rows:
		current["row"] = row["__row"]
		batch = row.get("batch_no")
		if batch and not frappe.db.exists("Batch", batch):
			doc = frappe.get_doc(
				{
					"doctype": "Batch",
					"batch_id": batch,
					"item": row["item_code"],
					"manufacturing_date": row.get("manufacturing_date"),
					"expiry_date": row.get("expiry_date"),
					"fo_supplier_batch_no": row.get("supplier_batch_no") or None,
				}
			)
			doc.insert()
			created.append(("Batch", doc.name))
		if batch:
			batches.append(batch)
	current["row"] = rows[0]["__row"] if rows else 0
	recon = frappe.new_doc("Stock Reconciliation")
	recon.update(
		{
			"company": company,
			"purpose": "Opening Stock",
			"posting_date": posting_date,
			"set_posting_time": 1,
			"expense_account": frappe.db.get_value(
				"Account", {"company": company, "account_type": "Temporary", "is_group": 0}, "name"
			),
		}
	)
	for row in rows:
		recon.append(
			"items",
			{
				"item_code": row["item_code"],
				"warehouse": row["warehouse"],
				"qty": row["qty"],
				"valuation_rate": row["valuation_rate"],
				"use_serial_batch_fields": 1 if row.get("batch_no") else 0,
				"batch_no": row.get("batch_no") or None,
			},
		)
	recon.insert()
	recon.submit()
	created.append(("Stock Reconciliation", recon.name))
	for batch in dict.fromkeys(batches):
		recompute(batch)
	return created


def _build_prices(rows, company, posting_date, current):
	created = []
	for row in rows:
		current["row"] = row["__row"]
		doc = frappe.get_doc(
			{
				"doctype": "Item Price",
				"item_code": row["item_code"],
				"price_list": row["price_list"],
				"price_list_rate": row["rate"],
				"uom": row.get("uom") or frappe.db.get_value("Item", row["item_code"], "stock_uom"),
				"valid_from": row.get("valid_from") or None,
			}
		)
		doc.insert()
		created.append(("Item Price", doc.name))
	return created


BUILDERS = {
	"Items": _build_items,
	"Suppliers": _build_suppliers,
	"Customers": _build_customers,
	"BOMs": _build_boms,
	"Opening Stock": _build_opening,
	"Item Prices": _build_prices,
}
