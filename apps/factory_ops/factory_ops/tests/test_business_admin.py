# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""The two administrator roles, kept apart.

System Administrator (System Manager): technical. Business Administrator: the owner, who sees every
business page and record, and approves only what the existing authorisation allows. Neither role
turns visibility into permission, and every business control still applies.
"""

import frappe

from factory_ops.inventory.guards import QCGuardError
from factory_ops.purchase.approval import ApprovalLimitError, approve_purchase_order
from factory_ops.setup import admin_access, audit_trail
from factory_ops.setup.workspaces import WORKSPACES, page_roles
from factory_ops.tests import test_purchase
from factory_ops.tests.test_admin_access import TRANSACTIONS, all_visible
from factory_ops.tests.utils import (
	LIMIT_LOW,
	PLAIN_ITEM,
	QC_ITEM,
	USER_APPROVER_LOW,
	USER_BUYER,
	FactoryOpsTestCase,
	ensure_approval_limit,
	ensure_user,
	production_auditing,
	production_mode,
)

BIZ = "Business Administrator"
SYS = "System Manager"
OWNER = "fo-owner@example.com"
OWNER_NO_LIMIT = "fo-owner-nolimit@example.com"
SYSADMIN = "fo-sysadmin@example.com"
DEPARTMENT_PAGES = set(WORKSPACES) - {"Administration"}
# configuration and identity: the System Administrator's job, never the owner's
TECHNICAL = (
	"User", "Role", "Role Profile", "Module Profile", "User Permission", "Custom Field", "Property Setter",
	"Report", "DocType", "Custom DocPerm", "DocPerm", "Custom Role", "System Settings",
	"Website Settings", "Webhook", "Integration Request", "Server Script", "Client Script", "Print Format",
	"Number Card", "Dashboard Chart", "Workflow", "Role Permission for Page and Report", "Email Account",
)  # fmt: skip
ACTIONS = admin_access.ACTION_RIGHTS  # write, create, submit, cancel, delete, amend


class OwnerTestCase(FactoryOpsTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_user(OWNER, [BIZ])
		ensure_user(OWNER_NO_LIMIT, [BIZ])
		ensure_user(SYSADMIN, [SYS, "Workspace Manager"])  # what the "System Administrator" Role Profile gives
		ensure_approval_limit(OWNER, 1_000_000)
		frappe.db.commit()


class TestRolesAndProfiles(OwnerTestCase):
	def test_the_business_administrator_role_exists_and_is_a_desk_role(self):
		self.assertTrue(frappe.db.exists("Role", BIZ))
		self.assertEqual(frappe.db.get_value("Role", BIZ, "desk_access"), 1)

	def test_each_administrator_is_made_by_choosing_a_role_profile(self):
		sys_profile = frappe.get_doc("Role Profile", "System Administrator")
		biz_profile = frappe.get_doc("Role Profile", "Business Administrator")
		self.assertEqual({r.role for r in sys_profile.roles}, {SYS, "Workspace Manager"})
		self.assertEqual({r.role for r in biz_profile.roles}, {BIZ})
		# the profiles never overlap: nobody becomes both by accident
		self.assertFalse({r.role for r in sys_profile.roles} & {r.role for r in biz_profile.roles})
		self.assertNotIn("Workspace Manager", {r.role for r in biz_profile.roles})

	def test_profiles_and_grants_are_idempotent(self):
		self.assertEqual(admin_access.ensure_role_profiles(), [])
		self.assertEqual(admin_access.grant_business_actions(), [])
		self.assertEqual(admin_access.grant_admin_read(), [])

	def test_a_role_profile_gives_exactly_its_role(self):
		email = "fo-profile-owner@example.com"
		user = frappe.get_doc({"doctype": "User", "email": email, "first_name": "Profile", "send_welcome_email": 0, "role_profile_name": "Business Administrator"}) if not frappe.db.exists("User", email) else frappe.get_doc("User", email)
		user.save(ignore_permissions=True)
		roles = set(frappe.get_roles(email))
		self.assertIn(BIZ, roles)
		self.assertNotIn(SYS, roles)


class TestWhatTheSystemAdministratorCanDo(OwnerTestCase):
	def test_identity_and_configuration_are_theirs(self):
		for doctype, right in (
			("User", "create"), ("User", "write"), ("Role", "create"), ("Role Profile", "write"),
			("Module Profile", "write"), ("User Permission", "create"), ("Custom Field", "create"),
			("Property Setter", "create"), ("Report", "write"), ("Webhook", "create"), ("Custom DocPerm", "create"),
			("Purchase Approval Limit", "write"),  # who may approve what is set by the technical/accounts side
		):
			self.assertTrue(frappe.has_permission(doctype, right, user=SYSADMIN), f"{doctype} {right}")

	def test_system_settings_and_the_audit_log_are_theirs(self):
		self.assertTrue(frappe.has_permission("System Settings", "write", user=SYSADMIN))
		for doctype in ("Activity Log", "Error Log", "Scheduled Job Log", "Access Log", "Version"):
			self.assertTrue(frappe.has_permission(doctype, "read", user=SYSADMIN), doctype)

	def test_workspace_configuration_comes_with_workspace_manager(self):
		from frappe.desk.doctype.workspace.workspace import is_workspace_manager

		self.assertTrue(is_workspace_manager(SYSADMIN))
		plain = ensure_user("fo-sysonly@example.com", [SYS])  # System Manager alone is not enough
		self.assertFalse(is_workspace_manager(plain))

	def test_the_technical_administrator_still_approves_nothing_in_the_business(self):
		for doctype in ("Purchase Order", "Sales Order", "Payment Entry", "Journal Entry", "QC Release", "Delivery Note"):
			for right in ("submit", "cancel"):
				self.assertFalse(frappe.has_permission(doctype, right, user=SYSADMIN), f"{doctype} {right}")


class TestWhatTheOwnerSees(OwnerTestCase):
	def test_every_business_page_but_not_the_technical_one(self):
		visible = all_visible(OWNER)
		self.assertFalse(DEPARTMENT_PAGES - visible, f"hidden from the owner: {DEPARTMENT_PAGES - visible}")
		self.assertNotIn("Administration", visible)
		self.assertNotIn(BIZ, {r.role for r in frappe.get_doc("Desktop Icon", "Administration").roles})
		self.assertNotIn(BIZ, {r.role for r in frappe.get_doc("Workspace", "Administration").roles})

	def test_the_system_administrator_still_sees_everything_including_the_technical_page(self):
		visible = all_visible(SYSADMIN)
		self.assertTrue(set(WORKSPACES) <= visible)

	def test_page_roles_follow_the_rule(self):
		for name, ws in WORKSPACES.items():
			roles = set(page_roles(ws))
			self.assertIn(SYS, roles, name)
			self.assertEqual(BIZ in roles, name != "Administration", name)

	def test_the_owner_reads_and_reports_on_every_operational_doctype(self):
		blocked = [
			d for d in admin_access.operational_doctypes()
			if not (frappe.has_permission(d, "read", user=OWNER) and frappe.has_permission(d, "report", user=OWNER))
		]
		self.assertFalse(blocked, blocked)

	def test_every_link_and_report_on_every_business_page_opens_for_the_owner(self):
		for name in DEPARTMENT_PAGES:
			doc = frappe.get_doc("Workspace", name)
			for sc in doc.shortcuts:
				if sc.type == "DocType":
					self.assertTrue(frappe.has_permission(sc.link_to, "read", user=OWNER), f"{name}: {sc.link_to}")
			for link in doc.links:
				if link.type == "Link" and link.link_type == "Report":
					report = frappe.get_doc("Report", link.link_to)
					with self.set_user(OWNER):
						self.assertTrue(report.is_permitted(), f"{name}: {link.link_to}")

	def test_the_owner_reads_the_document_audit_trail(self):
		self.assertTrue(frappe.has_permission("Version", "read", user=OWNER))


class TestWhatTheOwnerCannotDo(OwnerTestCase):
	def test_no_technical_configuration(self):
		for doctype in TECHNICAL:
			if not frappe.db.exists("DocType", doctype):
				continue
			# Frappe lets everyone manage their own User record, private workspaces, cards and charts, so
			# configuration is checked against a record somebody else owns whenever one exists
			doc = None if frappe.get_meta(doctype).issingle else frappe.db.get_value(doctype, {"owner": ("!=", OWNER)}, "name")
			for right in ACTIONS:
				self.assertFalse(
					frappe.has_permission(doctype, right, doc=doc, user=OWNER) if doc else frappe.has_permission(doctype, right, user=OWNER),
					f"owner can {right} {doctype}" + (f" {doc}" if doc else ""),
				)

	def test_shared_workspaces_can_only_be_changed_by_a_workspace_manager(self):
		"""Frappe gives the Desk User role DocPerm write on Workspace (for personal workspaces); the
		controller then reserves public ones for Workspace Manager, which the owner does not hold."""
		from frappe.desk.doctype.workspace.workspace import is_workspace_manager

		self.assertFalse(is_workspace_manager(OWNER))
		self.assertTrue(is_workspace_manager(SYSADMIN))
		with self.set_user(OWNER), production_mode():  # the guard is off under the test runner
			doc = frappe.get_doc("Workspace", "Management")
			doc.title = "Management (changed by the owner)"
			self.assertRaises((frappe.ValidationError, frappe.PermissionError), doc.save)
			self.assertRaises((frappe.ValidationError, frappe.PermissionError), frappe.delete_doc, "Workspace", "Plant Maintenance")
		self.assertTrue(frappe.db.exists("Workspace", "Plant Maintenance"))
		self.assertEqual(frappe.db.get_value("Workspace", "Management", "title"), "Management")

	def test_no_delete_right_anywhere(self):
		self.assertFalse(frappe.db.count("DocPerm", {"role": BIZ, "delete": 1}))
		self.assertFalse(frappe.db.count("Custom DocPerm", {"role": BIZ, "delete": 1}))
		for doctype in admin_access.operational_doctypes():
			self.assertFalse(frappe.has_permission(doctype, "delete", user=OWNER), f"owner can delete {doctype}")

	def test_the_only_action_right_is_to_submit_purchase_orders(self):
		"""Exactly one action right exists on any operational DocType: submit on Purchase Order."""
		roles = set(frappe.get_roles(OWNER))  # includes the automatic roles All and Desk User
		granted_by_us = set()
		for doctype in admin_access.operational_doctypes():
			standard = frappe.get_all("DocPerm", filters={"parent": doctype, "permlevel": 0}, fields=["role", *ACTIONS])
			for right in ACTIONS:
				if frappe.has_permission(doctype, right, user=OWNER) and not any(
					row.role in roles and row.get(right) for row in standard
				):
					granted_by_us.add((doctype, right))  # a right no upstream row gives this user
		# (upstream itself lets the automatic All / Desk User roles create e.g. Video; that is not ours)
		self.assertEqual(granted_by_us, {("Purchase Order", "submit")})

	def test_the_owner_cannot_change_who_may_approve(self):
		for right in ("write", "create", "delete"):
			self.assertFalse(frappe.has_permission("Purchase Approval Limit", right, user=OWNER))

	def test_the_system_administrator_is_not_an_approver(self):
		for doctype in ("Purchase Order", "Sales Order", "Payment Entry", "Journal Entry", "QC Release"):
			self.assertFalse(frappe.has_permission(doctype, "submit", user=SYSADMIN), doctype)


class TestApprovalsStayAuthorised(test_purchase.PurchaseTestCase):
	"""Approving is submitting. The Business Administrator holds the submit right but not write, so it
	approves through ``approve_purchase_order``; the existing limit rules decide whether it may."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_user(OWNER, [BIZ])
		ensure_user(OWNER_NO_LIMIT, [BIZ])
		ensure_approval_limit(OWNER, 1_000_000)
		frappe.db.commit()

	def po(self, total: float, submit_as=None):
		"""A Purchase Order whose base grand total is exactly ``total`` (one line, no tax)."""
		po = self.make_po(qty=1, rate=total, submit_as=submit_as)
		self.assertEqual(frappe.utils.flt(po.base_grand_total, 2), frappe.utils.flt(total, 2))
		return po

	def approve_as(self, user, po):
		with self.set_user(user):
			return approve_purchase_order(po.name)

	def status(self, po) -> int:
		return frappe.db.get_value("Purchase Order", po.name, "docstatus")

	# ---- the happy path and what makes it authorised --------------------------------------
	def test_the_owner_approves_within_the_limit_an_accountant_gave_them(self):
		po = self.po(5_000)
		self.assertEqual(self.approve_as(OWNER, po), {"name": po.name, "docstatus": 1})
		self.assertEqual(self.status(po), 1)

	def test_without_an_approval_limit_the_role_alone_approves_nothing(self):
		po = self.po(5_000)
		self.assertRaises(ApprovalLimitError, self.approve_as, OWNER_NO_LIMIT, po)
		self.assertEqual(self.status(po), 0)

	def test_the_standard_form_path_needs_write_which_the_owner_does_not_have(self):
		"""Frappe v16: Document.submit() saves, and saving checks write. This is why the controlled
		function exists, and it documents that the owner cannot edit orders."""
		po = self.po(5_000)
		with self.set_user(OWNER):
			self.assertRaises(frappe.PermissionError, frappe.get_doc("Purchase Order", po.name).submit)
		self.assertEqual(self.status(po), 0)

	# ---- boundary conditions of the limit ---------------------------------------------------
	def test_an_order_exactly_at_the_limit_is_approved(self):
		ensure_approval_limit(OWNER, 25_000)
		po = self.po(25_000)
		self.approve_as(OWNER, po)
		self.assertEqual(self.status(po), 1)

	def test_one_paisa_above_the_limit_is_refused(self):
		ensure_approval_limit(OWNER, 25_000)
		po = self.po(25_000.01)
		self.assertRaises(ApprovalLimitError, self.approve_as, OWNER, po)
		self.assertEqual(self.status(po), 0)

	def test_a_disabled_limit_counts_as_no_limit(self):
		ensure_approval_limit(OWNER, 1_000_000)
		frappe.db.set_value("Purchase Approval Limit", OWNER, "enabled", 0)
		po = self.po(100)
		self.assertRaises(ApprovalLimitError, self.approve_as, OWNER, po)
		frappe.db.set_value("Purchase Approval Limit", OWNER, "enabled", 1)
		self.approve_as(OWNER, po)  # enabling the limit is what authorises
		self.assertEqual(self.status(po), 1)

	def test_a_limit_of_zero_approves_nothing(self):
		ensure_approval_limit(OWNER, 0)
		po = self.po(1)
		self.assertRaises(ApprovalLimitError, self.approve_as, OWNER, po)

	def test_a_limit_for_another_company_does_not_apply(self):
		other = frappe.db.get_value("Company", {"name": ("!=", self.company)}, "name")
		if not other:
			self.skipTest("only one company on this site")
		frappe.db.delete("Purchase Approval Limit", {"user": OWNER_NO_LIMIT})
		frappe.get_doc({"doctype": "Purchase Approval Limit", "user": OWNER_NO_LIMIT, "approval_limit": 1_000_000, "company": other}).insert(ignore_permissions=True)
		po = self.po(100)
		self.assertRaises(ApprovalLimitError, self.approve_as, OWNER_NO_LIMIT, po)

	def test_limits_are_per_person_a_low_limit_approver_hits_the_same_boundary(self):
		at_limit = self.po(LIMIT_LOW)
		self.approve_as(USER_APPROVER_LOW, at_limit)
		self.assertEqual(self.status(at_limit), 1)
		above = self.po(LIMIT_LOW + 0.01)
		self.assertRaises(ApprovalLimitError, self.approve_as, USER_APPROVER_LOW, above)

	def test_the_function_is_not_a_back_door_for_people_without_the_right(self):
		po = self.po(100)
		for user in (USER_BUYER, "fo-stores@example.com", "fo-sales@example.com"):
			if frappe.has_permission("Purchase Order", "submit", user=user):
				continue  # a Purchase User may legitimately submit; their limit rules apply
			self.assertRaises(frappe.PermissionError, self.approve_as, user, po)
		nobody = ensure_user("fo-nobody-po@example.com", [])
		self.assertRaises(frappe.PermissionError, self.approve_as, nobody, po)
		self.assertEqual(self.status(po), 0)

	def test_a_submitted_order_cannot_be_approved_again(self):
		po = self.po(100)
		self.approve_as(OWNER, po)
		self.assertRaises(frappe.ValidationError, self.approve_as, OWNER, po)

	def test_the_owner_cannot_raise_cancel_or_amend_purchase_orders(self):
		for right in ("create", "write", "cancel", "amend", "delete"):
			self.assertFalse(frappe.has_permission("Purchase Order", right, user=OWNER), right)
		with self.set_user(OWNER):
			self.assertRaises(
				frappe.PermissionError,
				frappe.get_doc(
					{"doctype": "Purchase Order", "company": self.company, "supplier": test_purchase.SUPPLIER,
					 "schedule_date": frappe.utils.add_days(frappe.utils.nowdate(), 7),
					 "items": [{"item_code": test_purchase.RM, "qty": 1, "rate": 1}]}
				).insert,
			)

	def test_the_approval_is_on_the_audit_trail_and_the_owner_can_read_it(self):
		po = self.po(7_500)
		with production_auditing():
			self.approve_as(OWNER, po)
		versions = frappe.get_all("Version", filters={"ref_doctype": "Purchase Order", "docname": po.name}, pluck="data")
		self.assertTrue(versions, "approval left no Version record")
		self.assertTrue(any('"docstatus"' in v for v in versions), "the docstatus change 0 -> 1 is not recorded")
		with self.set_user(OWNER):
			self.assertTrue(frappe.get_list("Version", filters={"ref_doctype": "Purchase Order", "docname": po.name}))


