# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Real-data cards on the operational workspaces (Procurement, Stores, Sales, Management).

Every figure is checked against records created here, never against the card's own definition.
Where "time passes" (a due date lapsing) the date is moved on the record, as the sales tests do for
batch expiry, because ERPNext refuses to create such a document in the past.
"""

import frappe
from frappe.utils import add_days, nowdate

from factory_ops.setup import dashboard_data
from factory_ops.setup.workspaces import WORKSPACES, card_name
from factory_ops.tests import test_purchase, test_sales
from factory_ops.tests.test_plant_maintenance import card_value
from factory_ops.tests.utils import (
	PLAIN_ITEM,
	QC_ITEM,
	USER_APPROVER_HIGH,
	USER_BUYER,
	USER_MANAGEMENT,
	USER_SALES,
	USER_STORES,
	FactoryOpsTestCase,
)


class TestStoresCards(FactoryOpsTestCase):
	def setUp(self):
		self.hold, self.stores = self.wh["RM-QC-HOLD"], self.wh["RM-STORES"]

	def test_the_stores_page_shows_stores_figures_not_a_billing_one(self):
		cards = WORKSPACES["Stores and Warehouse"]["cards"]
		self.assertNotIn("Dispatches Not Billed", cards)  # a billing figure; it belongs to Sales and Accounts
		self.assertIn("FO Batches Expiring 30 Days", cards)
		self.assertIn("FO Items Below Reorder", cards)
		for key in cards:
			card_value(USER_STORES, key)  # computes with the stores user's own permissions

	def test_batches_expiring_within_30_days_are_counted_and_later_ones_are_not(self):
		before = card_value(USER_STORES, "FO Batches Expiring 30 Days")
		self.receive(QC_ITEM, self.hold, 10, expiry_days=10)
		self.assertEqual(card_value(USER_STORES, "FO Batches Expiring 30 Days"), before + 1)
		self.receive(QC_ITEM, self.hold, 10, expiry_days=90)  # not soon
		self.assertEqual(card_value(USER_STORES, "FO Batches Expiring 30 Days"), before + 1)
		# agrees with the Batch list the stores user would filter by hand
		listed = frappe.get_list(
			"Batch",
			filters={"disabled": 0, "batch_qty": [">", 0], "expiry_date": ["between", [nowdate(), add_days(nowdate(), 30)]]},
			pluck="name",
			limit_page_length=0,
		)
		self.assertEqual(card_value(USER_STORES, "FO Batches Expiring 30 Days"), len(listed))

	def test_an_already_expired_batch_is_not_counted_as_expiring(self):
		_, batch = self.receive(QC_ITEM, self.hold, 10, expiry_days=10)
		before = card_value(USER_STORES, "FO Batches Expiring 30 Days")
		frappe.db.set_value("Batch", batch, "expiry_date", add_days(nowdate(), -1))
		self.assertEqual(card_value(USER_STORES, "FO Batches Expiring 30 Days"), before - 1)

	def test_items_at_or_below_their_reorder_level_are_counted(self):
		item = frappe.get_doc("Item", PLAIN_ITEM)
		item.set("reorder_levels", [])
		frappe.set_user("Administrator")
		item.save()
		before = card_value(USER_STORES, "FO Items Below Reorder")
		item.set(
			"reorder_levels",
			[{"warehouse": self.stores, "warehouse_reorder_level": 1_000_000, "warehouse_reorder_qty": 10, "material_request_type": "Purchase"}],
		)
		item.save()
		self.assertEqual(card_value(USER_STORES, "FO Items Below Reorder"), before + 1)
		rows = dashboard_data.items_below_reorder()
		self.assertIn((PLAIN_ITEM, self.stores), [(r.item_code, r.warehouse) for r in rows])
		# a level of zero means "no reorder level set": not counted
		item.reload()
		item.reorder_levels[0].warehouse_reorder_level = 0
		item.save()
		self.assertEqual(card_value(USER_STORES, "FO Items Below Reorder"), before)
		# a disabled item is not counted
		item.reload()
		item.reorder_levels[0].warehouse_reorder_level = 1_000_000
		item.save()
		item.disabled = 1
		item.save()
		self.assertEqual(card_value(USER_STORES, "FO Items Below Reorder"), before)

	def test_a_user_without_stock_rights_gets_a_refusal_not_a_number(self):
		from factory_ops.tests.utils import ensure_user

		nobody = ensure_user("fo-nobody-cards@example.com", [])
		frappe.set_user(nobody)
		for fn in (dashboard_data.batches_expiring_soon, dashboard_data.items_below_reorder_count):
			self.assertRaises(frappe.PermissionError, fn)


class TestOverduePurchaseOrders(test_purchase.PurchaseTestCase):
	def test_an_approved_order_past_its_required_by_date_is_overdue_until_received(self):
		before = card_value(USER_BUYER, "FO Purchase Orders Overdue")
		po = self.make_po(submit_as=USER_APPROVER_HIGH)  # due in a week: not overdue
		self.assertEqual(card_value(USER_BUYER, "FO Purchase Orders Overdue"), before)
		frappe.db.set_value("Purchase Order", po.name, "schedule_date", add_days(nowdate(), -3))
		self.assertEqual(card_value(USER_BUYER, "FO Purchase Orders Overdue"), before + 1)
		# an order not yet approved is not "overdue": nothing has been ordered
		draft = self.make_po(submit_as=None)
		frappe.db.set_value("Purchase Order", draft.name, "schedule_date", add_days(nowdate(), -3))
		self.assertEqual(card_value(USER_BUYER, "FO Purchase Orders Overdue"), before + 1)
		# closing the order takes it off the card
		frappe.db.set_value("Purchase Order", po.name, "status", "Closed")
		self.assertEqual(card_value(USER_BUYER, "FO Purchase Orders Overdue"), before)

	def test_procurement_shows_what_is_waiting_approval_and_what_is_late(self):
		cards = WORKSPACES["Procurement"]["cards"]
		self.assertEqual(
			cards,
			["FO Open Material Requests", "FO Purchase Orders Awaiting Approval", "FO Open Purchase Orders", "FO Purchase Orders Overdue"],
		)
		for key in cards:
			card_value(USER_BUYER, key)


class TestOrdersPastDeliveryDate(test_sales.SalesTestCase):
	def test_an_open_order_whose_delivery_date_has_passed_is_counted(self):
		before = card_value(USER_SALES, "FO Orders Past Delivery Date")
		so = self.sales_order()  # delivery in a week: on time
		self.assertEqual(card_value(USER_SALES, "FO Orders Past Delivery Date"), before)
		frappe.db.set_value("Sales Order", so.name, "delivery_date", add_days(nowdate(), -2))
		self.assertEqual(card_value(USER_SALES, "FO Orders Past Delivery Date"), before + 1)
		frappe.db.set_value("Sales Order", so.name, "status", "Completed")  # delivered
		self.assertEqual(card_value(USER_SALES, "FO Orders Past Delivery Date"), before)

	def test_the_sales_page_cards(self):
		cards = WORKSPACES["Sales and Dispatch"]["cards"]
		self.assertEqual(len(cards), 4)
		self.assertIn("FO Orders Past Delivery Date", cards)
		for key in cards:
			card_value(USER_SALES, key)


class TestManagementDecisionRow(test_purchase.PurchaseTestCase):
	DECISIONS = (
		"FO Purchase Orders Awaiting Approval",
		"FO QC Releases Pending",
		"FO Deviations Pending",
		"FO Work Orders In Progress",
	)

	def test_the_first_row_is_what_waits_for_a_decision(self):
		self.assertEqual(WORKSPACES["Management"]["cards"][:4], list(self.DECISIONS))
		self.assertEqual(len(WORKSPACES["Management"]["cards"]) % 4, 0)  # whole rows

	def test_each_figure_equals_a_count_made_another_way(self):
		expected = {
			"FO Purchase Orders Awaiting Approval": lambda: frappe.db.count("Purchase Order", {"docstatus": 0}),
			"FO QC Releases Pending": lambda: frappe.db.count("QC Release", {"docstatus": 0}),
			"FO Deviations Pending": lambda: frappe.db.count("QC Deviation", {"docstatus": 0}),
			"FO Work Orders In Progress": lambda: frappe.db.count("Work Order", {"docstatus": 1, "status": ["in", ["Not Started", "In Process"]]}),
		}
		before = {k: card_value(USER_MANAGEMENT, k) for k in expected}
		self.make_po(submit_as=None)  # a Purchase Order waiting for approval
		self.assertEqual(card_value(USER_MANAGEMENT, "FO Purchase Orders Awaiting Approval"), before["FO Purchase Orders Awaiting Approval"] + 1)
		for key, count in expected.items():
			self.assertEqual(card_value(USER_MANAGEMENT, key), count(), key)

	def test_the_owner_sees_the_same_figures_as_management(self):
		from factory_ops.tests.utils import ensure_user

		owner = ensure_user("fo-owner-cards@example.com", ["Business Administrator"])
		for key in WORKSPACES["Management"]["cards"]:
			self.assertEqual(card_value(owner, key), card_value(USER_MANAGEMENT, key), card_name(key))
