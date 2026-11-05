"""Milestone 6, Part B: master data import (validate first, all-or-nothing; D-035)."""

import csv
import io
import typing
from unittest import mock

import frappe
from frappe.utils import add_days, flt, nowdate

from factory_ops.master_data import importer
from factory_ops.master_data.importer import SPECS, read_rows
from factory_ops.master_data.templates import build_csv, build_xlsx
from factory_ops.tests.utils import USER_QC, FactoryOpsTestCase, ensure_user


def to_csv(header: list[str], rows: list[list]) -> bytes:
	buf = io.StringIO()
	w = csv.writer(buf)
	w.writerow(header)
	w.writerows(rows)
	return buf.getvalue().encode()


class MasterDataTestCase(FactoryOpsTestCase):
	company = "_FO Perpetual Company"
	company_abbr = "_FOPC"

	def setUp(self):
		super().setUp()
		self.tag = frappe.generate_hash(length=5).upper()

	def make_import(self, import_type, content: bytes, filename="data.csv", company=None, posting_date=None):
		f = frappe.get_doc(
			{"doctype": "File", "file_name": f"{self.tag}-{filename}", "is_private": 1, "content": content}
		).insert(ignore_permissions=True)
		doc = frappe.get_doc(
			{
				"doctype": "Master Data Import",
				"import_type": import_type,
				"company": company,
				"posting_date": posting_date,
				"import_file": f.file_url,
			}
		).insert()
		return doc

	def check(self, doc):
		doc.validate_file()
		doc.reload()
		return doc

	def issues(self, doc, severity="Error"):
		return [(i.row_no, i.column_name, i.message) for i in doc.issues if i.severity == severity]

	def assert_issue(self, doc, row, column, text):
		self.assertTrue(
			any(r == row and c == column and text.lower() in m.lower() for r, c, m in self.issues(doc)),
			f"no error at row {row} / {column} containing '{text}': {self.issues(doc)}",
		)


class TestTemplates(MasterDataTestCase):
	def test_every_template_round_trips_and_documents_its_rules(self):
		from openpyxl import load_workbook

		for key, spec in SPECS.items():
			xlsx = build_xlsx(key)
			wb = load_workbook(io.BytesIO(xlsx))
			self.assertEqual(wb.sheetnames, ["Data", "Instructions"], key)
			header, rows = read_rows(xlsx, "t.xlsx")
			self.assertEqual(header, [c.name for c in spec.columns], key)
			self.assertTrue(rows, f"{key}: example rows")
			instructions = "\n".join(
				str(c.value) for row in wb["Instructions"].iter_rows() for c in row if c.value
			)
			self.assertIn("Duplicates", instructions)
			self.assertIn(spec.duplicates[:30], instructions)
			for c in spec.columns:
				self.assertIn(c.name, instructions)
			required = [h for h in next(csv.reader(io.StringIO(build_csv(key).decode())))]
			self.assertEqual([h.rstrip("*") for h in required], [c.name for c in spec.columns])
			self.assertEqual(
				{h[:-1] for h in required if h.endswith("*")}, {c.name for c in spec.columns if c.required}
			)


