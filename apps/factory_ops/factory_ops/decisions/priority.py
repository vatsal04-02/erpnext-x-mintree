# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Priority and age, as pure functions (no database), so they can be tested exhaustively."""

from datetime import datetime

from factory_ops.decisions.model import ATTENTION, BLOCKED, OVERDUE, PENDING, PRIORITY_RANK, bump


def age_hours(since: datetime | None, now: datetime) -> float | None:
	if since is None:
		return None
	return max((now - since).total_seconds() / 3600.0, 0.0)


def age_label(hours: float | None) -> str:
	if hours is None:
		return ""
	if hours < 1:
		return "under 1 hour"
	if hours < 48:
		return f"{int(hours)} hours"
	return f"{int(hours // 24)} days"


def state_of(entry) -> str:
	"""Blocked beats overdue beats pending: what the person should notice first."""
	if entry.blocker:
		return BLOCKED
	if entry.overdue:
		return OVERDUE
	return entry.state if entry.state in (PENDING, ATTENTION) else PENDING


def apply_rules(entry, hours: float | None, rules: list[dict]) -> None:
	"""Raise priority (and mark overdue) when a configured rule's threshold is reached.

	A rule is {kind, enabled, escalate_after_hours, escalate_to}. Rules only ever make an entry *more*
	urgent: an escalation can never lower a priority the source set."""
	if hours is None:
		return
	for rule in rules:
		if rule["kind"] != entry.kind or not rule.get("enabled"):
			continue
		after = float(rule.get("escalate_after_hours") or 0)
		if after > 0 and hours >= after:
			target = rule.get("escalate_to")
			if target in PRIORITY_RANK:
				entry.priority = bump(entry.priority, target)
			entry.overdue = True


def sort_key(row: dict) -> tuple:
	"""Most urgent first, then the longest waiting."""
	return (PRIORITY_RANK[row["priority"]], -(row.get("age_hours") or 0.0), row["key"])
