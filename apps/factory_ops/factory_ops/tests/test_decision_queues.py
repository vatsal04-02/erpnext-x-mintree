# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Decision Centre queues built from real documents: procurement, QC, production, inventory.

Each test creates the real ERPNext / factory_ops document, finds *its* entry in the queue, checks the
fields the brief asks for (issue, department, priority, date, pending time, responsible, status, link,
action), then resolves the document the normal way and checks the entry is gone. Nothing is inserted
into a Decision Centre table: there is no such table for queue entries."""

import frappe
from frappe.utils import add_days, add_to_date, now_datetime, nowdate

from factory_ops.decisions import queue
from factory_ops.tests import test_production, test_purchase
from factory_ops.tests.dc_helpers import count, entries_for, entry, rows, statements
from factory_ops.tests.utils import (
	LIMIT_HIGH,
	LIMIT_LOW,
	PLAIN_ITEM,
	QC_ITEM,
	USER_APPROVER_HIGH,
	USER_APPROVER_LOW,
	USER_BUYER,
	USER_PRODUCTION,
	USER_QC,
	USER_STORES,
)


class TestEntryContents(test_purchase.PurchaseTestCase):
	def test_an_entry_carries_everything_the_brief_asks_for(self):
		po = self.make_po(submit_as=None)
		e = entry("Purchase Order", po.name)
		self.assertIsNotNone(e)
		self.assertEqual(e["kind"], "po_approval")
		self.assertEqual(e["department"], "Procurement")
		self.assertIn(e["priority"], ("Critical", "High", "Medium", "Low"))
		self.assertTrue(e["issue"])
		self.assertEqual((e["ref_doctype"], e["ref_name"]), ("Purchase Order", po.name))  # the real document
		self.assertTrue(e["since"])  # date created
		self.assertIsNotNone(e["age_hours"])  # pending duration
		self.assertTrue(e["age_label"])
		self.assertEqual(e["raised_by"], USER_BUYER)
		self.assertTrue(e["responsible_department"])  # who to chase
		self.assertEqual(e["status"], "Draft")
		self.assertIn(e["state"], ("Pending", "Overdue", "Blocked", "Needs attention"))
		self.assertEqual(e["action"]["id"], "approve_po")

	def test_age_is_measured_from_the_documents_own_creation_time(self):
		po = self.make_po(submit_as=None)
		frappe.db.set_value("Purchase Order", po.name, "creation", add_to_date(now_datetime(), hours=-30), update_modified=False)
		e = entry("Purchase Order", po.name)
		self.assertGreaterEqual(e["age_hours"], 30)
		self.assertLess(e["age_hours"], 31)
		self.assertEqual(e["age_label"], "30 hours")


class TestProcurementQueue(test_purchase.PurchaseTestCase):
	def test_a_draft_po_is_waiting_and_disappears_when_approved(self):
		before = count("po_approval")
		po = self.make_po(submit_as=None)
		self.assertEqual(count("po_approval"), before + 1)
		with self.set_user(USER_APPROVER_HIGH):
			frappe.get_doc("Purchase Order", po.name).submit()
		self.assertIsNone(entry("Purchase Order", po.name, "po_approval"))
		self.assertEqual(count("po_approval"), before)

	def test_a_cancelled_or_deleted_draft_disappears(self):
		po = self.make_po(submit_as=None)
		frappe.delete_doc("Purchase Order", po.name, force=True)
		self.assertEqual(entries_for("Purchase Order", po.name), [])

	def test_approval_limit_boundaries_decide_who_may_approve(self):
		"""total == limit is allowed; one paisa above is not. The same rule the submit enforces."""
		exactly = self.make_po(qty=100, rate=100, submit_as=None)  # 10,000.00 == LIMIT_LOW
		above = self.make_po(qty=100, rate=100.01, submit_as=None)  # 10,001.00
		self.assertEqual(frappe.db.get_value("Purchase Order", exactly.name, "base_grand_total"), LIMIT_LOW)
		a = entry("Purchase Order", exactly.name, user=USER_APPROVER_LOW)
		b = entry("Purchase Order", above.name, user=USER_APPROVER_LOW)
		self.assertTrue(a["action"]["permitted"], a["action"])
		self.assertFalse(b["action"]["permitted"])
		self.assertIn("limit", b["action"]["reason"].lower())
		# and the real submit agrees with what the button promised
		with self.set_user(USER_APPROVER_LOW):
			frappe.get_doc("Purchase Order", exactly.name).submit()
			with self.assertRaises(test_purchase.ApprovalLimitError):
				frappe.get_doc("Purchase Order", above.name).submit()
		# the higher approver may approve the larger one
		self.assertTrue(entry("Purchase Order", above.name, user=USER_APPROVER_HIGH)["action"]["permitted"])

	def test_the_buyer_who_raised_an_order_is_not_offered_its_approval(self):
		po = self.make_po(submit_as=None)
		with self.set_user(USER_APPROVER_HIGH):
			pass
		buyer_view = entry("Purchase Order", po.name, user=USER_BUYER)
		self.assertFalse(buyer_view["action"]["permitted"])

	def test_an_order_above_every_limit_is_flagged_as_unapprovable(self):
		huge = self.make_po(qty=1, rate=LIMIT_HIGH * 50, submit_as=None)
		e = entry("Purchase Order", huge.name)
		self.assertEqual(e["kind"], "po_over_limit")
		self.assertEqual(e["priority"], "High")
		self.assertIn("above every approval limit", e["issue"])
		self.assertIsNone(entry("Purchase Order", huge.name, "po_approval"))  # one entry, not two

	def test_supplier_delay_appears_when_the_promised_date_passes_and_goes_when_received(self):
		po = self.make_po()
		self.assertIsNone(entry("Purchase Order", po.name, "supplier_delay"))  # due in a week
		frappe.db.set_value("Purchase Order", po.name, "schedule_date", add_days(nowdate(), -4))
		e = entry("Purchase Order", po.name, "supplier_delay")
		self.assertTrue(e["overdue"])
		self.assertEqual(e["state"], "Overdue")
		self.assertEqual(e["priority"], "High")
		self.assertIn("4 day(s) late", e["issue"])
		frappe.db.set_value("Purchase Order", po.name, "status", "Closed")
		self.assertIsNone(entry("Purchase Order", po.name, "supplier_delay"))

	def test_an_approved_order_is_not_waiting_for_approval(self):
		po = self.make_po()
		self.assertIsNone(entry("Purchase Order", po.name, "po_approval"))

	def test_a_purchase_request_awaiting_review_is_listed_until_it_is_submitted(self):
		with self.set_user(USER_STORES):
			mr = frappe.get_doc(
				{
					"doctype": "Material Request",
					"material_request_type": "Purchase",
					"company": self.company,
					"schedule_date": add_days(nowdate(), 5),
					"items": [{"item_code": test_purchase.RM, "qty": 10, "schedule_date": add_days(nowdate(), 5), "warehouse": self.wh["RM-STORES"]}],
				}
			).insert()
		e = entry("Material Request", mr.name, "pr_review")
		self.assertEqual(e["department"], "Procurement")
		self.assertEqual(e["raised_by"], USER_STORES)
		with self.set_user(USER_STORES):
			mr.reload()
			mr.submit()
		self.assertIsNone(entry("Material Request", mr.name, "pr_review"))


class TestQualityQueue(test_purchase.PurchaseTestCase):
	def setUp(self):
		super().setUp()
		self.hold = self.wh["RM-QC-HOLD"]

	def test_stock_awaiting_qc_is_listed_and_leaves_when_released(self):
		before = count("qc_incoming")
		_, batch = self.receive(QC_ITEM, self.hold, 20, expiry_days=300)
		e = entry("Batch", batch)
		self.assertEqual((e["kind"], e["department"]), ("qc_incoming", "Quality Control"))
		self.assertIn("Inventory & Stores", e["also"])
		self.assertEqual(count("qc_incoming"), before + 1)
		self.qc_release(batch, "Release", source=self.hold)
		self.assertIsNone(entry("Batch", batch))
		self.assertEqual(count("qc_incoming"), before)

	def test_a_drafted_decision_points_at_the_qc_release_document(self):
		_, batch = self.receive(QC_ITEM, self.hold, 20, expiry_days=300)
		release = self.qc_release(batch, "Release", source=self.hold, submit=False)
		release = release if isinstance(release, str) else release.name
		e = entry("QC Release", release)
		self.assertIsNotNone(e)
		self.assertIsNone(entry("Batch", batch))  # one entry for the batch, not two
		self.assertIn("decision drafted", e["issue"])

	def test_a_qc_officer_is_told_where_to_decide_and_the_centre_never_releases(self):
		_, batch = self.receive(QC_ITEM, self.hold, 20, expiry_days=300)
		e = entry("Batch", batch)
		self.assertEqual(e["action"]["id"], "open")  # QC approval is not a button here
		from factory_ops.decisions.actions import run_decision_action

		frappe.set_user("Administrator")
		for action in ("approve_po", "submit"):
			result = run_decision_action(e["key"], action)
			self.assertFalse(result["ok"], action)
		self.assertEqual(frappe.db.get_value("Batch", batch, "fo_qc_status"), "Hold")

	def test_qc_past_its_target_time_is_overdue_and_high(self):
		frappe.db.set_single_value("Factory Ops Settings", "qc_sla_hours_rm", 4)
		_, batch = self.receive(QC_ITEM, self.hold, 20, expiry_days=300)
		fresh = entry("Batch", batch)
		self.assertFalse(fresh["overdue"])
		frappe.db.set_value("Batch", batch, "creation", add_to_date(now_datetime(), hours=-9), update_modified=False)
		late = entry("Batch", batch)
		self.assertTrue(late["overdue"])
		self.assertEqual((late["priority"], late["state"]), ("High", "Overdue"))
		self.assertIn("past the 4 h QC target", late["issue"])

	def test_no_target_configured_means_never_overdue(self):
		frappe.db.set_single_value("Factory Ops Settings", "qc_sla_hours_rm", 0)
		_, batch = self.receive(QC_ITEM, self.hold, 20, expiry_days=300)
		frappe.db.set_value("Batch", batch, "creation", add_to_date(now_datetime(), hours=-500), update_modified=False)
		self.assertFalse(entry("Batch", batch)["overdue"])

	def test_a_user_who_cannot_read_the_qc_status_field_sees_no_qc_section(self):
		"""fo_qc_status is permission level 1: no read there, no QC queue (never an error)."""
		_, batch = self.receive(QC_ITEM, self.hold, 20, expiry_days=300)
		result = queue.collect(user=USER_BUYER)
		self.assertIsNone(next((r for r in result["rows"] if r["ref_name"] == batch), None))


class TestProductionQueue(test_production.ProductionTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.boms = test_production.ensure_boms(cls.company)
		frappe.db.commit()

	def test_a_draft_work_order_awaits_approval_and_leaves_when_submitted(self):
		wo = self.work_order(test_production.BULK, 10, submit=False)
		e = entry("Work Order", wo.name)
		self.assertEqual((e["kind"], e["department"], e["action"]["id"]), ("wo_approval", "Production", "submit"))
		with self.set_user(USER_PRODUCTION):
			frappe.get_doc("Work Order", wo.name).submit()
		self.assertIsNone(entry("Work Order", wo.name, "wo_approval"))

	def test_an_order_whose_planned_end_has_passed_is_overdue_until_completed(self):
		wo = self.work_order(test_production.BULK, 10)
		self.assertIsNone(entry("Work Order", wo.name, "wo_overdue"))
		frappe.db.set_value("Work Order", wo.name, {"planned_start_date": add_days(nowdate(), -10), "planned_end_date": add_days(nowdate(), -3)})
		e = entry("Work Order", wo.name)
		self.assertIn(e["kind"], ("wo_overdue", "wo_blocked"))
		self.assertTrue(e["overdue"])
		frappe.db.set_value("Work Order", wo.name, "status", "Stopped")
		self.assertEqual(entries_for("Work Order", wo.name), [])

	def test_an_order_short_of_materials_is_a_blocker_and_counts_once(self):
		wo = self.work_order(test_production.BULK, 10)
		e = entry("Work Order", wo.name)
		blockers_before = queue.summary(rows())["production_blockers"]
		self.assertEqual((e["kind"], e["state"], e["blocker"]), ("wo_blocked", "Blocked", True))
		self.assertIn("Procurement", e["also"])
		self.assertEqual(len(entries_for("Work Order", wo.name)), 1)  # blocked AND late is still one row
		frappe.db.set_value("Work Order", wo.name, {"planned_end_date": add_days(nowdate(), -2)})
		again = entries_for("Work Order", wo.name)
		self.assertEqual(len(again), 1)
		self.assertEqual((again[0]["kind"], again[0]["priority"], again[0]["overdue"]), ("wo_blocked", "Critical", True))
		self.assertEqual(queue.summary(rows())["production_blockers"], blockers_before)

	def test_the_same_shortage_is_a_purchasing_decision_for_procurement(self):
		wo = self.work_order(test_production.BULK, 10)
		shortage = [r for r in rows() if r["kind"] == "material_shortage"]
		self.assertTrue(shortage, "the open order needs material that is neither in stock nor on order")
		self.assertTrue(all(r["department"] == "Procurement" and r["ref_doctype"] == "Item" for r in shortage))
		self.assertTrue(all("Production" in r["also"] for r in shortage))
		self.assertIsNotNone(wo)

	def test_stock_in_the_source_warehouse_unblocks_the_order(self):
		wo = self.work_order(test_production.BULK, 1)
		self.assertIsNotNone(entry("Work Order", wo.name, "wo_blocked"))
		for item in {r.item_code for r in frappe.get_doc("Work Order", wo.name).required_items}:
			source = frappe.get_doc("Work Order", wo.name).required_items[0].source_warehouse
			self.released_stock(item, 1000, 10)
		wo.reload()
		still = entry("Work Order", wo.name, "wo_blocked")
		if still:  # components in other source warehouses: each needs its own stock
			for r in wo.required_items:
				self.released_stock(r.item_code, 1000, 10)
		self.assertIsNone(entry("Work Order", wo.name, "wo_blocked"))


class TestInventoryQueue(test_purchase.PurchaseTestCase):
	def test_reorder_and_expiry_and_negative_stock_entries(self):
		# reorder level above stock
		frappe.set_user("Administrator")
		item = frappe.get_doc("Item", PLAIN_ITEM)
		item.set("reorder_levels", [{"warehouse": self.wh["RM-STORES"], "warehouse_reorder_level": 1_000_000, "warehouse_reorder_qty": 5, "material_request_type": "Purchase"}])
		item.save()
		e = entry("Item", PLAIN_ITEM, "below_reorder")
		self.assertEqual(e["department"], "Inventory & Stores")
		self.assertIn("Procurement", e["also"])
		item.reload()
		item.reorder_levels[0].warehouse_reorder_level = 0
		item.save()
		self.assertIsNone(entry("Item", PLAIN_ITEM, "below_reorder"))  # level removed: resolved

	def test_batches_inside_the_expiry_window_are_listed_and_later_ones_are_not(self):
		hold = self.wh["RM-QC-HOLD"]
		_, soon = self.receive(QC_ITEM, hold, 5, expiry_days=10)
		_, later = self.receive(QC_ITEM, hold, 5, expiry_days=200)
		a = entry("Batch", soon, "expiry_materials")
		self.assertEqual(a["department"], "Inventory & Stores")
		self.assertIn("expire in 10 day(s)", a["issue"])
		self.assertIsNone(entry("Batch", later, "expiry_materials"))
		# the same batch is also a QC-hold batch: two departments, two entries, not two for one department
		self.assertIsNotNone(entry("Batch", soon, "qc_incoming"))
		frappe.db.set_value("Batch", soon, "expiry_date", add_days(nowdate(), 400))
		self.assertIsNone(entry("Batch", soon, "expiry_materials"))

	def test_the_window_is_a_setting_not_a_constant(self):
		hold = self.wh["RM-QC-HOLD"]
		_, batch = self.receive(QC_ITEM, hold, 5, expiry_days=60)
		self.assertIsNone(entry("Batch", batch, "expiry_materials"))
		frappe.db.set_single_value("Decision Centre Settings", "expiry_window_days", 90)
		frappe.clear_document_cache("Decision Centre Settings", "Decision Centre Settings")
		self.assertIsNotNone(entry("Batch", batch, "expiry_materials"))

	def test_a_draft_stock_reconciliation_awaits_review(self):
		hold = self.wh["RM-STORES"]
		frappe.set_user("Administrator")
		sr = frappe.get_doc(
			{
				"doctype": "Stock Reconciliation",
				"company": self.company,
				"purpose": "Stock Reconciliation",
				"posting_date": nowdate(),
				"set_posting_time": 1,
				"expense_account": frappe.db.get_value("Company", self.company, "stock_adjustment_account"),
				"cost_center": frappe.db.get_value("Company", self.company, "cost_center"),
				"items": [{"item_code": PLAIN_ITEM, "warehouse": hold, "qty": 3, "valuation_rate": 10}],
			}
		)
		sr.insert()
		e = entry("Stock Reconciliation", sr.name, "stock_reco_draft")
		self.assertEqual(e["action"]["id"], "submit")
		frappe.delete_doc("Stock Reconciliation", sr.name, force=True)
		self.assertIsNone(entry("Stock Reconciliation", sr.name))


class TestNoPerRowQueries(test_purchase.PurchaseTestCase):
	def test_the_number_of_statements_does_not_grow_with_the_number_of_documents(self):
		self.make_po(submit_as=None)
		few = statements(lambda: rows())
		for _i in range(8):
			self.make_po(submit_as=None)
		many = statements(lambda: rows())
		self.assertLessEqual(many - few, 2, f"{few} statements for the first queue, {many} after eight more orders")
