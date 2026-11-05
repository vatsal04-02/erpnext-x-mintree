# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Plant Maintenance workspace (B4): its audience, and that every figure counts real records.

Fixtures come from test_maintenance (register equipment, raise a breakdown, spare parts)."""

import json

import frappe
from frappe.desk.doctype.number_card.number_card import get_result
from frappe.utils import add_days, nowdate

from factory_ops.maintenance.setup import ensure_maintenance_masters
from factory_ops.setup import dashboard_data
from factory_ops.setup.workspaces import WORKSPACES, card_name
from factory_ops.tests.test_desk_ui import definition_blocks
from factory_ops.tests import test_maintenance  # the module, not its test class: importing the class would re-run its tests here
from factory_ops.tests.test_maintenance import MGR, OUTSIDER, SPARE, TECH
from factory_ops.tests.test_workspaces import visible_workspaces
from factory_ops.tests.utils import FactoryOpsTestCase, ensure_user

PAGE = "Plant Maintenance"


def card_value(user: str, key: str) -> int:
	card = frappe.get_doc("Number Card", card_name(key))
	with_user = frappe.session.user
	frappe.set_user(user)
	try:
		if card.type == "Custom":
			result = frappe.get_attr(card.method)()
			return int(result["value"] if isinstance(result, dict) else result)
		return int(get_result(card.as_dict(), card.filters_json))
	finally:
		frappe.set_user(with_user)


class TestPlantMaintenance(FactoryOpsTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_maintenance_masters(cls.company)
		ensure_user(TECH, ["Maintenance User", "Stock User"])
		ensure_user(MGR, ["Maintenance Manager", "Maintenance User", "Stock User"])
		ensure_user(OUTSIDER, ["Sales User"])
		if not frappe.db.exists("Item", SPARE):
			frappe.get_doc(
				{"doctype": "Item", "item_code": SPARE, "item_name": SPARE, "item_group": "Spare Parts",
				 "stock_uom": "Nos", "is_stock_item": 1, "valuation_rate": 250}
			).insert(ignore_permissions=True)
		cls.spare_wh = frappe.db.get_value(
			"Warehouse", {"company": cls.company, "is_group": 0, "warehouse_type": ["is", "not set"]}, "name"
		)
		frappe.db.commit()

	# fixtures borrowed from test_maintenance (plain functions of company, user)
	def register(self):
		return test_maintenance.TestMaintenance.register(self)

	def breakdown(self, asset, **kw):
		return test_maintenance.TestMaintenance.breakdown(self, asset, **kw)

	# ---------------------------------------------------------------- audience and layout
	def test_audience_is_maintenance_staff_and_the_administrator(self):
		doc = frappe.get_doc("Workspace", PAGE)
		self.assertEqual({r.role for r in doc.roles}, {"Maintenance User", "Maintenance Manager", "System Manager", "Business Administrator"})
		for user in (TECH, MGR):
			self.assertIn(PAGE, visible_workspaces(user), user)
		self.assertNotIn(PAGE, visible_workspaces(OUTSIDER))
		self.assertNotIn(PAGE, visible_workspaces("fo-production@example.com"))

	def test_it_has_its_own_navigation_module_and_does_not_take_over_erpnexts(self):
		"""'Maintenance' is the name of an ERPNext module (Maintenance Schedule and Visit); using it
		for this page took that module's navigation over. The page must not share it."""
		self.assertEqual(frappe.db.get_value("Module Def", PAGE, ["custom", "app_name"]), (1, "factory_ops"))
		erpnext_module = frappe.db.get_value("Module Def", "Maintenance", ["custom", "app_name"])
		self.assertEqual(erpnext_module, (0, "erpnext"))
		self.assertFalse(frappe.db.exists("Sidebar", "Maintenance"))
		self.assertFalse(frappe.db.exists("Workspace", "Maintenance"))

	def test_a_workspace_may_not_reuse_another_apps_module_name(self):
		from factory_ops.setup.workspaces import ensure_navigation_module

		self.assertRaises(frappe.ValidationError, ensure_navigation_module, "Maintenance")
		ensure_navigation_module(PAGE)  # its own module: fine, and idempotent

	def test_saved_layout_matches_and_has_four_real_shortcuts(self):
		self.assertEqual(json.loads(frappe.db.get_value("Workspace", PAGE, "content")), definition_blocks(PAGE))
		doc = frappe.get_doc("Workspace", PAGE)
		self.assertEqual([s.link_to for s in doc.shortcuts], ["Asset Repair", "Asset Maintenance Log", "Asset", "Asset Maintenance"])
		for sc in doc.shortcuts:
			for user in (TECH, MGR):
				self.assertTrue(frappe.has_permission(sc.link_to, "read", user=user), f"{user}: {sc.link_to}")

	def test_every_card_computes_for_maintenance_staff(self):
		for key in WORKSPACES[PAGE]["cards"]:
			for user in (TECH, MGR):
				card_value(user, key)  # raises PermissionError if the card is not allowed

	# ---------------------------------------------------------------- figures are real
	def test_breakdown_cards_move_by_one_when_a_breakdown_is_raised(self):
		open_before = card_value(MGR, "FO Open Breakdowns")
		down_before = card_value(MGR, "FO Machines Under Breakdown")
		asset = self.register()
		self.assertEqual(card_value(MGR, "FO Machines Under Breakdown"), down_before)  # a healthy machine
		self.breakdown(asset)
		self.assertEqual(card_value(MGR, "FO Open Breakdowns"), open_before + 1)
		self.assertEqual(card_value(MGR, "FO Machines Under Breakdown"), down_before + 1)
		# the card equals what the list the shortcut opens would show
		rows = frappe.get_list("Asset Repair", filters={"docstatus": 0, "repair_status": "Pending"}, pluck="name")
		self.assertEqual(card_value(MGR, "FO Open Breakdowns"), len(rows))

	def test_tasks_due_card_equals_the_dashboard_api_window(self):
		from factory_ops.api.maintenance import UPCOMING_DAYS

		self.assertEqual(dashboard_data.MAINTENANCE_DUE_DAYS, UPCOMING_DAYS)  # one definition of "due soon"
		frappe.set_user(MGR)
		expected = len(
			frappe.get_list(
				"Asset Maintenance Log",
				filters={"docstatus": 0, "maintenance_status": ["in", ["Planned", "Overdue"]], "due_date": ["<=", add_days(nowdate(), UPCOMING_DAYS)]},
				pluck="name",
				limit_page_length=0,
			)
		)
		self.assertEqual(card_value(MGR, "FO Maintenance Tasks Due"), expected)

	def test_spares_card_counts_items_at_or_below_their_reorder_level(self):
		from factory_ops.api.maintenance import spares_below_reorder

		before = card_value(MGR, "FO Spares Below Reorder")
		frappe.set_user("Administrator")
		item = frappe.get_doc("Item", SPARE)
		item.set("reorder_levels", [{"warehouse": self.spare_wh, "warehouse_reorder_level": 100000, "warehouse_reorder_qty": 50, "material_request_type": "Purchase"}])
		item.save()
		frappe.set_user(MGR)
		self.assertIn(SPARE, [r.item_code for r in spares_below_reorder()])
		self.assertEqual(card_value(MGR, "FO Spares Below Reorder"), len(spares_below_reorder()))
		self.assertGreaterEqual(card_value(MGR, "FO Spares Below Reorder"), before)

	# ---------------------------------------------------------------- backend permission
	def test_method_cards_refuse_users_who_may_not_read_the_data(self):
		nobody = ensure_user("fo-nobody-pm@example.com", [])  # no roles: no stock or maintenance rights
		frappe.set_user(nobody)
		self.assertRaises(frappe.PermissionError, dashboard_data.maintenance_tasks_due)
		self.assertRaises(frappe.PermissionError, dashboard_data.spares_below_reorder_count)
		# a sales user reads items and bins, so the spares figure is theirs to see; the maintenance one is not
		frappe.set_user(OUTSIDER)
		self.assertRaises(frappe.PermissionError, dashboard_data.maintenance_tasks_due)

	def test_count_cards_are_not_displayed_as_money(self):
		"""Regression (seen in the browser): a method card returning {"fieldtype": "Int"} was shown as
		"₹ 0.00" because Frappe gives every Number Card the default currency and the widget formats a
		card that has a currency as money. A count method must return a bare number (which the widget
		shows as it is); money cards must keep returning a Currency dict."""
		frappe.set_user(MGR)
		for fn in (dashboard_data.maintenance_tasks_due, dashboard_data.spares_below_reorder_count):
			result = fn()
			self.assertIsInstance(result, int, fn.__name__)
			self.assertNotIsInstance(result, bool)
		money = dashboard_data.rm_stock_value()
		self.assertEqual(money["fieldtype"], "Currency")
