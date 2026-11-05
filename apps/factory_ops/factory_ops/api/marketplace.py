"""Marketplace API for the Factory OS front end (MINTREE_ECOMMERCE_MODULE.md). Figures come from
Marketplace records and ERPNext stock; permissions are checked on the server for every call."""

import hashlib
import hmac
import json

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from factory_ops.marketplace import service


def _require(doctype: str, ptype: str = "read"):
	if not frappe.has_permission(doctype, ptype):
		frappe.throw(_("Not permitted."), frappe.PermissionError)


@frappe.whitelist(methods=["GET"])
def overview() -> dict:
	_require("Marketplace Order")
	channels = frappe.get_all(
		"Marketplace Channel",
		fields=["name", "provider", "connection_state", "allocation_percent", "last_success_at", "last_error"],
		order_by="name",
	)
	orders = frappe.get_all(
		"Marketplace Order",
		fields=["channel", "order_status", "import_status", "order_total", {"COUNT": "*", "as": "n"}],
		group_by="channel, order_status, import_status",
	)
	per = {c.name: {"orders": 0, "pending": 0, "failed": 0, "cancelled": 0, "returned": 0, "sales": 0.0} for c in channels}
	for o in orders:
		p = per.setdefault(o.channel, {"orders": 0, "pending": 0, "failed": 0, "cancelled": 0, "returned": 0, "sales": 0.0})
		p["orders"] += o.n
		if o.order_status in ("New", "Accepted"):
			p["pending"] += o.n
		if o.import_status == "Failed":
			p["failed"] += o.n
		if o.order_status == "Cancelled":
			p["cancelled"] += o.n
		if o.order_status == "Returned":
			p["returned"] += o.n
	totals = frappe.get_all(
		"Marketplace Order",
		filters={"order_status": ["not in", ["Cancelled"]]},
		fields=["channel", {"SUM": "order_total", "as": "total"}],
		group_by="channel",
	)
	for t in totals:
		per.setdefault(t.channel, {})["sales"] = float(t.total or 0)
	returns = frappe.get_all("Marketplace Return", fields=["channel", "status", {"COUNT": "*", "as": "n"}], group_by="channel, status") if frappe.has_permission("Marketplace Return", "read") else []
	listings = frappe.get_all("Marketplace Listing", fields=["channel", "sync_status", {"COUNT": "*", "as": "n"}], group_by="channel, sync_status")
	settlements = (
		frappe.get_all("Marketplace Settlement", fields=["channel", "reconciliation_status", {"COUNT": "*", "as": "n"}], group_by="channel, reconciliation_status")
		if frappe.has_permission("Marketplace Settlement", "read")
		else None
	)
	return {"channels": channels, "per_channel": per, "returns": returns, "listings": listings, "settlements": settlements}


@frappe.whitelist(methods=["GET"])
def inventory_allocation(company: str) -> list[dict]:
	_require("Marketplace Listing")
	return service.allocation(company)


@frappe.whitelist(methods=["POST"])
def refresh_published_quantities(company: str) -> int:
	_require("Marketplace Listing", "write")
	return service.refresh_published_quantities(company)


@frappe.whitelist(methods=["POST"])
def test_connection(channel: str) -> dict:
	_require("Marketplace Channel", "write")
	log = service.run_operation(channel, "Connection test", payload={"at": str(frappe.utils.now_datetime())})
	frappe.db.set_value("Marketplace Channel", channel, "last_tested_at", frappe.utils.now_datetime())
	return frappe.db.get_value("Marketplace Sync Log", log, ["name", "status", "message", "error"], as_dict=True)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=120, seconds=60)
def webhook(channel: str):
	"""Generic signed webhook intake: HMAC-SHA256 of the raw body with the channel's webhook secret,
	in the X-Signature header. Refused without a configured secret. Each provider's own signature
	scheme is to be verified against its documentation when its live connector is written."""
	if not frappe.db.exists("Marketplace Channel", channel):
		frappe.throw(_("Unknown channel."), frappe.DoesNotExistError)
	doc = frappe.get_doc("Marketplace Channel", channel)
	secret = doc.get_password("webhook_secret", raise_exception=False)
	if not secret:
		frappe.throw(_("Webhooks are not configured for this channel."), frappe.PermissionError)
	body = frappe.request.get_data() or b""
	expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
	if not hmac.compare_digest(expected, frappe.get_request_header("X-Signature") or ""):
		frappe.throw(_("Invalid signature."), frappe.AuthenticationError)
	payload = json.loads(body or b"{}")
	frappe.set_user("Administrator")  # signature verified; record on behalf of the integration
	name, created = service.ingest_order(channel, payload.get("order") or {}, source="API")
	frappe.get_doc(
		{"doctype": "Marketplace Sync Log", "channel": channel, "operation": "Webhook", "status": "Succeeded",
		 "message": f"{'created' if created else 'duplicate ignored'}: {name}", "records": 1}
	).insert(ignore_permissions=True)
	return {"order": name, "created": created}
