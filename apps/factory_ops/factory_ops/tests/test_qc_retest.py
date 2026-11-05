"""Send for Re-test (QC Retest): QA Manager only, rejected → QC Hold, and the re-tested material
becomes usable only through a fresh accepted Quality Inspection and a new QC Release."""

import frappe

from factory_ops.inventory.guards import QCGuardError
from factory_ops.inventory.stock_queries import batch_qty_in_warehouse
from factory_ops.quality.doctype.qc_release.qc_release import QCReleaseError
from factory_ops.quality.doctype.qc_retest.qc_retest import QCRetestError, send_for_retest
from factory_ops.tests.utils import COMPANY, QC_ITEM, USER_QA, USER_QC, USER_STORES, FactoryOpsTestCase


class TestQCRetest(FactoryOpsTestCase):
	def setUp(self):
		self.hold = self.wh["RM-QC-HOLD"]
		self.stores = self.wh["RM-STORES"]
		self.rejected = self.wh["REJECTED"]
		_, self.batch = self.receive(QC_ITEM, self.hold, 100)
		self.rejection = self.qc_release(
			self.batch, "Reject", qty=100, qi_status="Rejected", reason="SAMPLE: assay low"
		)
		self.old_qi = self.rejection.quality_inspection

	def qty(self, wh):
		return batch_qty_in_warehouse(self.batch, wh)

	def retest(self, user=USER_QA, qty=None):
		with self.set_user(user):
			return send_for_retest(self.rejection.name, "SAMPLE: lab error suspected", qty)

	def test_only_qa_manager_can_send_for_retest(self):
		for user in (USER_QC, USER_STORES):
			self.assertRaises(frappe.PermissionError, self.retest, user)
		self.assertEqual(self.qty(self.rejected), 100)
		# a QC Retest document saved and submitted directly is refused too
		with self.set_user(USER_QC):
			doc = frappe.get_doc(
				{
					"doctype": "QC Retest",
					"company": COMPANY,
					"item_code": QC_ITEM,
					"batch_no": self.batch,
					"rejected_warehouse": self.rejected,
					"qty": 100,
					"reason": "x",
				}
			)
			self.assertRaises(frappe.PermissionError, doc.insert)

	def test_retest_moves_rejected_stock_back_to_original_hold(self):
		name = self.retest()
		doc = frappe.get_doc("QC Retest", name)
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(doc.target_warehouse, self.hold)
		self.assertEqual(doc.rejection, self.rejection.name)
		self.assertEqual(doc.requested_by, USER_QA)
		self.assertEqual(frappe.db.get_value("Stock Entry", doc.stock_entry, "fo_qc_retest"), name)
		self.assertEqual(self.qty(self.rejected), 0)
		self.assertEqual(self.qty(self.hold), 100)
		self.assertEqual(frappe.db.get_value("Batch", self.batch, "fo_qc_status"), "Hold")

	def test_partial_retest_and_quantity_limit(self):
		self.retest(qty=30)
		self.assertEqual(self.qty(self.rejected), 70)
		self.assertEqual(self.qty(self.hold), 30)
		self.assertRaises(QCRetestError, self.retest, USER_QA, 500)

	def test_retested_material_needs_fresh_accepted_qi_and_release(self):
		self.retest()
		# 1. still held: a plain transfer out of hold is refused
		with self.set_user(USER_STORES):
			self.assertRaises(QCGuardError, self.transfer, QC_ITEM, self.batch, 100, self.hold, self.stores)
		# 2. the old (rejected) inspection cannot be reused on a new release: ERPNext's own QI
		#    reference check refuses it as soon as the release's transfer is saved
		with self.set_user(USER_QC):
			rel = frappe.get_doc(
				{
					"doctype": "QC Release",
					"company": COMPANY,
					"item_code": QC_ITEM,
					"batch_no": self.batch,
					"source_warehouse": self.hold,
					"decision": "Release",
					"quality_inspection": self.old_qi,
				}
			)
			self.assertRaises(frappe.ValidationError, rel.insert)
		# 3. a new release with a fresh rejected inspection is refused
		self.assertRaises(QCReleaseError, self.qc_release, self.batch, "Release", qi_status="Rejected")
		self.assertEqual(self.qty(self.hold), 100)
		# 4. a new release with a fresh accepted inspection works
		rel = self.qc_release(self.batch, "Release")
		self.assertNotEqual(rel.quality_inspection, self.old_qi)
		self.assertEqual(self.qty(self.stores), 100)
		self.assertEqual(frappe.db.get_value("Batch", self.batch, "fo_qc_status"), "Released")

	def test_retest_transfer_cannot_be_submitted_or_cancelled_directly(self):
		name = self.retest()
		se = frappe.get_doc("Stock Entry", frappe.db.get_value("QC Retest", name, "stock_entry"))
		self.assertRaises(QCGuardError, se.cancel)
		with self.set_user(USER_QA):
			frappe.get_doc("QC Retest", name).cancel()
		self.assertEqual(self.qty(self.rejected), 100)
		self.assertEqual(self.qty(self.hold), 0)
