# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""What a source gets: the viewer, the clock, the settings, and permission-aware readers.

All reads go through ``frappe.get_list`` (user permissions and permission-query conditions apply) or,
for the two aggregates that need a join, are gated on read permission for every table involved and on
the viewer's company restrictions. Each source issues a fixed number of queries, never one per row."""

from datetime import date, datetime

import frappe
from frappe.utils import get_datetime, now_datetime, nowdate

from factory_ops.decisions import settings as settings_module


class Context:
	def __init__(self, user: str | None = None, now: datetime | None = None):
		self.user = user or frappe.session.user
		self.now: datetime = now or now_datetime()
		self.today: date = get_datetime(self.now).date() if now else get_datetime(nowdate()).date()
		self.settings = settings_module.get()
		self.limit = int(self.settings.max_rows_per_source)
		self._can: dict[tuple, bool] = {}
		self.truncated: list[str] = []
		self.skipped: list[str] = []

	def can(self, doctype: str, ptype: str = "read") -> bool:
		key = (doctype, ptype)
		if key not in self._can:
			self._can[key] = bool(frappe.has_permission(doctype, ptype, user=self.user))
		return self._can[key]

	def can_all(self, *doctypes: str) -> bool:
		return all(self.can(d) for d in doctypes)

	def rows(self, doctype, filters, fields, order_by="creation asc", parent_doctype=None, source: str = ""):
		"""One permission-aware query. Marks the source truncated if the row limit was reached."""
		rows = frappe.get_list(
			doctype,
			filters=filters,
			fields=fields,
			order_by=order_by,
			limit_page_length=self.limit,
			parent_doctype=parent_doctype,
		)
		if len(rows) >= self.limit and source:
			self.truncated.append(source)
		return rows

	def companies(self) -> list[str] | None:
		"""Companies the viewer is restricted to by User Permissions, or None for no restriction."""
		allowed = frappe.permissions.get_user_permissions(self.user).get("Company")
		return [a["doc"] for a in allowed] if allowed else None

	def company_sql(self, alias: str) -> tuple[str, dict]:
		companies = self.companies()
		if not companies:
			return "", {}
		return f" and {alias}.company in %(fo_companies)s", {"fo_companies": tuple(companies)}


def assigned_user(row) -> str:
	"""First person a document is assigned to (Frappe's ToDo assignment), else its owner."""
	assign = row.get("_assign")
	if assign:
		try:
			people = frappe.parse_json(assign)
			if people:
				return people[0]
		except Exception:
			pass
	return row.get("owner") or ""
