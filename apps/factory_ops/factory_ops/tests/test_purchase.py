"""Purchase and inventory: standard ERPNext purchase chain plus factory_ops controls."""

import frappe
import frappe.client
from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
from erpnext.buying.doctype.request_for_quotation.request_for_quotation import (
	make_supplier_quotation_from_rfq,
)
from erpnext.buying.doctype.supplier_quotation.supplier_quotation import make_purchase_order
from erpnext.stock.doctype.material_request.material_request import make_request_for_quotation
from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice
from frappe.utils import add_days, nowdate

from factory_ops.inventory.receiving import ReceiptDataError
from factory_ops.inventory.report.stock_by_qc_status.stock_by_qc_status import execute as qc_stock
from factory_ops.inventory.stock_queries import batch_qty_in_warehouse
from factory_ops.purchase.approval import ApprovalLimitError, SelfApprovalError
from factory_ops.tests.utils import (
	COMPANY,
	USER_APPROVER_HIGH,
	USER_APPROVER_LOW,
	USER_BUYER,
	FactoryOpsTestCase,
)

RM = "SAMPLE-RM-0001"
PM = "SAMPLE-PM-0001"
SUPPLIER = "SAMPLE Supplier - Actives"


def sample_coa(name="SAMPLE-COA.pdf") -> str:
	"""A real one-page blank PDF titled as a SAMPLE COA (Frappe parses uploaded PDFs)."""
	import io

	from pypdf import PdfWriter

	writer = PdfWriter()
	writer.add_blank_page(width=200, height=200)
	writer.add_metadata({"/Title": "SAMPLE COA - not a real certificate"})
	buf = io.BytesIO()
	writer.write(buf)
	f = frappe.new_doc("File")
	f.file_name = name
	f.is_private = 1
	f.content = buf.getvalue()
	f.insert(ignore_permissions=True)
	return f.file_url


def supplier_details(coa=True, **overrides):
	data = {
		"fo_supplier_batch_no": "SAMPLE-SUP-LOT-001",
		"fo_mfg_date": add_days(nowdate(), -20),
		"fo_expiry_date": add_days(nowdate(), 700),
	}
	if coa:
		data["fo_coa"] = sample_coa()
	data.update(overrides)
	return data


class PurchaseTestCase(FactoryOpsTestCase):
	def make_po(self, qty=100, rate=50, item=RM, user=USER_BUYER, submit_as=USER_APPROVER_HIGH):
		with self.set_user(user):
			po = frappe.get_doc(
				{
					"doctype": "Purchase Order",
					"company": self.company,
					"supplier": SUPPLIER,
					"schedule_date": add_days(nowdate(), 7),
					"items": [
						{"item_code": item, "qty": qty, "rate": rate, "schedule_date": add_days(nowdate(), 7)}
					],
				}
			).insert()
		if submit_as:
			with self.set_user(submit_as):
				frappe.get_doc("Purchase Order", po.name).submit()
			po.reload()
		return po

	def make_pr(self, po, submit=True, user=None, **row_values):
		pr = make_purchase_receipt(po.name)
		pr.items[0].update(row_values)
		with self.set_user(user or "fo-stores@example.com"):
			pr.insert()
			if submit:
				pr.submit()
		pr.reload()
		return pr


