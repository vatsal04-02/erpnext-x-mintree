"""Part 3 acceptance: Supplier -> Purchase Order -> Purchase Receipt -> QC Hold -> Quality
Inspection -> QC Release -> Usable inventory, with the stock ledger, batch balances and GL
checked at every stage. Uses one SAMPLE supplier and one SAMPLE raw material.
"""

import frappe
import frappe.client
from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice
from frappe.utils import flt

from factory_ops.inventory.guards import QCGuardError
from factory_ops.inventory.report.stock_by_qc_status.stock_by_qc_status import execute as qc_stock
from factory_ops.inventory.stock_queries import batch_qty_by_warehouse
from factory_ops.purchase.approval import SelfApprovalError
from factory_ops.quality.report.qc_queue.qc_queue import execute as qc_queue
from factory_ops.tests.test_purchase import RM, SUPPLIER, PurchaseTestCase, supplier_details
from factory_ops.tests.utils import (
	COMPANY,
	USER_APPROVER_HIGH,
	USER_BUYER,
	USER_QC,
	USER_STORES,
)

RATE = 50


def sle(voucher_no: str) -> list[tuple[str, float]]:
	rows = frappe.get_all(
		"Stock Ledger Entry",
		filters={"voucher_no": voucher_no, "is_cancelled": 0},
		fields=["warehouse", "actual_qty"],
		order_by="actual_qty asc",
	)
	return [(r.warehouse, flt(r.actual_qty)) for r in rows]


def wh_balance(item: str, warehouse: str) -> float:
	"""Item balance per the last stock ledger entry for the warehouse."""
	return flt(
		frappe.db.get_value(
			"Stock Ledger Entry",
			{"item_code": item, "warehouse": warehouse, "is_cancelled": 0},
			"qty_after_transaction",
			order_by="posting_datetime desc, creation desc",
		)
	)


def gl_totals(voucher_no: str) -> tuple[float, float]:
	row = frappe.db.sql(
		"select coalesce(sum(debit),0), coalesce(sum(credit),0) from `tabGL Entry` where voucher_no=%s and is_cancelled=0",
		voucher_no,
	)[0]
	return round(flt(row[0]), 2), round(flt(row[1]), 2)


