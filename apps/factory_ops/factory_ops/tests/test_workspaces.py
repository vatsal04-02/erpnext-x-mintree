"""Milestone 6, Parts C-E: role-based workspaces, reports and dashboard figures.

- every workspace link, card and chart points at something that exists (functional, not decorative);
- each role sees its own workspaces, through the same functions Desk uses;
- the read-only management role can read and report but never create, submit or approve;
- no role can move QC Hold stock through any screen;
- every new dashboard figure equals the ERPNext ledger or report it summarises.
"""

from collections import defaultdict

import frappe
from erpnext.stock.report.stock_balance.stock_balance import execute as stock_balance
from frappe.utils import add_days, flt, nowdate

from factory_ops.inventory.guards import QCGuardError
from factory_ops.production.report.wastage_by_reason.wastage_by_reason import execute as wastage_report
from factory_ops.purchase.report.rm_pm_requirements.rm_pm_requirements import execute as requirements
from factory_ops.setup import dashboard_data
from factory_ops.setup.workspaces import (
	CARDS,
	CHARTS,
	WORKSPACES,
	card_name,
	icon_link,
	page_roles,
	workspace_route,
)
from factory_ops.tests.utils import (
	USER_ACCOUNTS,
	USER_APPROVER_HIGH,
	USER_BUYER,
	USER_DISPATCH,
	USER_MANAGEMENT,
	USER_PRODUCTION,
	USER_QA,
	USER_QC,
	USER_SALES,
	USER_STORES,
	FactoryOpsTestCase,
)

# representative user -> workspaces they must see (and no other factory workspace)
EXPECTED = {
	USER_MANAGEMENT: {"Management"},
	USER_BUYER: {"Procurement"},
	USER_APPROVER_HIGH: {"Procurement"},
	USER_STORES: {"Stores and Warehouse", "Procurement"},  # stores staff are Stock + Purchase users
	USER_PRODUCTION: {"Production Floor", "Stores and Warehouse"},  # Manufacturing User + Stock User
	USER_QC: {"Quality Control"},
	USER_QA: {"Quality Control"},
	USER_SALES: {"Sales and Dispatch", "Stores and Warehouse"},  # Sales User + Stock User
	USER_DISPATCH: {"Sales and Dispatch", "Stores and Warehouse"},  # Stock User + Delivery User
	USER_ACCOUNTS: {"Factory Accounts"},
}


def visible_workspaces(user: str) -> set[str]:
	from frappe.desk.desktop import get_workspaces

	frappe.set_user(user)
	frappe.local.request_cache.clear()  # Desk caches the list per request
	try:
		pages = get_workspaces()["pages"]
	finally:
		frappe.set_user("Administrator")
	return {p["name"] for p in pages} & set(WORKSPACES)