class TestItemImport(MasterDataTestCase):
	HEADER: typing.ClassVar[list[str]] = [
		"item_code*",
		"item_name*",
		"item_group*",
		"stock_uom*",
		"has_expiry_date",
		"shelf_life_in_days",
		"purchase_uom",
		"purchase_conversion_factor",
		"quality_inspection_template",
		"reorder_level",
		"reorder_qty",
	]

	def test_valid_file_imports_all_rows_with_factory_controls(self):
		rm, fg = f"T-RM-{self.tag}", f"T-FG-{self.tag}"
		doc = self.make_import(
			"Items",
			to_csv(
				self.HEADER,
				[
					[
						rm,
						"Test RM",
						"RM",
						"Kg",
						"1",
						"730",
						"Gram",
						"0.001",
						"SAMPLE RM Incoming",
						"50",
						"100",
					],
					[fg, "Test FG", "FG", "Nos", "1", "365", "", "", "", "", ""],
				],
			),
		)
		self.assertEqual(self.check(doc).status, "Ready to Import")
		self.assertEqual(self.issues(doc), [])
		doc.start_import()
		doc.reload()
		self.assertEqual(doc.status, "Imported")
		item = frappe.get_doc("Item", rm)
		self.assertEqual(
			(item.fo_qc_required, item.has_batch_no, item.has_expiry_date, item.shelf_life_in_days),
			(1, 1, 1, 730),
		)
		self.assertEqual(item.quality_inspection_template, "SAMPLE RM Incoming")
		self.assertEqual(
			(item.purchase_uom, flt(next(u.conversion_factor for u in item.uoms if u.uom == "Gram"))),
			("Gram", 0.001),
		)
		self.assertTrue(item.reorder_levels)
		self.assertEqual(flt(item.reorder_levels[0].warehouse_reorder_qty), 100)
		self.assertEqual(
			frappe.db.get_value("Warehouse", item.reorder_levels[0].warehouse, "warehouse_type"), "Usable"
		)
		self.assertEqual((item.is_purchase_item, frappe.db.get_value("Item", fg, "is_sales_item")), (1, 1))

	def test_invalid_file_reports_every_problem_and_imports_nothing(self):
		existing = "SAMPLE-RM-0001"
		dup = f"T-DUP-{self.tag}"
		doc = self.make_import(
			"Items",
			to_csv(
				[*self.HEADER, "colour"],
				[
					[dup, "a", "RM", "Kg", "", "", "", "", "", "", "", ""],
					[dup, "b", "RM", "Kg", "", "", "", "", "", "", "", ""],
					[existing, "c", "RM", "Kg", "", "", "", "", "", "", "", ""],
				],
			),
		)
		self.check(doc)
		self.assertEqual(doc.status, "Has Errors")
		self.assert_issue(doc, 1, "colour", "Unknown column")
		doc2 = self.make_import(
			"Items",
			to_csv(
				self.HEADER,
				[
					[dup, "a", "RM", "Kg", "", "", "", "", "", "", ""],
					[dup, "b", "RM", "Kg", "", "", "", "", "", "", ""],
					[existing, "c", "RM", "Kg", "", "", "", "", "", "", ""],
					[f"T-X1-{self.tag}", "d", "No Such Group", "Kg", "", "", "", "", "", "", ""],
					[f"T-X2-{self.tag}", "e", "RM", "Zorkmid", "", "", "", "", "", "", ""],
					[f"T-X3-{self.tag}", "f", "RM", "Kg", "1", "", "", "", "", "", ""],
					[f"T-X4-{self.tag}", "g", "RM", "Kg", "yes", "", "", "", "", "", ""],
					[f"T-X5-{self.tag}", "", "RM", "Kg", "", "", "Gram", "", "", "", ""],
					[f"T-X6-{self.tag}", "h", "RM", "Kg", "", "", "", "", "No Template", "10", ""],
					[f"T-X7-{self.tag}", "i", "Factory Items", "Kg", "", "", "", "", "", "", ""],
				],
			),
		)
		self.check(doc2)
		self.assertEqual(doc2.status, "Has Errors")
		for row, column, text in (
			(2, "item_code", "more than once"),
			(3, "item_code", "more than once"),
			(4, "item_code", "already exists"),
			(5, "item_group", "does not exist"),
			(6, "stock_uom", "UOM does not exist"),
			(7, "shelf_life_in_days", "Required and > 0"),
			(8, "has_expiry_date", "0 or 1"),
			(9, "item_name", "Required value is empty"),
			(9, "purchase_conversion_factor", "Required and > 0"),
			(10, "quality_inspection_template", "does not exist"),
			(10, "reorder_qty", "together"),
			(11, "item_group", "leaf"),
		):
			self.assert_issue(doc2, row, column, text)
		self.assertIn("rows with errors", doc2.summary)
		self.assertRaises(frappe.ValidationError, doc2.start_import)
		self.assertFalse(frappe.db.exists("Item", dup))
		self.assertFalse(frappe.db.exists("Item", f"T-X1-{self.tag}"))

	def test_failure_during_import_rolls_back_every_record(self):
		a, b = f"T-A-{self.tag}", f"T-B-{self.tag}"
		doc = self.make_import(
			"Items",
			to_csv(
				self.HEADER,
				[
					[a, "a", "RM", "Kg", "", "", "", "", "", "", ""],
					[b, "b", "RM", "Kg", "", "", "", "", "", "", ""],
				],
			),
		)
		self.assertEqual(self.check(doc).status, "Ready to Import")
		original = importer.BUILDERS["Items"]

		def fails_on_second_row(rows, company, posting_date, current):
			original(rows[:1], company, posting_date, current)  # the first item is really created
			current["row"] = rows[1]["__row"]
			frappe.throw("simulated ERPNext refusal")

		with mock.patch.dict(importer.BUILDERS, {"Items": fails_on_second_row}):
			doc.start_import()
		doc.reload()
		self.assertEqual(doc.status, "Failed")
		self.assertIn("Row 3", doc.summary)
		self.assertIn("Nothing was imported", doc.summary)
		self.assertFalse(frappe.db.exists("Item", a))  # rolled back
		self.assertFalse(frappe.db.exists("Item", b))

	def test_file_changed_after_validation_is_checked_again(self):
		code = f"T-C-{self.tag}"
		doc = self.make_import(
			"Items", to_csv(self.HEADER, [[code, "a", "RM", "Kg", "", "", "", "", "", "", ""]])
		)
		self.assertEqual(self.check(doc).status, "Ready to Import")
		bad = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"{self.tag}-bad.csv",
				"is_private": 1,
				"content": to_csv(self.HEADER, [[code, "a", "Nope", "Kg", "", "", "", "", "", "", ""]]),
			}
		).insert(ignore_permissions=True)
		doc.import_file = bad.file_url
		doc.save()
		self.assertEqual(doc.status, "Draft")  # a new file must be validated again
		doc.db_set("status", "Ready to Import")  # even if the status were forced ...
		self.assertRaises(frappe.ValidationError, frappe.get_doc("Master Data Import", doc.name).start_import)
		self.assertFalse(frappe.db.exists("Item", code))  # ... import re-validates the file

	def test_xlsx_template_with_examples_validates(self):
		# the shipped template, examples included, is itself a valid Items file once codes are new
		from openpyxl import load_workbook

		wb = load_workbook(io.BytesIO(build_xlsx("Items")))
		ws = wb["Data"]
		for row in ws.iter_rows(min_row=2):
			row[0].value = f"{row[0].value}-{self.tag}"
		buf = io.BytesIO()
		wb.save(buf)
		doc = self.make_import("Items", buf.getvalue(), "items.xlsx")
		self.assertEqual(self.check(doc).status, "Ready to Import", self.issues(doc))

	def test_permission_needed_for_the_target_doctype(self):
		doc = self.make_import(
			"Items", to_csv(self.HEADER, [[f"T-P-{self.tag}", "a", "RM", "Kg", "", "", "", "", "", "", ""]])
		)
		ensure_user("fo-sales-master@example.com", ["Sales Master Manager"])
		with self.set_user("fo-sales-master@example.com"):
			_rows, issues = importer.validate(
				"Items", to_csv(self.HEADER, [["x", "a", "RM", "Kg", "", "", "", "", "", "", ""]]), "x.csv"
			)
		self.assertTrue(any("not permitted" in i.message for i in issues))
		with self.set_user(USER_QC):  # QC has no right on the import form at all
			self.assertRaises(
				frappe.PermissionError, frappe.get_doc("Master Data Import", doc.name).validate_file
			)