class TestAcceptanceE2E(PurchaseTestCase):
	# perpetual inventory on, like the SAMPLE company, so every stage posts real GL entries
	company = "_FO Perpetual Company"
	company_abbr = "_FOPC"

	def setUp(self):
		self.hold, self.stores, self.rejected = (
			self.wh["RM-QC-HOLD"],
			self.wh["RM-STORES"],
			self.wh["REJECTED"],
		)
		self.start_hold = wh_balance(RM, self.hold)
		self.start_stores = wh_balance(RM, self.stores)
		self.start_rejected = wh_balance(RM, self.rejected)

	def test_full_flow_with_ledger_checks(self):
		# 1. Purchase Order: raised by the buyer, approved by an approver with enough limit
		po = self.make_po(qty=100, rate=RATE)
		self.assertEqual(po.docstatus, 1)
		self.assertEqual(po.supplier, SUPPLIER)
		self.assertEqual(sle(po.name), [])  # an order moves no stock

		# 2. Purchase Receipt: lands in QC Hold, with the supplier batch
		pr = self.make_pr(po, **supplier_details())
		batch = pr.items[0].batch_no
		self.assertEqual(sle(pr.name), [(self.hold, 100)])
		self.assertEqual(wh_balance(RM, self.hold), self.start_hold + 100)
		self.assertEqual(batch_qty_by_warehouse(batch), {self.hold: 100})
		debit, credit = gl_totals(pr.name)
		self.assertEqual(debit, credit)
		self.assertEqual(debit, 100 * RATE)  # stock in hand vs received-not-billed
		self.assertEqual(frappe.db.get_value("Batch", batch, "fo_qc_status"), "Hold")

		# 3. QC Hold: listed in the QC queue; nobody can move it by hand
		_, queue = qc_queue({"company": self.company})
		self.assertIn(batch, [r["batch_no"] for r in queue])
		with self.set_user(USER_STORES):
			self.assertRaises(QCGuardError, self.transfer, RM, batch, 100, self.hold, self.stores)
		self.assertEqual(batch_qty_by_warehouse(batch), {self.hold: 100})

		# 4-5. Quality Inspection (accepted) + QC Release of 60
		rel = self.qc_release(batch, "Partial Release", qty=60, item_code=RM, source=self.hold)
		qi = frappe.get_doc("Quality Inspection", rel.quality_inspection)
		self.assertEqual((qi.docstatus, qi.status, qi.batch_no), (1, "Accepted", batch))
		self.assertEqual(sle(rel.stock_entry), [(self.hold, -60), (self.stores, 60)])
		self.assertEqual(batch_qty_by_warehouse(batch), {self.hold: 40, self.stores: 60})
		self.assertEqual(wh_balance(RM, self.stores), self.start_stores + 60)
		d, c = gl_totals(rel.stock_entry)
		self.assertEqual(d, c)

		# 6. the remaining 40 fail: rejected with a second, rejected inspection
		rej = self.qc_release(
			batch,
			"Reject",
			qty=40,
			qi_status="Rejected",
			reason="SAMPLE: out of specification",
			item_code=RM,
			source=self.hold,
		)
		self.assertNotEqual(rej.quality_inspection, rel.quality_inspection)
		self.assertEqual(sle(rej.stock_entry), [(self.hold, -40), (self.rejected, 40)])
		self.assertEqual(batch_qty_by_warehouse(batch), {self.stores: 60, self.rejected: 40})

		# 7. Purchase Invoice linked to the receipt; GL balances
		pi = make_purchase_invoice(pr.name)
		pi.bill_no = "SAMPLE-BILL-E2E"
		pi.insert()
		pi.submit()
		self.assertEqual(pi.items[0].purchase_receipt, pr.name)
		self.assertEqual(sle(pi.name), [])  # billing moves no stock
		d, c = gl_totals(pi.name)
		self.assertEqual(d, c)
		self.assertEqual(frappe.db.get_value("Purchase Receipt", pr.name, "per_billed"), 100)

		# 8. Usable inventory as reported
		_, rows = qc_stock({"company": self.company, "item_code": RM})
		mine = {(r.warehouse_type, r.qty) for r in rows if r.batch_no == batch}
		self.assertEqual(mine, {("Usable", 60), ("Rejected", 40)})
		self.assertEqual(frappe.db.get_value("Batch", batch, "fo_qc_status"), "Released")
		self.assertEqual(wh_balance(RM, self.hold), self.start_hold)

	def test_partial_acceptance_at_the_dock(self):
		po = self.make_po(qty=100, rate=RATE)
		pr = self.make_pr(po, received_qty=100, qty=85, rejected_qty=15, **supplier_details())
		batch = pr.items[0].batch_no
		self.assertEqual(sorted(sle(pr.name)), sorted([(self.hold, 85), (self.rejected, 15)]))
		self.assertEqual(batch_qty_by_warehouse(batch), {self.hold: 85, self.rejected: 15})
		self.qc_release(batch, "Release", item_code=RM, source=self.hold)
		self.assertEqual(batch_qty_by_warehouse(batch), {self.stores: 85, self.rejected: 15})

	def test_duplicate_submissions_change_nothing(self):
		po = self.make_po(qty=100, rate=RATE)
		pr = self.make_pr(po, **supplier_details())
		batch = pr.items[0].batch_no
		rel = self.qc_release(batch, "Release", item_code=RM, source=self.hold)
		before = (sle(pr.name), sle(rel.stock_entry), batch_qty_by_warehouse(batch))
		for doctype, name, user in (
			("Purchase Order", po.name, USER_APPROVER_HIGH),
			("Purchase Receipt", pr.name, USER_STORES),
			("QC Release", rel.name, USER_QC),
		):
			with self.set_user(user):
				try:
					frappe.client.submit(frappe.get_doc(doctype, name).as_dict())
				except frappe.ValidationError:
					pass
		self.assertEqual((sle(pr.name), sle(rel.stock_entry), batch_qty_by_warehouse(batch)), before)
		self.assertEqual(frappe.db.count("Stock Entry", {"fo_qc_release": rel.name, "docstatus": 1}), 1)

	def test_permission_restrictions_along_the_flow(self):
		draft_po = self.make_po(qty=100, rate=500, submit_as=None)
		with self.set_user(USER_BUYER):  # creator cannot approve
			self.assertRaises(SelfApprovalError, frappe.get_doc("Purchase Order", draft_po.name).submit)
		with self.set_user(USER_QC):  # QC officer has no purchase rights at all
			self.assertRaises(frappe.PermissionError, frappe.get_doc("Purchase Order", draft_po.name).submit)
		po = self.make_po(qty=100, rate=RATE)
		pr = self.make_pr(po, **supplier_details())
		batch = pr.items[0].batch_no
		with self.set_user(USER_STORES):  # stores cannot decide on QC
			doc = frappe.get_doc(
				{
					"doctype": "QC Release",
					"company": self.company,
					"item_code": RM,
					"batch_no": batch,
					"source_warehouse": self.hold,
					"decision": "Release",
				}
			)
			self.assertRaises(frappe.PermissionError, doc.insert)
		# QC cannot receive goods
		other_po = self.make_po(qty=10, rate=RATE)
		self.assertRaises(frappe.PermissionError, self.make_pr, other_po, True, USER_QC, **supplier_details())
		# a purchase approver is not a QC officer
		self.assertRaises(
			frappe.PermissionError,
			self.qc_release,
			batch,
			"Release",
			item_code=RM,
			source=self.hold,
			user=USER_APPROVER_HIGH,
		)
		self.assertEqual(batch_qty_by_warehouse(batch), {self.hold: 100})
