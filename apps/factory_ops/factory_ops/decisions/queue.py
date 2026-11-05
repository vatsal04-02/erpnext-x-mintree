# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""The central aggregation layer: collect, de-duplicate, prioritise, scope, filter, summarise.

Nothing is copied into another table. Every call (or cache refill, at most once per ``cache_seconds``
per user) reads the real documents through the registered sources, so an issue that is resolved
disappears from the queue by itself."""

import frappe
from frappe import _
from frappe.utils import now_datetime

from factory_ops.decisions import settings as settings_module
from factory_ops.decisions import sources as registry
from factory_ops.decisions.context import Context
from factory_ops.decisions.model import CRITICAL, DEPARTMENTS, PRIORITIES
from factory_ops.decisions.priority import age_hours, age_label, apply_rules, sort_key, state_of

# Who may open the Decision Centre, and who sees the whole queue (versus only what is assigned to them)
MANAGER_ROLES = (
	"Business Administrator", "System Manager", "Management Viewer", "Purchase Manager", "Stock Manager",
	"Manufacturing Manager", "QA Manager", "Sales Manager", "Accounts Manager", "Maintenance Manager",
)  # fmt: skip
OPERATOR_ROLES = (
	"Purchase User", "Stock User", "Manufacturing User", "QC Officer", "Sales User", "Delivery User",
	"Accounts User", "Maintenance User",
)  # fmt: skip
PAGE_ROLES = MANAGER_ROLES + OPERATOR_ROLES

# the summary family each kind belongs to
PROCUREMENT_APPROVAL_KINDS = ("po_approval", "po_over_limit", "pr_review")
QC_APPROVAL_KINDS = ("qc_incoming", "qc_bulk", "qc_fg", "qc_deviation")
DISPATCH_DELAY_KINDS = ("order_overdue", "order_blocked")


def kinds() -> dict[str, str]:
	return registry.all_kinds()


def user_roles(user: str) -> set[str]:
	return set(frappe.get_roles(user))


def can_use(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return bool(user_roles(user) & set(PAGE_ROLES))


def scope_of(user: str | None = None) -> str:
	"""'all': the whole queue the person may read. 'assigned': only what is assigned to them."""
	user = user or frappe.session.user
	return "all" if user_roles(user) & set(MANAGER_ROLES) else "assigned"


# --------------------------------------------------------------------------- collecting
def dedupe(entries: list) -> list:
	"""One entry per document per department: the most telling source wins (lowest precedence number,
	then the more urgent priority). A document may still appear under another department."""
	best: dict[tuple, object] = {}
	for e in entries:
		key = (e.department, e.ref_doctype, e.ref_name)
		cur = best.get(key)
		if cur is None or (e.precedence, PRIORITIES.index(e.priority)) < (cur.precedence, PRIORITIES.index(cur.priority)):
			best[key] = e
	return list(best.values())


def collect(user: str | None = None, now=None, apply_scope: bool = True) -> dict:
	"""Run every source the user may read. Returns {"rows", "truncated", "skipped"}; rows are plain dicts."""
	ctx = Context(user, now)
	registry._load()
	entries = []
	for src in registry.REGISTRY:
		if not all(frappe.db.exists("DocType", d) for d in src.requires) or not ctx.can_all(*src.requires):
			ctx.skipped.append(src.name)
			continue
		try:
			entries += src.fetch(ctx)
		except frappe.PermissionError:  # a field the viewer may not query: they simply have no such section
			ctx.skipped.append(src.name)
	entries = dedupe(entries)
	names = _full_names({e.responsible for e in entries if e.responsible})
	rules = ctx.settings.rules
	rows = []
	for e in entries:
		hours = age_hours(e.since, ctx.now)
		apply_rules(e, hours, rules)
		e.state = state_of(e)
		row = e.to_dict()
		row["age_hours"] = None if hours is None else round(hours, 1)
		row["age_label"] = age_label(hours)
		row["responsible_name"] = names.get(e.responsible, e.responsible)
		rows.append(row)
	if apply_scope and scope_of(ctx.user) == "assigned":
		rows = [r for r in rows if r["responsible"] == ctx.user]
	rows.sort(key=sort_key)
	return {"rows": rows, "truncated": sorted(set(ctx.truncated)), "skipped": sorted(set(ctx.skipped))}


def _full_names(users: set[str]) -> dict[str, str]:
	"""One query for every responsible person (no per-row lookups)."""
	if not users:
		return {}
	return {
		u.name: u.full_name
		for u in frappe.get_all("User", filters={"name": ["in", list(users)]}, fields=["name", "full_name"], limit_page_length=0)
	}


# --------------------------------------------------------------------------- filtering, summary
def apply_filters(rows: list[dict], filters: dict | None) -> list[dict]:
	f = filters or {}

	def many(value):
		return [value] if isinstance(value, str) else list(value or [])

	departments, priorities, states, kinds_ = many(f.get("department")), many(f.get("priority")), many(f.get("state")), many(f.get("kind"))
	out = []
	for r in rows:
		if departments and r["department"] not in departments and not set(r["also"]) & set(departments):
			continue
		if priorities and r["priority"] not in priorities:
			continue
		if states and r["state"] not in states:
			continue
		if kinds_ and r["kind"] not in kinds_:
			continue
		if f.get("status") and f["status"].lower() not in (r["status"] or "").lower():
			continue
		if f.get("responsible") and r["responsible"] != f["responsible"]:
			continue
		since = (r["since"] or "")[:10]
		if f.get("from_date") and (not since or since < str(f["from_date"])[:10]):
			continue
		if f.get("to_date") and (not since or since > str(f["to_date"])[:10]):
			continue
		if f.get("text") and f["text"].lower() not in (r["issue"] + " " + r["ref_name"]).lower():
			continue
		out.append(r)
	return out


def summary(rows: list[dict]) -> dict:
	"""The overview figures, counted from the same rows the queue shows."""
	by_department = {d: 0 for d in DEPARTMENTS}
	for r in rows:
		by_department[r["department"]] = by_department.get(r["department"], 0) + 1
	return {
		"total_pending": len(rows),
		"critical": sum(r["priority"] == CRITICAL for r in rows),
		"overdue": sum(bool(r["overdue"]) for r in rows),
		"qc_approvals_pending": sum(r["kind"] in QC_APPROVAL_KINDS for r in rows),
		"procurement_approvals_pending": sum(r["kind"] in PROCUREMENT_APPROVAL_KINDS for r in rows),
		"production_blockers": sum(bool(r["blocker"]) and r["department"] == "Production" for r in rows),
		"dispatch_delays": sum(r["kind"] in DISPATCH_DELAY_KINDS for r in rows),
		"by_department": by_department,
	}


# --------------------------------------------------------------------------- caching and the API
def _ttl() -> int:
	if frappe.in_test:  # tests must see the documents they just changed
		return 0
	return int(settings_module.get().cache_seconds)


def _cache_key(user: str) -> str:
	return f"fo_decisions::{user}"


def snapshot(user: str | None = None, refresh: bool = False) -> dict:
	"""The viewer's whole visible queue, cached per user for ``cache_seconds`` (0 = never)."""
	user = user or frappe.session.user
	ttl = _ttl()
	if ttl > 0 and not refresh:
		hit = frappe.cache.get_value(_cache_key(user))
		if hit:
			return hit
	data = collect(user)
	data["generated_at"] = str(now_datetime())
	if ttl > 0:
		frappe.cache.set_value(_cache_key(user), data, expires_in_sec=ttl)
	return data


def clear_cache(user: str | None = None):
	frappe.cache.delete_value(_cache_key(user or frappe.session.user))


@frappe.whitelist(methods=["GET", "POST"])
def get_decision_queue(filters=None, refresh=0, limit=500):
	if not can_use():
		frappe.throw(_("You do not have access to the Decision Centre."), frappe.PermissionError)
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	data = snapshot(refresh=bool(int(refresh or 0)))
	rows = apply_filters(data["rows"], filters)
	limit = max(1, min(int(limit or 500), 2000))
	return {
		"entries": rows[:limit],
		"matching": len(rows),
		"summary": summary(data["rows"]),
		"generated_at": data["generated_at"],
		"truncated": data["truncated"],
		"skipped": data["skipped"],
		"scope": scope_of(),
	}


@frappe.whitelist(methods=["GET", "POST"])
def get_summary():
	if not can_use():
		frappe.throw(_("You do not have access to the Decision Centre."), frappe.PermissionError)
	return summary(snapshot()["rows"])


@frappe.whitelist(methods=["GET"])
def get_kinds():
	return kinds()