class TestPartyImport(MasterDataTestCase):
	def test_suppliers_valid_and_duplicates(self):
		name = f"Test Supplier {self.tag}"
		header = ["supplier_name*", "supplier_group*", "supplier_type", "country", "tax_id", "email_id"]
		doc = self.make_import(
			"Suppliers",
			to_csv(
				header, [[name, "SAMPLE Suppliers", "Company", "India", f"TAX{self.tag}", "a@example.com"]]
			),
		)
		self.assertEqual(self.check(doc).status, "Ready to Import", self.issues(doc))
		doc.start_import()
		self.assertTrue(frappe.db.exists("Supplier", {"supplier_name": name, "tax_id": f"TAX{self.tag}"}))
		bad = self.make_import(
			"Suppliers",
			to_csv(
				header,
				[
					[name.upper(), "SAMPLE Suppliers", "", "", "", ""],  # exists (case-insensitive)
					[f"New {self.tag}", "SAMPLE Suppliers", "", "", f"TAX{self.tag}", ""],  # tax id exists
					[f"Other {self.tag}", "All Supplier Groups", "Robot", "Atlantis", "", "not-an-email"],
				],
			),
		)
		self.check(bad)
		self.assert_issue(bad, 2, "supplier_name", "already exists")
		self.assert_issue(bad, 3, "tax_id", "already has this tax ID")
		self.assert_issue(bad, 4, "supplier_group", "leaf group")
		self.assert_issue(bad, 4, "supplier_type", "Must be one of")
		self.assert_issue(bad, 4, "country", "does not exist")
		self.assert_issue(bad, 4, "email_id", "valid e-mail")

	def test_customers_with_credit_limit_need_a_company(self):
		name = f"Test Distributor {self.tag}"
		header = ["customer_name*", "customer_group*", "territory*", "credit_limit", "default_price_list"]
		rows = [[name, "Distributors", "All Territories", "300000", "Standard Selling"]]
		no_company = self.make_import("Customers", to_csv(header, rows))
		self.check(no_company)
		self.assert_issue(no_company, 0, "company", "Select the company")
		doc = self.make_import("Customers", to_csv(header, rows), company=self.company)
		self.assertEqual(self.check(doc).status, "Ready to Import", self.issues(doc))
		doc.start_import()
		cust = frappe.get_doc("Customer", {"customer_name": name})
		self.assertEqual(
			[(r.company, flt(r.credit_limit)) for r in cust.credit_limits], [(self.company, 300000)]
		)
		dup = self.make_import(
			"Customers",
			to_csv(
				header,
				[
					[f"X {self.tag}", "Distributors", "All Territories", "-5", "Standard Buying"],
					[f"x {self.tag}", "Distributors", "All Territories", "", ""],
				],
			),
			company=self.company,
		)
		self.check(dup)
		self.assert_issue(dup, 2, "credit_limit", "negative")
		self.assert_issue(dup, 2, "default_price_list", "Selling Price List does not exist")
		self.assert_issue(dup, 2, "customer_name", "more than once")


