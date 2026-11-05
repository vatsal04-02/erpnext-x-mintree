"""QC quarantine and release: the scenarios from the Phase 1 execution brief, run through
server APIs (document methods and frappe.client endpoints), never through the UI."""

import frappe
import frappe.client

from factory_ops.inventory.guards import QCGuardError
from factory_ops.inventory.stock_queries import batch_qty_in_warehouse
from factory_ops.quality.doctype.qc_release.qc_release import QCReleaseError, SegregationOfDutiesError
from factory_ops.tests.utils import (
	COMPANY,
	QC_ITEM,
	USER_QA,
	USER_QC,
	USER_QC_RECEIVER,
	USER_STORES,
	FactoryOpsTestCase,
)


class TestQCRelease(FactoryOpsTestCase):
	def setUp(self):
		self.hold = self.wh["RM-QC-HOLD"]
		self.stores = self.wh["RM-STORES"]
		self.rejected = self.wh["REJECTED"]
		# Scenario 1: receive 100 kg into RM-QC-HOLD (by the stores user)
		_, self.batch = self.receive(QC_ITEM, self.hold, 100)

	def qty(self, wh):
		return batch_qty_in_warehouse(self.batch, wh)

	# Scenarios 1-3 -------------------------------------------------------------
	def test_01_receipt_lands_in_hold_and_status_is_hold(self):
		self.assertEqual(self.qty(self.hold), 100)
		self.assertEqual(frappe.db.get_value("Batch", self.batch, "fo_qc_status"), "Hold")

	def test_02_manual_transfer_out_of_hold_fails(self):
		with self.set_user(USER_STORES):
			self.assertRaises(QCGuardError, self.transfer, QC_ITEM, self.batch, 100, self.hold, self.stores)
		self.assertEqual(self.qty(self.hold), 100)
		self.assertEqual(self.qty(self.stores), 0)

	# Scenarios 4-8 -------------------------------------------------------------
	def test_03_partial_release_then_reject_remainder(self):
		rel = self.qc_release(self.batch, "Partial Release", qty=80)
		self.assertEqual(rel.docstatus, 1)
		self.assertEqual(frappe.db.get_value("Stock Entry", rel.stock_entry, "docstatus"), 1)
		self.assertEqual(rel.approved_by, USER_QC)
		self.assertEqual(self.qty(self.stores), 80)
		self.assertEqual(self.qty(self.hold), 20)
		self.assertEqual(frappe.db.get_value("Batch", self.batch, "fo_qc_status"), "Partly Released")

		rej = self.qc_release(
			self.batch, "Reject", qty=20, qi_status="Rejected", reason="SAMPLE: assay below limit"
		)
		self.assertEqual(rej.target_warehouse, self.rejected)
		self.assertEqual(self.qty(self.rejected), 20)
		self.assertEqual(self.qty(self.hold), 0)
		self.assertEqual(frappe.db.get_value("Batch", self.batch, "fo_qc_status"), "Released")

	def test_04_release_requires_accepted_qi(self):
		self.assertRaises(QCReleaseError, self.qc_release, self.batch, "Release", qi_status="Rejected")
		self.assertEqual(self.qty(self.hold), 100)

	def test_05_reject_requires_rejected_qi_and_reason(self):
		self.assertRaises(
			QCReleaseError, self.qc_release, self.batch, "Reject", qty=100, qi_status="Accepted", reason="x"
		)
		self.assertRaises(
			QCReleaseError, self.qc_release, self.batch, "Reject", qty=100, qi_status="Rejected"
		)

	def test_06_cannot_release_more_than_held(self):
		self.assertRaises(QCReleaseError, self.qc_release, self.batch, "Partial Release", qty=150)

	def test_07_full_release(self):
		rel = self.qc_release(self.batch, "Release")
		self.assertEqual(rel.qty, 100)
		self.assertEqual(self.qty(self.stores), 100)
		self.assertEqual(frappe.db.get_value("Batch", self.batch, "fo_qc_status"), "Released")

	# Scenario 9 ----------------------------------------------------------------
	def test_08_rejected_stock_cannot_go_to_production(self):
		self.qc_release(self.batch, "Reject", qty=100, qi_status="Rejected", reason="SAMPLE: contaminated")
		wip = self.wh["WIP-PRODUCTION"]
		with self.set_user(USER_STORES):
			self.assertRaises(
				QCGuardError,
				self.transfer,
				QC_ITEM,
				self.batch,
				10,
				self.rejected,
				wip,
				purpose="Material Transfer for Manufacture",
			)
			self.assertRaises(
				QCGuardError, self.transfer, QC_ITEM, self.batch, 10, self.rejected, self.stores
			)
			# scrapping without a reason is refused; with a reason it is allowed
			self.assertRaises(
				QCGuardError,
				self.transfer,
				QC_ITEM,
				self.batch,
				10,
				self.rejected,
				None,
				purpose="Material Issue",
			)
			se = self.transfer(
				QC_ITEM,
				self.batch,
				10,
				self.rejected,
				None,
				purpose="Material Issue",
				fo_disposal_reason="SAMPLE: destroyed",
			)
			self.assertEqual(se.docstatus, 1)
		self.assertEqual(self.qty(self.rejected), 90)

	def test_09_rejected_stock_cannot_be_moved_back_to_hold_by_a_plain_transfer(self):
		"""Only a QC Retest may move rejected stock back into QC Hold (test_qc_retest.py)."""
		self.qc_release(
			self.batch, "Reject", qty=100, qi_status="Rejected", reason="SAMPLE: retest requested"
		)
		with self.set_user(USER_STORES):
			self.assertRaises(QCGuardError, self.transfer, QC_ITEM, self.batch, 100, self.rejected, self.hold)
		# not even Administrator, who holds every role
		self.assertRaises(QCGuardError, self.transfer, QC_ITEM, self.batch, 100, self.rejected, self.hold)
		self.assertEqual(self.qty(self.rejected), 100)

	# Scenario 10 ---------------------------------------------------------------
	def test_10_receiver_cannot_release_own_batch(self):
		_, own_batch = self.receive(QC_ITEM, self.hold, 50, user=USER_QC_RECEIVER)
		self.assertRaises(
			SegregationOfDutiesError, self.qc_release, own_batch, "Release", user=USER_QC_RECEIVER
		)
		# another QC officer can
		rel = self.qc_release(own_batch, "Release", user=USER_QC)
		self.assertEqual(rel.docstatus, 1)

	def test_11_receiver_may_reject_own_batch(self):
		_, own_batch = self.receive(QC_ITEM, self.hold, 50, user=USER_QC_RECEIVER)
		rej = self.qc_release(
			own_batch,
			"Reject",
			qty=50,
			user=USER_QC_RECEIVER,
			qi_status="Rejected",
			reason="SAMPLE: damaged drums",
		)
		self.assertEqual(rej.docstatus, 1)

	# Scenario 11 ---------------------------------------------------------------
	def test_12_non_qc_user_cannot_create_or_submit_release(self):
		with self.set_user(USER_STORES):
			doc = frappe.get_doc(
				{
					"doctype": "QC Release",
					"company": COMPANY,
					"item_code": QC_ITEM,
					"batch_no": self.batch,
					"source_warehouse": self.hold,
					"decision": "Release",
				}
			)
			self.assertRaises(frappe.PermissionError, doc.insert)
		draft = self.qc_release(self.batch, "Release", submit=False)
		with self.set_user(USER_STORES):
			self.assertRaises(frappe.PermissionError, frappe.get_doc("QC Release", draft.name).submit)

	def test_13_conditional_release_needs_qa_manager_and_approved_deviation(self):
		self.assertRaises(
			frappe.PermissionError,
			self.qc_release,
			self.batch,
			"Conditional Release",
			qi_status="Rejected",
			reason="SAMPLE: viscosity marginal",
			user=USER_QC,
		)
		self.assertRaises(
			QCReleaseError,
			self.qc_release,
			self.batch,
			"Conditional Release",
			qi_status="Rejected",
			reason="SAMPLE: viscosity marginal",
			user=USER_QA,
		)
		with self.set_user(USER_QC):
			dev = frappe.get_doc(
				{
					"doctype": "QC Deviation",
					"company": COMPANY,
					"item_code": QC_ITEM,
					"batch_no": self.batch,
					"description": "SAMPLE viscosity 5% above upper limit",
					"impact_assessment": "SAMPLE low",
					"proposed_disposition": "SAMPLE use within 30 days",
				}
			).insert()
			self.assertRaises(frappe.PermissionError, dev.submit)  # QC officer cannot approve
		with self.set_user(USER_QA):
			frappe.get_doc("QC Deviation", dev.name).submit()
		rel = self.qc_release(
			self.batch,
			"Conditional Release",
			qty=100,
			qi_status="Rejected",
			reason="SAMPLE: accepted under deviation",
			user=USER_QA,
			qc_deviation=dev.name,
		)
		self.assertEqual(rel.docstatus, 1)
		self.assertEqual(self.qty(self.stores), 100)

	# Scenario 12: direct API calls -----------------------------------------------
	def test_14_api_transfer_out_of_hold_is_blocked(self):
		payload = {
			"doctype": "Stock Entry",
			"purpose": "Material Transfer",
			"stock_entry_type": "Material Transfer",
			"company": COMPANY,
			"items": [
				{
					"item_code": QC_ITEM,
					"qty": 100,
					"s_warehouse": self.hold,
					"t_warehouse": self.stores,
					"use_serial_batch_fields": 1,
					"batch_no": self.batch,
				}
			],
		}
		with self.set_user(USER_STORES):
			self.assertRaises(QCGuardError, frappe.client.insert, payload)
		# pointing a new entry at a real (draft) QC Release does not unlock it either
		draft = self.qc_release(self.batch, "Release", submit=False)
		payload["fo_qc_release"] = draft.name
		with self.set_user(USER_STORES):
			doc = frappe.client.insert(payload)  # a draft may exist, it moves nothing
			self.assertRaises(QCGuardError, frappe.client.submit, doc)
		self.assertEqual(self.qty(self.hold), 100)

	def test_15_draft_transfer_of_a_release_cannot_be_submitted_directly(self):
		draft = self.qc_release(self.batch, "Release", submit=False)
		se = frappe.get_doc("Stock Entry", draft.stock_entry)
		self.assertRaises(QCGuardError, se.submit)  # even as Administrator

	def test_16_release_transfer_cannot_be_cancelled_directly(self):
		rel = self.qc_release(self.batch, "Release")
		se = frappe.get_doc("Stock Entry", rel.stock_entry)
		self.assertRaises(QCGuardError, se.cancel)
		with self.set_user(USER_QA):
			frappe.get_doc("QC Release", rel.name).cancel()
		self.assertEqual(self.qty(self.hold), 100)
		self.assertEqual(frappe.db.get_value("Batch", self.batch, "fo_qc_status"), "Hold")

	def test_17_batch_qc_status_cannot_be_set_through_api(self):
		with self.set_user(USER_STORES):
			try:
				frappe.client.set_value("Batch", self.batch, "fo_qc_status", "Released")
			except frappe.PermissionError:
				pass
		self.assertEqual(frappe.db.get_value("Batch", self.batch, "fo_qc_status"), "Hold")

	def test_18_repeated_submission_moves_stock_only_once(self):
		rel = self.qc_release(self.batch, "Release")
		# Frappe treats submit() on a submitted document as an update-after-submit; on_submit
		# does not run again. What matters is that stock moves exactly once.
		with self.set_user(USER_QC):
			try:
				frappe.client.submit(frappe.get_doc("QC Release", rel.name).as_dict())
			except frappe.ValidationError:
				pass
		self.assertEqual(self.qty(self.stores), 100)
		self.assertEqual(frappe.db.count("Stock Entry", {"fo_qc_release": rel.name, "docstatus": 1}), 1)
		# a second QC Release for the same, now empty, hold is refused
		self.assertRaises(QCReleaseError, self.qc_release, self.batch, "Release")
