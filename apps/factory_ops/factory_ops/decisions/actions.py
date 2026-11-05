# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Management actions on queue entries. Every action is a normal Frappe operation under the viewer's own
permissions; the Decision Centre adds no authority of its own.

- approve_po: ``purchase.approval.approve_purchase_order`` (real submit permission, then the approval
  limit and no-self-approval rules).
- submit: the document's own ``submit()`` with the viewer's permissions (Work Order, invoices, ...).
- acknowledge / note / assign: need read access to the document; assign uses Frappe's assignment.

QC release, deviation approval of a batch and the like are never executed here: those entries link to the
QC document, where only QC roles can decide. Seeing an entry grants nothing: the server re-reads the queue
for this viewer and re-checks permission for the action. Every attempt, including refusals, is logged in
Management Action."""

import re

import frappe
from frappe import _
from frappe.utils import now_datetime

from factory_ops.decisions import queue
from factory_ops.purchase.approval import approve_purchase_order

LABELS = {"approve_po": "Approved", "submit": "Submitted", "acknowledge": "Acknowledged", "assign": "Assigned", "note": "Note"}


def _plain(message: str) -> str:
	return re.sub(r"<[^>]+>", "", message or "").strip()


def _reason(exc: Exception) -> str:
	"""The human message behind a refusal (frappe.throw puts it in the message log)."""
	logged = [_plain(m.get("message") if isinstance(m, dict) else str(m)) for m in (frappe.local.message_log or [])]
	frappe.clear_messages()
	return (logged[-1] if logged else "") or _plain(str(exc)) or exc.__class__.__name__


def log_action(row: dict, action: str, outcome: str, note: str = "", assigned_to: str = "", message: str = "", user: str | None = None):
	return frappe.get_doc(
		{
			"doctype": "Management Action",
			"action": action,
			"outcome": outcome,
			"user": user or frappe.session.user,
			"acted_on": now_datetime(),
			"category": row["category"],
			"department": row["department"],
			"priority": row["priority"],
			"ref_doctype": row["ref_doctype"],
			"ref_name": row["ref_name"],
			"kind": row["kind"],
			"entry_key": row["key"],
			"note": note,
			"assigned_to": assigned_to,
			"message": (message or "")[:1000],
		}
	).insert(ignore_permissions=True)


def _find(entry_key: str) -> dict:
	"""Rebuild the viewer's queue now (never the cache): the entry must still be pending and visible."""
	for row in queue.collect()["rows"]:
		if row["key"] == entry_key:
			return row
	frappe.throw(_("This item is no longer pending, or you cannot see it."), frappe.DoesNotExistError)


def _perform(row: dict, action: str, note: str, assignee: str) -> str:
	doctype, name = row["ref_doctype"], row["ref_name"]
	if action == "approve_po":
		if row["kind"] not in ("po_approval", "po_over_limit"):
			frappe.throw(_("This item is not a Purchase Order awaiting approval."))
		approve_purchase_order(name)
		return _("Purchase Order {0} approved.").format(name)
	if action == "submit":
		if (row.get("action") or {}).get("id") != "submit":
			frappe.throw(_("This item cannot be approved from the Decision Centre; open the document."))
		frappe.get_doc(doctype, name).submit()  # the viewer's own permissions and validations apply
		return _("{0} {1} submitted.").format(doctype, name)
	frappe.has_permission(doctype, "read", doc=name, throw=True)
	if action == "acknowledge":
		return _("Acknowledged.")
	if action == "note":
		if not (note or "").strip():
			frappe.throw(_("Write the note first."))
		frappe.get_doc(doctype, name).add_comment("Comment", note)
		return _("Note added to {0} {1}.").format(doctype, name)
	if action == "assign":
		if not assignee:
			frappe.throw(_("Choose who to assign it to."))
		from frappe.desk.form.assign_to import add

		add({"assign_to": [assignee], "doctype": doctype, "name": name, "description": note or row["issue"], "notify": 1})
		return _("Assigned to {0}.").format(assignee)
	frappe.throw(_("Unknown action {0}.").format(action))


@frappe.whitelist(methods=["POST"])
def run_decision_action(entry_key: str, action: str, note: str = "", assignee: str = ""):
	"""Returns {"ok": bool, "message": str}. A refusal is a normal answer (ok False) so that it is logged;
	nothing the refused action started is kept."""
	if not queue.can_use():
		frappe.throw(_("You do not have access to the Decision Centre."), frappe.PermissionError)
	if action not in LABELS:
		frappe.throw(_("Unknown action {0}.").format(action))
	row = _find(entry_key)
	frappe.db.savepoint("fo_decision_action")
	try:
		message = _perform(row, action, note, assignee)
	except (frappe.PermissionError, frappe.ValidationError, frappe.DoesNotExistError, frappe.LinkValidationError) as exc:
		frappe.db.rollback(save_point="fo_decision_action")
		reason = _reason(exc)
		log_action(row, LABELS[action], "Refused", note, assignee, reason)
		return {"ok": False, "message": reason}
	log_action(row, LABELS[action], "Done", note, assignee, message)
	queue.clear_cache()
	return {"ok": True, "message": message}