class TestPurchaseChain(PurchaseTestCase):
	def test_requisition_rfq_quotation_purchase_order(self):
		with self.set_user(USER_BUYER):
			mr = frappe.get_doc(
				{
					"doctype": "Material Request",
					"material_request_type": "Purchase",
					"company": self.company,
					"schedule_date": add_days(nowdate(), 10),
					"items": [{"item_code": RM, "qty": 200, "schedule_date": add_days(nowdate(), 10)}],
				}
			).insert()
			mr.submit()
			rfq = make_request_for_quotation(mr.name)
			rfq.message_for_supplier = "SAMPLE request for quotation"
			rfq.append("suppliers", {"supplier": SUPPLIER, "send_email": 0})
			rfq.insert()
		# standard ERPNext roles: a Purchase User drafts, a Purchase Manager submits RFQs and quotations
		with self.set_user(USER_APPROVER_HIGH):
			frappe.get_doc("Request for Quotation", rfq.name).submit()
			sq = make_supplier_quotation_from_rfq(rfq.name, for_supplier=SUPPLIER)
			sq.items[0].rate = 40
			sq.insert()
			sq.submit()
		with self.set_user(USER_BUYER):
			po = make_purchase_order(sq.name)
			po.schedule_date = add_days(nowdate(), 7)
			po.insert()
		# goods for a QC item are ordered into its QC Hold warehouse (item group default)
		self.assertEqual(po.items[0].warehouse, self.wh["RM-QC-HOLD"])
		self.assertEqual(po.items[0].material_request, mr.name)
		self.assertEqual(po.items[0].supplier_quotation, sq.name)
		with self.set_user(USER_APPROVER_HIGH):
			frappe.get_doc("Purchase Order", po.name).submit()
		self.assertEqual(frappe.db.get_value("Purchase Order", po.name, "docstatus"), 1)

	def test_creator_cannot_approve_own_purchase_order(self):
		po = self.make_po(user=USER_APPROVER_HIGH, submit_as=None)
		with self.set_user(USER_APPROVER_HIGH):
			self.assertRaises(SelfApprovalError, frappe.get_doc("Purchase Order", po.name).submit)

	def test_approval_limits_are_per_user(self):
		po = self.make_po(qty=100, rate=500, submit_as=None)  # 50,000 > low limit 10,000
		with self.set_user(USER_BUYER):  # the creator: never, whatever the amount
			self.assertRaises(SelfApprovalError, frappe.get_doc("Purchase Order", po.name).submit)
		with self.set_user(USER_APPROVER_LOW):  # limit 10,000 < 50,000
			self.assertRaises(ApprovalLimitError, frappe.get_doc("Purchase Order", po.name).submit)
		with self.set_user(USER_APPROVER_HIGH):
			frappe.get_doc("Purchase Order", po.name).submit()
		small = self.make_po(qty=10, rate=50, submit_as=USER_APPROVER_LOW)  # 500, within 10,000
		self.assertEqual(small.docstatus, 1)

	def test_approval_cannot_be_bypassed_through_the_api(self):
		po = self.make_po(qty=100, rate=500, submit_as=None)
		with self.set_user(USER_APPROVER_LOW):
			self.assertRaises(
				ApprovalLimitError, frappe.client.submit, frappe.get_doc("Purchase Order", po.name).as_dict()
			)
		self.assertEqual(frappe.db.get_value("Purchase Order", po.name, "docstatus"), 0)


class TestGoodsReceipt(PurchaseTestCase):
	def test_receipt_defaults_to_qc_hold_and_needs_supplier_data(self):
		po = self.make_po()
		pr = self.make_pr(po, submit=False)
		self.assertEqual(pr.items[0].warehouse, self.wh["RM-QC-HOLD"])
		with self.set_user("fo-stores@example.com"):
			self.assertRaises(ReceiptDataError, frappe.get_doc("Purchase Receipt", pr.name).submit)

	def test_receipt_date_checks(self):
		po = self.make_po()
		self.assertRaises(ReceiptDataError, self.make_pr, po, False, None, fo_mfg_date=add_days(nowdate(), 3))
		self.assertRaises(
			ReceiptDataError,
			self.make_pr,
			po,
			False,
			None,
			fo_mfg_date=add_days(nowdate(), -10),
			fo_expiry_date=add_days(nowdate(), -20),
		)

	def test_rm_needs_coa_pm_does_not(self):
		po = self.make_po()
		self.assertRaises(ReceiptDataError, self.make_pr, po, True, None, **supplier_details(coa=False))
		pm_po = self.make_po(item=PM, qty=500, rate=2)
		pr = self.make_pr(pm_po, **supplier_details(coa=False, fo_expiry_date=None))
		self.assertEqual(pr.docstatus, 1)

	def test_supplier_batch_created_with_supplier_details(self):
		po = self.make_po()
		details = supplier_details()
		pr = self.make_pr(po, **details)
		batch = frappe.get_doc("Batch", pr.items[0].batch_no)
		self.assertEqual(batch.item, RM)
		self.assertEqual(batch.supplier, SUPPLIER)
		self.assertEqual(batch.fo_supplier_batch_no, "SAMPLE-SUP-LOT-001")
		self.assertEqual(str(batch.manufacturing_date), str(details["fo_mfg_date"]))
		self.assertEqual(str(batch.expiry_date), str(details["fo_expiry_date"]))
		self.assertEqual(batch.fo_coa, details["fo_coa"])
		self.assertEqual(batch.reference_name, pr.name)
		self.assertEqual(batch.fo_qc_status, "Hold")
		self.assertEqual(batch_qty_in_warehouse(batch.name, self.wh["RM-QC-HOLD"]), 100)

	def test_accepted_and_rejected_quantities(self):
		po = self.make_po()
		pr = self.make_pr(po, received_qty=100, qty=90, rejected_qty=10, **supplier_details())
		row = pr.items[0]
		self.assertEqual(row.rejected_warehouse, self.wh["REJECTED"])  # defaulted
		self.assertEqual(batch_qty_in_warehouse(row.batch_no, self.wh["RM-QC-HOLD"]), 90)
		self.assertEqual(batch_qty_in_warehouse(row.batch_no, self.wh["REJECTED"]), 10)

	def test_purchase_invoice_from_receipt(self):
		po = self.make_po()
		pr = self.make_pr(po, **supplier_details())
		pi = make_purchase_invoice(pr.name)
		pi.bill_no = "SAMPLE-BILL-1"
		pi.insert()
		pi.submit()
		self.assertEqual(pi.items[0].purchase_receipt, pr.name)
		self.assertEqual(pi.items[0].purchase_order, po.name)
		self.assertEqual(pi.update_stock, 0)
		self.assertEqual(frappe.db.get_value("Purchase Receipt", pr.name, "per_billed"), 100)
		gl = frappe.db.sql(
			"select sum(debit) d, sum(credit) c from `tabGL Entry` where voucher_no=%s and is_cancelled=0",
			pi.name,
			as_dict=True,
		)[0]
		self.assertGreater(gl.d, 0)
		self.assertEqual(round(gl.d, 2), round(gl.c, 2))

	def test_duplicate_receipt_submission_moves_stock_once(self):
		po = self.make_po()
		pr = self.make_pr(po, **supplier_details())
		sle_before = frappe.db.count("Stock Ledger Entry", {"voucher_no": pr.name})
		try:
			frappe.client.submit(frappe.get_doc("Purchase Receipt", pr.name).as_dict())
		except frappe.ValidationError:
			pass
		self.assertEqual(frappe.db.count("Stock Ledger Entry", {"voucher_no": pr.name}), sle_before)
		self.assertEqual(batch_qty_in_warehouse(pr.items[0].batch_no, self.wh["RM-QC-HOLD"]), 100)