class TestBOMImport(MasterDataTestCase):
	def make_items(self):
		from factory_ops.tests.utils import ensure_batch_item

		codes = {k: f"T-{k}-{self.tag}" for k in ("RM1", "RM2", "PM", "BULK", "FG")}
		groups = {
			"RM1": ("RM", "Kg"),
			"RM2": ("RM", "Kg"),
			"PM": ("PM", "Nos"),
			"BULK": ("BULK", "Kg"),
			"FG": ("FG", "Nos"),
		}
		for k, code in codes.items():
			g, uom = groups[k]
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": code,
					"item_name": code,
					"item_group": g,
					"stock_uom": uom,
					"is_stock_item": 1,
				}
			).insert()
		return codes

	def test_two_stage_boms_import_with_sub_assembly(self):
		c = self.make_items()
		header = [
			"bom_item*",
			"bom_quantity*",
			"process_loss_percentage",
			"component_item*",
			"component_qty*",
		]
		doc = self.make_import(
			"BOMs",
			to_csv(
				header,
				[
					[c["BULK"], "100", "2", c["RM1"], "5"],
					[c["BULK"], "100", "2", c["RM2"], "95"],
					[c["FG"], "1000", "", c["BULK"], "30"],
					[c["FG"], "1000", "", c["PM"], "1000"],
				],
			),
			company=self.company,
		)
		self.assertEqual(self.check(doc).status, "Ready to Import", self.issues(doc))
		doc.start_import()
		bulk = frappe.get_doc("BOM", {"item": c["BULK"], "is_default": 1, "docstatus": 1})
		fg = frappe.get_doc("BOM", {"item": c["FG"], "is_default": 1, "docstatus": 1})
		self.assertEqual((flt(bulk.quantity), flt(bulk.process_loss_percentage)), (100, 2))
		self.assertEqual(next(r.bom_no for r in fg.items if r.item_code == c["BULK"]), bulk.name)
		# a second file for the same items is refused (versions are made in Desk)
		again = self.make_import(
			"BOMs", to_csv(header, [[c["BULK"], "100", "", c["RM1"], "5"]]), company=self.company
		)
		self.check(again)
		self.assert_issue(again, 2, "bom_item", "Already has a default BOM")

	def test_bom_errors_and_warnings(self):
		c = self.make_items()
		header = [
			"bom_item*",
			"bom_quantity*",
			"process_loss_percentage",
			"component_item*",
			"component_qty*",
		]
		doc = self.make_import(
			"BOMs",
			to_csv(
				header,
				[
					[c["FG"], "1000", "", c["BULK"], "30"],  # BULK has no BOM yet
					[c["FG"], "500", "", c["RM1"], "1"],  # quantity differs; RM in FG BOM (warning)
					[c["FG"], "1000", "", c["FG"], "1"],  # itself
					[c["RM2"], "10", "", c["RM1"], "1"],  # RM is not made with a BOM
					[c["BULK"], "100", "150", c["PM"], "0"],  # loss out of range; PM in BULK (warning); qty 0
				],
			),
			company=self.company,
		)
		self.check(doc)
		self.assertEqual(doc.status, "Has Errors")
		self.assert_issue(doc, 2, "component_item", "no active default BOM")
		self.assert_issue(doc, 2, "bom_quantity", "differs")
		self.assert_issue(doc, 4, "component_item", "cannot consume the item it makes")
		self.assert_issue(doc, 5, "bom_item", "Only BULK and FG")
		self.assert_issue(doc, 6, "process_loss_percentage", "0 to below 100")
		self.assert_issue(doc, 6, "component_qty", "> 0")
		warnings = self.issues(doc, "Warning")
		self.assertTrue(any(r == 3 and "two-stage" in m for r, _c, m in warnings))
		self.assertTrue(any(r == 6 and "normally consumes RM" in m for r, _c, m in warnings))


