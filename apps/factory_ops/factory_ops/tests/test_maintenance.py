"""Maintenance on ERPNext's asset DocTypes: registration, preventive schedules, breakdowns with
closure approval, spare parts through stock, dashboard figures from records."""

import frappe
from frappe.utils import add_days, flt, nowdate

from factory_ops.api.maintenance import dashboard, register_asset
from factory_ops.maintenance.setup import ensure_maintenance_masters
from factory_ops.tests.utils import FactoryOpsTestCase, ensure_user

TECH = "fo-maint-tech@example.com"
MGR = "fo-maint-manager@example.com"
OUTSIDER = "fo-maint-outsider@example.com"
SPARE = "_FO Spare V-belt"


class TestMaintenance(FactoryOpsTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_maintenance_masters(cls.company)
		ensure_user(TECH, ["Maintenance User", "Stock User"])
		ensure_user(MGR, ["Maintenance Manager", "Maintenance User", "Stock User"])
		ensure_user(OUTSIDER, ["Sales User"])
		if not frappe.db.exists("Item", SPARE):
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": SPARE,
					"item_name": SPARE,
					"item_group": "Spare Parts",
					"stock_uom": "Nos",
					"is_stock_item": 1,
					"valuation_rate": 250,
				}
			).insert(ignore_permissions=True)
		cls.spare_wh = frappe.db.get_value("Warehouse", {"company": cls.company, "is_group": 0, "warehouse_type": ["is", "not set"]}, "name")
		if not frappe.db.exists("Asset Maintenance Team", "_FO Team"):
			frappe.get_doc(
				{
					"doctype": "Asset Maintenance Team",
					"maintenance_team_name": "_FO Team",
					"company": cls.company,
					"maintenance_manager": MGR,
					"maintenance_team_members": [{"team_member": TECH, "maintenance_role": "Maintenance User"}],
				}
			).insert(ignore_permissions=True)
		frappe.db.commit()

	def register(self, name="Mixer", kind="Machine"):
		frappe.set_user(MGR)
		return register_asset(
			asset_name=f"_FO {name} {frappe.generate_hash(length=5)}",
			equipment_type=kind,
			company=self.company,
			manufacturer="Test Works",
			model="M-1",
			serial_no="SN-1",
			installation_date="2025-06-01",
			purchase_amount=150000,
		)

	def breakdown(self, asset, user=TECH, **kw):
		frappe.set_user(user)
		return frappe.get_doc(
			{
				"doctype": "Asset Repair",
				"asset": asset,
				"company": self.company,
				"failure_date": kw.pop("failure_date", f"{nowdate()} 08:00:00"),
				"description": "Motor overheating",
				"fo_severity": kw.pop("severity", "High"),
				**kw,
			}
		).insert()

	def test_register_equipment_as_a_submitted_asset(self):
		name = self.register()
		a = frappe.get_doc("Asset", name)
		self.assertEqual((a.docstatus, a.fo_equipment_type, a.fo_operational_status, a.fo_serial_no), (1, "Machine", "Active", "SN-1"))
		self.assertEqual(frappe.db.get_value("Item", a.item_code, "is_fixed_asset"), 1)
		frappe.set_user(OUTSIDER)
		with self.assertRaises(frappe.PermissionError):
			register_asset(asset_name="_FO Not allowed", equipment_type="Machine", company=self.company, purchase_amount=1)

	def test_preventive_tasks_get_due_dates_and_logs(self):
		asset = self.register("Filler", "Filling Machine")
		frappe.set_user(MGR)
		am = frappe.get_doc(
			{
				"doctype": "Asset Maintenance",
				"asset_name": asset,
				"company": self.company,
				"maintenance_team": "_FO Team",
				"asset_maintenance_tasks": [
					{
						"maintenance_task": "Lubricate drive",
						"maintenance_type": "Preventive Maintenance",
						"maintenance_status": "Planned",
						"start_date": add_days(nowdate(), -20),
						"periodicity": "Monthly",
						"assign_to": TECH,
					}
				],
			}
		).insert()
		due = am.asset_maintenance_tasks[0].next_due_date
		self.assertIsNotNone(due)
		logs = frappe.get_all("Asset Maintenance Log", filters={"asset_maintenance": am.name}, fields=["due_date", "maintenance_status"])
		self.assertEqual(len(logs), 1)
		self.assertEqual(str(logs[0].due_date), str(due))

	def test_breakdown_marks_the_machine_and_closure_needs_approval_and_root_cause(self):
		asset = self.register()
		with self.assertRaisesRegex(frappe.ValidationError, "severity"):
			self.breakdown(asset, severity="")
		rep = self.breakdown(asset)
		self.assertEqual(frappe.db.get_value("Asset", asset, "fo_operational_status"), "Breakdown")
		self.assertEqual(rep.fo_reported_by, TECH)
		rep.completion_date = f"{nowdate()} 12:30:00"
		rep.actions_performed = "Replaced bearing"
		rep.save()
		self.assertEqual(rep.fo_downtime_hours, 4.5)
		rep.repair_status = "Completed"
		with self.assertRaisesRegex(frappe.ValidationError, "root cause"):
			rep.save()  # completed work must say why it failed and who fixed it
		rep.reload()
		rep.update({"repair_status": "Completed", "fo_root_cause": "Worn bearing", "fo_technician": TECH})
		rep.save()
		with self.assertRaises(frappe.PermissionError):
			rep.submit()  # a technician cannot approve closure
		frappe.set_user(MGR)
		rep = frappe.get_doc("Asset Repair", rep.name)
		rep.submit()
		self.assertEqual((rep.fo_closure_approved_by, rep.repair_status), (MGR, "Completed"))
		self.assertEqual(frappe.db.get_value("Asset", asset, "fo_operational_status"), "Active")

	def test_completion_before_failure_is_refused(self):
		asset = self.register()
		with self.assertRaisesRegex(frappe.ValidationError, "before Failure Date"):
			self.breakdown(asset, completion_date=f"{add_days(nowdate(), -1)} 08:00:00")

	def test_spare_parts_are_issued_from_stock_on_closure(self):
		frappe.set_user("Administrator")
		se = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"purpose": "Material Receipt",
				"stock_entry_type": "Material Receipt",
				"company": self.company,
				"items": [{"item_code": SPARE, "qty": 10, "t_warehouse": self.spare_wh, "basic_rate": 250}],
			}
		).insert()
		se.submit()
		before = flt(frappe.db.get_value("Bin", {"item_code": SPARE, "warehouse": self.spare_wh}, "actual_qty"))
		asset = self.register()
		rep = self.breakdown(asset, completion_date=f"{nowdate()} 10:00:00", actions_performed="Changed belt",
			fo_root_cause="Belt worn", fo_technician=TECH, repair_status="Completed",
			stock_items=[{"item_code": SPARE, "warehouse": self.spare_wh, "consumed_quantity": "2"}])  # fmt: skip
		frappe.set_user(MGR)
		rep = frappe.get_doc("Asset Repair", rep.name)
		rep.submit()
		after = flt(frappe.db.get_value("Bin", {"item_code": SPARE, "warehouse": self.spare_wh}, "actual_qty"))
		self.assertEqual(before - after, 2)
		self.assertGreater(flt(rep.consumed_items_cost), 0)
		self.assertTrue(frappe.db.exists("Stock Entry", {"asset_repair": rep.name, "docstatus": 1}))

	def test_dashboard_counts_what_the_records_say(self):
		asset = self.register()
		self.breakdown(asset, severity="Critical")
		frappe.set_user(MGR)
		d = dashboard(self.company)
		self.assertIn("breakdowns", d["sections"])
		self.assertIn(asset, [r.asset for r in d["open_breakdowns"]])
		self.assertGreaterEqual(d["assets_by_status"].get("Breakdown", 0), 1)
		self.assertEqual(len(d["monthly"]), 6)
		frappe.set_user(OUTSIDER)
		d = dashboard(self.company)
		self.assertNotIn("breakdowns", d["sections"])
		self.assertNotIn("open_breakdowns", d)

	def test_spares_at_or_below_reorder_level_are_listed(self):
		frappe.set_user("Administrator")
		item = frappe.get_doc("Item", SPARE)
		item.set("reorder_levels", [{"warehouse": self.spare_wh, "warehouse_reorder_level": 1000, "warehouse_reorder_qty": 50, "material_request_type": "Purchase"}])
		item.save()
		frappe.set_user(MGR)
		self.assertIn(SPARE, [r.item_code for r in dashboard(self.company)["spares_below_reorder"]])

	def test_technician_completes_a_task_and_the_next_is_scheduled(self):
		asset = self.register("Labeller", "Packaging Equipment")
		frappe.set_user(MGR)
		am = frappe.get_doc(
			{
				"doctype": "Asset Maintenance",
				"asset_name": asset,
				"company": self.company,
				"maintenance_team": "_FO Team",
				"asset_maintenance_tasks": [
					{
						"maintenance_task": "Clean sensors",
						"maintenance_type": "Preventive Maintenance",
						"maintenance_status": "Planned",
						"start_date": nowdate(),
						"periodicity": "Weekly",
						"assign_to": TECH,
					}
				],
			}
		).insert()
		frappe.set_user(TECH)
		log = frappe.get_doc("Asset Maintenance Log", {"asset_maintenance": am.name})
		log.update({"maintenance_status": "Completed", "completion_date": nowdate(), "actions_performed": "Cleaned"})
		log.save()
		log.submit()  # ERPNext updates the schedule and inserts the next occurrence, with the technician's rights
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.count("Asset Maintenance Log", {"asset_maintenance": am.name}), 2)