class TestControlsHoldForTheOwner(OwnerTestCase):
	def setUp(self):
		self.hold, self.stores = self.wh["RM-QC-HOLD"], self.wh["RM-STORES"]

	def test_qc_approval_stays_with_qc(self):
		for doctype in ("QC Release", "QC Deviation", "QC Retest", "Quality Inspection"):
			for right in ("create", "submit", "cancel", "amend"):
				self.assertFalse(frappe.has_permission(doctype, right, user=OWNER), f"owner can {right} {doctype}")
		_, batch = self.receive(QC_ITEM, self.hold, 10)
		with self.assertRaises(frappe.PermissionError):
			self.qc_release(batch, "Release", user=OWNER)
		self.assertEqual(frappe.db.get_value("Batch", batch, "fo_qc_status"), "Hold")

	def test_held_stock_cannot_be_moved_or_dispatched_by_the_owner(self):
		_, batch = self.receive(QC_ITEM, self.hold, 10)
		with self.set_user(OWNER):
			self.assertRaises(frappe.PermissionError, self.transfer, QC_ITEM, batch, 5, self.hold, self.stores)
			dn = frappe.get_doc(
				{"doctype": "Delivery Note", "company": self.company, "customer": "_Test Customer",
				 "items": [{"item_code": QC_ITEM, "qty": 5, "rate": 10, "warehouse": self.hold, "use_serial_batch_fields": 1, "batch_no": batch}]}
			)
			self.assertRaises((frappe.PermissionError, QCGuardError), dn.insert)

	def test_financial_postings_follow_accounting_permissions(self):
		for doctype in ("Journal Entry", "Payment Entry", "Sales Invoice", "Purchase Invoice", "GL Entry", "Account", "Bank Account"):
			for right in ("create", "write", "submit", "cancel", "amend", "delete"):
				self.assertFalse(frappe.has_permission(doctype, right, user=OWNER), f"owner can {right} {doctype}")

	def test_transaction_documents_are_view_only_except_the_one_approval(self):
		for doctype in TRANSACTIONS:
			for right in ACTIONS:
				allowed = frappe.has_permission(doctype, right, user=OWNER)
				self.assertEqual(allowed, (doctype, right) == ("Purchase Order", "submit"), f"owner {right} {doctype}")


