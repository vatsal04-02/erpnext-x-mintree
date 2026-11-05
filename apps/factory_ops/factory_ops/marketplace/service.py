"""Marketplace service: stock allocation, order intake, sales orders, synchronisation
(MINTREE_ECOMMERCE_MODULE.md).

**Inventory.** ERPNext stock is the only source of truth. What a channel may publish for an item is
the enforced available-to-sell figure (`inventory.batch_eligibility`: released, usable, unexpired,
above the minimum shelf life, not reserved, not picked) times the channel's share, minus its safety
stock. The shares of all channels add up to at most 100 %, so the same unit is never offered on
two marketplaces. QC Hold, Rejected, expired or short-dated stock is never in the figure.

**Orders.** One record per (channel, marketplace order ID): a repeated order is reported, not
created twice. A received order becomes a standard ERPNext Sales Order (channel D2C Marketplace,
external order ID "<channel>/<order ID>", which the sales rules keep unique as well).

**Sync.** Every call to a channel goes through `run_operation`: one log row per operation with an
idempotency key, attempts, result or error; rate-limited calls are retried with back-off by a
scheduled job; nothing is sent to a channel that is not Connected.
"""

from __future__ import annotations

import hashlib
import json
import math

import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, flt, getdate, now_datetime

from factory_ops.marketplace.connectors import ConnectorError, NotConnected, RateLimited, connector_for

MAX_ATTEMPTS = 5


# --------------------------------------------------------------------------- inventory allocation
def channel_shares() -> dict[str, frappe._dict]:
	return {
		c.name: c
		for c in frappe.get_all(
			"Marketplace Channel",
			fields=["name", "allocation_percent", "safety_stock", "connection_state", "sync_enabled"],
		)
	}


def allocation(company: str, item_codes: list[str] | None = None) -> list[dict]:
	"""Per item: enforced available-to-sell and what each channel with an active listing may publish."""
	from factory_ops.inventory.batch_eligibility import available_to_sell

	shares = channel_shares()
	filters = {"active": 1}
	if item_codes:
		filters["item_code"] = ["in", item_codes]
	listings = frappe.get_all(
		"Marketplace Listing", filters=filters, fields=["name", "channel", "item_code", "item_name", "marketplace_sku"]
	)
	by_item: dict[str, list] = {}
	for lst in listings:
		by_item.setdefault(lst.item_code, []).append(lst)
	rows = []
	for item, lists in sorted(by_item.items()):
		available = flt(available_to_sell(company, item))
		channels = []
		for lst in lists:
			share = shares.get(lst.channel)
			pct = flt(share.allocation_percent) if share else 0
			qty = max(0, math.floor(available * pct / 100) - flt(share.safety_stock if share else 0))
			channels.append(
				{
					"listing": lst.name,
					"channel": lst.channel,
					"marketplace_sku": lst.marketplace_sku,
					"share_percent": pct,
					"publish_qty": qty,
					"connection_state": share.connection_state if share else None,
				}
			)
		rows.append(
			{
				"item_code": item,
				"item_name": lists[0].item_name,
				"available_to_sell": available,
				"published_total": sum(c["publish_qty"] for c in channels),
				"kept_back": available - sum(c["publish_qty"] for c in channels),
				"channels": channels,
			}
		)
	return rows


def refresh_published_quantities(company: str) -> int:
	"""Store each listing's quantity to publish; queue a push only for Connected channels."""
	n = 0
	for row in allocation(company):
		for c in row["channels"]:
			status = "Queued" if c["connection_state"] == "Connected" else "Not sent"
			frappe.db.set_value(
				"Marketplace Listing", c["listing"], {"published_qty": c["publish_qty"], "sync_status": status}, update_modified=False
			)
			n += 1
	return n


