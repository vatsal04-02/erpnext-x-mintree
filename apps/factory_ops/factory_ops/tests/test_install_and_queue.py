"""Milestone 1 acceptance: setup is repeatable, the app's DocTypes exist, and the QC queue
report reflects what is actually in QC Hold."""

import frappe

from factory_ops.constants import FACTORY_WAREHOUSE_TYPES, QC_ROLES, RELEASE_MAP
from factory_ops.quality.report.qc_queue.qc_queue import execute
from factory_ops.setup.custom_fields import CUSTOM_FIELDS
from factory_ops.setup.install import apply_setup
from factory_ops.setup.warehouses import ensure_warehouse_tree
from factory_ops.tests.utils import COMPANY, QC_ITEM, FactoryOpsTestCase


class TestInstall(FactoryOpsTestCase):
	def test_setup_is_idempotent(self):
		before = frappe.db.count("Custom DocPerm")
		apply_setup()
		apply_setup()
		self.assertEqual(frappe.db.count("Custom DocPerm"), before)
		first = ensure_warehouse_tree(COMPANY)
		second = ensure_warehouse_tree(COMPANY)
		self.assertEqual(first, second)

	def test_roles_types_fields_and_doctypes_exist(self):
		for role in QC_ROLES:
			self.assertTrue(frappe.db.exists("Role", role))
		for wh_type in FACTORY_WAREHOUSE_TYPES:
			self.assertTrue(frappe.db.exists("Warehouse Type", wh_type))
		for dt, fields in CUSTOM_FIELDS.items():
			for f in fields:
				self.assertTrue(frappe.get_meta(dt).has_field(f["fieldname"]), f"{dt}.{f['fieldname']}")
		for dt in ("QC Release", "QC Deviation", "Factory Ops Settings"):
			self.assertTrue(frappe.db.exists("DocType", dt))
		self.assertTrue(frappe.db.exists("Report", "QC Queue"))

	def test_warehouse_tree_and_release_map(self):
		wh = self.wh
		for hold, usable in RELEASE_MAP.items():
			self.assertEqual(frappe.db.get_value("Warehouse", wh[hold], "warehouse_type"), "QC Hold")
			self.assertEqual(frappe.db.get_value("Warehouse", wh[hold], "fo_release_warehouse"), wh[usable])
		self.assertEqual(frappe.db.get_value("Warehouse", wh["REJECTED"], "is_rejected_warehouse"), 1)

	def test_qc_queue_lists_held_batches_only(self):
		_, batch = self.receive(QC_ITEM, self.wh["RM-QC-HOLD"], 40)
		_, rows = execute({"company": COMPANY})
		mine = [r for r in rows if r["batch_no"] == batch]
		self.assertEqual(len(mine), 1)
		self.assertEqual(mine[0]["qty"], 40)
		self.assertEqual(mine[0]["sla_hours"], 48)
		self.qc_release(batch, "Release")
		_, rows = execute({"company": COMPANY})
		self.assertFalse([r for r in rows if r["batch_no"] == batch])


class TestQCScreenPermissions(FactoryOpsTestCase):
	"""Gaps found by the browser and HTTP checks: QC users must be able to open their screens."""

	def test_qc_roles_can_run_their_reports(self):
		from frappe.desk.query_report import run

		from factory_ops.tests.utils import USER_QA, USER_QC

		for user in (USER_QC, USER_QA):
			with self.set_user(user):
				self.assertTrue(frappe.has_permission("Company", "read"))  # report Company filter
				self.assertTrue(frappe.has_permission("Stock Ledger Entry", "report"))
				for report in ("QC Queue", "Stock by QC Status"):
					result = run(report, filters={"company": COMPANY})
					self.assertIn("result", result, f"{user} / {report}")
