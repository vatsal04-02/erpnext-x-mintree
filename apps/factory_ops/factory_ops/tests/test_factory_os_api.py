"""Factory OS front end: the session boot endpoint (factory_ops.api.factory_os.boot).

The boot answer only decides what the front end shows, so it must say exactly what Frappe's own
permission checks say for the same user, never more; and a guest gets nothing.
"""

import frappe

from factory_ops.api.factory_os import DOCTYPES, PTYPES, boot
from factory_ops.tests.utils import (
	USER_ACCOUNTS,
	USER_BUYER,
	USER_MANAGEMENT,
	USER_PRODUCTION,
	USER_QC,
	USER_SALES,
	USER_STORES,
	FactoryOpsTestCase,
)


class TestFactoryOSBoot(FactoryOpsTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")

	def test_guest_is_refused(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.AuthenticationError):
			boot()

	def test_permissions_match_frappe_for_each_role(self):
		for user in (USER_BUYER, USER_STORES, USER_QC, USER_PRODUCTION, USER_SALES, USER_ACCOUNTS, USER_MANAGEMENT):
			frappe.set_user(user)
			data = boot()
			self.assertEqual(data["user"], user)
			self.assertEqual(set(data["roles"]), set(frappe.get_roles(user)))
			self.assertTrue(data["csrf_token"])
			for dt, perms in data["doctypes"].items():
				self.assertIn(dt, DOCTYPES)
				for ptype in PTYPES:
					self.assertEqual(perms[ptype], bool(frappe.has_permission(dt, ptype)), f"{user} {dt} {ptype}")
			for report in data["reports"]:
				doc = frappe.get_doc("Report", report)
				self.assertTrue(doc.is_permitted(), f"{user} {report}")
				self.assertTrue(frappe.has_permission(doc.ref_doctype, "report"), f"{user} {report}")

	def test_role_boundaries_are_reported(self):
		frappe.set_user(USER_QC)
		data = boot()
		self.assertTrue(data["doctypes"]["QC Release"]["submit"])
		self.assertFalse(data["doctypes"]["Purchase Order"]["create"])
		self.assertIn("QC Queue", data["reports"])

		# Wastage by Reason reads Stock Entries: offered only to roles with report rights on them
		frappe.set_user(USER_ACCOUNTS)
		self.assertEqual(
			"Wastage by Reason" in boot()["reports"], bool(frappe.has_permission("Stock Entry", "report"))
		)

		frappe.set_user(USER_MANAGEMENT)
		data = boot()
		self.assertFalse(any(p["create"] or p["submit"] or p["write"] for p in data["doctypes"].values()))
		self.assertNotIn("QC Queue", data["reports"])