class TestMastersAndReports(PurchaseTestCase):
	def test_item_group_controls(self):
		code = "_FO Test RM via group"
		if frappe.db.exists("Item", code):
			frappe.delete_doc("Item", code, force=True)
		item = frappe.get_doc(
			{"doctype": "Item", "item_code": code, "item_group": "RM", "stock_uom": "Kg", "is_stock_item": 1}
		).insert()
		self.assertEqual((item.has_batch_no, item.fo_qc_required, item.fo_coa_required), (1, 1, 1))

	def test_sample_masters_exist(self):
		for code in (
			"SAMPLE-RM-0001",
			"SAMPLE-RM-0002",
			"SAMPLE-PM-0001",
			"SAMPLE-BULK-0001",
			"SAMPLE-FG-0001",
		):
			item = frappe.get_cached_doc("Item", code)
			self.assertTrue(item.item_name.startswith("SAMPLE"))
			self.assertEqual(item.fo_qc_required, 1)
		self.assertEqual(frappe.get_cached_doc("Item", RM).fo_coa_required, 1)
		gram = [u for u in frappe.get_cached_doc("Item", RM).uoms if u.uom == "Gram"]
		self.assertAlmostEqual(gram[0].conversion_factor, 0.001)

	def test_stock_reports(self):
		po = self.make_po()
		pr = self.make_pr(po, **supplier_details())
		_, rows = qc_stock({"company": self.company, "item_code": RM})
		mine = [r for r in rows if r.batch_no == pr.items[0].batch_no]
		self.assertEqual(len(mine), 1)
		self.assertEqual((mine[0].warehouse_type, mine[0].qty), ("QC Hold", 100))
		from erpnext.stock.report.stock_balance.stock_balance import execute as stock_balance
		from erpnext.stock.report.stock_ledger.stock_ledger import execute as stock_ledger

		f = {
			"company": self.company,
			"from_date": add_days(nowdate(), -1),
			"to_date": nowdate(),
			"item_code": RM,
		}
		ledger = stock_ledger(frappe._dict({**f, "item_code": [RM]}))[1]  # v16: multi-select filter
		self.assertTrue(any(r.get("voucher_no") == pr.name for r in ledger if isinstance(r, dict)))
		balance = stock_balance(frappe._dict({**f, "item_code": [RM], "warehouse": self.wh["RM-QC-HOLD"]}))[1]
		self.assertTrue(balance)
