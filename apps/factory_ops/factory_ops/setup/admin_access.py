# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Global read access for the two administrator roles.

Two administrator roles, kept apart on purpose (see docs/UI_ARCHITECTURE.md section 9):

- **System Administrator** = Frappe's ``System Manager`` role (Role Profile "System Administrator"):
  technical administration. Users and roles, system settings, integrations, workspace configuration.
- **Business Administrator** (role ``Business Administrator``, Role Profile of the same name): the
  owner's view of the business. Everything below, plus the narrow business actions in
  ``BUSINESS_ACTIONS``; **no** technical configuration and **no** delete right anywhere.

**Visibility is not permission.** Both roles get complete *visibility* of the ERP and nothing more: ``read`` and ``report`` on every ERPNext operational DocType, and permission to
run the standard reports. It never adds write, create, submit, cancel, delete or amend, so an
administrator still cannot approve, release QC, post or alter financial records, or delete
anything unless ERPNext's own permissions for System Manager already say so. Every business control
(QC guards, purchase approval limits, segregation of duties) is server-side validation that does not
look at roles for this purpose, so it applies to the administrator exactly as to anyone else.

Why this is needed: ERPNext v16.53 gives System Manager read access to only about half of its
operational DocTypes (not Work Order, Batch, Stock Entry, Purchase Order, Sales Invoice, ...) and to
none of the reports behind the Mintree workspaces, so a workspace shown to an administrator would be
empty. ERPNext's standard workspaces have no role restriction, so no change is needed to make them
visible.

Mechanisms, all supported by Frappe and re-applied on every migrate (idempotent, self-healing):

- **Custom DocPerm** rows (read, report) through ``frappe.permissions.add_permission``. Frappe then
  uses the DocType's custom rows instead of its standard ones, so ``add_permission`` first copies
  the standard rows; other roles' rights are unchanged. After an ERPNext upgrade, re-run migrate
  and review ``shadowed_doctypes()`` for upstream permission changes on these DocTypes.
