# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Helpers shared by the Decision Centre tests.

The site holds other test data, so a test never asserts on the whole queue: it looks for the entries of
the documents it created (``entries_for``) and compares counts before and after (``count``)."""

import frappe

from factory_ops.decisions import queue


def rows(user: str = "Administrator", now=None) -> list[dict]:
	"""The user's full queue, built fresh (never from the cache)."""
	return queue.collect(user=user, now=now)["rows"]


def entries_for(ref_doctype: str, ref_name: str, user: str = "Administrator", now=None) -> list[dict]:
	return [r for r in rows(user, now) if r["ref_doctype"] == ref_doctype and r["ref_name"] == ref_name]


def entry(ref_doctype: str, ref_name: str, kind: str | None = None, user: str = "Administrator", now=None):
	found = [r for r in entries_for(ref_doctype, ref_name, user, now) if kind is None or r["kind"] == kind]
	return found[0] if found else None


def count(kind: str | None = None, department: str | None = None, user: str = "Administrator") -> int:
	return sum(1 for r in rows(user) if (kind is None or r["kind"] == kind) and (department is None or r["department"] == department))


def statements(fn) -> int:
	"""How many SQL statements ``fn`` issues (to prove the layer does not query per row)."""
	counter = {"n": 0}
	original = frappe.db.sql

	def counting(*args, **kwargs):
		counter["n"] += 1
		return original(*args, **kwargs)

	frappe.db.sql = counting
	try:
		fn()
	finally:
		frappe.db.sql = original
	return counter["n"]