class TestWorkspaceDefinitions(FactoryOpsTestCase):
	def test_every_link_card_and_chart_exists(self):
		for name, ws in WORKSPACES.items():
			doc = frappe.get_doc("Workspace", name)
			self.assertEqual({r.role for r in doc.roles}, set(page_roles(ws)), name)  # own roles + System Manager
			for link in doc.links:
				if link.type == "Link":
					self.assertTrue(
						frappe.db.exists(link.link_type, link.link_to),
						f"{name}: {link.link_type} {link.link_to}",
					)
			for card in doc.number_cards:
				self.assertTrue(
					frappe.db.exists("Number Card", card.number_card_name),
					f"{name}: card {card.number_card_name}",
				)
			for chart in doc.charts:
				self.assertTrue(
					frappe.db.exists("Dashboard Chart", chart.chart_name), f"{name}: chart {chart.chart_name}"
				)
			for sc in doc.shortcuts:
				if sc.type == "URL":  # a tile to another page: its workspace must exist and match the route
					target = next(n for n in (w.name for w in frappe.get_all("Workspace")) if workspace_route(n) == sc.url)
					self.assertEqual(target, sc.label, f"{name}: tile {sc.label}")
				else:
					self.assertTrue(frappe.db.exists(sc.type, sc.link_to), f"{name}: shortcut {sc.link_to}")
			self.assertEqual(doc.module, name)  # its own navigation module (custom, no code)
			self.assertEqual(
				frappe.db.get_value("Module Def", name, ["custom", "app_name"]), (1, "factory_ops")
			)
			sidebar = frappe.get_doc("Sidebar", name)
			self.assertEqual(sidebar.module, name)
			# one item: the role-restricted page, so the shell shows only to the page's roles
			self.assertEqual([(i.link_type, i.link_to) for i in sidebar.items], [("Workspace", name)])
			for item in sidebar.items:
				if item.type == "Link":
					self.assertTrue(
						frappe.db.exists(item.link_type, item.link_to), f"{name} sidebar: {item.link_to}"
					)
			icon = frappe.get_doc("Desktop Icon", name)
			self.assertEqual((icon.link_type, icon.link_to), ("Workspace Sidebar", icon_link(name)))
			self.assertFalse(frappe.db.exists("Workspace", icon.link_to))  # see icon_link
			self.assertEqual({r.role for r in icon.roles}, set(page_roles(ws)))
			# the administrator sees every page (visibility only: rights come from permissions)
			self.assertIn("System Manager", {r.role for r in doc.roles}, name)
			self.assertIn("System Manager", {r.role for r in icon.roles}, name)
		for key in CARDS:
			self.assertTrue(frappe.db.exists("Number Card", card_name(key)), key)
		for name in CHARTS:
			self.assertTrue(frappe.db.exists("Dashboard Chart", name))

	def test_names_are_valid_desk_routes(self):
		"""Frappe v16: a workspace's URL may not clash with a DocType, Page or other Workspace, and a
		sidebar may not carry another module's name (both found while building Milestone 6)."""
		from frappe.desk.utils import slug

		for name in WORKSPACES:
			for doctype in ("DocType", "Page"):
				self.assertFalse(
					[d for d in frappe.get_all(doctype, pluck="name") if slug(d) == slug(name)],
					f"{name} vs {doctype}",
				)
			# the only module of that name is the workspace's own navigation module
			self.assertEqual(frappe.db.get_value("Module Def", name, "custom"), 1, name)

	def test_setup_is_idempotent(self):
		from factory_ops.setup.workspaces import ensure_workspaces

		before = {n: len(frappe.get_doc("Workspace", n).links) for n in WORKSPACES}
		ensure_workspaces()
		ensure_workspaces()
		self.assertEqual({n: len(frappe.get_doc("Workspace", n).links) for n in WORKSPACES}, before)
		self.assertEqual(frappe.db.count("Desktop Icon", {"app": "factory_ops"}), len(WORKSPACES))