# --------------------------------------------------------------------------- orders
def ingest_order(channel: str, payload: dict, source: str = "Manual entry", is_demo: int = 0) -> tuple[str, bool]:
	"""Record a marketplace order once. Returns (Marketplace Order name, created?)."""
	external_id = str(payload.get("external_order_id") or "").strip()
	if not external_id:
		frappe.throw(_("The marketplace order ID is missing."))
	existing = frappe.db.get_value("Marketplace Order", {"channel": channel, "external_order_id": external_id})
	if existing:
		return existing, False
	doc = frappe.get_doc(
		{
			"doctype": "Marketplace Order",
			"channel": channel,
			"external_order_id": external_id,
			"order_date": payload.get("order_date") or now_datetime(),
			"order_status": payload.get("order_status") or "New",
			"payment_status": payload.get("payment_status") or "Prepaid",
			"customer_name": payload.get("customer_name"),
			"ship_to_city": payload.get("ship_to_city"),
			"ship_to_state": payload.get("ship_to_state"),
			"source": source,
			"is_demo": is_demo,
			"items": payload.get("items") or [],
			"raw_payload": json.dumps(payload, default=str, indent=1),
		}
	)
	doc.insert()
	return doc.name, True


def resolve_items(order) -> list[str]:
	"""Fill each row's ERPNext item from the channel's listing; return the SKUs that have none."""
	unknown = []
	for row in order.items:
		if not row.item_code:
			row.item_code = frappe.db.get_value(
				"Marketplace Listing", {"channel": order.channel, "marketplace_sku": row.marketplace_sku}, "item_code"
			)
		if not row.item_code:
			unknown.append(row.marketplace_sku)
		row.amount = flt(row.qty) * flt(row.rate)
	order.order_total = sum(flt(r.amount) for r in order.items)
	return unknown


def create_sales_order(order_name: str, company: str | None = None) -> str:
	order = frappe.get_doc("Marketplace Order", order_name)
	order.check_permission("write")
	if order.sales_order:
		frappe.throw(_("Sales order {0} already exists for this marketplace order.").format(order.sales_order))
	if order.order_status == "Cancelled":
		frappe.throw(_("The marketplace cancelled this order; no sales order is made."))
	unknown = resolve_items(order)
	if unknown:
		frappe.throw(_("No listing maps these marketplace SKUs to an ERPNext item: {0}.").format(", ".join(unknown)))
	channel = frappe.get_doc("Marketplace Channel", order.channel)
	if not channel.default_customer:
		frappe.throw(_("Set the customer for {0} orders on the channel first.").format(channel.name))
	company = (
		company
		or frappe.defaults.get_user_default("Company")
		or frappe.db.get_single_value("Global Defaults", "default_company")
	)
	if not company:
		frappe.throw(_("Choose the company the sales order is for."))
	try:
		so = frappe.get_doc(
			{
				"doctype": "Sales Order",
				"company": company,
				"customer": channel.default_customer,
				"transaction_date": getdate(order.order_date),
				"delivery_date": add_days(getdate(order.order_date), 3),
				"fo_sales_channel": "D2C Marketplace",
				"fo_external_order_id": f"{order.channel}/{order.external_order_id}",
				"selling_price_list": channel.price_list or None,
				"items": [
					{"item_code": r.item_code, "qty": r.qty, "rate": r.rate, "warehouse": dispatch_warehouse(channel, r.item_code, company)}
					for r in order.items
				],
			}
		)
		so.insert()
	except frappe.ValidationError as e:
		order.db_set({"import_status": "Failed", "import_error": str(e)[:1000]})
		raise
	order.db_set({"sales_order": so.name, "import_status": "Sales order created", "import_error": None})
	return so.name


def dispatch_warehouse(channel, item_code: str, company: str) -> str | None:
	"""Where a marketplace order ships from: the channel's store, else the release destination of the
	item's default (QC Hold) store, e.g. FG-QC-HOLD -> FG-STORES. Never a QC Hold store: the QC guard
	would refuse it anyway."""
	if channel.dispatch_warehouse:
		return channel.dispatch_warehouse
	default = frappe.db.get_value("Item Default", {"parent": item_code, "company": company}, "default_warehouse")
	if not default:
		group = frappe.db.get_value("Item", item_code, "item_group")
		default = frappe.db.get_value("Item Default", {"parent": group, "company": company}, "default_warehouse")
	if default:
		return frappe.db.get_value("Warehouse", default, "fo_release_warehouse") or default
	return None


