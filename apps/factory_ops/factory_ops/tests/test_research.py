"""R&D: formulation validation, versioning, role-checked approval, approved version -> BOM."""

import frappe

from factory_ops.research.doctype.formulation.formulation import DEMO_LABEL
from factory_ops.tests.utils import FactoryOpsTestCase, ensure_user

CHEMIST = "fo-rnd-chemist@example.com"
MANAGER = "fo-rnd-manager@example.com"
MANAGER_2 = "fo-rnd-manager-2@example.com"
OUTSIDER = "fo-rnd-outsider@example.com"
RMS = ("_FO RnD Water", "_FO RnD Glycerin", "_FO RnD Thickener", "_FO RnD Preservative")
BULK = "_FO RnD Bulk Gel"


def ensure_item(code: str, group: str, disabled: int = 0) -> str:
	if not frappe.db.exists("Item", code):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": code,
				"item_name": code,
				"item_group": group,
				"stock_uom": "Kg",
				"is_stock_item": 1,
				"has_expiry_date": 1,
				"shelf_life_in_days": 365,
				"valuation_rate": 50,
			}
		).insert(ignore_permissions=True)
	frappe.db.set_value("Item", code, "disabled", disabled)
	return code


class TestResearch(FactoryOpsTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_user(CHEMIST, ["R&D Chemist"])
		ensure_user(MANAGER, ["R&D Manager"])
		ensure_user(MANAGER_2, ["R&D Manager", "Manufacturing Manager"])
		ensure_user(OUTSIDER, ["Stock User"])
		for code in RMS:
			ensure_item(code, "RM")
		ensure_item("_FO RnD Disabled RM", "RM", disabled=1)
		ensure_item(BULK, "BULK")
		frappe.db.commit()

	def formulation(self, rows, **kw):
		frappe.set_user(CHEMIST)
		doc = frappe.get_doc(
			{
				"doctype": "Formulation",
				"formulation_name": kw.pop("name", "Test gel"),
				"formulation_code": kw.pop("code", f"TG-{frappe.generate_hash(length=6)}"),
				"batch_size_kg": 200,
				"product_item": BULK,
				"ingredients": [
					{"item_code": i, "percentage": p, "required": int(r)} for i, p, *rr in rows for r in [rr[0] if rr else 0]
				],
				**kw,
			}
		).insert()
		return doc

	def full_rows(self):
		return [(RMS[0], 80, True), (RMS[1], 15), (RMS[2], 4), (RMS[3], 1)]

	def act(self, doc, user, action, note=None):
		frappe.set_user(user)
		d = frappe.get_doc("Formulation", doc.name)
		return d.apply_action(action, note)

	def pass_trial(self, doc, user=CHEMIST):
		frappe.set_user(user)
		t = frappe.get_doc(
			{
				"doctype": "Formulation Trial",
				"title": "Trial 1",
				"formulation": doc.name,
				"outcome": "Pass",
				"observations": "Homogeneous, no separation after 24 h.",
			}
		).insert()
		t.submit()
		return t

	def approved(self, rows=None, **kw):
		doc = self.formulation(rows or self.full_rows(), **kw)
		self.act(doc, CHEMIST, "send_for_review")
		self.act(doc, MANAGER, "start_trial")
		self.pass_trial(doc)
		self.act(doc, MANAGER, "approve")
		return frappe.get_doc("Formulation", doc.name)

	# -- validation ----------------------------------------------------------------------------
	def test_quantities_and_total_are_computed(self):
		doc = self.formulation(self.full_rows())
		self.assertEqual(doc.total_percentage, 100)
		self.assertEqual([r.quantity_kg for r in doc.ingredients], [160, 30, 8, 2])

	def test_review_refused_until_the_formula_adds_up(self):
		doc = self.formulation([(RMS[0], 80), (RMS[1], 15)])
		with self.assertRaisesRegex(frappe.ValidationError, "add up to 95"):
			self.act(doc, CHEMIST, "send_for_review")
		self.assertEqual(frappe.db.get_value("Formulation", doc.name, "status"), "Draft")

	def test_invalid_ingredients_are_refused(self):
		for rows, message in (
			([(RMS[0], 99), ("_FO RnD Disabled RM", 1)], "disabled"),
			([(RMS[0], 90), (BULK, 10)], "not a raw material"),
			([(RMS[0], 50), (RMS[0], 50)], "listed twice"),
			([(RMS[0], 100), (RMS[1], 0)], "above zero"),
		):
			with self.subTest(message), self.assertRaisesRegex(frappe.ValidationError, message):
				self.formulation(rows)

	# -- workflow and roles --------------------------------------------------------------------
	def test_a_version_is_locked_once_it_leaves_draft(self):
		doc = self.formulation(self.full_rows())
		self.act(doc, CHEMIST, "send_for_review")
		frappe.set_user(CHEMIST)
		d = frappe.get_doc("Formulation", doc.name)
		d.ingredients[1].percentage = 14
		d.ingredients[0].percentage = 81
		with self.assertRaisesRegex(frappe.ValidationError, "Only a Draft can be changed"):
			d.save()
		d.reload()
		d.status = "Approved"
		with self.assertRaisesRegex(frappe.ValidationError, "only through the workflow"):
			d.save()

	def test_only_managers_decide_and_never_on_their_own_submission(self):
		doc = self.formulation(self.full_rows())
		self.act(doc, CHEMIST, "send_for_review")
		with self.assertRaises(frappe.PermissionError):
			self.act(doc, CHEMIST, "start_trial")
		with self.assertRaises(frappe.PermissionError):
			self.act(doc, OUTSIDER, "start_trial")
		self.act(doc, MANAGER, "start_trial")
		self.pass_trial(doc)
		own = self.formulation(self.full_rows(), name="Manager's own")
		frappe.set_user(MANAGER)
		frappe.get_doc("Formulation", own.name).apply_action("send_for_review")
		self.act(own, MANAGER, "start_trial")
		self.pass_trial(own)
		with self.assertRaisesRegex(frappe.PermissionError, "cannot decide"):
			self.act(own, MANAGER, "approve")
		self.act(own, MANAGER_2, "approve")
		self.assertEqual(frappe.db.get_value("Formulation", own.name, "status"), "Approved")

	def test_approval_needs_a_passed_trial_and_rejection_a_reason(self):
		doc = self.formulation(self.full_rows())
		self.act(doc, CHEMIST, "send_for_review")
		self.act(doc, MANAGER, "start_trial")
		with self.assertRaisesRegex(frappe.ValidationError, "trial of this version with outcome Pass"):
			self.act(doc, MANAGER, "approve")
		with self.assertRaisesRegex(frappe.ValidationError, "reason"):
			self.act(doc, MANAGER, "reject")
		self.act(doc, MANAGER, "reject", "Separates at 40 °C")
		d = frappe.get_doc("Formulation", doc.name)
		self.assertEqual((d.status, d.reviewed_by, d.decision_note), ("Rejected", MANAGER, "Separates at 40 °C"))

	def test_trial_needs_an_outcome_and_observations(self):
		doc = self.formulation(self.full_rows())
		frappe.set_user(CHEMIST)
		with self.assertRaisesRegex(frappe.ValidationError, "under review or in trial"):
			frappe.get_doc({"doctype": "Formulation Trial", "title": "Too early", "formulation": doc.name}).insert()
		self.act(doc, CHEMIST, "send_for_review")
		frappe.set_user(CHEMIST)
		t = frappe.get_doc({"doctype": "Formulation Trial", "title": "T", "formulation": doc.name}).insert()
		with self.assertRaisesRegex(frappe.ValidationError, "outcome"):
			t.submit()

	# -- versions ------------------------------------------------------------------------------
	def test_new_version_keeps_required_ingredients_and_needs_a_change_note(self):
		v1 = self.approved()
		frappe.set_user(CHEMIST)
		v2 = frappe.get_doc("Formulation", frappe.get_doc("Formulation", v1.name).new_version())
		self.assertEqual((v2.version, v2.previous_version, v2.status), (2, v1.name, "Draft"))
		v2.ingredients = [r for r in v2.ingredients if r.item_code != RMS[0]]
		v2.append("ingredients", {"item_code": RMS[0], "percentage": 0.0001})
		v2.ingredients = [r for r in v2.ingredients if r.item_code != RMS[0]]
		with self.assertRaisesRegex(frappe.ValidationError, "Required ingredients"):
			v2.save()
		v2.reload()
		v2.ingredients[1].percentage = 14
		v2.ingredients[2].percentage = 5
		v2.save()
		with self.assertRaisesRegex(frappe.ValidationError, "what changed"):
			self.act(v2, CHEMIST, "send_for_review")
		v2.reload()
		v2.change_note = "More thickener for viscosity"
		v2.save()
		self.act(v2, CHEMIST, "send_for_review")
		self.act(v2, MANAGER, "start_trial")
		self.pass_trial(v2)
		self.act(v2, MANAGER, "approve")
		self.assertEqual(frappe.db.get_value("Formulation", v1.name, "status"), "Archived")
		self.assertEqual(frappe.db.get_value("Formulation", v2.name, "status"), "Approved")

	# -- BOM -----------------------------------------------------------------------------------
	def test_approved_version_becomes_a_traceable_bom(self):
		doc = self.formulation(self.full_rows())
		self.act(doc, CHEMIST, "send_for_review")
		self.act(doc, MANAGER, "start_trial")
		self.pass_trial(doc)
		frappe.set_user(MANAGER)
		with self.assertRaisesRegex(frappe.ValidationError, "approved"):
			frappe.get_doc("Formulation", doc.name).create_bom()
		self.act(doc, MANAGER, "approve")
		frappe.set_user(CHEMIST)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("Formulation", doc.name).create_bom()
		frappe.set_user(MANAGER_2)
		bom_name = frappe.get_doc("Formulation", doc.name).create_bom(self.company)
		bom = frappe.get_doc("BOM", bom_name)
		self.assertEqual((bom.item, bom.quantity, bom.docstatus), (BULK, 200, 1))
		self.assertEqual((bom.fo_formulation, bom.fo_formulation_version), (doc.name, 1))
		self.assertEqual({(r.item_code, r.qty) for r in bom.items}, {(RMS[0], 160), (RMS[1], 30), (RMS[2], 8), (RMS[3], 2)})
		self.assertEqual(frappe.db.get_value("Formulation", doc.name, "bom"), bom_name)

	def test_a_bom_cannot_claim_an_unapproved_formulation(self):
		doc = self.formulation(self.full_rows())
		frappe.set_user("Administrator")
		with self.assertRaisesRegex(frappe.ValidationError, "not approved"):
			frappe.get_doc(
				{
					"doctype": "BOM",
					"item": BULK,
					"company": self.company,
					"quantity": 100,
					"fo_formulation": doc.name,
					"items": [{"item_code": RMS[0], "qty": 100}],
				}
			).insert()

	def test_demo_formulations_never_become_production_boms(self):
		doc = self.approved(is_demo=1, name="Demo gel")
		self.assertIn(DEMO_LABEL, doc.formulation_name)
		frappe.set_user(MANAGER_2)
		with self.assertRaisesRegex(frappe.ValidationError, "never become production BOMs"):
			frappe.get_doc("Formulation", doc.name).create_bom()

	# -- projects and stability ----------------------------------------------------------------
	def test_only_a_manager_approves_a_research_project(self):
		frappe.set_user(CHEMIST)
		p = frappe.get_doc({"doctype": "Research Project", "title": "Gel base", "objective": "A stable gel base"}).insert()
		p.status = "Approved"
		with self.assertRaises(frappe.PermissionError):
			p.save()
		frappe.set_user(MANAGER)
		p = frappe.get_doc("Research Project", p.name)
		p.status = "Approved"
		p.save()
		self.assertEqual(p.approved_by, MANAGER)

	def test_completed_stability_study_needs_result_and_readings(self):
		doc = self.formulation(self.full_rows())
		frappe.set_user(CHEMIST)
		s = frappe.get_doc(
			{
				"doctype": "Stability Study",
				"title": "40/75 three months",
				"formulation": doc.name,
				"storage_condition": "40°C / 75% RH",
				"start_date": "2026-01-01",
				"duration_days": 90,
				"status": "Completed",
			}
		)
		with self.assertRaisesRegex(frappe.ValidationError, "result"):
			s.insert()
		s.result = "Pass"
		with self.assertRaisesRegex(frappe.ValidationError, "reading"):
			s.insert()
		s.append("readings", {"interval": "Month 3", "ph": 5.6, "within_spec": "Yes"})
		s.insert()