class TestRoleAccess(FactoryOpsTestCase):
	def test_each_role_sees_its_own_workspaces_and_icons(self):
		# Desktop icons are resolved against the boot payload, so the home screen itself is
		# verified in the browser check (ui_check_workspaces.js). Here: workspaces through Desk's
		# own function, and that each icon carries exactly its workspace's roles.
		for user, expected in EXPECTED.items():
			self.assertEqual(visible_workspaces(user), expected, user)
			user_roles = set(frappe.get_roles(user))
			icons = {
				name
				for name in WORKSPACES
				if user_roles & {r.role for r in frappe.get_doc("Desktop Icon", name).roles}
			}
			self.assertEqual(icons, expected, user)

	def test_every_card_on_a_workspace_is_readable_by_its_users(self):
		"""Regression: custom-method cards without a document_type were hidden from everyone but
		System Managers (seen as blank slots in the browser check)."""
		for user, expected in EXPECTED.items():
			for ws in expected:
				for row in frappe.get_doc("Workspace", ws).number_cards:
					card = frappe.get_doc("Number Card", row.number_card_name)
					self.assertTrue(
						frappe.has_permission("Number Card", doc=card, user=user),
						f"{user}: {ws} / {card.name}",
					)

	def test_every_card_on_a_workspace_computes_for_its_users(self):
		"""Regression (found by the browser check after Milestone 6): seeing a card is not enough;
		its figure is computed with the user's permissions, including field-level (permlevel)
		ones. Management Viewer could see 'Batches Awaiting QC' but not filter on the permlevel-1
		Batch.fo_qc_status, so the workspace opened with a Permission Error."""
		from frappe.desk.doctype.number_card.number_card import get_result

		for user, expected in EXPECTED.items():
			for ws in expected:
				for row in frappe.get_doc("Workspace", ws).number_cards:
					card = frappe.get_doc("Number Card", row.number_card_name)
					with self.set_user(user):
						try:
							if card.type == "Custom":
								frappe.get_attr(card.method)()
							else:
								get_result(card.as_dict(), card.filters_json)
						except frappe.PermissionError as e:
							self.fail(f"{user}: {ws} / {card.name}: {e}")

	def test_rolling_window_cards_include_today(self):
		"""Regression: "Batches Made (30 days)" filtered on Timespan "last month", which Frappe defines
		as the previous calendar month, so batches made this month were never counted."""
		from frappe.utils import date_diff, get_timespan_date_range, getdate, nowdate

		for key, card in CARDS.items():
			for f in card.get("filters", []):
				if f[1] != "Timespan":
					continue
				start, end = get_timespan_date_range(f[2])
				self.assertLessEqual(getdate(start), getdate(nowdate()), key)
				self.assertGreaterEqual(getdate(end), getdate(nowdate()), f"{key}: window ends before today")
				if "30 days" in card["label"]:
					self.assertEqual(date_diff(end, start), 30, key)

	def test_management_viewer_reads_but_never_changes(self):
		frappe.set_user(USER_MANAGEMENT)
		try:
			for doctype in (
				"Sales Invoice",
				"Stock Ledger Entry",
				"Work Order",
				"QC Release",
				"GL Entry",
				"Bin",
			):
				self.assertTrue(frappe.has_permission(doctype, "read"), doctype)
			for doctype in (
				"Sales Order",
				"Sales Invoice",
				"Purchase Order",
				"Stock Entry",
				"QC Release",
				"Payment Entry",
			):
				for right in ("create", "write", "submit", "cancel"):
					self.assertFalse(frappe.has_permission(doctype, right), f"{doctype}.{right}")
			for report in (
				"Stock Balance",
				"Accounts Receivable Summary",
				"Profit and Loss Statement",
				"Wastage by Reason",
				"Available to Sell",
			):
				self.assertTrue(frappe.get_doc("Report", report).is_permitted(), report)
		finally:
			frappe.set_user("Administrator")
		# granting the role to reports kept their standard roles
		self.assertTrue(frappe.get_doc("Report", "Stock Balance").is_permitted())  # Administrator
		frappe.set_user(USER_STORES)
		try:
			self.assertTrue(frappe.get_doc("Report", "Stock Balance").is_permitted())
		finally:
			frappe.set_user("Administrator")

	def test_no_screen_lets_anyone_move_held_stock(self):
		"""Workspaces only link to documents; every role meets the same server-side guards."""
		_, held = self.receive("SAMPLE-FG-0001", self.wh["FG-QC-HOLD"], 10, expiry_days=300)
		for user in EXPECTED:
			with self.set_user(user):
				try:
					self.transfer("SAMPLE-FG-0001", held, 5, self.wh["FG-QC-HOLD"], self.wh["FG-STORES"])
				except (frappe.PermissionError, QCGuardError):
					continue
				self.fail(f"{user} moved QC Hold stock")
		self.assertEqual(frappe.db.get_value("Batch", held, "fo_qc_status"), "Hold")