class TestAuditTrail(OwnerTestCase):
	def test_every_sensitive_doctype_records_changes(self):
		self.assertEqual(audit_trail.untracked(), [])
		for doctype in audit_trail.SENSITIVE_DOCTYPES:
			if frappe.db.exists("DocType", doctype):
				self.assertTrue(frappe.get_meta(doctype).track_changes, f"{doctype} does not track changes")

	def test_tracking_that_was_switched_off_is_switched_back_on(self):
		from frappe.custom.doctype.property_setter.property_setter import make_property_setter

		make_property_setter("Material Request", None, "track_changes", 0, "Check", for_doctype=True)
		frappe.clear_cache(doctype="Material Request")
		self.assertIn("Material Request", audit_trail.untracked())
		self.assertEqual(audit_trail.ensure_audit_trails(), ["Material Request"])
		self.assertEqual(audit_trail.untracked(), [])
		self.assertEqual(audit_trail.ensure_audit_trails(), [])

	# Real audit records. Frappe skips Version rows under the test runner, so each change is made inside
	# production_auditing(); the meta flag alone (tested above) does not prove a record is written.
	def versions(self, doctype: str, name: str) -> list[dict]:
		return frappe.get_all("Version", filters={"ref_doctype": doctype, "docname": name}, fields=["name", "data", "owner"])

	def changed_fields(self, version: dict) -> set[str]:
		import json

		return {row[0] for row in json.loads(version["data"]).get("changed", [])}

	def test_a_change_to_a_quality_inspection_leaves_a_version(self):
		"""Quality Inspection had no change tracking; QC decisions rest on it."""
		from factory_ops.quality.doctype.qc_release.qc_release import create_quality_inspection

		_, batch = self.receive(QC_ITEM, self.wh["RM-QC-HOLD"], 10)
		with self.set_user("fo-qc-officer@example.com"):
			release = frappe.get_doc(
				{"doctype": "QC Release", "company": self.company, "item_code": QC_ITEM, "batch_no": batch,
				 "source_warehouse": self.wh["RM-QC-HOLD"], "decision": "Release", "qty": 0}
			).insert()
			qi = frappe.get_doc("Quality Inspection", create_quality_inspection(release.name))
			self.assertEqual(self.versions("Quality Inspection", qi.name), [])
			with production_auditing():
				qi.remarks = "retested twice"
				qi.save()
		versions = self.versions("Quality Inspection", qi.name)
		self.assertEqual(len(versions), 1)
		self.assertIn("remarks", self.changed_fields(versions[0]))
		self.assertEqual(versions[0]["owner"], "fo-qc-officer@example.com")  # who changed it

	def test_a_change_to_a_material_request_leaves_a_version(self):
		with self.set_user("fo-buyer@example.com"):
			mr = frappe.get_doc(
				{"doctype": "Material Request", "material_request_type": "Purchase", "company": self.company,
				 "schedule_date": frappe.utils.add_days(frappe.utils.nowdate(), 5),
				 "items": [{"item_code": test_purchase.RM, "qty": 5, "schedule_date": frappe.utils.add_days(frappe.utils.nowdate(), 5)}]}
			).insert()
			with production_auditing():
				mr.title = "Needed for the next bulk batch"
				mr.save()
		versions = self.versions("Material Request", mr.name)
		self.assertEqual(len(versions), 1)
		self.assertIn("title", self.changed_fields(versions[0]))

	def test_a_change_to_a_stock_reconciliation_leaves_a_version(self):
		_, batch = self.receive(PLAIN_ITEM, self.wh["RM-STORES"], 10)  # a non-QC item may be received into stores
		with self.set_user("fo-stores@example.com"):
			reco = frappe.get_doc(
				{"doctype": "Stock Reconciliation", "company": self.company, "purpose": "Stock Reconciliation",
				 "items": [{"item_code": PLAIN_ITEM, "warehouse": self.wh["RM-STORES"], "qty": 11, "valuation_rate": 100,
				            "use_serial_batch_fields": 1, "batch_no": batch}]}
			).insert()
			with production_auditing():
				reco.set_posting_time = 1  # otherwise ERPNext resets the date on save and nothing changes
				reco.posting_date = frappe.utils.add_days(reco.posting_date, -1)
				reco.save()
		versions = self.versions("Stock Reconciliation", reco.name)
		self.assertEqual(len(versions), 1)
		self.assertTrue({"posting_date", "set_posting_time"} & self.changed_fields(versions[0]), self.changed_fields(versions[0]))


class TestLanding(OwnerTestCase):
	def test_the_owner_lands_on_the_business_dashboard_and_the_technician_on_administration(self):
		from factory_ops.setup.landing import landing_workspace

		self.assertEqual(landing_workspace([BIZ]), "Management")
		self.assertEqual(landing_workspace([SYS]), "Administration")
		self.assertEqual(landing_workspace([BIZ, SYS]), "Management")  # the business view first
		self.assertEqual(landing_workspace([BIZ, "Manufacturing User"]), "Management")  # never one department
		self.assertEqual(frappe.db.get_value("User", OWNER, "default_workspace"), "Management")
		self.assertIn("Management", all_visible(OWNER))
