"""Purchase Order approval: per-user limits and no self-approval (brief section 6, rules 4-5).

Approval = submitting the Purchase Order. Enforced in ``before_submit`` so it holds for Desk,
the REST API and data import alike. Limits live in the Purchase Approval Limit DocType and
compare against the PO's base (company currency) grand total.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, fmt_money


class ApprovalLimitError(frappe.PermissionError):
	pass


class SelfApprovalError(frappe.PermissionError):
	pass


def approval_enforced() -> bool:
	return bool(cint(frappe.db.get_single_value("Factory Ops Settings", "enforce_purchase_approval")))


def get_limit(user: str, company: str) -> float | None:
	rows = frappe.get_all(
		"Purchase Approval Limit",
		filters={"user": user, "enabled": 1},
		fields=["approval_limit", "company"],
	)
	for row in rows:
		if not row.company or row.company == company:
			return flt(row.approval_limit)
	return None


def validate_purchase_order_approval(doc, method=None):
	if not approval_enforced():
		return
	user = frappe.session.user
	if doc.owner == user:
		frappe.throw(
			_("You created Purchase Order {0}, so another approver must submit it.").format(doc.name),
			SelfApprovalError,
			title=_("Approval required"),
		)
	limit = get_limit(user, doc.company)
	total = flt(doc.base_grand_total)
	currency = frappe.get_cached_value("Company", doc.company, "default_currency")
	if limit is None:
		frappe.throw(
			_("You have no purchase approval limit. Ask an approver to submit {0}.").format(doc.name),
			ApprovalLimitError,
			title=_("Approval required"),
		)
	if total > limit:
		frappe.throw(
			_("Purchase Order {0} is {1}, above your approval limit of {2}.").format(
				doc.name, fmt_money(total, currency=currency), fmt_money(limit, currency=currency)
			),
			ApprovalLimitError,
			title=_("Approval required"),
		)


@frappe.whitelist(methods=["POST"])
def approve_purchase_order(name: str) -> dict:
	"""Approve (submit) a draft Purchase Order for someone who holds the *submit* right but not *write*.

	Frappe's ``Document.submit`` saves the document, and saving asks for the write right as well, so
	a role that may approve but must not edit orders (the Business Administrator) cannot submit from
	the form. This is the one narrow path for it: the real submit permission on this document is
	checked first (Frappe's own ``has_permission``, including user permissions), then the document is
	submitted without the write check. Nothing else is skipped: ``validate_purchase_order_approval``
	still runs in ``before_submit``, so the approval limit and the no-self-approval rule apply exactly
	as for anyone else. The change is recorded by Frappe's Version history."""
	doc = frappe.get_doc("Purchase Order", name)
	if doc.docstatus != 0:
		frappe.throw(_("Purchase Order {0} is not awaiting approval.").format(name), title=_("Not pending"))
	frappe.has_permission("Purchase Order", "submit", doc=doc, throw=True)
	doc.flags.ignore_permissions = True  # the submit right was verified above; save() would also want write
	doc.submit()
	return {"name": doc.name, "docstatus": doc.docstatus}
