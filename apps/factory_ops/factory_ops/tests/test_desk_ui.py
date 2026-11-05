# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Desk UI (B1): workspace layout, no duplicate entry points, the Research and Development
workspace, and the Milestone A reports reaching the roles that can run them.

Layout rules come from what the browser check showed (see ``setup/workspaces.layout_blocks``):
mixing widget kinds in one row stretched shortcuts into tall empty boxes.
"""

import json

import frappe
from frappe.desk.doctype.number_card.number_card import get_result

from factory_ops.setup.workspaces import WORKSPACES, card_name, layout_blocks, unique_links
from factory_ops.tests.test_admin_access import all_visible
from factory_ops.tests.test_workspaces import visible_workspaces
from factory_ops.tests.utils import USER_PRODUCTION, FactoryOpsTestCase, ensure_user

RND = "Research and Development"
USER_RND = "fo-rnd-chemist@example.com"
USER_RND_MANAGER = "fo-rnd-manager@example.com"
ROW_ORDER = ["number_card", "shortcut", "card", "chart"]


def rows_of(blocks):
	"""Split blocks into rows the way Desk does: a spacer ends a row."""
	rows, current = [], []
	for b in blocks:
		if b["type"] == "spacer":
			rows.append(current)
			current = []
		else:
			current.append(b)
	rows.append(current)
	return [r for r in rows if r]


def definition_blocks(name):
	ws = WORKSPACES[name]
	cards = [card_name(c) for c in ws["cards"]]
	return layout_blocks(name, cards, ws["charts"], ws["shortcuts"], unique_links(ws["shortcuts"], ws["links"]))


class TestWorkspaceLayout(FactoryOpsTestCase):
	def test_every_row_holds_one_kind_of_widget_in_a_fixed_order(self):
		for name in WORKSPACES:
			kinds = []
			for row in rows_of(definition_blocks(name)):
				row_kinds = {b["type"] for b in row}
				self.assertEqual(len(row_kinds), 1, f"{name}: a row mixes {row_kinds}")
				kinds.append(row_kinds.pop())
			# each kind appears as one run, and runs follow cards, shortcuts, link cards, charts
			collapsed = [k for i, k in enumerate(kinds) if i == 0 or k != kinds[i - 1]]
			self.assertEqual(collapsed, sorted(set(collapsed), key=ROW_ORDER.index), name)
			self.assertEqual(len(collapsed), len(set(collapsed)), f"{name}: a kind is split across runs")

	def test_no_title_block_and_no_stray_spacers(self):
		for name in WORKSPACES:
			blocks = definition_blocks(name)
			self.assertNotIn("header", {b["type"] for b in blocks}, f"{name} repeats its own title")
			self.assertNotEqual(blocks[0]["type"], "spacer", name)
			self.assertNotEqual(blocks[-1]["type"], "spacer", name)
			for a, b in zip(blocks, blocks[1:], strict=False):
				self.assertFalse(a["type"] == b["type"] == "spacer", f"{name}: two spacers in a row")

	def test_saved_pages_match_the_definitions(self):
		"""The page a user opens is the one the definitions describe (migrate ran)."""
		for name in WORKSPACES:
			saved = json.loads(frappe.db.get_value("Workspace", name, "content"))
			self.assertEqual(saved, definition_blocks(name), name)

	def test_a_target_has_one_entry_point_per_page(self):
		for name in WORKSPACES:
			doc = frappe.get_doc("Workspace", name)
			shortcuts = {(s.type, s.link_to) for s in doc.shortcuts}
			links = [(link.link_type, link.link_to) for link in doc.links if link.type == "Link"]
			self.assertFalse(shortcuts & set(links), f"{name}: {shortcuts & set(links)} appear twice")
			self.assertEqual(len(links), len(set(links)), f"{name}: duplicate links")
			# no link card without links
			sizes, current = [], None
			for link in doc.links:
				if link.type == "Card Break":
					current = [link.label, 0]
					sizes.append(current)
				else:
					current[1] += 1
			self.assertTrue(all(n for _label, n in sizes), f"{name}: an empty link card")

	def test_at_most_four_shortcuts_so_the_row_is_never_ragged(self):
		for name, ws in WORKSPACES.items():
			self.assertLessEqual(len(ws["shortcuts"]), 4, f"{name}: shortcuts wrap onto a second row")


class TestMilestoneAReportsOnWorkspaces(FactoryOpsTestCase):
	def page_text(self, user: str, workspace: str) -> str:
		"""What Desk actually sends this user for the workspace page, flattened to text."""
		from frappe.desk.desktop import get_desktop_page

		with self.set_user(user):
			frappe.local.request_cache.clear()
			page = get_desktop_page(json.dumps({"name": workspace, "title": workspace}))
		return json.dumps(page, default=str)

	def test_production_floor_offers_both_reports_to_production(self):
		text = self.page_text(USER_PRODUCTION, "Production Floor")
		self.assertIn("Production Reconciliation", text)
		self.assertIn("Batch Genealogy", text)

	def test_a_report_is_offered_only_to_roles_that_can_run_it(self):
		"""Report right on the reference DocType is what Frappe checks when it opens; a page must not
		offer a report its audience cannot open."""
		for report in ("Batch Genealogy", "Production Reconciliation"):
			doc = frappe.get_doc("Report", report)
			for name, ws in WORKSPACES.items():
				if not any(
					(s.type, s.link_to) == ("Report", report) for s in frappe.get_doc("Workspace", name).shortcuts
				) and not any(
					link.link_to == report for link in frappe.get_doc("Workspace", name).links
				):
					continue
				for role in ws["roles"]:
					if role == "System Manager":
						continue
					email = f"fo-ui-{role.lower().replace(' ', '-')}@example.com"
					ensure_user(email, [role])
					text = self.page_text(email, name)
					if not self._can_run(email, doc):
						self.assertNotIn(
							f'"{report}"', text, f"{name} offers {report} to {role}, who cannot open it"
						)

	def _can_run(self, user: str, report) -> bool:
		roles = {r.role for r in report.roles}
		return bool(roles & set(frappe.get_roles(user))) and bool(
			frappe.has_permission(report.ref_doctype, "report", user=user)
		)


class TestResearchWorkspace(FactoryOpsTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_user(USER_RND, ["R&D Chemist"])
		ensure_user(USER_RND_MANAGER, ["R&D Manager"])
		frappe.db.commit()

	def test_it_belongs_to_the_research_roles_only(self):
		doc = frappe.get_doc("Workspace", RND)
		self.assertEqual({r.role for r in doc.roles}, {"R&D Chemist", "R&D Manager", "System Manager", "Business Administrator"})
		for user in (USER_RND, USER_RND_MANAGER):
			self.assertEqual(visible_workspaces(user), {RND}, user)
		self.assertNotIn(RND, visible_workspaces(USER_PRODUCTION))

	def test_url_is_a_clean_slug(self):
		from frappe.desk.utils import slug

		self.assertEqual(slug(RND), "research-and-development")

	def test_every_card_is_readable_and_computes_for_research_users(self):
		for user in (USER_RND, USER_RND_MANAGER):
			for row in frappe.get_doc("Workspace", RND).number_cards:
				card = frappe.get_doc("Number Card", row.number_card_name)
				self.assertTrue(frappe.has_permission("Number Card", doc=card, user=user), f"{user}: {card.name}")
				with self.set_user(user):
					get_result(card.as_dict(), card.filters_json)  # raises PermissionError if not allowed

	def test_cards_count_what_they_say(self):
		"""Each card's filters select documents by the status the card names."""
		cards = {c.label: c for c in (frappe.get_doc("Number Card", n.number_card_name) for n in frappe.get_doc("Workspace", RND).number_cards)}
		expected = {
			"Formulations in Draft or Review": ("Formulation", "status", ["Draft", "Under Review"]),
			"Formulations in Trial": ("Formulation", "status", "Trial"),
			"Trials Awaiting Outcome": ("Formulation Trial", "outcome", "Pending"),
			"Stability Studies Running": ("Stability Study", "status", ["Planned", "In Progress"]),
		}
		self.assertEqual(set(cards), set(expected))
		for label, (doctype, field, value) in expected.items():
			card = cards[label]
			self.assertEqual(card.document_type, doctype, label)
			flt = next(f for f in json.loads(card.filters_json) if f[1] == field)
			self.assertEqual(flt[3], value, label)
			# and the field/values exist on the DocType
			df = frappe.get_meta(doctype).get_field(field)
			options = (df.options or "").split("\n")
			for v in value if isinstance(value, list) else [value]:
				self.assertIn(v, options, f"{label}: {v} is not a {doctype}.{field} option")

	def test_every_shortcut_opens_for_the_research_roles(self):
		for sc in frappe.get_doc("Workspace", RND).shortcuts:
			for user in (USER_RND, USER_RND_MANAGER):
				self.assertTrue(frappe.has_permission(sc.link_to, "read", user=user), f"{user}: {sc.link_to}")


