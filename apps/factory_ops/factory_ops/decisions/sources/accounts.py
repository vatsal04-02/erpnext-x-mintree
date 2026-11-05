# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Accounts exceptions: overdue receivables and payables, documents awaiting approval, reconciliation.

Submitting a financial document needs the accounting permissions of the person doing it: the entry's
action is only offered to someone who holds the submit right, and the server checks again."""

import frappe
from frappe.utils import flt, get_datetime

from factory_ops.decisions.context import assigned_user
from factory_ops.decisions.model import ACCOUNTS, MEDIUM, OVERDUE, Action, Entry
from factory_ops.decisions.sources import source


def _submit_action(ctx, doctype: str, label: str = "Approve") -> Action:
	ok = ctx.can(doctype, "submit")
	return Action("submit", label, ok, "" if ok else "Posting financial documents is limited to the Accounts roles.")


@source(
	"overdue_receivables",
	ACCOUNTS,
	("Sales Invoice",),
	{"customer_overdue": "Outstanding customer payments"},
)
def overdue_receivables(ctx):
	return _overdue(ctx, "Sales Invoice", "customer", "customer_overdue", "Outstanding customer payments", "overdue_receivables")


@source("overdue_payables", ACCOUNTS, ("Purchase Invoice",), {"supplier_overdue": "Overdue supplier payments"})
def overdue_payables(ctx):
	return _overdue(ctx, "Purchase Invoice", "supplier", "supplier_overdue", "Overdue supplier payments", "overdue_payables")


def _overdue(ctx, doctype, party_field, kind, label, source_name):
	rows = ctx.rows(
		doctype,
		{"docstatus": 1, "is_return": 0, "outstanding_amount": [">", 0], "due_date": ["<", ctx.today]},
		["name", party_field, "due_date", "outstanding_amount", "owner", "creation", "_assign"],
		order_by="due_date asc",
		source=source_name,
	)
	out = []
	for r in rows:
		days = (ctx.today - get_datetime(r.due_date).date()).days
		out.append(
			Entry(
				kind=kind,
				category=label,
				department=ACCOUNTS,
				issue=f"{r.get(party_field)}: {flt(r.outstanding_amount):,.2f} outstanding, {days} day(s) overdue",
				priority=MEDIUM,
				ref_doctype=doctype,
				ref_name=r.name,
				status="Overdue",
				state=OVERDUE,
				since=get_datetime(r.due_date),
				responsible=assigned_user(r),
				raised_by=r.owner,
				responsible_department="Accounts Manager",
				amount=flt(r.outstanding_amount),
				overdue=True,
				precedence=20,
				action=Action("open", "Open"),
			)
		)
	return out


@source(
	"invoice_approval",
	ACCOUNTS,
	("Sales Invoice",),
	{"invoice_approval": "Invoices awaiting approval", "financial_review": "Financial transactions requiring authorised review"},
)
def documents_awaiting_approval(ctx):
	out = []
	for doctype, party in (("Sales Invoice", "customer"), ("Purchase Invoice", "supplier")):
		if not ctx.can(doctype):
			continue
		for r in ctx.rows(
			doctype,
			{"docstatus": 0, "is_return": 0},
			["name", party, "grand_total", "owner", "creation", "_assign"],
			source="invoice_approval",
		):
			out.append(
				Entry(
					kind="invoice_approval",
					category="Invoices awaiting approval",
					department=ACCOUNTS,
					issue=f"{doctype} for {r.get(party)}: {flt(r.grand_total):,.2f} awaiting approval",
					priority=MEDIUM,
					ref_doctype=doctype,
					ref_name=r.name,
					status="Draft",
					since=get_datetime(r.creation),
					responsible=assigned_user({"_assign": r.get("_assign"), "owner": ""}),
					raised_by=r.owner,
					responsible_department="Accounts Manager",
					amount=flt(r.grand_total),
					precedence=10,
					action=_submit_action(ctx, doctype),
				)
			)
	for doctype, amount_field in (("Journal Entry", "total_debit"), ("Payment Entry", "paid_amount")):
		if not ctx.can(doctype):
			continue
		for r in ctx.rows(
			doctype,
			{"docstatus": 0},
			["name", amount_field, "owner", "creation", "_assign"],
			source="invoice_approval",
		):
			out.append(
				Entry(
					kind="financial_review",
					category="Financial transactions requiring authorised review",
					department=ACCOUNTS,
					issue=f"{doctype} {r.name}: {flt(r.get(amount_field)):,.2f} awaiting authorised review",
					priority=MEDIUM,
					ref_doctype=doctype,
					ref_name=r.name,
					status="Draft",
					since=get_datetime(r.creation),
					responsible=assigned_user({"_assign": r.get("_assign"), "owner": ""}),
					raised_by=r.owner,
					responsible_department="Accounts Manager",
					amount=flt(r.get(amount_field)),
					precedence=10,
					action=_submit_action(ctx, doctype, "Authorise"),
				)
			)
	return out


@source("bank_reconciliation", ACCOUNTS, ("Bank Transaction",), {"reconciliation_exception": "Reconciliation exceptions"})
def reconciliation_exceptions(ctx):
	rows = ctx.rows(
		"Bank Transaction",
		{"docstatus": 1, "status": "Unreconciled"},
		["name", "bank_account", "date", "deposit", "withdrawal", "unallocated_amount", "creation", "owner"],
		order_by="date asc",
		source="bank_reconciliation",
	)
	return [
		Entry(
			kind="reconciliation_exception",
			category="Reconciliation exceptions",
			department=ACCOUNTS,
			issue=f"{r.bank_account}: {flt(r.unallocated_amount or r.deposit or r.withdrawal):,.2f} on {r.date} not matched to a payment or invoice",
			priority=MEDIUM,
			ref_doctype="Bank Transaction",
			ref_name=r.name,
			status="Unreconciled",
			state="Needs attention",
			since=get_datetime(r.date),
			responsible_department="Accounts Manager",
			amount=flt(r.unallocated_amount or r.deposit or r.withdrawal),
			precedence=30,
			action=Action("open", "Open"),
		)
		for r in rows
	]
