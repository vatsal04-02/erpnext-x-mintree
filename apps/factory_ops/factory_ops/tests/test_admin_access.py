# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Super Admin global access: complete visibility, no extra action rights, no bypassed controls.

A "system administrator" here is a named user with the System Manager role (not the built-in
Administrator account, which Frappe lets bypass permission checks, but whose business controls
are tested too). Visibility is the workspace and read access; action permission is Frappe's
DocPerm; business controls are server-side validation.
"""

import json

import frappe
from frappe.desk.doctype.number_card.number_card import get_result

from factory_ops.inventory.guards import QCGuardError
from factory_ops.quality.doctype.qc_release.qc_release import SegregationOfDutiesError
from factory_ops.setup import admin_access
from factory_ops.setup import admin_home
from factory_ops.setup.workspaces import (
	ADMIN_HOME,
	DEPARTMENT_ORDER,
	ERPNEXT_MODULES,
	WORKSPACES,
	card_name,
	workspace_route,
)
from factory_ops.tests.test_production import BULK, ProductionTestCase
from factory_ops.tests.test_workspaces import EXPECTED
from factory_ops.tests.utils import (
	QC_ITEM,
	USER_ACCOUNTS,
	USER_BUYER,
	USER_MANAGEMENT,
	USER_PRODUCTION,
	USER_QC,
	USER_QA,
	USER_SALES,
	USER_STORES,
	FactoryOpsTestCase,
	ensure_user,
)

SYSADMIN = "fo-sysadmin@example.com"
ADMIN = "System Manager"
# documents an administrator must be able to see but never create, change, submit, cancel or delete
TRANSACTIONS = (
	"Sales Order", "Delivery Note", "Sales Invoice", "Pick List", "Payment Entry", "Journal Entry",
	"Purchase Order", "Purchase Receipt", "Purchase Invoice", "Material Request", "Stock Entry",
	"Stock Reconciliation", "Work Order", "BOM", "Quality Inspection", "Batch", "QC Release",
	"QC Deviation", "QC Retest", "Asset Repair",
)  # fmt: skip
# R&D masters (Formulation, Research Project, ...) deliberately give System Manager create/write/delete in
# their own DocType JSON (an earlier design decision); they are masters, not transactions, and are
# asserted separately below so the exception is visible and cannot widen unnoticed.
RND_MASTERS_WITH_EXPLICIT_ADMIN_RIGHTS = ("Formulation", "Research Project", "Ingredient Category")


def effective_home() -> "frappe.model.document.Document":
	"""ERPNext's Home workspace with the site customization applied, as Desk renders it."""
	from frappe.desk.doctype.custom_workspace.custom_workspace import apply_customization, get_customization

	doc = frappe.get_doc("Workspace", "Home")
	customization = get_customization("Home")
	assert customization, "Home has no Custom Workspace: the Administration page is not applied"
	apply_customization(doc, customization)
	return doc


def all_visible(user: str) -> set[str]:
	from frappe.desk.desktop import get_workspaces

	frappe.set_user(user)
	frappe.local.request_cache.clear()
	try:
		return {p["name"] for p in get_workspaces()["pages"]}
	finally:
		frappe.set_user("Administrator")


