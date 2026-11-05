"""Session boot for the Factory OS front end (apps/web).

The front end reads and writes through Frappe's standard REST API and ERPNext's own whitelisted
methods. The one thing it cannot get from them cheaply is a summary of the signed-in user: roles,
the CSRF token for this session, and which of the documents and reports the front end offers this
user may actually use. Desk gets the same facts from its boot; this returns only those facts.

The answer only decides what the front end shows. Every read and write is still checked by Frappe
against the same role permissions, so a hidden button is never the security boundary.
"""

import frappe
import frappe.sessions
from frappe import _

# Documents the front end works with, by module (mirrors apps/web/src/modules)
DOCTYPES = (
	"Material Request", "Purchase Order", "Purchase Receipt", "Supplier", "Item", "Batch",
	"Stock Entry", "Warehouse", "Work Order", "BOM", "QC Release", "Quality Inspection",
	"QC Deviation", "QC Retest", "Sales Order", "Pick List", "Delivery Note", "Packing Slip",
	"Sales Invoice", "Payment Entry", "Customer", "Purchase Invoice", "Journal Entry",
	# R&D
	"Formulation", "Research Project", "Formulation Trial", "Stability Study", "Ingredient Category",
	# Maintenance (ERPNext asset DocTypes)
	"Asset", "Asset Maintenance", "Asset Maintenance Log", "Asset Repair", "Asset Maintenance Team", "Bin",
	# Marketplaces
	"Marketplace Channel", "Marketplace Listing", "Marketplace Order", "Marketplace Sync Log",
	"Marketplace Return", "Marketplace Settlement",
	# Brand and marketing
	"Marketing Campaign", "Marketing Content", "Campaign Performance", "Meta Connection",
)
REPORTS = (
	"Stock by QC Status", "QC Queue", "Available to Sell", "Dispatch Register",
	"RM PM Requirements", "Wastage by Reason", "Accounts Receivable", "Accounts Payable",
	"Stock Balance", "General Ledger",
)
PTYPES = ("read", "create", "write", "submit", "cancel")


def report_usable(report: str) -> bool:
	"""The checks Frappe makes before running a query report: the report's roles, then report
	permission on its reference DocType."""
	if not frappe.db.exists("Report", report):
		return False
	doc = frappe.get_doc("Report", report)
	return doc.is_permitted() and (not doc.ref_doctype or bool(frappe.has_permission(doc.ref_doctype, "report")))


@frappe.whitelist(methods=["GET"])
def boot() -> dict:
	"""The signed-in user's identity, roles, CSRF token and permitted documents and reports."""
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Please sign in."), frappe.AuthenticationError)

	doctypes = {
		dt: {ptype: bool(frappe.has_permission(dt, ptype)) for ptype in PTYPES}
		for dt in DOCTYPES
		if frappe.db.exists("DocType", dt)
	}
	reports = [r for r in REPORTS if report_usable(r)]
	company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)

	return {
		"user": user,
		"full_name": frappe.utils.get_fullname(user),
		"roles": frappe.get_roles(user),
		"csrf_token": frappe.sessions.get_csrf_token(),
		"company": company,
		"currency": frappe.get_cached_value("Company", company, "default_currency") if company else None,
		"doctypes": doctypes,
		"reports": reports,
		"desk_url": "/desk",
	}
