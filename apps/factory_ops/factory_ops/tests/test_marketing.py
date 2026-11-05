"""Brand & marketing: campaign and content approval, honest performance figures, Meta connection state."""

import frappe
from frappe.utils import nowdate

from factory_ops.api.marketing import overview
from factory_ops.tests.utils import FactoryOpsTestCase, ensure_user

EXEC = "fo-mktg-exec@example.com"
MGR = "fo-mktg-manager@example.com"
MGR_2 = "fo-mktg-manager-2@example.com"
OUTSIDER = "fo-mktg-outsider@example.com"


class TestMarketing(FactoryOpsTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_user(EXEC, ["Marketing User"])
		ensure_user(MGR, ["Marketing Manager", "Marketing User"])
		ensure_user(MGR_2, ["Marketing Manager", "Marketing User"])
		ensure_user(OUTSIDER, ["Stock User"])
		if not frappe.db.exists("Meta Connection", "Meta Business"):
			frappe.get_doc({"doctype": "Meta Connection", "connection_name": "Meta Business"}).insert(ignore_permissions=True)
		frappe.db.commit()

	def campaign(self, user=EXEC, **kw):
		frappe.set_user(user)
		return frappe.get_doc(
			{"doctype": "Marketing Campaign", "campaign_name": kw.pop("name", "Monsoon hair care"), "platform": "Meta (Facebook + Instagram)",
			 "objective": "Leads", "start_date": nowdate(), "budget": 50000, **kw}
		).insert()  # fmt: skip

	def act(self, doc, user, action, note=None):
		frappe.set_user(user)
		return frappe.get_doc(doc.doctype, doc.name).apply_action(action, note)

	def test_campaign_approval_by_a_manager_who_did_not_submit(self):
		c = self.campaign()
		self.assertEqual(c.status, "Draft")
		self.act(c, EXEC, "send_for_approval")
		with self.assertRaises(frappe.PermissionError):
			self.act(c, EXEC, "approve")
		with self.assertRaisesRegex(frappe.ValidationError, "reason"):
			self.act(c, MGR, "reject")
		self.act(c, MGR, "approve")
		self.act(c, EXEC, "mark_live")
		d = frappe.get_doc("Marketing Campaign", c.name)
		self.assertEqual((d.status, d.approved_by), ("Live", MGR))
		own = self.campaign(user=MGR, name="Manager's own")
		self.act(own, MGR, "send_for_approval")
		with self.assertRaisesRegex(frappe.PermissionError, "cannot decide"):
			self.act(own, MGR, "approve")
		self.act(own, MGR_2, "approve")

	def test_status_only_through_actions(self):
		c = self.campaign()
		frappe.set_user(EXEC)
		d = frappe.get_doc("Marketing Campaign", c.name)
		d.status = "Live"
		with self.assertRaisesRegex(frappe.ValidationError, "only through"):
			d.save()

	def test_content_needs_a_creative_and_a_url_to_be_published(self):
		c = self.campaign()
		frappe.set_user(EXEC)
		content = frappe.get_doc(
			{"doctype": "Marketing Content", "title": "Reel: routine", "campaign": c.name, "content_type": "Reel / video",
			 "platform": "Instagram", "planned_date": nowdate(), "assigned_to": EXEC}
		).insert()  # fmt: skip
		with self.assertRaisesRegex(frappe.ValidationError, "creative or the draft caption"):
			self.act(content, EXEC, "send_for_review")
		frappe.db.set_value("Marketing Content", content.name, "caption", "Three steps to a calm scalp.")
		self.act(content, EXEC, "send_for_review")
		with self.assertRaises(frappe.PermissionError):
			self.act(content, EXEC, "approve")
		self.act(content, MGR, "approve")
		self.act(content, EXEC, "schedule")
		with self.assertRaisesRegex(frappe.ValidationError, "by hand"):
			self.act(content, EXEC, "mark_published")
		frappe.db.set_value("Marketing Content", content.name, "published_url", "https://www.instagram.com/p/example")
		self.assertEqual(self.act(content, EXEC, "mark_published"), "Published")

	def test_performance_figures_are_honest(self):
		c = self.campaign()
		frappe.set_user(EXEC)
		base = {"doctype": "Campaign Performance", "campaign": c.name, "period_start": nowdate(), "period_end": nowdate()}
		with self.assertRaisesRegex(frappe.ValidationError, "verified Meta connection"):
			frappe.get_doc({**base, "source": "Meta API", "spend": 100}).insert()
		with self.assertRaisesRegex(frappe.ValidationError, "DEMO figures"):
			frappe.get_doc({**base, "source": "DEMO - NOT REAL", "spend": 100}).insert()
		p = frappe.get_doc({**base, "source": "Manual entry from the ad platform", "spend": 1200, "impressions": 40000, "clicks": 600}).insert()
		self.assertEqual((round(p.ctr, 2), p.cpc), (1.5, 2))
		self.assertIsNone(p.cost_per_lead)  # no leads recorded: no cost per lead, not zero
		self.assertIsNone(p.roas)

	def test_demo_campaigns_take_only_demo_figures_and_are_reported_apart(self):
		frappe.set_user(MGR)
		demo = frappe.get_doc({"doctype": "Marketing Campaign", "campaign_name": "Festive gifting", "platform": "Instagram", "objective": "Sales",
			"start_date": nowdate(), "is_demo": 1}).insert()  # fmt: skip
		self.assertTrue(demo.campaign_name.startswith("DEMO"))
		with self.assertRaisesRegex(frappe.ValidationError, "DEMO figures"):
			frappe.get_doc({"doctype": "Campaign Performance", "campaign": demo.name, "period_start": nowdate(), "period_end": nowdate(),
				"source": "Manual entry from the ad platform", "spend": 10}).insert()  # fmt: skip
		frappe.get_doc({"doctype": "Campaign Performance", "campaign": demo.name, "period_start": nowdate(), "period_end": nowdate(),
			"source": "DEMO - NOT REAL", "spend": 500, "leads": 20}).insert()  # fmt: skip
		o = overview()
		self.assertIn("DEMO - NOT REAL", o["by_source"])
		self.assertEqual(o["by_source"]["DEMO - NOT REAL"]["cost_per_lead"], o["by_source"]["DEMO - NOT REAL"]["spend"] / o["by_source"]["DEMO - NOT REAL"]["leads"])

	def test_meta_connection_never_claims_to_be_connected(self):
		frappe.set_user("Administrator")
		m = frappe.get_doc("Meta Connection", "Meta Business")
		self.assertEqual(m.connection_state, "Not configured")
		m.update({"business_id": "123", "ad_account_id": "act_1"})
		m.save()
		self.assertEqual(m.connection_state, "Awaiting credentials")
		m.update({"app_id": "1", "app_secret": "s", "system_user_token": "t"})
		m.save()
		self.assertEqual(m.connection_state, "Not implemented")
		self.assertEqual(m.test_connection()["status"], "Skipped")

	def test_only_marketing_roles_see_the_overview(self):
		frappe.set_user(OUTSIDER)
		with self.assertRaises(frappe.PermissionError):
			overview()