class AdminTestCase(FactoryOpsTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_user(SYSADMIN, [ADMIN])
		frappe.db.commit()


class TestAdminVisibility(AdminTestCase):
	def test_admin_sees_every_department_and_every_erpnext_module_page(self):
		visible = all_visible(SYSADMIN)
		missing_departments = set(WORKSPACES) - visible
		self.assertFalse(missing_departments, f"not visible to the administrator: {missing_departments}")
		standard = {
			w.name
			for w in frappe.get_all(
				"Workspace",
				filters={"public": 1, "is_hidden": 0, "module": ("in", [m for m in frappe.get_all("Module Def", filters={"app_name": "erpnext"}, pluck="name")])},
			)
		}
		self.assertTrue(standard, "no ERPNext workspaces found")
		self.assertFalse(standard - visible, f"ERPNext pages hidden from the administrator: {standard - visible}")

	def test_department_pages_carry_the_administrator_and_the_icons_too(self):
		for name in WORKSPACES:
			self.assertIn(ADMIN, {r.role for r in frappe.get_doc("Workspace", name).roles}, name)
			self.assertIn(ADMIN, {r.role for r in frappe.get_doc("Desktop Icon", name).roles}, name)

	def test_admin_can_read_every_operational_doctype_and_report_on_it(self):
		doctypes = admin_access.operational_doctypes()
		self.assertGreater(len(doctypes), 200)
		blocked = [
			d for d in doctypes
			if not (frappe.has_permission(d, "read", user=SYSADMIN) and frappe.has_permission(d, "report", user=SYSADMIN))
		]
		self.assertFalse(blocked, f"administrator cannot read/report: {blocked}")

	def test_every_link_and_report_on_every_page_opens_for_the_administrator(self):
		for name in WORKSPACES:
			doc = frappe.get_doc("Workspace", name)
			for sc in doc.shortcuts:
				if sc.type == "DocType":
					self.assertTrue(frappe.has_permission(sc.link_to, "read", user=SYSADMIN), f"{name}: {sc.link_to}")
				elif sc.type == "Report":
					self.assert_report_opens(name, sc.link_to)
			for link in doc.links:
				if link.type != "Link":
					continue
				if link.link_type == "DocType":
					self.assertTrue(frappe.has_permission(link.link_to, "read", user=SYSADMIN), f"{name}: {link.link_to}")
				elif link.link_type == "Report":
					self.assert_report_opens(name, link.link_to)

	def assert_report_opens(self, workspace: str, report_name: str):
		report = frappe.get_doc("Report", report_name)
		with self.set_user(SYSADMIN):
			self.assertTrue(report.is_permitted(), f"{workspace}: report {report_name} refused to the administrator")
			self.assertTrue(frappe.has_permission(report.ref_doctype, "report"), f"{workspace}: {report_name}")

	def test_admin_can_search_other_departments_records_with_the_permission_aware_list(self):
		self.assertTrue(frappe.has_permission("Work Order", "read", user=SYSADMIN))
		with self.set_user(SYSADMIN):
			frappe.get_list("Work Order", limit_page_length=1)  # raises PermissionError if not readable
			frappe.get_list("Batch", limit_page_length=1)
			frappe.get_list("Purchase Order", limit_page_length=1)


class TestAdminHasNoActionRights(AdminTestCase):
	def test_no_action_right_beyond_what_erpnext_itself_grants(self):
		"""For every operational DocType, any action the administrator can take must be backed by
		the upstream (standard) permission rows of one of the administrator's own roles."""
		roles = set(frappe.get_roles(SYSADMIN))
		for doctype in admin_access.operational_doctypes():
			if frappe.db.get_value("DocType", doctype, "module") == "Regional":
				# country-specific DocTypes (UAE / South Africa VAT): ERPNext's own regional setup grants
				# System Manager rights through Custom DocPerm when a company of that country is created
				# (the ERPNext test bootstrap creates one). That is an upstream grant, not one of ours.
				continue
			standard = frappe.get_all("DocPerm", filters={"parent": doctype, "permlevel": 0}, fields=["role", *admin_access.ACTION_RIGHTS])
			for right in admin_access.ACTION_RIGHTS:
				if frappe.has_permission(doctype, right, user=SYSADMIN):
					backed = any(r.role in roles and r.get(right) for r in standard)
					self.assertTrue(backed, f"{doctype}: administrator can {right} without an upstream permission for it")

	def test_transaction_documents_are_view_only_for_the_administrator(self):
		for doctype in TRANSACTIONS:
			for right in admin_access.ACTION_RIGHTS:
				self.assertFalse(
					frappe.has_permission(doctype, right, user=SYSADMIN), f"administrator can {right} {doctype}"
				)

	def test_rnd_masters_keep_the_explicit_admin_rights_their_doctype_declares(self):
		"""Documented exception: not granted by this module, declared in the DocType itself."""
		for doctype in RND_MASTERS_WITH_EXPLICIT_ADMIN_RIGHTS:
			standard = frappe.get_all("DocPerm", filters={"parent": doctype, "role": ADMIN, "permlevel": 0}, fields=["write", "create", "delete"])
			self.assertTrue(standard and standard[0].write, f"{doctype}: expected an explicit System Manager row")

	def test_what_is_granted_is_exactly_read_and_report(self):
		self.assertEqual(admin_access.GRANTED_RIGHTS, ("read", "report"))
		self.assertFalse(set(admin_access.GRANTED_RIGHTS) & set(admin_access.ACTION_RIGHTS))

	def test_grants_and_report_roles_are_idempotent(self):
		self.assertEqual(admin_access.grant_admin_read(), [])
		self.assertEqual(admin_access.ensure_admin_reports(), [])

	def test_report_roles_are_the_union_and_heal_after_drift(self):
		custom = frappe.get_all("Custom Role", filters={"report": ("is", "set")}, fields=["name", "report"], limit=5)
		self.assertTrue(custom)
		for c in custom:
			roles = {r.role for r in frappe.get_doc("Custom Role", c.name).roles}
			standard = set(frappe.get_all("Has Role", filters={"parenttype": "Report", "parent": c.report}, pluck="role"))
			self.assertIn(ADMIN, roles, c.report)
			self.assertIn("Business Administrator", roles, c.report)
			self.assertTrue({r for r in standard if frappe.db.exists("Role", r)} <= roles, c.report)
		# drift: someone removes the administrator from a report's custom roles; migrate puts it back
		victim = custom[0]
		frappe.db.delete("Has Role", {"parenttype": "Custom Role", "parent": victim.name, "role": ADMIN})
		self.assertIn(victim.report, admin_access.ensure_admin_reports())
		self.assertIn(ADMIN, {r.role for r in frappe.get_doc("Custom Role", victim.name).roles})


class TestDepartmentIsolation(AdminTestCase):
	def test_no_department_user_sees_the_administration_page(self):
		for user in EXPECTED:
			self.assertNotIn("Home", all_visible(user), user)

	def test_departments_cannot_open_other_departments_documents(self):
		denied = {
			USER_PRODUCTION: ("Journal Entry", "Payment Entry", "Sales Invoice", "Purchase Invoice"),
			USER_ACCOUNTS: ("Work Order", "QC Release", "Formulation", "Asset Repair"),
			USER_QC: ("Journal Entry", "Payment Entry", "Sales Order", "Purchase Order"),
			USER_BUYER: ("Work Order", "Journal Entry", "Sales Invoice"),
			USER_SALES: ("Journal Entry", "Payment Entry", "Formulation"),  # Stock User may read Work Order, QC Release
		}
		for user, doctypes in denied.items():
			for doctype in doctypes:
				self.assertFalse(frappe.has_permission(doctype, "read", user=user), f"{user} can read {doctype}")

	def test_the_administration_page_belongs_to_the_administrator_only(self):
		self.assertEqual({r.role for r in effective_home().roles}, {ADMIN})
		self.assertIn("Home", all_visible(SYSADMIN))
		for user in (USER_MANAGEMENT, USER_BUYER, USER_QC, USER_STORES):
			self.assertNotIn("Home", all_visible(user), user)
		business_owner = ensure_user("fo-bizadmin-home@example.com", ["Business Administrator"])
		self.assertNotIn("Home", all_visible(business_owner))  # the owner sees business pages, not the technical one


class TestOperatorLimits(AdminTestCase):
	def test_only_qc_roles_create_or_submit_qc_decisions(self):
		for user in (USER_PRODUCTION, USER_STORES, USER_SALES, USER_BUYER, USER_ACCOUNTS, USER_MANAGEMENT, SYSADMIN):
			for doctype in ("QC Release", "QC Deviation", "QC Retest"):
				for right in ("create", "submit", "cancel", "amend", "delete"):
					self.assertFalse(frappe.has_permission(doctype, right, user=user), f"{user} can {right} {doctype}")

	def test_a_qc_officer_cannot_do_the_qa_managers_actions(self):
		self.assertTrue(frappe.has_permission("QC Release", "submit", user=USER_QC))
		self.assertFalse(frappe.has_permission("QC Release", "cancel", user=USER_QC))  # QA Manager only
		self.assertFalse(frappe.has_permission("QC Deviation", "submit", user=USER_QC))
		self.assertFalse(frappe.has_permission("QC Retest", "create", user=USER_QC))
		self.assertTrue(frappe.has_permission("QC Release", "cancel", user=USER_QA))

	def test_operators_cannot_delete_core_records(self):
		for user in (USER_PRODUCTION, USER_STORES, USER_SALES, USER_BUYER, USER_QC):
			for doctype in ("Batch", "QC Release", "QC Deviation", "QC Retest"):  # upstream lets Stock User delete drafts
				self.assertFalse(frappe.has_permission(doctype, "delete", user=user), f"{user} can delete {doctype}")


class TestControlsHoldForAdministrators(AdminTestCase):
	def setUp(self):
		self.hold, self.stores = self.wh["RM-QC-HOLD"], self.wh["RM-STORES"]

	def test_system_manager_cannot_create_or_submit_a_qc_release(self):
		_, batch = self.receive(QC_ITEM, self.hold, 20)
		with self.assertRaises(frappe.PermissionError):
			self.qc_release(batch, "Release", user=SYSADMIN)

	def test_held_stock_cannot_be_moved_by_the_administrator_account_or_a_system_manager(self):
		_, batch = self.receive(QC_ITEM, self.hold, 20)
		# the built-in Administrator bypasses permissions, but not the QC guard
		with self.set_user("Administrator"):
			self.assertRaises(QCGuardError, self.transfer, QC_ITEM, batch, 5, self.hold, self.stores)
		# a named system manager has no stock rights at all
		with self.set_user(SYSADMIN):
			self.assertRaises(frappe.PermissionError, self.transfer, QC_ITEM, batch, 5, self.hold, self.stores)

	def test_the_administrator_account_cannot_release_what_it_received(self):
		"""Segregation of duties is checked by user identity, so holding every role changes nothing."""
		_, batch = self.receive(QC_ITEM, self.hold, 20, user="Administrator")
		self.assertRaises(SegregationOfDutiesError, self.qc_release, batch, "Release", user="Administrator")
		self.assertEqual(frappe.db.get_value("Batch", batch, "fo_qc_status"), "Hold")

	def test_the_dispatch_guard_applies_to_the_administrator_account(self):
		_, batch = self.receive(QC_ITEM, self.hold, 20)
		with self.set_user("Administrator"):
			dn = frappe.get_doc(
				{
					"doctype": "Delivery Note",
					"company": self.company,
					"customer": "_Test Customer",
					"items": [
						{"item_code": QC_ITEM, "qty": 5, "rate": 10, "warehouse": self.hold, "use_serial_batch_fields": 1, "batch_no": batch}
					],
				}
			)
			self.assertRaises(QCGuardError, dn.insert)


class TestAdministrationWorkspace(AdminTestCase):
	"""The Administration page is ERPNext's own Setup/Home workspace, customised (never a separate page)."""

	def admin_cards(self):
		wanted = {card_name(c) for c in ADMIN_HOME["cards"]}
		return [row for row in effective_home().number_cards if row.number_card_name in wanted]

	def test_it_is_the_native_home_workspace_not_a_separate_page(self):
		self.assertFalse(frappe.db.exists("Workspace", "Administration"))
		self.assertFalse(frappe.db.exists("Desktop Icon", "Administration"))
		self.assertFalse(frappe.db.exists("Sidebar", "Administration"))
		self.assertFalse(frappe.db.exists("Module Def", "Administration"))
		self.assertTrue(frappe.db.get_value("Workspace", "Home", "standard"))
		self.assertEqual(frappe.db.get_value("Custom Workspace", "Home", "workspace"), "Home")

	def test_erpnexts_own_record_and_onboarding_are_untouched(self):
		base = frappe.get_doc("Workspace", "Home")
		self.assertEqual({r.role for r in base.roles}, {"Desk User"})  # the customization layers on top
		self.assertTrue(any(b["type"] == "onboarding" for b in json.loads(base.content)))
		self.assertTrue(frappe.db.exists("Module Onboarding", "Organization Onboarding"))

	def test_the_page_shows_administration_not_the_onboarding_panel(self):
		blocks = json.loads(effective_home().content)
		self.assertEqual((blocks[0]["type"], "Administration" in blocks[0]["data"]["text"]), ("header", True))
		self.assertNotIn("onboarding", {b["type"] for b in blocks})
		self.assertEqual([b["type"] for b in blocks[1:5]], ["number_card"] * 4)

	def test_it_links_every_department_and_the_erpnext_modules(self):
		doc = effective_home()
		tiles = [(s.label, s.url) for s in doc.shortcuts if s.type == "URL"]
		self.assertEqual([t[0] for t in tiles], list(DEPARTMENT_ORDER) + list(ERPNEXT_MODULES))
		self.assertEqual(set(DEPARTMENT_ORDER), set(WORKSPACES))
		for label, url in tiles:
			self.assertTrue(frappe.db.exists("Workspace", label), label)
			self.assertEqual(url, workspace_route(label), label)

	def test_every_link_points_at_something_that_exists(self):
		doc = effective_home()
		groups = {}
		for link in doc.links:
			if link.type == "Card Break":
				groups[link.label] = []
			elif link.type == "Link":
				groups[list(groups)[-1]].append(link.link_to)
				self.assertTrue(frappe.db.exists(link.link_type, link.link_to), link.link_to)
		self.assertEqual(
			list(groups), ["Users and access", "Audit trail", "Settings and integrations"]
		)
		self.assertEqual(
			groups["Users and access"], ["User", "Role", "Role Profile", "Module Profile", "User Permission"]
		)
		self.assertEqual(
			groups["Audit trail"], ["Activity Log", "Version", "Access Log", "Error Log", "Scheduled Job Log"]
		)

	def test_its_four_cards_compute_for_the_administrator(self):
		rows = self.admin_cards()
		self.assertEqual(len(rows), 4)
		for row in rows:
			card = frappe.get_doc("Number Card", row.number_card_name)
			self.assertTrue(frappe.has_permission("Number Card", doc=card, user=SYSADMIN), card.name)
			with self.set_user(SYSADMIN):
				get_result(card.as_dict(), card.filters_json)

	def test_its_cards_count_real_records(self):
		card = frappe.get_doc("Number Card", card_name("FO Enabled Users"))
		with self.set_user(SYSADMIN):
			shown = get_result(card.as_dict(), card.filters_json)
		# Frappe's User list never includes the built-in Administrator/Guest accounts
		expected = frappe.db.count(
			"User", {"enabled": 1, "user_type": "System User", "name": ("not in", ("Administrator", "Guest"))}
		)
		self.assertEqual(int(shown), expected)

	def test_each_figure_uses_the_same_seven_day_window_and_real_status(self):
		by_label = {frappe.get_doc("Number Card", r.number_card_name).label: json.loads(
			frappe.db.get_value("Number Card", r.number_card_name, "filters_json")
		) for r in self.admin_cards()}
		self.assertIn(["Activity Log", "status", "=", "Failed", False], by_label["Failed Logins (7 days)"])
		self.assertIn(["Activity Log", "operation", "=", "Login", False], by_label["Failed Logins (7 days)"])
		self.assertIn(["Scheduled Job Log", "status", "=", "Failed", False], by_label["Failed Scheduled Jobs (7 days)"])
		for label in ("Failed Logins (7 days)", "Errors Logged (7 days)", "Failed Scheduled Jobs (7 days)"):
			self.assertTrue(any(f[1] == "creation" and f[3] == "last 7 days" for f in by_label[label]), label)

	def test_applying_it_again_changes_nothing(self):
		self.assertFalse(admin_home.ensure_admin_home())
		self.assertEqual(admin_home.retire_standalone_administration(), None)

	def test_the_audit_figures_are_refused_by_the_server_to_those_without_access(self):
		"""Not just hidden from the menu: Frappe returns a workspace's layout without a role gate, but every
		figure is computed by the Number Card API under the caller's own rights on the underlying DocType."""
		owner = ensure_user("fo-bizadmin-home2@example.com", ["Business Administrator"])
		audit = ("FO Failed Logins 7 Days", "FO Errors 7 Days", "FO Failed Jobs 7 Days")
		for user in (USER_PRODUCTION, USER_BUYER, USER_QC, USER_MANAGEMENT, owner):
			for key in audit:
				card = frappe.get_doc("Number Card", card_name(key))
				self.assertFalse(
					frappe.has_permission("Number Card", doc=card, user=user), f"{user} may read {card.name}"
				)
				with self.set_user(user):
					with self.assertRaises(frappe.PermissionError, msg=f"{user}: {card.name}"):
						get_result(card.as_dict(), card.filters_json)
		for key in audit:  # and the System Administrator is served
			card = frappe.get_doc("Number Card", card_name(key))
			with self.set_user(SYSADMIN):
				get_result(card.as_dict(), card.filters_json)

	def test_it_is_not_a_department_page(self):
		self.assertNotIn("Home", DEPARTMENT_ORDER)
		self.assertNotIn("Administration", WORKSPACES)


class TestAdminSearchAcrossDepartments(ProductionTestCase):
	company = "_FO Perpetual Company"
	company_abbr = "_FOPC"

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from factory_ops.setup.sample_company import ensure_boms

		cls.boms = ensure_boms(cls.company)
		ensure_user(SYSADMIN, [ADMIN])
		frappe.db.commit()

	def test_a_system_manager_finds_a_work_order_made_by_production(self):
		wo = self.work_order(BULK, 10)
		with self.set_user(SYSADMIN):
			self.assertEqual(frappe.get_list("Work Order", filters={"name": wo.name}, pluck="name"), [wo.name])
			self.assertTrue(frappe.has_permission("Work Order", "read", doc=wo.name))
			found = frappe.desk.search.search_widget("Work Order", txt=wo.name)
			self.assertIn(wo.name, [(r.get("value") if hasattr(r, "get") else r[0]) for r in found])
			# ... and can look but not touch
			doc = frappe.get_doc("Work Order", wo.name)
			self.assertRaises(frappe.PermissionError, doc.cancel)
		self.assertEqual(frappe.db.get_value("Work Order", wo.name, "docstatus"), 1)