class TestDashboardFigures(FactoryOpsTestCase):
	company = "_FO Perpetual Company"
	company_abbr = "_FOPC"

	def test_stock_value_cards_equal_the_stock_balance_report(self):
		self.receive("SAMPLE-RM-0001", self.wh["RM-QC-HOLD"], 12, rate=150, expiry_days=300)
		self.receive("SAMPLE-FG-0001", self.wh["FG-QC-HOLD"], 40, rate=90, expiry_days=300)
		_, rows = stock_balance(
			frappe._dict(
				{
					"company": self.company,
					"from_date": add_days(nowdate(), -3650),
					"to_date": nowdate(),
					"valuation_field_type": "Currency",
				}
			)
		)[:2]
		by_group = defaultdict(float)
		for r in rows:
			if isinstance(r, dict) and r.get("item_code"):
				by_group[r.get("item_group")] += flt(r.get("bal_val"))
		total_report = flt(sum(by_group.values()), 2)
		for group in ("RM", "PM", "BULK", "FG"):
			self.assertAlmostEqual(
				dashboard_data.stock_value(self.company, group),
				flt(by_group.get(group), 2),
				places=2,
				msg=group,
			)
		self.assertAlmostEqual(
			flt(
				frappe.db.sql(
					"select sum(stock_value) from `tabBin` b inner join `tabWarehouse` w on w.name=b.warehouse where w.company=%s",
					self.company,
				)[0][0],
				2,
			),
			total_report,
			places=2,
		)
		self.assertAlmostEqual(
			dashboard_data.stock_value(self.company, warehouse_type="Rejected"),
			flt(
				frappe.db.sql(
					"select coalesce(sum(stock_value),0) from `tabBin` b inner join `tabWarehouse` w on w.name=b.warehouse where w.company=%s and w.warehouse_type='Rejected'",
					self.company,
				)[0][0],
				2,
			),
			places=2,
		)

	def test_wastage_card_report_and_ledger_agree(self):
		from factory_ops.tests.test_production import REASON, ProductionTestCase

		batch = ProductionTestCase.released_stock(self, "SAMPLE-PM-0001", 100, rate=5)
		before = dashboard_data.wastage_value(self.company)
		with self.set_user(USER_STORES):
			se = ProductionTestCase.issue(self, "SAMPLE-PM-0001", self.wh["PM-STORES"], 12, REASON, batch)
		self.assertEqual(flt(dashboard_data.wastage_value(self.company) - before, 2), 60)
		_, rows = wastage_report({"company": self.company, "from_date": nowdate(), "to_date": nowdate()})
		mine = [r for r in rows if r.stock_entry == se.name]
		self.assertEqual(
			[(r.item_code, flt(r.qty), flt(r.value), r.wastage_reason, r.batch_no) for r in mine],
			[("SAMPLE-PM-0001", 12, 60, REASON, batch)],
		)
		account = frappe.db.get_value("Company", self.company, "fo_wastage_account")
		gl = frappe.db.sql(
			"select sum(debit - credit) from `tabGL Entry` where voucher_no=%s and account=%s and is_cancelled=0",
			(se.name, account),
		)[0][0]
		self.assertEqual(flt(gl, 2), 60)
		_, summary = wastage_report(
			{"company": self.company, "from_date": nowdate(), "to_date": nowdate(), "summary": 1}
		)
		self.assertEqual(flt(sum(r["value"] for r in summary), 2), flt(sum(flt(r.value) for r in rows), 2))

	def test_document_cards_count_what_they_say(self):
		from frappe.desk.doctype.number_card.number_card import get_result

		_, held = self.receive("SAMPLE-FG-0001", self.wh["FG-QC-HOLD"], 5, expiry_days=300)
		card = frappe.get_doc("Number Card", card_name("FO Batches Awaiting QC"))
		self.assertEqual(
			flt(get_result(card.as_dict(), "[]")),
			frappe.db.count("Batch", {"fo_qc_status": ("in", ["Hold", "Partly Released"])}),
		)
		self.assertTrue(frappe.db.get_value("Batch", held, "fo_qc_status") == "Hold")
		card = frappe.get_doc("Number Card", card_name("FO QC Releases Pending"))
		self.assertEqual(
			flt(get_result(card.as_dict(), "[]")), frappe.db.count("QC Release", {"docstatus": 0})
		)

	def test_empty_data_is_handled(self):
		self.assertEqual(
			wastage_report({"company": self.company, "from_date": "2001-01-01", "to_date": "2001-01-02"})[1],
			[],
		)
		self.assertEqual(
			wastage_report(
				{"company": self.company, "from_date": "2001-01-01", "to_date": "2001-01-02", "summary": 1}
			)[1],
			[],
		)
		self.assertIsInstance(
			requirements({"company": self.company, "factory_group": "PM", "only_shortfall": 1})[1], list
		)