- **Custom Role** on reports. For a report Frappe *replaces* the standard role list with the Custom
  Role's, so the Custom Role always holds the union of the standard roles and System Manager.
  Standard Report documents are never saved (in developer mode that would rewrite ERPNext's JSON).

The built-in ``Administrator`` account bypasses permission checks in Frappe itself; use named
accounts for daily administration.
"""

import frappe
from frappe.permissions import add_permission, update_permission_property

ADMIN_ROLE = "System Manager"  # the System Administrator
BUSINESS_ADMIN_ROLE = "Business Administrator"
READ_ROLES = (ADMIN_ROLE, BUSINESS_ADMIN_ROLE)
APPS = ("erpnext", "factory_ops")
# The Business Administrator's business actions, and nothing else. Approving a Purchase Order is its
# *submit*: the right alone authorises nothing, because factory_ops.purchase.approval still requires
# an enabled Purchase Approval Limit covering the amount and refuses self-approval, so the owner
# approves only what an accountant has authorised them to approve. Reading Version is the document
# audit trail (who changed what, when).
BUSINESS_ACTIONS = (
	("Purchase Order", ("read", "submit")),
	("Version", ("read",)),
)
GRANTED_RIGHTS = ("read", "report")
# rights this module must never grant (asserted by tests)
ACTION_RIGHTS = ("write", "create", "submit", "cancel", "delete", "amend")


def _app_of(module: str | None) -> str | None:
	return (frappe.local.module_app or {}).get(frappe.scrub(module)) if module else None


def operational_doctypes() -> list[str]:
	"""Standalone (not child, not single, not custom) DocTypes of ERPNext and factory_ops."""
	rows = frappe.get_all(
		"DocType", filters={"istable": 0, "issingle": 0, "custom": 0}, fields=["name", "module"]
	)
	return sorted(r.name for r in rows if _app_of(r.module) in APPS)


def _effective_rows(doctype: str) -> list[dict]:
	"""The permission rows Frappe uses for the DocType: custom rows if any exist, else standard."""
	fields = ["role", "permlevel", *ALL_RIGHTS]
	custom = frappe.get_all("Custom DocPerm", filters={"parent": doctype}, fields=fields)
	return custom or frappe.get_all("DocPerm", filters={"parent": doctype}, fields=fields)


def _lacks(doctype: str, level: int, rights: tuple[str, ...], role: str = ADMIN_ROLE) -> list[str]:
	rows = [r for r in _effective_rows(doctype) if r.role == role and int(r.permlevel or 0) == level]
	return [right for right in rights if not any(r.get(right) for r in rows)]


# Every right of a permission row. Frappe's add_permission does not make a clean read-only row
# (rows it creates came out with write and create on, and export defaults on), so a row this module
# creates is written out right by right.
ALL_RIGHTS = (
	"read", "write", "create", "delete", "submit", "cancel", "amend", "report", "export", "import",
	"share", "print", "email", "select",
)  # fmt: skip


def _has_row(doctype: str, level: int, role: str = ADMIN_ROLE) -> bool:
	return any(r.role == role and int(r.permlevel or 0) == level for r in _effective_rows(doctype))


def _grant(doctype: str, level: int, rights: tuple[str, ...], role: str = ADMIN_ROLE) -> bool:
	"""Give System Manager ``rights`` (and only those) at ``level``.

	If the role already has a row there (upstream's, or one an administrator made), only the
	missing rights among ``rights`` are switched on and nothing else is touched. If there is none, the
	row is created and every right is set explicitly: ``rights`` on, all others off."""
	missing = _lacks(doctype, level, rights, role)
	if not missing:
		return False
	created = not _has_row(doctype, level, role)
	if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": level}):
		add_permission(doctype, role, level)
	targets = {right: int(right in rights) for right in ALL_RIGHTS} if created else dict.fromkeys(missing, 1)
	for right, value in targets.items():
		update_permission_property(doctype, role, level, right, value, validate=False)
	return True


def grant_admin_read(roles: tuple[str, ...] = READ_ROLES) -> list[str]:
	"""read + report at permlevel 0, and read on higher permlevels the DocType's fields use (so the
	administrators see e.g. Batch.fo_qc_status). Returns the DocTypes changed; [] when nothing to do."""
	changed = set()
	for doctype in operational_doctypes():
		levels = sorted({int(f.permlevel) for f in frappe.get_meta(doctype).fields if f.permlevel})
		for role in roles:
			did = _grant(doctype, 0, GRANTED_RIGHTS, role)
			for level in levels:
				did |= _grant(doctype, level, ("read",), role)
			if did:
				changed.add(doctype)
	return sorted(changed)


def grant_business_actions() -> list[str]:
	"""The Business Administrator's few business rights (BUSINESS_ACTIONS). Returns DocTypes changed."""
	changed = []
	for doctype, rights in BUSINESS_ACTIONS:
		if frappe.db.exists("DocType", doctype) and _grant(doctype, 0, rights, BUSINESS_ADMIN_ROLE):
			changed.append(doctype)
	return changed


def ensure_admin_reports() -> list[str]:
	"""Standard ERPNext / factory_ops reports restricted to roles: let both administrator roles run
	them. Returns the reports changed."""
	changed = []
	for report in frappe.get_all(
		"Report", filters={"is_standard": "Yes"}, fields=["name", "module", "ref_doctype"]
	):
		if _app_of(report.module) not in APPS:
			continue
		# roles of apps that are not installed (e.g. "Employee Self Service") do not exist on this site
		# and cannot be held by anyone; they are picked up by the first migrate after that app is added
		standard = {
			r
			for r in frappe.get_all(
				"Has Role", filters={"parenttype": "Report", "parent": report.name}, pluck="role"
			)
			if frappe.db.exists("Role", r)
		}
		existing = frappe.db.get_value("Custom Role", {"report": report.name}, "name")
		if not standard and not existing:
			continue  # open to anyone with the right on the DocType
		# (a report that is open upstream can still gain a restrictive Custom Role, e.g. ERPNext's South
		# Africa setup restricts the VAT Audit Report: the administrators must be added to that too)
		if existing:
			doc = frappe.get_doc("Custom Role", existing)
			have = {r.role for r in doc.roles}
			missing = (standard | set(READ_ROLES)) - have  # union only: never remove anyone's role
			if missing:
				for role in sorted(missing):
					doc.append("roles", {"role": role})
				doc.save(ignore_permissions=True)
				changed.append(report.name)
		elif not set(READ_ROLES) <= standard:
			frappe.get_doc(
				{
					"doctype": "Custom Role",
					"report": report.name,
					"ref_doctype": report.ref_doctype,
					"roles": [{"role": r} for r in sorted(standard | set(READ_ROLES))],
				}
			).insert(ignore_permissions=True)
			changed.append(report.name)
	return changed


# "System Administrator" is System Manager plus Workspace Manager: Frappe reserves editing and deleting
# public workspaces for Workspace Manager and says explicitly that System Manager does not imply it
# (frappe/desk/doctype/workspace/workspace.py, is_workspace_manager), yet workspace configuration is
# part of the job.
SYSTEM_ADMIN_ROLES = (ADMIN_ROLE, "Workspace Manager")
ROLE_PROFILES = {
	"System Administrator": SYSTEM_ADMIN_ROLES,
	"Business Administrator": (BUSINESS_ADMIN_ROLE,),
}


def ensure_role_profiles() -> list[str]:
	"""One Role Profile per administrator role, so a person is made an administrator by choosing a
	profile, not by assembling roles. Never removes a role someone added to a profile."""
	changed = []
	for name, roles in ROLE_PROFILES.items():
		if not all(frappe.db.exists("Role", r) for r in roles):
			continue
		if frappe.db.exists("Role Profile", name):
			doc = frappe.get_doc("Role Profile", name)
			missing = set(roles) - {r.role for r in doc.roles}
			if missing:
				for role in sorted(missing):
					doc.append("roles", {"role": role})
				doc.save(ignore_permissions=True)
				changed.append(name)
		else:
			frappe.get_doc(
				{"doctype": "Role Profile", "role_profile": name, "roles": [{"role": r} for r in roles]}
			).insert(ignore_permissions=True)
			changed.append(name)
	return changed


def ensure_admin_access() -> dict[str, list[str]]:
	return {
		"doctypes": grant_admin_read(),
		"business_actions": grant_business_actions(),
		"reports": ensure_admin_reports(),
		"role_profiles": ensure_role_profiles(),
	}


def shadowed_doctypes() -> list[str]:
	"""DocTypes that now use custom permission rows (so upstream changes to their standard rows are
	not applied). Review these after an ERPNext upgrade."""
	return sorted(set(frappe.get_all("Custom DocPerm", pluck="parent")))
