"""Marketplaces: honest connector states, stock shared without double counting, orders once,
orders -> sales orders, sync idempotency / retry, signed webhooks, returns, settlements."""

import hashlib
import hmac
import json
from unittest import mock

import frappe
from frappe.utils import add_to_date, now_datetime, nowdate
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from factory_ops.marketplace import service
from factory_ops.marketplace.connectors import Connector, RateLimited
from factory_ops.marketplace.setup import ensure_marketplace_channels
from factory_ops.tests.utils import FactoryOpsTestCase, ensure_user

MKT = "fo-marketplace@example.com"
OUTSIDER = "fo-mkt-outsider@example.com"
SOAP = "_FO Mkt Soap 75 g"
CUSTOMER = "_FO Marketplace Buyers"


class TestMarketplace(FactoryOpsTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_marketplace_channels()
		ensure_user(MKT, ["Marketplace Manager", "Marketplace User", "Sales User"])
		ensure_user(OUTSIDER, ["Stock User"])
		if not frappe.db.exists("Item", SOAP):
			frappe.get_doc(
				{"doctype": "Item", "item_code": SOAP, "item_name": SOAP, "item_group": "_Test Item Group", "stock_uom": "Nos",
				 "is_stock_item": 1, "is_sales_item": 1, "valuation_rate": 40}
			).insert(ignore_permissions=True)  # fmt: skip
		if not frappe.db.exists("Customer", CUSTOMER):
			frappe.get_doc({"doctype": "Customer", "customer_name": CUSTOMER, "customer_group": "_Test Customer Group", "territory": "_Test Territory"}).insert(
				ignore_permissions=True
			)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		for name in ("Amazon", "Flipkart", "Myntra", "Nykaa"):
			frappe.db.set_value("Marketplace Channel", name, {"allocation_percent": 0, "safety_stock": 0, "default_customer": CUSTOMER})

	def share(self, **pcts):
		for name, pct in pcts.items():
			doc = frappe.get_doc("Marketplace Channel", name)
			doc.allocation_percent = pct
			doc.save()

	def listing(self, channel, item=SOAP, sku=None):
		return frappe.get_doc(
			{"doctype": "Marketplace Listing", "channel": channel, "item_code": item, "marketplace_sku": sku or f"{channel[:3].upper()}-{frappe.generate_hash(length=6)}"}
		).insert()

	def stock(self, warehouse_key, qty):
		se = frappe.get_doc(
			{"doctype": "Stock Entry", "purpose": "Material Receipt", "stock_entry_type": "Material Receipt", "company": self.company,
			 "items": [{"item_code": SOAP, "qty": qty, "t_warehouse": self.wh[warehouse_key], "basic_rate": 40}]}
		).insert()  # fmt: skip
		se.submit()

	# -- connectors ----------------------------------------------------------------------------
	def test_channels_never_claim_a_connection(self):
		states = {c.name: c.connection_state for c in frappe.get_all("Marketplace Channel", fields=["name", "connection_state"])}
		self.assertEqual(states["Amazon"], "Not configured")
		self.assertEqual(states["Myntra"], "Access required")
		amazon = frappe.get_doc("Marketplace Channel", "Amazon")
		amazon.update({"seller_id": "A1TEST", "client_id": "amzn1.app.test"})
		amazon.save()
		self.assertEqual(amazon.connection_state, "Awaiting credentials")
		amazon.update({"marketplace_region": "A21TJRUUN4KGV", "client_secret": "s", "refresh_token": "r"})
		amazon.save()
		self.assertEqual(amazon.connection_state, "Not implemented")  # complete credentials are not a connection
		log = frappe.get_doc("Marketplace Sync Log", service.run_operation("Amazon", "Connection test", {"t": 1}))
		self.assertEqual(log.status, "Skipped")
		self.assertIn("official API documentation", log.message)
		self.assertNotIn("Connected", frappe.get_all("Marketplace Channel", pluck="connection_state"))

	# -- stock -----------------------------------------------------------------------------------
	def test_shares_may_not_add_up_to_more_than_all_stock(self):
		self.share(Amazon=50, Flipkart=40)
		with self.assertRaisesRegex(frappe.ValidationError, "may not exceed 100"):
			self.share(Myntra=20)

	def test_published_quantities_come_from_available_to_sell_and_never_double_count(self):
		from factory_ops.inventory.batch_eligibility import available_to_sell

		self.stock("FG-STORES", 101)
		self.share(Amazon=40, Flipkart=30, Nykaa=20)
		for ch in ("Amazon", "Flipkart", "Nykaa"):
			self.listing(ch)
		frappe.db.set_value("Marketplace Channel", "Nykaa", "safety_stock", 5)
		available = available_to_sell(self.company, SOAP)
		row = next(r for r in service.allocation(self.company, [SOAP]) if r["item_code"] == SOAP)
		self.assertEqual(row["available_to_sell"], available)
		by = {c["channel"]: c["publish_qty"] for c in row["channels"]}
		self.assertEqual(by, {"Amazon": int(available * 0.4), "Flipkart": int(available * 0.3), "Nykaa": int(available * 0.2) - 5})
		self.assertLessEqual(row["published_total"], available)
		self.assertEqual(row["kept_back"], available - row["published_total"])

	def test_qc_hold_stock_is_never_published(self):
		self.share(Amazon=100)
		self.listing("Amazon")
		before = next(r for r in service.allocation(self.company, [SOAP]))["available_to_sell"]
		self.stock("FG-QC-HOLD", 500)
		after = next(r for r in service.allocation(self.company, [SOAP]))
		self.assertEqual(after["available_to_sell"], before)
		self.assertEqual(after["channels"][0]["publish_qty"], int(before))

	def test_one_item_on_every_channel_but_never_twice_on_one(self):
		for ch in ("Amazon", "Flipkart", "Myntra", "Nykaa"):
			self.listing(ch)
		self.assertEqual(frappe.db.count("Marketplace Listing", {"item_code": SOAP}), 4)
		with self.assertRaisesRegex(frappe.ValidationError, "already has a listing"):
			self.listing("Amazon")

	# -- orders ----------------------------------------------------------------------------------
	def payload(self, ext, sku, qty=2):
		return {"external_order_id": ext, "order_date": f"{nowdate()} 10:00:00", "items": [{"marketplace_sku": sku, "qty": qty, "rate": 199}]}

	def test_an_order_is_recorded_once(self):
		lst = self.listing("Flipkart")
		ext = f"OD{frappe.generate_hash(length=8)}"
		first, created = service.ingest_order("Flipkart", self.payload(ext, lst.marketplace_sku))
		again, created_again = service.ingest_order("Flipkart", self.payload(ext, lst.marketplace_sku))
		self.assertEqual((first, created, created_again), (again, True, False))
		self.assertEqual(frappe.db.count("Marketplace Order", {"external_order_id": ext}), 1)
		with self.assertRaisesRegex(frappe.ValidationError, "already recorded"):
			frappe.get_doc({"doctype": "Marketplace Order", "channel": "Flipkart", "external_order_id": ext, "order_date": now_datetime(),
				"items": [{"marketplace_sku": lst.marketplace_sku, "qty": 1}]}).insert()  # fmt: skip

	def test_order_becomes_a_marketplace_sales_order(self):
		lst = self.listing("Amazon")
		name, _ = service.ingest_order("Amazon", self.payload("408-0000001-TEST", lst.marketplace_sku, 3))
		order = frappe.get_doc("Marketplace Order", name)
		self.assertEqual((order.items[0].item_code, order.order_total), (SOAP, 597))
		frappe.set_user(MKT)
		so_name = frappe.get_doc("Marketplace Order", name).make_sales_order(self.company)
		so = frappe.get_doc("Sales Order", so_name)
		self.assertEqual((so.fo_sales_channel, so.fo_external_order_id, so.customer, so.items[0].qty), ("D2C Marketplace", "Amazon/408-0000001-TEST", CUSTOMER, 3))
		with self.assertRaisesRegex(frappe.ValidationError, "already exists"):
			frappe.get_doc("Marketplace Order", name).make_sales_order(self.company)

	def test_a_qc_controlled_item_ships_from_its_released_store_not_qc_hold(self):
		serum = "_FO Mkt Serum 30 mL"
		if not frappe.db.exists("Item", serum):
			frappe.get_doc({"doctype": "Item", "item_code": serum, "item_name": serum, "item_group": "FG", "stock_uom": "Nos",
				"is_stock_item": 1, "is_sales_item": 1, "has_expiry_date": 1, "shelf_life_in_days": 365}).insert()  # fmt: skip
		lst = self.listing("Flipkart", item=serum)
		name, _ = service.ingest_order("Flipkart", self.payload("OD-SERUM-1", lst.marketplace_sku))
		so = frappe.get_doc("Sales Order", service.create_sales_order(name, self.company))
		self.assertEqual(so.items[0].warehouse, self.wh["FG-STORES"])

	def test_unknown_sku_and_cancelled_orders_make_no_sales_order(self):
		name, _ = service.ingest_order("Nykaa", self.payload("NYK-UNKNOWN-1", "NO-SUCH-SKU"))
		with self.assertRaisesRegex(frappe.ValidationError, "No listing maps"):
			service.create_sales_order(name, self.company)
		lst = self.listing("Nykaa")
		p = self.payload("NYK-CANCEL-1", lst.marketplace_sku)
		p["order_status"] = "Cancelled"
		name, _ = service.ingest_order("Nykaa", p)
		with self.assertRaisesRegex(frappe.ValidationError, "cancelled"):
			service.create_sales_order(name, self.company)

	def test_demo_orders_must_say_so(self):
		lst = self.listing("Myntra")
		with self.assertRaisesRegex(frappe.ValidationError, "DEMO"):
			service.ingest_order("Myntra", self.payload("MYN-1001", lst.marketplace_sku), is_demo=1)
		service.ingest_order("Myntra", self.payload("DEMO-MYN-1001", lst.marketplace_sku), is_demo=1)

	# -- synchronisation ---------------------------------------------------------------------------
	def test_sync_is_idempotent_and_rate_limits_are_retried_with_back_off(self):
		calls = []

		class Flaky(Connector):
			provider = "Amazon SP-API"

			def push_inventory(self, rows):
				calls.append(rows)
				if len(calls) == 1:
					raise RateLimited("429 Too Many Requests", retry_after=1)
				return len(rows)

		payload = [{"marketplace_sku": "AMZ-1", "published_qty": 4}]
		with mock.patch("factory_ops.marketplace.service.connector_for", lambda ch: Flaky(ch)):
			name = service.run_operation("Amazon", "Inventory push", payload)
			log = frappe.get_doc("Marketplace Sync Log", name)
			self.assertEqual((log.status, log.attempts), ("Failed", 1))
			self.assertIsNotNone(log.next_retry_at)
			frappe.db.set_value("Marketplace Sync Log", name, "next_retry_at", add_to_date(now_datetime(), seconds=-1))
			service.retry_due_operations()
			log.reload()
			self.assertEqual((log.status, log.attempts, log.message), ("Succeeded", 2, "1"))
			again = service.run_operation("Amazon", "Inventory push", payload)
		self.assertEqual(again, name)  # the same push is never sent twice
		self.assertEqual(len(calls), 2)

	def test_webhook_needs_a_secret_and_a_valid_signature(self):
		from factory_ops.api.marketplace import webhook

		lst = self.listing("Flipkart")
		body = json.dumps({"order": self.payload("OD-WEBHOOK-1", lst.marketplace_sku)}).encode()

		def call(signature):
			env = EnvironBuilder(method="POST", data=body, headers={"X-Signature": signature}).get_environ()
			frappe.local.request = Request(env)
			frappe.local.request_ip = "127.0.0.1"
			return webhook("Flipkart")

		with self.assertRaisesRegex(frappe.PermissionError, "not configured"):
			call("x")
		ch = frappe.get_doc("Marketplace Channel", "Flipkart")
		ch.webhook_secret = "test-secret"
		ch.save()
		with self.assertRaises(frappe.AuthenticationError):
			call("bad")
		good = hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
		self.assertEqual(call(good)["created"], True)
		self.assertEqual(call(good)["created"], False)  # redelivery is harmless

	# -- returns, settlements, permissions ---------------------------------------------------------
	def test_returns_go_into_quarantine(self):
		lst = self.listing("Amazon")
		name, _ = service.ingest_order("Amazon", self.payload("408-RET-1", lst.marketplace_sku))
		ret = frappe.get_doc({"doctype": "Marketplace Return", "channel": "Amazon", "marketplace_order": name, "external_return_id": "R-1",
			"reason": "Damaged in transit", "received_into": self.wh["FG-STORES"]})  # fmt: skip
		with self.assertRaisesRegex(frappe.ValidationError, "QC Hold"):
			ret.insert()
		ret.received_into = self.wh["RETURNS-QC-HOLD"]
		ret.insert()

	def test_settlement_reconciliation_reports_a_difference(self):
		s = frappe.get_doc({"doctype": "Marketplace Settlement", "channel": "Flipkart", "settlement_id": "NEFT-TEST-1", "period_from": nowdate(),
			"period_to": nowdate(), "gross_sales": 1000, "commission": 120, "fees": 30})  # fmt: skip
		s.insert()
		self.assertEqual(s.net_payout, 850)
		self.assertEqual(s.reconcile(), "Difference")
		self.assertEqual(s.difference, 1000 - s.erp_invoiced)

	def test_only_marketplace_roles_see_the_overview(self):
		from factory_ops.api.marketplace import overview

		frappe.set_user(OUTSIDER)
		with self.assertRaises(frappe.PermissionError):
			overview()
		frappe.set_user(MKT)
		self.assertEqual({c.name for c in overview()["channels"]}, {"Amazon", "Flipkart", "Myntra", "Nykaa"})
