# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Role landing: where each person's Desk opens.

Frappe v16 opens a user on ``User.default_workspace`` (the post-login page and the home shell);
without one it picks the shell holding most of the user's entities, which for a factory user was
often a generic ERPNext page (seen in the browser: Administrator opened on Setup).

This sets that field from the user's roles, **only when it is empty**, so a person's own choice is
never overwritten. It runs when a user is saved and, once per migrate, for existing users.

A Business Administrator (the owner) lands on **Management**, the consolidated business dashboard.
A System Administrator (System Manager, including the built-in Administrator) lands on the technical
**Administration** page, which is ERPNext's own Setup/Home workspace customised
for them (not a separate page). Neither lands on a single department, whatever other roles they hold; from
there, and from the shell switcher, every department they may see is one click away. Someone holding
both roles lands on Management: the business view first. Landing is a
convenience only: it decides which page opens, not what anyone may do.
"""

import frappe

# First workspace whose roles the user holds. Specific working roles come before general ones:
# stores staff are Stock + Purchase users and belong in Stores; production, sales and dispatch
# staff are Stock users too, so their own workspaces come first.
ADMIN_ROLE = "System Manager"
BUSINESS_ADMIN_ROLE = "Business Administrator"
ADMIN_PAGE = "Home"  # ERPNext's own Setup/Home, shown as the Administration page (setup/admin_home.py)
BUSINESS_PAGE = "Management"
LANDING_PRIORITY = (
	"Management",
	"Production Floor",
	"Quality Control",
	"Research and Development",
	"Plant Maintenance",
	"Sales and Dispatch",
	"Factory Accounts",
	"Stores and Warehouse",
	"Procurement",
)


def landing_workspace(roles) -> str | None:
	"""The workspace a holder of ``roles`` should open on, or None."""
	from factory_ops.setup.workspaces import WORKSPACES

	roles = set(roles)
	if BUSINESS_ADMIN_ROLE in roles:
		return BUSINESS_PAGE
	if ADMIN_ROLE in roles:
		return ADMIN_PAGE
	for name in LANDING_PRIORITY:
		if roles & (set(WORKSPACES[name]["roles"]) - {ADMIN_ROLE}):
			return name
	return None


def set_default_workspace(doc, method=None):
	"""User.validate: fill an empty default workspace from the user's roles."""
	if doc.get("default_workspace") or doc.get("user_type") != "System User":
		return
	roles = {r.role for r in doc.get("roles") or []}
	if doc.name == "Administrator":
		roles = {ADMIN_ROLE}  # Frappe grants it every role implicitly; it is the technical account
	workspace = landing_workspace(roles)
	if workspace and frappe.db.exists("Workspace", workspace):
		doc.default_workspace = workspace


def apply_to_existing_users() -> int:
	"""Once per migrate: users without a default workspace get the one their roles imply."""
	changed = 0
	for user in frappe.get_all(
		"User",
		filters={"enabled": 1, "user_type": "System User", "name": ("!=", "Guest")},
		fields=["name", "default_workspace"],
	):
		if user.default_workspace:
			continue
		# the built-in account implicitly holds every role, but it is the technical account
		roles = {ADMIN_ROLE} if user.name == "Administrator" else frappe.get_roles(user.name)
		workspace = landing_workspace(roles)
		if workspace and frappe.db.exists("Workspace", workspace):
			frappe.db.set_value("User", user.name, "default_workspace", workspace, update_modified=False)
			frappe.clear_cache(user=user.name)
			changed += 1
	return changed
