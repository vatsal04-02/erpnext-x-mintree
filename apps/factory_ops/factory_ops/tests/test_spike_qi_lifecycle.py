"""Week-1 spike, kept as a regression test.

Question (DATABASE_AND_WORKFLOW_DESIGN.md section 3.2): on ERPNext v16.53.0, can a Quality
Inspection reference a *draft* Material Transfer Stock Entry, and does ERPNext's own
inspection check then gate submission of that transfer on the QI being submitted and
Accepted?

These tests use plain warehouses (no QC Hold type) so only standard ERPNext behaviour is
measured, not factory_ops guards. If an ERPNext upgrade changes this behaviour, these tests
fail first.
"""

import frappe
from erpnext.controllers.stock_controller import (
	QualityInspectionNotSubmittedError,
	QualityInspectionRejectedError,
)

from factory_ops.inventory.stock_queries import batch_qty_in_warehouse
from factory_ops.tests.utils import PLAIN_ITEM, FactoryOpsTestCase, ensure_plain_warehouse


def make_qi(se, item_code, batch_no, status="Accepted", submit=True):
	qi = frappe.get_doc(
		{
			"doctype": "Quality Inspection",
			"inspection_type": "Incoming",
			"reference_type": "Stock Entry",
			"reference_name": se.name,
			"item_code": item_code,
			"batch_no": batch_no,
			"sample_size": 1,
			"inspected_by": "Administrator",
			"manual_inspection": 1,
			"status": status,
		}
	)
	qi.insert()
	if submit:
		qi.submit()
	return qi


class TestSpikeQILifecycle(FactoryOpsTestCase):
	def setUp(self):
		self.hold = ensure_plain_warehouse("FO Spike Hold")
		self.stores = ensure_plain_warehouse("FO Spike Stores")
		_, self.batch = self.receive(PLAIN_ITEM, self.hold, 100)

	def draft_transfer(self, qty):
		return self.transfer(
			PLAIN_ITEM, self.batch, qty, self.hold, self.stores, submit=False, inspection_required=1
		)

	def test_s1_qi_on_draft_transfer_links_row_and_allows_submit(self):
		se = self.draft_transfer(80)
		qi = make_qi(se, PLAIN_ITEM, self.batch)
		se.reload()
		# QI.on_submit writes itself onto the matching draft row (Stop mode default)
		self.assertEqual(se.items[0].quality_inspection, qi.name)
		se.submit()
		self.assertEqual(batch_qty_in_warehouse(self.batch, self.stores), 80)
		self.assertEqual(batch_qty_in_warehouse(self.batch, self.hold), 20)

	def test_s2_rejected_qi_blocks_transfer(self):
		se = self.draft_transfer(20)
		make_qi(se, PLAIN_ITEM, self.batch, status="Rejected")
		se.reload()
		self.assertRaises(QualityInspectionRejectedError, se.submit)

	def test_s3_draft_qi_blocks_transfer(self):
		se = self.draft_transfer(20)
		qi = make_qi(se, PLAIN_ITEM, self.batch, submit=False)
		se.reload()
		se.items[0].quality_inspection = qi.name
		self.assertRaises(QualityInspectionNotSubmittedError, se.submit)

	def test_s4_missing_qi_blocks_transfer(self):
		se = self.draft_transfer(20)
		self.assertRaises(frappe.ValidationError, se.submit)

	def test_s5_inspection_not_required_means_no_native_check(self):
		"""Reject path: with inspection_required=0 ERPNext does not look at the QI at all,
		so the reject transfer must be checked by factory_ops itself."""
		se = self.draft_transfer(20)
		make_qi(se, PLAIN_ITEM, self.batch, status="Rejected")
		se.reload()
		se.inspection_required = 0
		se.save()
		se.submit()
		self.assertEqual(se.docstatus, 1)

	def test_s6_qi_cannot_be_reused_on_another_transfer(self):
		se_a = self.draft_transfer(50)
		qi = make_qi(se_a, PLAIN_ITEM, self.batch)
		se_b = self.draft_transfer(30)
		se_b.items[0].quality_inspection = qi.name
		self.assertRaises(frappe.ValidationError, se_b.save)

	def test_s7_partial_releases_need_one_qi_each(self):
		se_a = self.draft_transfer(60)
		make_qi(se_a, PLAIN_ITEM, self.batch)
		se_a.reload()
		se_a.submit()
		se_b = self.draft_transfer(40)
		make_qi(se_b, PLAIN_ITEM, self.batch)
		se_b.reload()
		se_b.submit()
		self.assertEqual(batch_qty_in_warehouse(self.batch, self.stores), 100)
		self.assertEqual(batch_qty_in_warehouse(self.batch, self.hold), 0)

	def test_s8_qi_cannot_be_cancelled_while_transfer_is_submitted(self):
		se = self.draft_transfer(80)
		qi = make_qi(se, PLAIN_ITEM, self.batch)
		se.reload()
		se.submit()
		qi.reload()
		self.assertRaises(frappe.ValidationError, qi.cancel)