class TestOpeningStockImport(MasterDataTestCase):
	HEADER: typing.ClassVar[list[str]] = [
		"item_code*",
		"warehouse*",
		"qty*",
		"valuation_rate*",
		"batch_no",
		"manufacturing_date",
		"expiry_date",
		"supplier_batch_no",
	]

	def new_item(self, group, uom, expiry=1):
		code = f"T-{group}-{frappe.generate_hash(length=6).upper()}"
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": code,
				"item_name": code,
				"item_group": group,
				"stock_uom": uom,
				"is_stock_item": 1,
				"has_expiry_date": expiry,
				"shelf_life_in_days": 365 if expiry else 0,
			}
		).insert()
		return code

	def test_valid_opening_stock_posts_once_with_batches_and_qc_status(self):
		rm, fg = self.new_item("RM", "Kg"), self.new_item("FG", "Nos")
		exp = add_days(nowdate(), 400)
		doc = self.make_import(
			"Opening Stock",
			to_csv(
				self.HEADER,
				[
					[
						rm,
						self.wh["RM-STORES"],
						"25",
						"1200",
						f"OB-{self.tag}-1",
						add_days(nowdate(), -30),
						exp,
						"SUP-77",
					],
					[
						fg,
						self.wh["FG-QC-HOLD"],
						"500",
						"180",
						f"OB-{self.tag}-2",
						add_days(nowdate(), -5),
						exp,
						"",
					],
				],
			),
			company=self.company,
			posting_date=nowdate(),
		)
		self.assertEqual(self.check(doc).status, "Ready to Import", self.issues(doc))
		doc.start_import()
		doc.reload()
		self.assertEqual(doc.status, "Imported", doc.summary)
		recon = frappe.get_doc(
			"Stock Reconciliation",
			{
				"purpose": "Opening Stock",
				"docstatus": 1,
				"company": self.company,
				"name": ("in", [r[1] for r in frappe.parse_json(doc.created_records)]),
			},
		)
		sle = frappe.get_all(
			"Stock Ledger Entry",
			{"voucher_no": recon.name, "is_cancelled": 0},
			["item_code", "warehouse", "actual_qty"],
		)
		self.assertEqual(
			sorted((r.item_code, r.warehouse, flt(r.actual_qty)) for r in sle),
			sorted([(rm, self.wh["RM-STORES"], 25), (fg, self.wh["FG-QC-HOLD"], 500)]),
		)
		self.assertEqual(
			frappe.db.get_value("Batch", f"OB-{self.tag}-1", ["expiry_date", "fo_supplier_batch_no"]),
			(frappe.utils.getdate(exp), "SUP-77"),
		)
		self.assertEqual(
			frappe.db.get_value("Batch", f"OB-{self.tag}-2", "fo_qc_status"), "Hold"
		)  # awaits QC Release
		d, cr = frappe.db.sql(
			"select sum(debit), sum(credit) from `tabGL Entry` where voucher_no=%s and is_cancelled=0",
			recon.name,
		)[0]
		self.assertEqual(flt(d, 2), flt(cr, 2))
		self.assertEqual(flt(d, 2), 25 * 1200 + 500 * 180)
		# opening stock is entered once
		again = self.make_import(
			"Opening Stock",
			to_csv(self.HEADER, [[rm, self.wh["RM-STORES"], "1", "1", f"OB-{self.tag}-9", "", exp, ""]]),
			company=self.company,
			posting_date=nowdate(),
		)
		self.check(again)
		self.assert_issue(again, 2, "item_code", "already has stock")

	def test_invalid_opening_stock(self):
		rm, fg, plain = (
			self.new_item("RM", "Kg"),
			self.new_item("FG", "Nos"),
			self.new_item("RM", "Kg", expiry=0),
		)
		other_wh = frappe.db.get_value("Warehouse", {"company": "_Test Company", "is_group": 0}, "name")
		doc = self.make_import(
			"Opening Stock",
			to_csv(
				self.HEADER,
				[
					[rm, self.wh["REJECTED"], "1", "1", "B1", "", add_days(nowdate(), 100), ""],
					[rm, other_wh, "1", "1", "B2", "", add_days(nowdate(), 100), ""],
					[rm, self.wh["RM-STORES"], "1", "1", "", "", add_days(nowdate(), 100), ""],
					[rm, self.wh["RM-STORES"], "1", "1", "B3", "", add_days(nowdate(), -1), ""],
					[
						fg,
						self.wh["FG-STORES"],
						"2.5",
						"0",
						"B4",
						add_days(nowdate(), 5),
						add_days(nowdate(), 100),
						"",
					],
					[fg, self.wh["FG-STORES"], "1", "1", "B5", "", "", ""],
					[plain, self.wh["RM-STORES"], "1", "1", "B6", "", "", ""],
					[fg, self.wh["FG-STORES"], "1", "1", "B5", "", "", ""],
				],
			),
			company=self.company,
			posting_date=nowdate(),
		)
		self.check(doc)
		self.assertEqual(doc.status, "Has Errors")
		self.assert_issue(doc, 2, "warehouse", "Usable or QC Hold")
		self.assert_issue(doc, 3, "warehouse", "another company")
		self.assert_issue(doc, 4, "batch_no", "Required")
		self.assert_issue(doc, 5, "expiry_date", "expired")
		self.assert_issue(doc, 6, "qty", "whole number")
		self.assert_issue(doc, 6, "valuation_rate", "> 0")
		self.assert_issue(doc, 6, "manufacturing_date", "after the posting date")
		self.assert_issue(doc, 7, "expiry_date", "Required")
		self.assert_issue(doc, 9, "item_code", "more than once")
		self.assertFalse(frappe.db.exists("Batch", "B1"))
		del plain


