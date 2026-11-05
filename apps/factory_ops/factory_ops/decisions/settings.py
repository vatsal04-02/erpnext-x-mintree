# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Decision Centre configuration, read once per call with sensible structural defaults.

Nothing here is a Mintree business number except what Mintree enters: the window sizes are
configuration, and escalation thresholds live in the rules table and start empty/disabled.
QC response times are not new numbers: they are the existing QC SLA hours in Factory Ops Settings."""

import frappe

DEFAULTS = {"cache_seconds": 30, "expiry_window_days": 30, "dispatch_window_days": 3, "max_rows_per_source": 200}


def get() -> frappe._dict:
	values = frappe._dict(DEFAULTS)
	try:
		doc = frappe.get_cached_doc("Decision Centre Settings")
	except frappe.DoesNotExistError:
		values.rules = []
		return values
	for key, default in DEFAULTS.items():
		values[key] = int(doc.get(key) or default) if key != "cache_seconds" else int(doc.get(key) if doc.get(key) is not None else default)
	values.rules = [
		{
			"name": r.name,
			"kind": r.kind,
			"enabled": int(r.enabled or 0),
			"escalate_after_hours": float(r.escalate_after_hours or 0),
			"escalate_to": r.escalate_to,
			"notify_role": r.notify_role,
			"notify_user": r.notify_user,
		}
		for r in doc.get("rules") or []
	]
	return values


def qc_sla_hours() -> dict[str, int]:
	"""The existing QC response targets (Factory Ops Settings), by factory item group."""
	row = frappe.db.get_value(
		"Factory Ops Settings",
		"Factory Ops Settings",
		["qc_sla_hours_rm", "qc_sla_hours_pm", "qc_sla_hours_bulk", "qc_sla_hours_fg"],
		as_dict=True,
	) or {}
	return {
		"RM": int(row.get("qc_sla_hours_rm") or 0),
		"PM": int(row.get("qc_sla_hours_pm") or 0),
		"BULK": int(row.get("qc_sla_hours_bulk") or 0),
		"FG": int(row.get("qc_sla_hours_fg") or 0),
	}
