# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Escalation: notify the right people when an entry has waited too long, once.

Rules live in Decision Centre Settings and start disabled. The hourly job reads the whole queue (as the
system, so it is not limited to one person's view), and for each enabled rule that matches an entry whose
age has reached the rule's hours it creates one Notification Log per recipient and one Escalation Event.
The event is what prevents a repeat: it is keyed on (entry, rule), so an hourly job notifies once however
long the item stays open. When the issue is resolved its events are removed, so if it comes back it is
escalated afresh."""

import hashlib

import frappe
from frappe.utils import now_datetime

from factory_ops.decisions import queue
from factory_ops.decisions.actions import log_action


def _recipients(rule: dict) -> list[str]:
	users = set()
	if rule.get("notify_role"):
		users |= set(
			frappe.get_all(
				"Has Role", filters={"role": rule["notify_role"], "parenttype": "User"}, pluck="parent", limit_page_length=0
			)
		)
	if rule.get("notify_user"):
		users.add(rule["notify_user"])
	if not users:
		return []
	enabled = frappe.get_all("User", filters={"name": ["in", list(users)], "enabled": 1, "user_type": "System User"}, pluck="name", limit_page_length=0)
	return sorted(u for u in enabled if u not in ("Administrator", "Guest"))


def run(now=None) -> dict:
	"""Hourly job (and callable directly). Returns what it did, for the log and for tests."""
	data = queue.collect(user="Administrator", now=now, apply_scope=False)
	rules = [r for r in _settings_rules() if r["enabled"] and (r["notify_role"] or r["notify_user"])]
	done = {"notified": 0, "events_created": 0, "events_cleared": 0}
	live = {row["key"] for row in data["rows"]}
	existing = {e.name: e.entry_key for e in frappe.get_all("Escalation Event", fields=["name", "entry_key"], limit_page_length=0)}
	for row in data["rows"]:
		hours = row.get("age_hours")
		if hours is None:
			continue
		for rule in rules:
			if rule["kind"] != row["kind"] or hours < float(rule["escalate_after_hours"] or 0):
				continue
			event = hashlib.sha1(f"{row['key']}::{rule['name']}".encode()).hexdigest()  # a safe, stable name
			if event in existing:
				continue
			people = _recipients(rule)
			for person in people:
				frappe.get_doc(
					{
						"doctype": "Notification Log",
						"for_user": person,
						"from_user": "Administrator",
						"type": "Alert",
						"subject": f"[{row['priority']}] {row['category']}: {row['issue']}"[:140],
						"email_content": f"{row['issue']} (pending {row['age_label']})",
						"document_type": row["ref_doctype"],
						"document_name": row["ref_name"],
					}
				).insert(ignore_permissions=True)
			frappe.get_doc(
				{
					"doctype": "Escalation Event",
					"event_key": event,
					"entry_key": row["key"],
					"kind": row["kind"],
					"rule": rule["name"],
					"notified_on": now_datetime(),
					"ref_doctype": row["ref_doctype"],
					"ref_name": row["ref_name"],
					"recipients": ", ".join(people),
				}
			).insert(ignore_permissions=True)
			log_action(row, "Escalated", "Done", message=f"Notified {', '.join(people) or 'nobody (no recipients)'} after {row['age_label']}", user="Administrator")
			existing[event] = row["key"]
			done["events_created"] += 1
			done["notified"] += len(people)
	for name, entry_key in list(existing.items()):  # resolved items: forget them, so a recurrence escalates again
		if entry_key not in live:
			frappe.delete_doc("Escalation Event", name, ignore_permissions=True, force=True)
			done["events_cleared"] += 1
	return done


def _settings_rules() -> list[dict]:
	from factory_ops.decisions import settings

	return settings.get().rules