class TestPriceImport(MasterDataTestCase):
	def test_prices(self):
		header = ["item_code*", "price_list*", "rate*", "uom", "valid_from"]
		doc = self.make_import(
			"Item Prices", to_csv(header, [["SAMPLE-RM-0001", "Standard Buying", "1100", "Kg", ""]])
		)
		self.assertEqual(self.check(doc).status, "Ready to Import", self.issues(doc))
		doc.start_import()
		self.assertEqual(
			flt(
				frappe.db.get_value(
					"Item Price",
					{"item_code": "SAMPLE-RM-0001", "price_list": "Standard Buying"},
					"price_list_rate",
				)
			),
			1100,
		)
		bad = self.make_import(
			"Item Prices",
			to_csv(
				header,
				[
					["SAMPLE-RM-0001", "Standard Buying", "1200", "Kg", ""],
					["SAMPLE-RM-0001", "No List", "1", "", ""],
					["SAMPLE-RM-0001", "Standard Selling", "0", "Litre", "31-02-2026"],
				],
			),
		)
		self.check(bad)
		self.assert_issue(bad, 2, "item_code", "already exists")
		self.assert_issue(bad, 3, "price_list", "does not exist")
		self.assert_issue(bad, 4, "rate", "> 0")
		self.assert_issue(bad, 4, "uom", "no conversion")
		self.assert_issue(bad, 4, "valid_from", "date")