# --------------------------------------------------------------------------- synchronisation
def idempotency_key(channel: str, operation: str, payload) -> str:
	digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:24]
	return f"{channel}:{operation}:{digest}"


def run_operation(channel_name: str, operation: str, payload=None, log_name: str | None = None) -> str:
	"""Run one channel operation with a log row; a retry reuses the same log row."""
	channel = frappe.get_doc("Marketplace Channel", channel_name)
	key = idempotency_key(channel_name, operation, payload)
	if log_name:
		log = frappe.get_doc("Marketplace Sync Log", log_name)
	else:
		done = frappe.db.get_value("Marketplace Sync Log", {"idempotency_key": key}, ["name", "status"], as_dict=True)
		if done and done.status in ("Succeeded", "Running", "Queued"):
			return done.name  # the same request was already made: never send it twice
		if done:
			log = frappe.get_doc("Marketplace Sync Log", done.name)
		else:
			log = frappe.get_doc(
				{
					"doctype": "Marketplace Sync Log",
					"channel": channel_name,
					"operation": operation,
					"idempotency_key": key,
					"payload": json.dumps(payload, default=str) if payload is not None else None,
				}
			).insert(ignore_permissions=True)
	log.attempts = (log.attempts or 0) + 1
	log.started_at = now_datetime()
	log.status = "Running"
	log.save(ignore_permissions=True)
	if log_name and payload is None and log.payload:
		payload = json.loads(log.payload)  # a retry sends exactly what the first attempt sent
	connector = connector_for(channel)
	try:
		if operation == "Connection test":
			result = connector.test_connection()
		elif operation == "Inventory push":
			result = connector.push_inventory(payload or [])
		elif operation == "Order pull":
			result = connector.pull_orders()
		else:
			raise ConnectorError(f"{operation} is not supported")
		log.update({"status": "Succeeded", "message": str(result)[:1000], "error": None, "next_retry_at": None})
		frappe.db.set_value("Marketplace Channel", channel_name, {"last_success_at": now_datetime(), "last_error": None})
	except NotConnected as e:
		log.update({"status": "Skipped", "message": str(e), "next_retry_at": None})
	except RateLimited as e:
		retry = log.attempts < MAX_ATTEMPTS
		delay = max(e.retry_after, 60 * 2 ** (log.attempts - 1))
		log.update(
			{
				"status": "Failed",
				"error": str(e),
				"next_retry_at": add_to_date(now_datetime(), seconds=delay) if retry else None,
				"message": f"Rate limited; {'retry ' + str(log.attempts + 1) + ' scheduled' if retry else 'gave up after ' + str(log.attempts) + ' attempts'}",
			}
		)
		frappe.db.set_value("Marketplace Channel", channel_name, "last_error", str(e)[:500])
	except ConnectorError as e:
		log.update({"status": "Failed", "error": str(e), "next_retry_at": None})
		frappe.db.set_value("Marketplace Channel", channel_name, "last_error", str(e)[:500])
	log.finished_at = now_datetime()
	log.save(ignore_permissions=True)
	return log.name


def retry_due_operations():
	"""Scheduled: retry rate-limited operations whose back-off has passed."""
	for log in frappe.get_all(
		"Marketplace Sync Log",
		filters={"status": "Failed", "next_retry_at": ["<=", now_datetime()], "attempts": ["<", MAX_ATTEMPTS]},
		fields=["name", "channel", "operation"],
	):
		run_operation(log.channel, log.operation, log_name=log.name)


def push_inventory_for_connected_channels():
	"""Scheduled: queue an inventory push per Connected, sync-enabled channel. Today none is Connected."""
	company = frappe.db.get_single_value("Global Defaults", "default_company")
	if not company:
		return
	refresh_published_quantities(company)
	for name in frappe.get_all("Marketplace Channel", filters={"connection_state": "Connected", "sync_enabled": 1}, pluck="name"):
		rows = frappe.get_all(
			"Marketplace Listing", filters={"channel": name, "active": 1}, fields=["marketplace_sku", "published_qty"]
		)
		frappe.enqueue(run_operation, queue="long", channel_name=name, operation="Inventory push", payload=rows)
