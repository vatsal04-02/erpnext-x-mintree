# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Mintree Administration on ERPNext's own Setup/Home workspace.

``Home`` is a standard workspace shipped by ERPNext (``erpnext/setup/workspace/home``), the first page of
the Setup shell. Its stock layout is the "Get to know ERPNext" onboarding panel, a chart and three cards.
This module replaces what an administrator sees there, without touching ERPNext's files or its base record,
through Frappe's supported site-customization layer: a **Custom Workspace** record for ``Home`` carrying

- the block layout (statistic cards, the workspace launcher, three link groups),
- the widgets those blocks use (number cards, URL tiles, link cards),
- the role change: ``Desk User`` removed, ``System Manager`` added, so only the System Administrator sees
  it (Frappe applies a customization on top of the live base, so ERPNext upgrades keep arriving).

Nothing is copied or duplicated: every tile opens the real workspace route, every link the real list, every
number comes from a real DocType through the Number Cards in setup/workspaces.py. A link whose target
does not exist on this site is left out rather than shown as a placeholder. ERPNext's onboarding records and
the Setup sidebar are untouched; the checklist stays reachable under Settings and integrations.

It runs on every migrate (idempotent: nothing is saved when nothing changed).
"""

import json

import frappe

HOME = "Home"
HEADING = "Administration"
BASE_ROLE = "Desk User"  # who ERPNext shows Home to; replaced by the System Administrator below


def _exists(link_type: str, link_to: str) -> bool:
	if link_type in ("DocType", "Report"):
		return bool(frappe.db.exists(link_type, link_to))
	return True


def _link_cards():
	"""The three link groups, keeping only targets that exist on this site."""
	from factory_ops.setup.workspaces import ADMIN_HOME, R, unique_links

	groups = []
	for label, items in unique_links([], ADMIN_HOME["links"]):
		kept = [i for i in items if _exists(i[1], i[2])]
		if kept:
			groups.append((label, kept))
	return groups


def build_layout() -> tuple[list[dict], dict]:
	"""(blocks, widgets) of the Administration layout."""
	from factory_ops.setup.workspaces import (
		ADMIN_HOME,
		R,
		_is_query_report,
		available_shortcuts,
		card_name,
		layout_blocks,
		shortcut_row,
	)

	cards = [card_name(c) for c in ADMIN_HOME["cards"]]
	shortcuts = available_shortcuts(ADMIN_HOME)  # a workspace tile only when that workspace exists
	link_cards = _link_cards()
	blocks = layout_blocks(HEADING, cards, [], shortcuts, link_cards)
	heading = {
		"id": "fo-adm-heading",
		"type": "header",
		"data": {"text": f'<span class="h4"><b>{HEADING}</b></span>', "col": 12},
	}
	widgets = {
		"number_card": [{"label": c, "number_card_name": c} for c in cards],
		"shortcut": [shortcut_row(to, t) for to, t in shortcuts],
		"card": [
			{
				"label": label,
				"link_count": len(items),
				"links": json.dumps(
					[
						{
							"label": text,
							"link_type": link_type,
							"link_to": link_to,
							"is_query_report": int(link_type == R and _is_query_report(link_to)),
						}
						for text, link_type, link_to in items
					]
				),
			}
			for label, items in link_cards
		],
	}
	return [heading, *blocks], widgets


def ensure_admin_home() -> bool:
	"""Create or update the Custom Workspace for Home. True if anything was saved."""
	if not frappe.db.exists("Workspace", HOME):
		return False  # ERPNext's Setup module is not installed: nothing to customise
	blocks, widgets = build_layout()
	content, widget_json = json.dumps(blocks), json.dumps(widgets)
	roles = frappe.get_all("Has Role", filters={"parenttype": "Workspace", "parent": HOME}, pluck="role")
	removed = sorted(set(roles))  # everything ERPNext shows Home to ...
	added = ["System Manager"]  # ... becomes the System Administrator only
	doc = (
		frappe.get_doc("Custom Workspace", HOME)
		if frappe.db.exists("Custom Workspace", HOME)
		else frappe.new_doc("Custom Workspace")
	)
	same = (
		not doc.is_new()
		and _same_json(doc.content, content)
		and _same_json(doc.widgets, widget_json)
		and sorted(r.role for r in doc.removed_roles) == removed
		and sorted(r.role for r in doc.added_roles) == added
		and doc.visibility in (None, "", "Inherit")
	)
	if same:
		return False
	doc.workspace = HOME
	doc.content = content
	doc.widgets = widget_json
	doc.visibility = "Inherit"
	doc.set("removed_roles", [{"role": r} for r in removed])
	doc.set("added_roles", [{"role": r} for r in added])
	doc.save(ignore_permissions=True) if not doc.is_new() else doc.insert(ignore_permissions=True)
	return True


def _same_json(a, b) -> bool:
	try:
		return json.loads(a or "null") == json.loads(b or "null")
	except ValueError:
		return False


def retire_standalone_administration():
	"""Remove the separate "Administration" page (workspace, sidebar, desktop icon, navigation module) that
	an earlier milestone built, and move anyone who landed on it to Home. Only records this app created
	are touched."""
	name = "Administration"
	if frappe.db.get_value("Workspace", name, "module") == name:
		for doctype in ("Desktop Icon", "Workspace"):
			if frappe.db.exists(doctype, name):
				frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)
	if frappe.db.exists("Sidebar", name) and frappe.db.get_value("Sidebar", name, "module") == name:
		frappe.delete_doc("Sidebar", name, ignore_permissions=True, force=True)
	menu = f"{name} Menu"
	if frappe.db.exists("Workspace Sidebar", menu):
		frappe.delete_doc("Workspace Sidebar", menu, ignore_permissions=True, force=True)
	if frappe.db.exists("Module Def", name):
		mod = frappe.db.get_value("Module Def", name, ["custom", "app_name"], as_dict=True)
		if mod.custom and mod.app_name == "factory_ops":
			frappe.delete_doc("Module Def", name, ignore_permissions=True, force=True)
	for user in frappe.get_all("User", filters={"default_workspace": name}, pluck="name"):
		frappe.db.set_value("User", user, "default_workspace", HOME, update_modified=False)