class TestQualityMasters(MasterDataTestCase):
	def test_templates_per_group_and_inspection_type_from_the_item_group(self):
		from factory_ops.setup.sample_company import QI_TEMPLATE_BY_GROUP

		for group, template in QI_TEMPLATE_BY_GROUP.items():
			self.assertTrue(frappe.db.exists("Quality Inspection Template", template), group)
		for item, group, hold, expected in (
			("SAMPLE-RM-0001", "RM", "RM-QC-HOLD", "Incoming"),
			("SAMPLE-BULK-0001", "BULK", "BULK-QC-HOLD", "In Process"),
			("SAMPLE-FG-0001", "FG", "FG-QC-HOLD", "Outgoing"),
		):
			self.assertEqual(
				frappe.db.get_value("Item", item, "quality_inspection_template"), QI_TEMPLATE_BY_GROUP[group]
			)
			_, batch = self.receive(item, self.wh[hold], 10, expiry_days=300)
			rel = self.qc_release(batch, "Release", item_code=item, source=self.wh[hold], submit=False)
			qi = frappe.get_doc("Quality Inspection", rel.quality_inspection)
			self.assertEqual(qi.inspection_type, expected, item)  # Desk button passes no type
			self.assertEqual(qi.quality_inspection_template, QI_TEMPLATE_BY_GROUP[group])
			self.assertTrue(qi.readings, "template parameters copied into the inspection")
