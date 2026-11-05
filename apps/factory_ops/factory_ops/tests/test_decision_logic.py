# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Decision Centre: the pure logic (priority, age, escalation rules, de-duplication, filters, summary).

No database and no fixtures: synthetic entries, so every branch can be covered exhaustively."""

import unittest
from datetime import datetime, timedelta

from factory_ops.decisions import queue
from factory_ops.decisions.model import CRITICAL, HIGH, LOW, MEDIUM, Entry, bump
from factory_ops.decisions.priority import age_hours, age_label, apply_rules, sort_key, state_of

NOW = datetime(2026, 11, 5, 12, 0, 0)


def make(kind="po_approval", priority=MEDIUM, **kw) -> Entry:
	defaults = dict(category="c", department="Procurement", issue="i", ref_doctype="Purchase Order", ref_name="PO-1")
	defaults.update(kw)
	return Entry(kind=kind, priority=priority, **defaults)


def row(**kw) -> dict:
	e = make(**kw)
	d = e.to_dict()
	d.update(age_hours=age_hours(e.since, NOW), age_label="", responsible_name=e.responsible)
	d["state"] = state_of(e)
	return d


class TestPriorityAndAge(unittest.TestCase):
	def test_bump_returns_the_more_urgent_priority(self):
		self.assertEqual(bump(MEDIUM, HIGH), HIGH)
		self.assertEqual(bump(HIGH, MEDIUM), HIGH)  # never lowers
		self.assertEqual(bump(CRITICAL, LOW), CRITICAL)
		self.assertEqual(bump(LOW, LOW), LOW)

	def test_age_in_hours_and_its_label(self):
		self.assertIsNone(age_hours(None, NOW))
		self.assertEqual(age_hours(NOW - timedelta(hours=5), NOW), 5.0)
		self.assertEqual(age_hours(NOW + timedelta(hours=5), NOW), 0.0)  # a future date is not negative age
		self.assertEqual(age_label(None), "")
		self.assertEqual(age_label(0.4), "under 1 hour")
		self.assertEqual(age_label(5.9), "5 hours")
		self.assertEqual(age_label(47.9), "47 hours")
		self.assertEqual(age_label(48), "2 days")
		self.assertEqual(age_label(24 * 10 + 3), "10 days")

	def test_blocked_beats_overdue_beats_pending(self):
		self.assertEqual(state_of(make(blocker=True, overdue=True)), "Blocked")
		self.assertEqual(state_of(make(overdue=True)), "Overdue")
		self.assertEqual(state_of(make()), "Pending")
		self.assertEqual(state_of(make(state="Needs attention")), "Needs attention")

	def test_sort_is_priority_then_longest_waiting(self):
		rows = [
			row(kind="a", priority=MEDIUM, ref_name="1", since=NOW - timedelta(hours=100)),
			row(kind="b", priority=CRITICAL, ref_name="2", since=NOW - timedelta(hours=1)),
			row(kind="c", priority=CRITICAL, ref_name="3", since=NOW - timedelta(hours=9)),
			row(kind="d", priority=HIGH, ref_name="4", since=NOW - timedelta(hours=50)),
		]
		rows.sort(key=sort_key)
		self.assertEqual([r["ref_name"] for r in rows], ["3", "2", "4", "1"])


class TestEscalationRulesChangePriority(unittest.TestCase):
	RULE = {"kind": "po_approval", "enabled": 1, "escalate_after_hours": 24, "escalate_to": HIGH}

	def test_below_the_threshold_nothing_changes(self):
		e = make()
		apply_rules(e, 23.9, [self.RULE])
		self.assertEqual((e.priority, e.overdue), (MEDIUM, False))

	def test_at_the_threshold_it_is_raised_and_marked_overdue(self):
		e = make()
		apply_rules(e, 24, [self.RULE])
		self.assertEqual((e.priority, e.overdue), (HIGH, True))

	def test_a_rule_never_lowers_a_priority(self):
		e = make(priority=CRITICAL)
		apply_rules(e, 500, [{**self.RULE, "escalate_to": LOW}])
		self.assertEqual(e.priority, CRITICAL)

	def test_disabled_zero_hour_and_other_kind_rules_are_ignored(self):
		for rule in ({**self.RULE, "enabled": 0}, {**self.RULE, "escalate_after_hours": 0}, {**self.RULE, "kind": "qc_fg"}):
			e = make()
			apply_rules(e, 9999, [rule])
			self.assertEqual((e.priority, e.overdue), (MEDIUM, False), rule)

	def test_an_entry_with_no_age_is_never_escalated(self):
		e = make()
		apply_rules(e, None, [self.RULE])
		self.assertEqual((e.priority, e.overdue), (MEDIUM, False))


class TestDedupe(unittest.TestCase):
	def test_one_entry_per_document_per_department_lowest_precedence_wins(self):
		a = make(kind="wo_overdue", ref_doctype="Work Order", ref_name="WO-1", department="Production", precedence=20)
		b = make(kind="wo_blocked", ref_doctype="Work Order", ref_name="WO-1", department="Production", precedence=5)
		kept = queue.dedupe([a, b])
		self.assertEqual([e.kind for e in kept], ["wo_blocked"])

	def test_same_precedence_keeps_the_more_urgent(self):
		a = make(kind="x", priority=MEDIUM, precedence=10)
		b = make(kind="y", priority=CRITICAL, precedence=10)
		self.assertEqual([e.kind for e in queue.dedupe([a, b])], ["y"])

	def test_the_same_document_under_two_departments_is_two_entries(self):
		a = make(department="Procurement")
		b = make(department="Production")
		self.assertEqual(len(queue.dedupe([a, b])), 2)


class TestFiltersAndSummary(unittest.TestCase):
	def setUp(self):
		self.rows = [
			row(kind="po_approval", department="Procurement", priority=MEDIUM, ref_name="PO-1", since=NOW - timedelta(days=3), responsible="a@x.com"),
			row(kind="po_over_limit", department="Procurement", priority=HIGH, ref_name="PO-2", since=NOW - timedelta(days=1)),
			row(kind="qc_bulk", department="Quality Control", priority=HIGH, ref_name="B-1", since=NOW - timedelta(days=2), overdue=True, also=("Production",)),
			row(kind="wo_blocked", department="Production", priority=CRITICAL, ref_name="WO-1", blocker=True, overdue=True, since=NOW - timedelta(days=9), responsible="b@x.com"),
			row(kind="order_overdue", department="Dispatch & Sales", priority=HIGH, ref_name="SO-1", overdue=True, since=NOW - timedelta(days=4)),
			row(kind="order_blocked", department="Dispatch & Sales", priority=HIGH, ref_name="SO-2", since=NOW - timedelta(days=1)),
			row(kind="qc_fg", department="Quality Control", priority=MEDIUM, ref_name="B-2", since=NOW - timedelta(days=1)),
		]

	def names(self, **f):
		return sorted(r["ref_name"] for r in queue.apply_filters(self.rows, f))

	def test_each_filter(self):
		self.assertEqual(self.names(department="Procurement"), ["PO-1", "PO-2"])
		self.assertEqual(self.names(department=["Procurement", "Production"]), ["PO-1", "PO-2", "WO-1"])
		self.assertEqual(self.names(priority="Critical"), ["WO-1"])
		self.assertEqual(self.names(state="Blocked"), ["WO-1"])
		self.assertEqual(self.names(state="Overdue"), ["B-1", "SO-1"])
		self.assertEqual(self.names(responsible="a@x.com"), ["PO-1"])
		self.assertEqual(self.names(kind="qc_fg"), ["B-2"])
		self.assertEqual(self.names(text="so-2"), ["SO-2"])
		self.assertEqual(self.names(from_date="2026-11-03"), ["B-1", "B-2", "PO-2", "SO-2"])
		self.assertEqual(self.names(to_date="2026-10-30"), ["WO-1"])
		self.assertEqual(self.names(), sorted(r["ref_name"] for r in self.rows))

	def test_a_department_filter_also_finds_entries_that_list_it_as_interested(self):
		self.assertIn("B-1", self.names(department="Production"))

	def test_filters_combine(self):
		self.assertEqual(self.names(department="Dispatch & Sales", state="Overdue"), ["SO-1"])
		self.assertEqual(self.names(department="Procurement", priority="Critical"), [])

	def test_summary_is_counted_from_the_same_rows(self):
		s = queue.summary(self.rows)
		self.assertEqual(s["total_pending"], 7)
		self.assertEqual(s["critical"], 1)
		self.assertEqual(s["overdue"], 3)
		self.assertEqual(s["qc_approvals_pending"], 2)  # qc_bulk, qc_fg
		self.assertEqual(s["procurement_approvals_pending"], 2)  # po_approval, po_over_limit
		self.assertEqual(s["production_blockers"], 1)
		self.assertEqual(s["dispatch_delays"], 2)  # order_overdue, order_blocked
		self.assertEqual(s["by_department"]["Quality Control"], 2)
		self.assertEqual(sum(s["by_department"].values()), 7)

	def test_summary_of_an_empty_queue_is_zeros(self):
		s = queue.summary([])
		self.assertTrue(all(v == 0 for k, v in s.items() if k != "by_department"))
		self.assertTrue(all(v == 0 for v in s["by_department"].values()))
