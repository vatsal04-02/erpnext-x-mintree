"""Small role-checked workflow helper shared by marketing campaigns and content."""

import frappe
from frappe import _
from frappe.utils import now_datetime

ROLE_MANAGER, ROLE_USER = "Marketing Manager", "Marketing User"


def apply(doc, actions: dict, action: str, note: str | None = None, submitter_field: str | None = None):
	if action not in actions:
		frappe.throw(_("Unknown action {0}.").format(action))
	allowed_from, target, roles, needs = actions[action]
	if doc.status not in allowed_from:
		frappe.throw(_("{0} is not possible while it is {1}.").format(action.replace("_", " "), doc.status))
	user_roles = set(frappe.get_roles())
	if "System Manager" not in user_roles and not user_roles & roles:
		frappe.throw(_("Only {0} may {1}.").format(" or ".join(sorted(roles)), action.replace("_", " ")), frappe.PermissionError)
	doc.check_permission("write")
	if "decision" in needs:
		if submitter_field and doc.get(submitter_field) == frappe.session.user and "System Manager" not in user_roles:
			frappe.throw(_("The person who sent it for approval cannot decide on it."), frappe.PermissionError)
	if "note" in needs and not (note or "").strip():
		frappe.throw(_("Give the reason."))
	return target


def stamp_decision(doc, note):
	doc.approved_by = frappe.session.user
	if doc.meta.has_field("approved_on"):
		doc.approved_on = now_datetime()
	if doc.meta.has_field("decision_note"):
		doc.decision_note = note
	elif doc.meta.has_field("review_note") and note:
		doc.review_note = note


def guard_status(doc):
	"""Status moves only through actions."""
	if doc.flags.fo_transition or doc.is_new():
		if doc.is_new() and not doc.flags.fo_transition:
			doc.status = doc.meta.get_field("status").default
		return
	before = doc.get_doc_before_save()
	if before and before.status != doc.status:
		frappe.throw(_("Status changes only through the workflow actions."))