class TestRequirementsReport(FactoryOpsTestCase):
	company = "_FO Perpetual Company"
	company_abbr = "_FOPC"

	def test_need_supply_and_shortfall(self):
		from factory_ops.setup.sample_company import ensure_boms

		boms = ensure_boms(self.company)
		rm = "SAMPLE-RM-0001"
		base = {
			r["item_code"]: r for r in requirements({"company": self.company, "factory_group": "RM"})[1]
		}.get(rm, {})
		# need: a bulk Work Order for 200 kg needs 10 kg of RM-0001
		wo = frappe.get_doc(
			{
				"doctype": "Work Order",
				"company": self.company,
				"production_item": "SAMPLE-BULK-0001",
				"bom_no": boms["SAMPLE-BULK-0001"],
				"qty": 200,
			}
		).insert()
		wo.submit()
		# supply: 3 kg usable, 2 kg still in QC Hold
		_, b1 = self.receive(rm, self.wh["RM-QC-HOLD"], 3, expiry_days=300)
		self.qc_release(b1, "Release", item_code=rm, source=self.wh["RM-QC-HOLD"])
		self.receive(rm, self.wh["RM-QC-HOLD"], 2, expiry_days=300)
		row = {r["item_code"]: r for r in requirements({"company": self.company, "factory_group": "RM"})[1]}[
			rm
		]
		self.assertEqual(flt(row["required"] - flt(base.get("required"))), 10)
		self.assertEqual(flt(row["usable"] - flt(base.get("usable"))), 3)
		self.assertEqual(flt(row["qc_hold"] - flt(base.get("qc_hold"))), 2)
		expected = max(
			row["required"]
			+ row["reorder_level"]
			- row["usable"]
			- row["qc_hold"]
			- row["on_order"]
			- row["requested"],
			0,
		)
		self.assertEqual(flt(row["shortfall"], 3), flt(expected, 3))
		# usable figure equals the Bins of Usable warehouses
		usable = frappe.db.sql(
			"select sum(actual_qty) from `tabBin` b inner join `tabWarehouse` w on w.name=b.warehouse where b.item_code=%s and w.company=%s and w.warehouse_type='Usable'",
			(rm, self.company),
		)[0][0]
		self.assertEqual(flt(row["usable"], 3), flt(usable, 3))
		# a Work Order that is completed no longer needs anything
		frappe.db.set_value("Work Order", wo.name, "status", "Completed")
		row2 = {r["item_code"]: r for r in requirements({"company": self.company, "factory_group": "RM"})[1]}[
			rm
		]
		self.assertEqual(flt(row2["required"]), flt(base.get("required")))


class TestStockByQCStatusEligibility(FactoryOpsTestCase):
	def test_dispatch_eligibility_column_follows_the_central_rule(self):
		from factory_ops.inventory.report.stock_by_qc_status.stock_by_qc_status import execute as qc_stock

		_, short = self.receive("SAMPLE-FG-0001", self.wh["FG-QC-HOLD"], 5, expiry_days=300)
		frappe.db.set_value("Batch", short, "expiry_date", add_days(nowdate(), 20))
		frappe.db.set_single_value("Factory Ops Settings", "min_shelf_life_days_for_dispatch", 30)
		row = next(
			r
			for r in qc_stock({"company": self.company, "item_code": "SAMPLE-FG-0001"})[1]
			if r.batch_no == short
		)
		self.assertEqual(row.dispatch_eligibility, "Below minimum shelf life")
		frappe.db.set_single_value("Factory Ops Settings", "min_shelf_life_days_for_dispatch", 0)
		row = next(
			r
			for r in qc_stock({"company": self.company, "item_code": "SAMPLE-FG-0001"})[1]
			if r.batch_no == short
		)
		self.assertEqual(row.dispatch_eligibility, "Eligible")
