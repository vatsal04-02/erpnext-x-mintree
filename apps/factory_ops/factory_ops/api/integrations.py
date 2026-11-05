"""Integration Centre: the real state of every outside connection (FACTORY_OS docs, D-044).

States come from records, never from this file's opinion: marketplace channels and the Meta
connection from their own records and sync logs, e-mail from Frappe's Email Account and Email Queue.
Connections with no code at all are listed as "Not implemented". Nothing here can mark anything
Connected.
"""

import frappe
from frappe import _
from frappe.utils import add_days, now_datetime


def _last_log(channel: str) -> dict | None:
	rows = frappe.get_all(
		"Marketplace Sync Log",
		filters={"channel": channel},
		fields=["name", "operation", "status", "finished_at", "message", "error"],
		order_by="creation desc",
		limit=1,
	)
	return rows[0] if rows else None


@frappe.whitelist(methods=["GET"])
def integrations() -> list[dict]:
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.AuthenticationError)
	out: list[dict] = [
		{
			"name": "ERPNext / Frappe",
			"group": "System of record",
			"state": "Connected",
			"note": "This app reads and writes only through the ERP's API in your signed-in session.",
		}
	]
	can_mkt = frappe.has_permission("Marketplace Channel", "read")
	for ch in frappe.get_all(
		"Marketplace Channel",
		fields=["name", "provider", "connection_state", "access_note", "last_success_at", "last_error", "last_tested_at"],
		order_by="name",
	) if can_mkt else []:  # fmt: skip
		failed = frappe.db.count("Marketplace Sync Log", {"channel": ch.name, "status": "Failed"})
		out.append(
			{
				"name": ch.name,
				"group": "Marketplaces",
				"state": ch.connection_state,
				"connector": ch.provider,
				"note": ch.access_note,
				"last_success": ch.last_success_at,
				"last_error": ch.last_error,
				"last_tested": ch.last_tested_at,
				"last_log": _last_log(ch.name),
				"failed_operations": failed,
				"configure": f"/marketplace/channels/{ch.name}",
				"can_configure": bool(frappe.has_permission("Marketplace Channel", "write")),
			}
		)
	if not can_mkt:
		out.append({"name": "Amazon, Flipkart, Myntra, Nykaa", "group": "Marketplaces", "state": "Hidden", "note": "Visible to marketplace roles."})
	meta = (
		frappe.db.get_value("Meta Connection", "Meta Business", ["connection_state", "access_note", "last_tested_at", "last_error"], as_dict=True)
		if frappe.has_permission("Meta Connection", "read")
		else None
	)
	for name in ("Meta Business", "Instagram (through Meta Business)", "Facebook (through Meta Business)"):
		out.append(
			{
				"name": name,
				"group": "Marketing",
				"state": meta.connection_state if meta else "Hidden",
				"note": (meta.access_note if meta else "Visible to marketing managers.") if name == "Meta Business" else "Uses the Meta Business connection; no separate credentials.",
				"last_tested": meta.last_tested_at if meta else None,
				"last_error": meta.last_error if meta else None,
				"configure": "/marketing/meta" if name == "Meta Business" and meta else None,
				"can_configure": bool(frappe.has_permission("Meta Connection", "write")),
			}
		)
	out.append(_email())
	out.append(
		{
			"name": "WhatsApp Business",
			"group": "Communication",
			"state": "Not implemented",
			"note": "No connector. Needs a WhatsApp Business Platform account (Meta) and approved message templates.",
		}
	)
	out.append(
		{
			"name": "Courier partners",
			"group": "Logistics",
			"state": "Not implemented",
			"note": "No connector. Transporter, LR / AWB number and date are typed on the delivery note.",
		}
	)
	out.append(
		{
			"name": "Bank statements",
			"group": "Finance",
			"state": "Not implemented",
			"note": "No bank feed. Payments are recorded as Payment Entries; bank reconciliation is a planned milestone.",
		}
	)
	out.append(
		{
			"name": "Marketplace settlements",
			"group": "Finance",
			"state": "Not implemented",
			"note": "Settlement statements are entered by hand and reconciled against ERPNext invoices (Marketplaces > Settlements).",
			"configure": "/marketplace/settlements" if frappe.has_permission("Marketplace Settlement", "read") else None,
		}
	)
	out.append(
		{
			"name": "GST e-invoice / e-way bill",
			"group": "Finance",
			"state": "Not configured",
			"note": "Waiting for the company's GSTIN and the accountant's sign-off; no tax is calculated until then.",
		}
	)
	return out


def _email() -> dict:
	row = {"name": "E-mail (outgoing)", "group": "Communication"}
	account = frappe.db.get_value("Email Account", {"enable_outgoing": 1}, ["name", "email_id"], as_dict=True)
	if not account:
		return {**row, "state": "Not configured", "note": "No outgoing e-mail account in Frappe; notifications stay in the app."}
	since = add_days(now_datetime(), -30)
	sent = frappe.db.count("Email Queue", {"status": "Sent", "modified": [">=", since]})
	errors = frappe.db.count("Email Queue", {"status": "Error", "modified": [">=", since]})
	if sent:
		return {**row, "state": "Connected", "note": f"{account.email_id}: {sent} e-mails sent in the last 30 days."}
	if errors:
		return {**row, "state": "Error", "note": f"{account.email_id}: {errors} e-mails failed in the last 30 days, none sent."}
	return {**row, "state": "Not configured", "note": f"{account.email_id} is set up but no e-mail has been sent yet, so it is not verified."}
