# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Decision Centre queues: dispatch and sales, plant maintenance, accounts.

Real Sales Orders, Asset Repairs, Maintenance Logs and invoices; a lapsing date is moved on the record
(ERPNext refuses to create documents in the past). Every entry must leave the queue when the underlying
document is resolved the normal way."""

import frappe
from frappe.utils import add_days, nowdate

from factory_ops.tests import test_maintenance, test_sales
from factory_ops.tests.dc_helpers import entries_for, entry
from factory_ops.maintenance.setup import ensure_maintenance_masters
from factory_ops.tests.utils import USER_ACCOUNTS, USER_SALES, FactoryOpsTestCase, ensure_user


class TestDispatchQueue(test_sales.SalesTestCase):
	def test_an_order_past_its_delivery_date_is_overdue_and_leaves_when_delivered(self):
		batch = self.released_fg(100)
		so = self.sales_order(qty=10)
		self.assertIsNone(entry("Sales Order", so.name))  # due in a week: outside the 3-day window
		frappe.db.set_value("Sales Order", so.name, "delivery_date", add_days(nowdate(), -2))
		e = entry("Sales Order", so.name)
		self.assertEqual((e["kind"], e["department"], e["priority"]), ("order_overdue", "Dispatch & Sales", "High"))
		self.assertTrue(e["overdue"])
		self.assertIn("2 day(s) past the delivery date", e["issue"])
		self.deliver_order(so)
		self.assertEqual(entries_for("Sales Order", so.name), [])
		self.assertTrue(batch)

	def test_an_order_due_inside_the_window_is_waiting_for_dispatch(self):
		self.released_fg(100)
		so = self.sales_order(qty=10)
		frappe.db.set_value("Sales Order", so.name, "delivery_date", add_days(nowdate(), 2))
		e = entry("Sales Order", so.name)
		self.assertEqual((e["kind"], e["priority"], e["overdue"]), ("order_due_soon", "Medium", False))

	def test_overdue_and_short_is_critical(self):
		self.released_fg(5)
		so = self.sales_order(qty=3)
		frappe.db.set_value("Sales Order", so.name, "delivery_date", add_days(nowdate(), -1))
		frappe.db.set_value("Sales Order Item", {"parent": so.name}, "qty", 50)  # more than is releasable
		e = entry("Sales Order", so.name)
		self.assertEqual((e["kind"], e["state"], e["priority"]), ("order_blocked", "Blocked", "Critical"))
		self.assertIn("available to sell", e["issue"])
		self.assertIn("Quality Control", e["also"])

	def test_a_high_priority_complaint_is_listed_until_closed(self):
		for p in ("High", "Low"):
			if not frappe.db.exists("Issue Priority", p):
				frappe.get_doc({"doctype": "Issue Priority", "name": p}).insert(ignore_permissions=True)
		frappe.set_user("Administrator")
		urgent = frappe.get_doc({"doctype": "Issue", "subject": "Leaking cap on batch", "priority": "High"}).insert()
		minor = frappe.get_doc({"doctype": "Issue", "subject": "Label colour query", "priority": "Low"}).insert()
		e = entry("Issue", urgent.name)
		self.assertEqual((e["kind"], e["priority"]), ("complaint_escalation", "High"))
		self.assertIn("Quality Control", e["also"])
		self.assertIsNone(entry("Issue", minor.name))  # only escalated priorities are decisions
		frappe.db.set_value("Issue", urgent.name, "status", "Closed")
		self.assertIsNone(entry("Issue", urgent.name))


def _asset_and_breakdown(case, severity="High"):
	asset = case.register()
	repair = case.breakdown(asset, severity=severity)
	frappe.set_user("Administrator")
	return asset, repair


class TestBreakdowns(FactoryOpsTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_maintenance_masters(cls.company)
		ensure_user(test_maintenance.TECH, ["Maintenance User", "Stock User"])
		ensure_user(test_maintenance.MGR, ["Maintenance Manager", "Maintenance User", "Stock User"])
		if not frappe.db.exists("Asset Maintenance Team", "_FO Team"):
			frappe.get_doc(
				{
					"doctype": "Asset Maintenance Team",
					"maintenance_team_name": "_FO Team",
					"company": cls.company,
					"maintenance_manager": test_maintenance.MGR,
					"maintenance_team_members": [{"team_member": test_maintenance.TECH, "maintenance_role": "Maintenance User"}],
				}
			).insert(ignore_permissions=True)
		frappe.db.commit()

	def register(self):
		return test_maintenance.TestMaintenance.register(self)

	def breakdown(self, asset, **kw):
		return test_maintenance.TestMaintenance.breakdown(self, asset, **kw)

	def test_each_severity_maps_to_a_priority_and_a_critical_one_alerts_production(self):
		expected = {"Critical": "Critical", "High": "High", "Medium": "Medium", "Low": "Low"}
		for severity, priority in expected.items():
			_asset, repair = _asset_and_breakdown(self, severity)
			e = entry("Asset Repair", repair.name)
			self.assertEqual((e["kind"], e["priority"], e["department"]), ("breakdown", priority, "Plant Maintenance"), severity)
			self.assertEqual("Production" in e["also"], severity == "Critical", severity)

	def test_a_closed_breakdown_leaves_the_queue(self):
		_asset, repair = _asset_and_breakdown(self)
		self.assertIsNotNone(entry("Asset Repair", repair.name))
		frappe.db.set_value("Asset Repair", repair.name, "repair_status", "Completed")
		e = entry("Asset Repair", repair.name)
		self.assertEqual(e["kind"], "repair_closure")  # repaired; the manager's approval is outstanding
		frappe.db.set_value("Asset Repair", repair.name, {"docstatus": 1})
		self.assertIsNone(entry("Asset Repair", repair.name))

	def test_the_age_of_a_breakdown_is_its_downtime(self):
		_asset, repair = _asset_and_breakdown(self)
		frappe.db.set_value("Asset Repair", repair.name, "failure_date", f"{add_days(nowdate(), -2)} 08:00:00")
		e = entry("Asset Repair", repair.name)
		self.assertGreaterEqual(e["age_hours"], 40)
		self.assertIn("hour(s)", e["issue"])


class TestAccountsQueue(test_sales.SalesTestCase):
	def test_an_unpaid_invoice_past_due_is_outstanding_until_paid(self):
		self.released_fg(100)
		so = self.sales_order(qty=10)
		dn = self.deliver_order(so)
		si = self.invoice(dn)
		self.assertIsNone(entry("Sales Invoice", si.name))  # not yet due
		frappe.db.set_value("Sales Invoice", si.name, "due_date", add_days(nowdate(), -5))
		e = entry("Sales Invoice", si.name)
		self.assertEqual((e["kind"], e["department"], e["state"]), ("customer_overdue", "Accounts", "Overdue"))
		self.assertGreater(e["amount"], 0)
		self.assertIn("5 day(s) overdue", e["issue"])
		self.pay(si)
		self.assertIsNone(entry("Sales Invoice", si.name))

	def test_a_part_paid_invoice_stays_with_the_remaining_amount(self):
		self.released_fg(100)
		si = self.invoice(self.deliver_order(self.sales_order(qty=10)))
		frappe.db.set_value("Sales Invoice", si.name, "due_date", add_days(nowdate(), -1))
		full = entry("Sales Invoice", si.name)["amount"]
		self.pay(si, amount=full / 2)
		e = entry("Sales Invoice", si.name)
		self.assertAlmostEqual(e["amount"], full / 2, 2)

	def test_a_draft_invoice_awaits_approval_and_the_submit_right_decides_the_action(self):
		self.released_fg(100)
		dn = self.deliver_order(self.sales_order(qty=10))
		from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice

		with self.set_user(USER_ACCOUNTS):
			si = make_sales_invoice(dn.name)
			si.insert()
		e = entry("Sales Invoice", si.name, "invoice_approval", user=USER_ACCOUNTS)
		self.assertEqual((e["department"], e["status"]), ("Accounts", "Draft"))
		self.assertTrue(e["action"]["permitted"])
		self.assertIsNone(entry("Sales Invoice", si.name, user=USER_SALES))  # sales users do not see accounts
		with self.set_user(USER_ACCOUNTS):
			frappe.get_doc("Sales Invoice", si.name).submit()
		self.assertIsNone(entry("Sales Invoice", si.name, "invoice_approval"))
