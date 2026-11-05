# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Decision Centre setup: example escalation rules, shipped disabled.

They show the four examples from the brief in the settings table. Hours are left at 0 and every rule is
disabled: the thresholds are Mintree's decision, not ours. Only runs while the table is empty."""

import frappe

EXAMPLES = (
	("breakdown", 0, "", "Business Administrator", "Critical machine breakdown: make it visible to management at once."),
	("qc_incoming", 0, "High", "QA Manager", "QC approval pending beyond the configured time: notify the QA Manager and escalate."),
	("qc_bulk", 0, "High", "QA Manager", "Bulk QC pending beyond the configured time: notify the QA Manager and escalate."),
	("qc_fg", 0, "High", "QA Manager", "Finished goods QC pending beyond the configured time: notify the QA Manager and escalate."),
	("po_approval", 0, "High", "Purchase Manager", "Purchase approval pending beyond the configured time: notify an approver."),
	("wo_blocked", 0, "Critical", "Manufacturing Manager", "Production blocked by a material shortage: notify Production."),
	("wo_blocked", 0, "Critical", "Purchase Manager", "Production blocked by a material shortage: notify Procurement."),
)


def ensure_default_rules() -> int:
	if not frappe.db.exists("DocType", "Decision Centre Settings"):
		return 0
	doc = frappe.get_doc("Decision Centre Settings")
	if doc.rules:
		return 0
	for kind, hours, priority, role, note in EXAMPLES:
		if frappe.db.exists("Role", role):
			doc.append(
				"rules",
				{"kind": kind, "enabled": 0, "escalate_after_hours": hours, "escalate_to": priority or None, "notify_role": role, "note": note},
			)
	doc.flags.ignore_permissions = True
	doc.save()
	return len(doc.rules)