class TestRoleLanding(FactoryOpsTestCase):
	CASES = [
		(["Manufacturing User", "Stock User"], "Production Floor"),
		(["Manufacturing User", "Manufacturing Manager"], "Production Floor"),
		(["Stock User", "Purchase User"], "Stores and Warehouse"),  # stores staff
		(["Purchase User"], "Procurement"),
		(["Sales User", "Stock User"], "Sales and Dispatch"),
		(["Stock User", "Delivery User"], "Sales and Dispatch"),
		(["Accounts User", "Accounts Manager"], "Factory Accounts"),
		(["QC Officer"], "Quality Control"),
		(["QA Manager"], "Quality Control"),
		(["Management Viewer"], "Management"),
		(["R&D Chemist"], "Research and Development"),
		(["R&D Manager"], "Research and Development"),
		(["Maintenance User"], "Plant Maintenance"),
		(["Maintenance Manager", "Maintenance User"], "Plant Maintenance"),
		(["Maintenance User", "Stock User"], "Plant Maintenance"),  # technicians are Stock users too
		(["System Manager"], "Home"),
		(["System Manager", "Manufacturing User", "Stock User"], "Home"),  # global, not a department
		(["System Manager", "QC Officer"], "Home"),
		(["Business Administrator"], "Management"),  # the owner: the consolidated business dashboard
		(["Business Administrator", "Manufacturing User"], "Management"),
		(["Business Administrator", "System Manager"], "Management"),  # the business view first
	]

	def email(self, roles) -> str:
		return "fo-landing-" + "-".join(r.lower().replace("&", "and").replace(" ", "") for r in roles) + "@example.com"

	def test_role_combinations_map_to_one_workspace(self):
		from factory_ops.setup.landing import landing_workspace

		for roles, expected in self.CASES:
			self.assertEqual(landing_workspace(roles), expected, roles)

	def test_roles_without_a_department_get_no_landing(self):
		from factory_ops.setup.landing import landing_workspace

		for roles in ([], ["Employee"], ["Desk User"]):
			self.assertIsNone(landing_workspace(roles), roles)

	def test_landing_is_always_a_workspace_the_user_can_see(self):
		for roles, expected in self.CASES:
			email = ensure_user(self.email(roles), roles)
			self.assertEqual(frappe.db.get_value("User", email, "default_workspace"), expected, roles)
			self.assertIn(expected, all_visible(email), f"{roles} lands on a page they cannot open")

	def test_a_users_own_choice_is_never_overwritten(self):
		roles = ["Manufacturing User", "Stock User"]
		email = ensure_user("fo-landing-choice@example.com", roles)
		frappe.db.set_value("User", email, "default_workspace", "Stores and Warehouse")
		user = frappe.get_doc("User", email)
		user.add_roles("QC Officer")  # saving again runs the hook
		self.assertEqual(frappe.db.get_value("User", email, "default_workspace"), "Stores and Warehouse")
		from factory_ops.setup.landing import apply_to_existing_users

		apply_to_existing_users()
		self.assertEqual(frappe.db.get_value("User", email, "default_workspace"), "Stores and Warehouse")

	def test_existing_users_without_a_choice_are_filled_by_migrate(self):
		from factory_ops.setup.landing import apply_to_existing_users

		email = ensure_user("fo-landing-existing@example.com", ["QC Officer"])
		frappe.db.set_value("User", email, "default_workspace", None)
		self.assertGreaterEqual(apply_to_existing_users(), 1)
		self.assertEqual(frappe.db.get_value("User", email, "default_workspace"), "Quality Control")
		self.assertEqual(apply_to_existing_users(), 0)  # nothing left to fill: idempotent

	def test_administrators_land_on_the_global_page_not_a_department(self):
		email = ensure_user("fo-landing-sysmgr@example.com", ["System Manager"])
		self.assertEqual(frappe.db.get_value("User", email, "default_workspace"), "Home")
		# a system administrator who also holds department roles is still not tied to one department
		both = ensure_user("fo-landing-sysmgr-prod@example.com", ["System Manager", "Manufacturing User"])
		self.assertEqual(frappe.db.get_value("User", both, "default_workspace"), "Home")
		# the built-in account too (Frappe grants it every role implicitly)
		frappe.db.set_value("User", "Administrator", "default_workspace", None)
		from factory_ops.setup.landing import apply_to_existing_users

		apply_to_existing_users()
		self.assertEqual(frappe.db.get_value("User", "Administrator", "default_workspace"), "Home")
