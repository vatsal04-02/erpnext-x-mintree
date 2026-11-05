"""Milestone 5: sales, dispatch and accounts on standard ERPNext, with factory_ops controls.

Everything runs on a perpetual-inventory company so each step posts real GL entries.

ERPNext v16 reserves stock for a Pick List *from the Pick List* (a Sales Order that already holds
reserved stock cannot be picked). The main flow is therefore

    QC-released FG -> Sales Order -> FEFO Pick List -> reservation of the picked batches ->
    Delivery Note (+ Packing Slip, dispatch details) -> Sales Invoice -> Payment Entry

and the alternative without a Pick List, Sales Order -> reservation -> Delivery Note, is tested too.

- ``TestSalesControls``: customers, channels and duplicate D2C orders, available-to-sell,
  reservation of sellable stock only, QC Hold / Rejected / expired / short-dated refusals,
  shelf-life segregation, FEFO, insufficient stock, credit limits, one stock path, dispatch
  details, permissions, dashboard and reports.
- ``TestSalesE2E``: the full flow with ledger and GL checks, duplicate submissions, cancellations.
"""

import frappe
import frappe.client
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
from erpnext.accounts.report.accounts_receivable.accounts_receivable import execute as ar_report
from erpnext.selling.doctype.sales_order.sales_order import create_pick_list
from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note as so_to_dn
from erpnext.stock.doctype.delivery_note.delivery_note import make_packing_slip
from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice as dn_to_si
from erpnext.stock.doctype.pick_list.pick_list import create_delivery, create_delivery_note
from frappe.utils import add_days, flt, getdate, nowdate

from factory_ops.inventory.guards import QCGuardError
from factory_ops.inventory.stock_queries import batch_qty_by_warehouse
from factory_ops.sales_ops.available_to_sell import available_to_sell, stock_positions
from factory_ops.sales_ops.controls import (
	DispatchDetailsError,
	DuplicateOrderError,
	InsufficientSellableStockError,
	SalesStockPathError,
)
from factory_ops.sales_ops.report.available_to_sell.available_to_sell import execute as ats_report
from factory_ops.sales_ops.report.dispatch_register.dispatch_register import execute as dispatch_report
from factory_ops.sales_ops.shelf_life import REMARK, segregate_undispatchable_stock
from factory_ops.setup.dashboards import DASHBOARD, NUMBER_CARDS
from factory_ops.tests.utils import (
	USER_ACCOUNTS,
	USER_DISPATCH,
	USER_QC,
	USER_SALES,
	USER_SALES_MANAGER,
	USER_STORES,
	FactoryOpsTestCase,
)

FG = "SAMPLE-FG-0001"
DISTRIBUTOR = "SAMPLE Customer - Distributor"  # credit limit 5,00,000
RETAILER = "SAMPLE Customer - Retailer"  # credit limit 25,000
D2C = "SAMPLE D2C - Website Orders"  # no credit limit
RATE = 250
AR_RANGE = "30, 60, 90, 120"


def sle_rows(voucher_no: str) -> list[tuple[str, float]]:
	rows = frappe.get_all(
		"Stock Ledger Entry",
		filters={"voucher_no": voucher_no, "is_cancelled": 0},
		fields=["warehouse", "actual_qty"],
	)
	return sorted((r.warehouse, flt(r.actual_qty, 3)) for r in rows)


def gl_net(voucher_no: str, condition: str) -> float:
	return flt(
		frappe.db.sql(
			f"""select coalesce(sum(gle.debit - gle.credit), 0) from `tabGL Entry` gle
			inner join `tabAccount` acc on acc.name = gle.account
			where gle.voucher_no = %s and gle.is_cancelled = 0 and {condition}""",
			voucher_no,
		)[0][0],
		2,
	)


def gl_balanced(voucher_no: str) -> bool:
	d, c = frappe.db.sql(
		"select coalesce(sum(debit),0), coalesce(sum(credit),0) from `tabGL Entry`"
		" where voucher_no=%s and is_cancelled=0",
		voucher_no,
	)[0]
	return flt(d, 2) == flt(c, 2)


class SalesTestCase(FactoryOpsTestCase):
	company = "_FO Perpetual Company"
	company_abbr = "_FOPC"

	def setUp(self):
		super().setUp()
		self.fg_hold, self.fg_stores = self.wh["FG-QC-HOLD"], self.wh["FG-STORES"]
		self.nfd = self.wh["NOT-FOR-DISPATCH"]
		# selling rules as on the real site (rolled back after each test)
		frappe.db.set_single_value("Selling Settings", "so_required", "Yes")
		frappe.db.set_single_value("Selling Settings", "dn_required", "Yes")
		frappe.db.set_single_value("Accounts Settings", "credit_controller", "Sales Manager")
		frappe.db.set_single_value("Factory Ops Settings", "require_dispatch_details", 1)
		frappe.db.set_single_value("Factory Ops Settings", "allow_sales_invoice_stock_update", 0)
		self.set_min_shelf_life(0)

	def set_min_shelf_life(self, days: int):
		frappe.db.set_single_value("Factory Ops Settings", "min_shelf_life_days_for_dispatch", days)

	# ---- stock --------------------------------------------------------------------
	def released_fg(self, qty: float, expiry_days: int = 600) -> str:
		_, batch = self.receive(FG, self.fg_hold, qty, rate=100, expiry_days=expiry_days)
		self.qc_release(batch, "Release", item_code=FG, source=self.fg_hold)
		return batch

	def age_batch(self, batch: str, days_left: int):
		"""Simulate the passage of time: ERPNext refuses to receive an already expired batch."""
		frappe.db.set_value("Batch", batch, "expiry_date", add_days(nowdate(), days_left))

	def ats(self) -> float:
		return available_to_sell(self.company, FG, self.fg_stores)

	# ---- documents ----------------------------------------------------------------
	def sales_order(
		self,
		customer=DISTRIBUTOR,
		qty=10,
		channel="B2B",
		ref=None,
		user=USER_SALES,
		submit=True,
		warehouse=None,
	):
		with self.set_user(user):
			so = frappe.get_doc(
				{
					"doctype": "Sales Order",
					"company": self.company,
					"customer": customer,
					"fo_sales_channel": channel,
					"fo_external_order_id": ref,
					"delivery_date": add_days(nowdate(), 7),
					"set_warehouse": warehouse or self.fg_stores,
					"items": [
						{"item_code": FG, "qty": qty, "rate": RATE, "warehouse": warehouse or self.fg_stores}
					],
				}
			).insert()
			if submit:
				so.submit()
		return so

	def reservations(self, so) -> list:
		return frappe.get_all(
			"Stock Reservation Entry",
			filters={"voucher_no": so.name, "docstatus": 1},
			fields=["name", "warehouse", "reserved_qty", "delivered_qty", "status", "from_voucher_type"],
		)

	def reserve_order(self, so, user=USER_SALES):
		"""Reservation from the Sales Order (no Pick List)."""
		with self.set_user(user):
			frappe.get_doc("Sales Order", so.name).create_stock_reservation_entries()
		return self.reservations(so)

	def pick(self, so, user=USER_STORES):
		with self.set_user(user):
			pl = create_pick_list(so.name)
			pl.insert()
			pl.submit()
		return pl

	def reserve_picked(self, pl, user=USER_STORES):
		"""ERPNext v16: reserve exactly the picked batches, from the Pick List."""
		with self.set_user(user):
			frappe.get_doc("Pick List", pl.name).create_stock_reservation_entries()

	def _dispatch_details(self, dn):
		dn.transporter_name = "SAMPLE Transport Co"
		dn.lr_no = "SAMPLE-LR-0001"
		dn.lr_date = nowdate()
		dn.vehicle_no = "SAMPLE-MH-00"

	def deliver(self, pl, user=USER_DISPATCH, dispatch=True, packing=True):
		with self.set_user(user):
			dn = create_delivery_note(pl.name)
			if dispatch:
				self._dispatch_details(dn)
			dn.save()
			if packing:
				ps = make_packing_slip(dn.name)
				for row in ps.items:
					row.net_weight = 0.05
				ps.insert()
				ps.submit()
			dn.reload()
			dn.submit()
		return dn

	def deliver_order(self, so, user=USER_DISPATCH):
		"""Delivery Note straight from the Sales Order (batches chosen by FEFO on submit)."""
		with self.set_user(user):
			dn = so_to_dn(so.name)
			self._dispatch_details(dn)
			dn.insert()
			dn.submit()
		return dn

	def invoice(self, dn, user=USER_ACCOUNTS):
		with self.set_user(user):
			si = dn_to_si(dn.name)
			si.insert()
			si.submit()
		return si

	def pay(self, si, amount=None, user=USER_ACCOUNTS):
		with self.set_user(user):
			pe = get_payment_entry("Sales Invoice", si.name)
			pe.reference_no = "SAMPLE-UTR-0001"
			pe.reference_date = nowdate()
			if amount is not None:
				pe.paid_amount = pe.received_amount = amount
				pe.references[0].allocated_amount = amount
			pe.insert()
			pe.submit()
		return pe

	def assert_refused(self, exc, fn, *args, **kwargs):
		"""fn must raise exc; roll back what it wrote, as a failed request would."""
		frappe.db.savepoint("fo_refused")
		with self.assertRaises(exc) as ctx:
			try:
				fn(*args, **kwargs)
			except Exception:
				frappe.db.rollback(save_point="fo_refused")
				raise
		return ctx.exception

	def assert_stock_matches_gl(self):
		sle_value = frappe.db.sql(
			"select coalesce(sum(stock_value_difference),0) from `tabStock Ledger Entry`"
			" where company=%s and is_cancelled=0",
			self.company,
		)[0][0]
		gl_value = frappe.db.sql(
			"""select coalesce(sum(gle.debit - gle.credit),0) from `tabGL Entry` gle
			inner join `tabAccount` acc on acc.name = gle.account
			where gle.company=%s and gle.is_cancelled=0 and acc.account_type='Stock'""",
			self.company,
		)[0][0]
		self.assertAlmostEqual(flt(sle_value, 2), flt(gl_value, 2), places=2)

	def customer_ledger(self, customer: str) -> float:
		return flt(
			frappe.db.sql(
				"""select coalesce(sum(debit - credit),0) from `tabGL Entry`
				where company=%s and party_type='Customer' and party=%s and is_cancelled=0""",
				(self.company, customer),
			)[0][0],
			2,
		)

	def customer_outstanding(self, customer: str) -> float:
		return flt(
			frappe.db.sql(
				"select coalesce(sum(outstanding_amount),0) from `tabSales Invoice`"
				" where company=%s and customer=%s and docstatus=1",
				(self.company, customer),
			)[0][0],
			2,
		)

	def ar_outstanding(self, customer: str, voucher_no: str) -> float:
		_, rows, *_ = ar_report(
			{
				"company": self.company,
				"report_date": nowdate(),
				"party_type": "Customer",
				"party": [customer],
				"range": AR_RANGE,
			}
		)
		return flt(sum(flt(r.get("outstanding")) for r in rows if r.get("voucher_no") == voucher_no), 2)


class TestSalesControls(SalesTestCase):
	# ---- customers and channels ------------------------------------------------------
	def test_sample_customers_groups_and_credit_limits(self):
		self.assertEqual(frappe.db.get_value("Customer", DISTRIBUTOR, "customer_group"), "Distributors")
		self.assertEqual(frappe.db.get_value("Customer", RETAILER, "customer_group"), "B2B Customers")
		self.assertEqual(frappe.db.get_value("Customer", D2C, "customer_group"), "D2C Consumers")
		limits = {
			c: flt(
				frappe.db.get_value(
					"Customer Credit Limit", {"parent": c, "company": self.company}, "credit_limit"
				)
			)
			for c in (DISTRIBUTOR, RETAILER, D2C)
		}
		self.assertEqual(limits, {DISTRIBUTOR: 500_000, RETAILER: 25_000, D2C: 0})

	def test_d2c_orders_need_a_unique_external_order_id(self):
		self.assert_refused(DuplicateOrderError, self.sales_order, D2C, 1, "D2C Website")
		first = self.sales_order(D2C, 1, "D2C Website", "WEB-1001")
		self.assertEqual(first.docstatus, 1)
		# the same number again (e.g. a re-imported file) is refused, also with stray spaces
		self.assert_refused(DuplicateOrderError, self.sales_order, D2C, 1, "D2C Website", "WEB-1001")
		self.assert_refused(
			DuplicateOrderError, self.sales_order, D2C, 1, "D2C Website", " WEB-1001 ", submit=False
		)
		# the same number on another channel is a different order
		self.assertEqual(self.sales_order(D2C, 1, "D2C Marketplace", "WEB-1001").docstatus, 1)
		# a cancelled order frees its number
		with self.set_user(USER_SALES):
			frappe.get_doc("Sales Order", first.name).cancel()
		self.assertEqual(self.sales_order(D2C, 1, "D2C Website", "WEB-1001").docstatus, 1)
		# B2B orders default the channel and need no external number
		b2b = self.sales_order(DISTRIBUTOR, 1, channel=None)
		self.assertEqual((b2b.fo_sales_channel, b2b.fo_external_order_id), ("B2B", None))

	# ---- available to sell, reservations ----------------------------------------------
	def test_available_to_sell_counts_only_released_in_date_unreserved_stock(self):
		before = stock_positions(self.company, FG).get((FG, self.fg_stores), {})
		base_sellable = flt(before.get("sellable")) if before else 0
		self.released_fg(100, expiry_days=600)
		short = self.released_fg(40, expiry_days=600)
		self.age_batch(short, 60)
		self.receive(FG, self.fg_hold, 30, rate=100, expiry_days=600)  # still on hold
		rejected = self.receive(FG, self.fg_hold, 20, rate=100, expiry_days=600)[1]
		self.qc_release(
			rejected,
			"Reject",
			qty=20,
			item_code=FG,
			source=self.fg_hold,
			qi_status="Rejected",
			reason="SAMPLE",
		)
		self.set_min_shelf_life(90)
		pos = stock_positions(self.company, FG)
		self.assertEqual(flt(pos[(FG, self.fg_stores)]["sellable"]) - base_sellable, 100)
		self.assertGreaterEqual(pos[(FG, self.fg_stores)]["short_dated"], 40)
		self.assertGreaterEqual(pos[(FG, self.fg_hold)]["qc_hold"], 30)
		self.assertGreaterEqual(pos[(FG, self.wh["REJECTED"])]["rejected"], 20)
		ats_before = self.ats()
		segregate_undispatchable_stock(self.company)  # short-dated stock leaves FG-STORES
		self.assertEqual(self.ats(), ats_before)  # it was never counted as available
		so = self.sales_order(DISTRIBUTOR, 60)
		self.reserve_order(so)
		self.assertEqual(flt(ats_before - self.ats()), 60)
		_, rows = ats_report({"company": self.company, "item_code": FG})
		self.assertEqual(len(rows), 1)
		self.assertEqual(flt(rows[0]["available"]), flt(rows[0]["sellable"] - rows[0]["reserved"]))
		self.assertEqual(flt(rows[0]["available"]), self.ats())

	def test_reservation_never_exceeds_available_to_sell(self):
		self.released_fg(30, expiry_days=600)
		available = self.ats()
		so = self.sales_order(DISTRIBUTOR, available + 25)  # an order may exceed stock (backorder)
		self.reserve_order(so)  # ERPNext reserves what it can ...
		reserved = sum(flt(r.reserved_qty) for r in self.reservations(so))
		self.assertEqual(reserved, available)  # ... never more than is available to sell
		self.assertEqual(self.ats(), 0)
		# a hand-made reservation beyond what is available is refused by factory_ops
		so2 = self.sales_order(DISTRIBUTOR, 5)
		sre = frappe.get_doc(
			{
				"doctype": "Stock Reservation Entry",
				"item_code": FG,
				"warehouse": self.fg_stores,
				"voucher_type": "Sales Order",
				"voucher_no": so2.name,
				"voucher_detail_no": so2.items[0].name,
				"company": self.company,
				"reserved_qty": 5,
				"voucher_qty": 5,
				"stock_uom": "Nos",
				"reservation_based_on": "Qty",
			}
		)
		self.assert_refused(frappe.ValidationError, lambda: (sre.insert(), sre.submit()))

	def test_short_dated_batches_are_segregated_and_never_reserved_or_picked(self):
		self.set_min_shelf_life(90)
		self.released_fg(50, expiry_days=600)
		short = self.released_fg(40, expiry_days=600)
		expired = self.released_fg(20, expiry_days=600)
		self.age_batch(short, 60)
		self.age_batch(expired, -3)
		# Milestone 6 (D-034): even before segregation, the reservation pins only eligible batches
		# (ERPNext's FEFO would have pinned the short-dated one first)
		so0 = self.sales_order(DISTRIBUTOR, 30)
		self.reserve_order(so0)
		pinned0 = frappe.db.sql_list(
			"""select sbe.batch_no from `tabSerial and Batch Entry` sbe
			inner join `tabStock Reservation Entry` sre on sre.name = sbe.parent
			where sre.voucher_no = %s and sre.docstatus = 1""",
			so0.name,
		)
		self.assertTrue(pinned0)
		self.assertNotIn(short, pinned0)
		self.assertNotIn(expired, pinned0)
		# more than the eligible free stock (50 - 30 = 20) cannot be reserved
		self.assert_refused(
			InsufficientSellableStockError, self.reserve_order, self.sales_order(DISTRIBUTOR, 25)
		)
		so = self.sales_order(DISTRIBUTOR, 15)
		# segregation (supplementary safeguard) moves short-dated and expired stock out of FG-STORES, with a stock transfer
		entries = segregate_undispatchable_stock(self.company)
		self.assertEqual(len(entries), 1)
		se = frappe.get_doc("Stock Entry", entries[0])
		self.assertEqual(se.remarks, REMARK)
		moved = {r.batch_no: flt(r.qty) for r in se.items}
		self.assertEqual((moved.get(short), moved.get(expired)), (40, 20))
		self.assertTrue(all(r.t_warehouse == self.nfd for r in se.items))
		self.assertEqual(segregate_undispatchable_stock(self.company), [])  # idempotent
		# now FEFO only sees dispatchable batches
		self.reserve_order(so)
		pinned = frappe.db.sql_list(
			"""select sbe.batch_no from `tabSerial and Batch Entry` sbe
			inner join `tabStock Reservation Entry` sre on sre.name = sbe.parent
			where sre.voucher_no = %s and sre.docstatus = 1""",
			so.name,
		)
		self.assertTrue(pinned)
		self.assertNotIn(short, pinned)
		self.assertNotIn(expired, pinned)
		pos = stock_positions(self.company, FG)[(FG, self.nfd)]
		self.assertGreaterEqual(pos["short_dated"], 40)
		self.assertGreaterEqual(pos["expired"], 20)

	def test_reserved_short_dated_stock_is_not_moved_but_cannot_be_dispatched(self):
		batch = self.released_fg(20, expiry_days=600)
		so = self.sales_order(DISTRIBUTOR, 20)
		self.reserve_order(so)
		self.age_batch(batch, 30)
		self.set_min_shelf_life(90)
		segregate_undispatchable_stock(self.company)
		self.assertEqual(batch_qty_by_warehouse(batch).get(self.fg_stores), 20)  # left in place
		err = self.assert_refused(QCGuardError, self.deliver_order, so)  # still cannot be shipped
		self.assertIn("reserved for this order", str(err))

	def test_qc_hold_and_rejected_stock_cannot_be_sold_reserved_or_dispatched(self):
		_, held = self.receive(FG, self.fg_hold, 30, rate=100, expiry_days=600)
		self.assert_refused(QCGuardError, self.sales_order, DISTRIBUTOR, 5, warehouse=self.fg_hold)
		so = self.sales_order(DISTRIBUTOR, 5)
		sre = frappe.get_doc(
			{
				"doctype": "Stock Reservation Entry",
				"item_code": FG,
				"warehouse": self.fg_hold,
				"voucher_type": "Sales Order",
				"voucher_no": so.name,
				"voucher_detail_no": so.items[0].name,
				"company": self.company,
				"reserved_qty": 5,
				"voucher_qty": 5,
				"available_qty": 30,
				"stock_uom": "Nos",
			}
		)
		self.assert_refused(QCGuardError, sre.insert)
		with self.set_user(USER_STORES):
			dn = so_to_dn(so.name)
			self._dispatch_details(dn)
			dn.items[0].warehouse = self.fg_hold
			dn.items[0].use_serial_batch_fields = 1
			dn.items[0].batch_no = held
			self.assert_refused(QCGuardError, dn.insert)
		rejected = self.receive(FG, self.fg_hold, 20, rate=100, expiry_days=600)[1]
		self.qc_release(
			rejected,
			"Reject",
			qty=20,
			item_code=FG,
			source=self.fg_hold,
			qi_status="Rejected",
			reason="SAMPLE",
		)
		with self.set_user(USER_STORES):
			dn = so_to_dn(so.name)
			self._dispatch_details(dn)
			dn.items[0].warehouse = self.wh["REJECTED"]
			dn.items[0].use_serial_batch_fields = 1
			dn.items[0].batch_no = rejected
			self.assert_refused(QCGuardError, dn.insert)

	def test_expired_and_short_dated_batches_are_not_dispatched(self):
		expired = self.released_fg(20, expiry_days=600)
		self.age_batch(expired, -3)
		so = self.sales_order(DISTRIBUTOR, 5)
		with self.set_user(USER_STORES):
			dn = so_to_dn(so.name)
			self._dispatch_details(dn)
			dn.items[0].use_serial_batch_fields = 1
			dn.items[0].batch_no = expired
			self.assert_refused(frappe.ValidationError, dn.insert)  # expired: ERPNext and G6
		short = self.released_fg(20, expiry_days=600)
		self.age_batch(short, 60)
		self.set_min_shelf_life(120)
		with self.set_user(USER_STORES):
			dn = so_to_dn(so.name)
			self._dispatch_details(dn)
			dn.items[0].use_serial_batch_fields = 1
			dn.items[0].batch_no = short
			err = self.assert_refused(QCGuardError, dn.insert)  # G6: below minimum shelf life
		self.assertIn("shelf life", str(err))

	def test_a_pick_list_is_not_a_stock_movement(self):
		"""Regression (D-031): ERPNext submits batch bundles for Pick Lists that move no stock;
		factory_ops balances must not count them."""
		from factory_ops.inventory.report.stock_by_qc_status.stock_by_qc_status import execute as qc_stock
		from factory_ops.quality.report.qc_queue.qc_queue import execute as qc_queue

		batch = self.released_fg(40)
		before_qty, before_ats = batch_qty_by_warehouse(batch), self.ats()
		pl = self.pick(self.sales_order(DISTRIBUTOR, 25))
		self.assertEqual(sum(flt(r.qty) for r in pl.locations if r.batch_no == batch), 25)
		self.assertTrue(
			frappe.db.exists(
				"Serial and Batch Bundle",
				{"voucher_type": "Pick List", "voucher_no": pl.name, "docstatus": 1},
			)
		)
		# the pick moves no stock; available to sell drops by exactly the picked 25 (ERPNext v16
		# treats picked stock as allocated, D-034), not by the Pick List's bundle a second time
		self.assertEqual(batch_qty_by_warehouse(batch), before_qty)
		self.assertEqual(flt(before_ats - self.ats()), 25)
		_, rows = qc_stock({"company": self.company, "item_code": FG})
		self.assertEqual(sum(flt(r.qty) for r in rows if r.batch_no == batch), 40)
		self.assertIsNotNone(qc_queue({"company": self.company}))

	def test_fefo_pick_skips_expired_and_takes_earliest_expiry_first(self):
		expired = self.released_fg(20, expiry_days=600)
		self.age_batch(expired, -3)
		late = self.released_fg(100, expiry_days=700)
		early = self.released_fg(30, expiry_days=200)
		so = self.sales_order(DISTRIBUTOR, 50)
		pl = self.pick(so)
		picked = [(r.batch_no, flt(r.qty)) for r in pl.locations]
		batches = [b for b, _ in picked]
		self.assertNotIn(expired, batches)
		expiry = {b: getdate(frappe.db.get_value("Batch", b, "expiry_date")) for b in batches}
		self.assertTrue(all(e >= getdate(nowdate()) for e in expiry.values()))
		self.assertEqual(batches, sorted(batches, key=lambda b: expiry[b]))  # earliest expiry first
		self.assertTrue({early, late})

	def test_insufficient_stock_is_refused_and_writes_nothing(self):
		short_by = 50
		so = self.sales_order(DISTRIBUTOR, self.ats() + short_by)
		with self.set_user(USER_STORES):
			dn = so_to_dn(so.name)
			self._dispatch_details(dn)
			dn.insert()
		before = frappe.db.count("Stock Ledger Entry", {"item_code": FG, "is_cancelled": 0})
		with self.set_user(USER_STORES):
			self.assert_refused(frappe.ValidationError, frappe.get_doc("Delivery Note", dn.name).submit)
		self.assertEqual(frappe.db.count("Stock Ledger Entry", {"item_code": FG, "is_cancelled": 0}), before)
		self.assertEqual(frappe.db.get_value("Delivery Note", dn.name, "docstatus"), 0)
		self.assertEqual(
			frappe.db.count("Serial and Batch Bundle", {"voucher_no": dn.name, "docstatus": 1}), 0
		)

	# ---- credit limits -----------------------------------------------------------------
	def test_credit_limit_is_enforced_and_only_a_credit_controller_may_override(self):
		so = self.sales_order(RETAILER, 120, submit=False)  # 30,000 > limit 25,000
		with self.set_user(USER_SALES):
			err = self.assert_refused(frappe.ValidationError, frappe.get_doc("Sales Order", so.name).submit)
		self.assertIn("Credit limit", str(err))
		self.assertEqual(frappe.db.get_value("Sales Order", so.name, "docstatus"), 0)
		with self.set_user(USER_SALES_MANAGER):  # Sales Manager = credit controller
			frappe.get_doc("Sales Order", so.name).submit()
		self.assertEqual(frappe.db.get_value("Sales Order", so.name, "docstatus"), 1)
		# within its limit a customer's order goes through as usual
		self.assertEqual(self.sales_order(RETAILER, 10, submit=False).docstatus, 0)
		self.assertEqual(self.sales_order(D2C, 2, "D2C Other", "PHONE-1").docstatus, 1)

	def test_credit_limit_counts_unpaid_invoices(self):
		self.released_fg(100)
		so = self.sales_order(RETAILER, 90)  # 22,500 within 25,000
		dn = self.deliver_order(so)
		self.invoice(dn)  # 22,500 now outstanding
		with self.set_user(USER_SALES):
			err = self.assert_refused(frappe.ValidationError, self.sales_order, RETAILER, 20, user=USER_SALES)
		self.assertIn("Credit limit", str(err))

	# ---- one stock path ----------------------------------------------------------------
	def test_invoice_never_moves_stock_a_second_time(self):
		self.released_fg(50)
		so = self.sales_order(DISTRIBUTOR, 10)
		dn = self.deliver(self.pick(so))
		self.assertEqual(sle_rows(dn.name), [(self.fg_stores, -10)])
		# an invoice made from the Delivery Note may not update stock (ERPNext)
		with self.set_user(USER_ACCOUNTS):
			si = dn_to_si(dn.name)
			si.update_stock = 1
			self.assert_refused(frappe.ValidationError, si.insert)
		# a stock-updating invoice on its own is refused by factory_ops (one stock path)
		frappe.db.set_single_value("Selling Settings", "so_required", "No")
		frappe.db.set_single_value("Selling Settings", "dn_required", "No")
		with self.set_user(USER_ACCOUNTS):
			direct = frappe.get_doc(
				{
					"doctype": "Sales Invoice",
					"company": self.company,
					"customer": DISTRIBUTOR,
					"update_stock": 1,
					"items": [{"item_code": FG, "qty": 1, "rate": RATE, "warehouse": self.fg_stores}],
				}
			)
			self.assert_refused(SalesStockPathError, direct.insert)
		# Pick List -> Sales Invoice (ERPNext sets update_stock) is refused too
		# (as Administrator: the rule is about the document, whoever creates it)
		pl2 = self.pick(self.sales_order(DISTRIBUTOR, 5))
		self.assert_refused(SalesStockPathError, create_delivery, pl2.name, None, "Sales Invoice")
		si = self.invoice(dn)
		self.assertEqual(sle_rows(si.name), [])

		# a second invoice for the same delivery is refused
		def second_invoice():
			again = dn_to_si(dn.name)
			again.insert()
			again.submit()

		with self.set_user(USER_ACCOUNTS):
			self.assert_refused(frappe.ValidationError, second_invoice)
		billed = frappe.db.sql(
			"select count(distinct parent) from `tabSales Invoice Item` where delivery_note=%s and docstatus=1",
			dn.name,
		)[0][0]
		self.assertEqual(billed, 1)

	def test_dispatch_details_are_required(self):
		self.released_fg(20)
		pl = self.pick(self.sales_order(DISTRIBUTOR, 5))
		self.assert_refused(DispatchDetailsError, self.deliver, pl, dispatch=False, packing=False)
		frappe.db.set_single_value("Factory Ops Settings", "require_dispatch_details", 0)
		self.assertEqual(self.deliver(pl, dispatch=False, packing=False).docstatus, 1)

	# ---- permissions, dashboard, reports -------------------------------------------------
	def test_permissions(self):
		self.assertRaises(frappe.PermissionError, self.sales_order, DISTRIBUTOR, 1, user=USER_QC)
		self.released_fg(20)
		si = self.invoice(self.deliver(self.pick(self.sales_order(DISTRIBUTOR, 5))))
		with self.set_user(USER_SALES):  # sales cannot record money received
			self.assertRaises(frappe.PermissionError, get_payment_entry, "Sales Invoice", si.name)
		with self.set_user(USER_STORES):  # stores cannot invoice
			self.assertRaises(frappe.PermissionError, lambda: dn_to_si(si.items[0].delivery_note).insert())

	def test_dashboard_and_reports(self):
		from frappe.desk.doctype.number_card.number_card import get_result

		self.assertTrue(frappe.db.exists("Dashboard", DASHBOARD))
		for label in NUMBER_CARDS:
			card = frappe.get_doc("Number Card", label)
			self.assertIsNotNone(get_result(card.as_dict(), "[]"))
		self.released_fg(20)
		dn = self.deliver(self.pick(self.sales_order(DISTRIBUTOR, 5)))
		_, rows = dispatch_report(
			{"company": self.company, "from_date": add_days(nowdate(), -1), "to_date": nowdate()}
		)
		mine = next(r for r in rows if r.delivery_note == dn.name)
		self.assertEqual(
			(mine.transporter, mine.lr_no, mine.packages, flt(mine.total_qty)),
			("SAMPLE Transport Co", "SAMPLE-LR-0001", 1, 5),
		)
		self.assertTrue(mine.batches)


class TestSalesE2E(SalesTestCase):
	def test_released_stock_to_cash_with_reconciliation(self):
		# 0. QC-released finished goods: two batches; the earlier expiry must go first
		early = self.released_fg(100, expiry_days=300)
		late = self.released_fg(100, expiry_days=700)
		_, held = self.receive(FG, self.fg_hold, 50, rate=100, expiry_days=200)  # not sellable
		ats0 = self.ats()
		ledger0, outstanding0 = self.customer_ledger(DISTRIBUTOR), self.customer_outstanding(DISTRIBUTOR)

		# 1. Sales Order 150 x 250 = 37,500, within the distributor's credit limit
		so = self.sales_order(DISTRIBUTOR, 150)
		self.assertEqual((so.docstatus, flt(so.grand_total), so.fo_sales_channel), (1, 37500, "B2B"))
		self.assertEqual(sle_rows(so.name), [])

		# 2. FEFO Pick List: all 100 of the earlier batch, then 50 of the later; never held stock
		pl = self.pick(so)
		picked = {}
		for r in pl.locations:
			picked[r.batch_no] = picked.get(r.batch_no, 0) + flt(r.qty)
		self.assertEqual(picked, {early: 100, late: 50})
		self.assertNotIn(held, picked)
		self.assertTrue(all(r.warehouse == self.fg_stores for r in pl.locations))

		# 3. Reservation of the picked batches (from the Pick List): nothing moves, ATS drops
		self.reserve_picked(pl)
		sre = self.reservations(so)
		self.assertEqual(sum(flt(r.reserved_qty) for r in sre), 150)
		self.assertTrue(all(r.from_voucher_type == "Pick List" for r in sre))
		self.assertEqual(flt(ats0 - self.ats()), 150)
		for r in sre:
			self.assertEqual(sle_rows(r.name), [])

		# 4. Delivery Note with dispatch details and a Packing Slip: stock leaves once
		dn = self.deliver(pl)
		self.assertEqual(sorted(sle_rows(dn.name)), [(self.fg_stores, -100), (self.fg_stores, -50)])
		self.assertEqual(batch_qty_by_warehouse(early).get(self.fg_stores, 0), 0)
		self.assertEqual(batch_qty_by_warehouse(late)[self.fg_stores], 50)
		self.assertEqual(frappe.db.count("Packing Slip", {"delivery_note": dn.name, "docstatus": 1}), 1)
		self.assertTrue(gl_balanced(dn.name))
		self.assertEqual(gl_net(dn.name, "acc.account_type = 'Stock'"), -15000)  # 150 x 100.00
		self.assertEqual(gl_net(dn.name, "acc.root_type = 'Expense'"), 15000)  # cost of goods sold
		self.assertEqual(flt(frappe.db.get_value("Sales Order", so.name, "per_delivered")), 100)
		self.assertEqual({r.status for r in self.reservations(so)}, {"Delivered"})
		self.assertEqual(flt(ats0 - self.ats()), 150)  # delivered, no longer reserved

		# 5. Sales Invoice from the Delivery Note: no stock movement; receivable 37,500
		si = self.invoice(dn)
		self.assertEqual((si.update_stock, flt(si.grand_total), si.fo_sales_channel), (0, 37500, "B2B"))
		self.assertEqual(sle_rows(si.name), [])
		self.assertTrue(gl_balanced(si.name))
		self.assertEqual(gl_net(si.name, "acc.account_type = 'Receivable'"), 37500)
		self.assertEqual(gl_net(si.name, "acc.root_type = 'Income'"), -37500)
		self.assertEqual(flt(frappe.db.get_value("Delivery Note", dn.name, "per_billed")), 100)
		self.assertEqual(self.customer_outstanding(DISTRIBUTOR) - outstanding0, 37500)
		self.assertEqual(self.ar_outstanding(DISTRIBUTOR, si.name), 37500)

		# 6. Part payment, then the balance
		pe1 = self.pay(si, 20000)
		self.assertEqual(flt(frappe.db.get_value("Sales Invoice", si.name, "outstanding_amount")), 17500)
		self.assertEqual(self.ar_outstanding(DISTRIBUTOR, si.name), 17500)
		pe2 = self.pay(si)
		self.assertEqual(
			frappe.db.get_value("Sales Invoice", si.name, ["outstanding_amount", "status"]), (0.0, "Paid")
		)
		for pe in (pe1, pe2):
			self.assertTrue(gl_balanced(pe.name))
			self.assertEqual(sle_rows(pe.name), [])
		self.assertEqual(gl_net(pe1.name, "acc.account_type = 'Receivable'"), -20000)

		# 7. Reconciliation
		self.assertEqual(self.customer_ledger(DISTRIBUTOR), ledger0)  # receivable cleared
		self.assertEqual(self.customer_outstanding(DISTRIBUTOR), outstanding0)
		self.assertEqual(self.ar_outstanding(DISTRIBUTOR, si.name), 0)
		self.assert_stock_matches_gl()
		_, rows = dispatch_report(
			{"company": self.company, "from_date": nowdate(), "to_date": nowdate(), "customer": DISTRIBUTOR}
		)
		row = next(r for r in rows if r.delivery_note == dn.name)
		self.assertEqual((flt(row.per_billed), row.packages, row.lr_no), (100, 1, "SAMPLE-LR-0001"))

	def test_order_reservation_then_delivery_without_pick_list(self):
		self.released_fg(40, expiry_days=400)
		so = self.sales_order(D2C, 3, "D2C Website", "WEB-E2E-1")
		self.reserve_order(so)
		self.assertEqual(sum(flt(r.reserved_qty) for r in self.reservations(so)), 3)
		dn = self.deliver_order(so)
		self.assertEqual(sle_rows(dn.name), [(self.fg_stores, -3)])
		self.assertEqual(dn.fo_external_order_id, "WEB-E2E-1")
		si = self.invoice(dn)
		self.assertEqual((si.fo_sales_channel, si.fo_external_order_id), ("D2C Website", "WEB-E2E-1"))
		self.pay(si)
		self.assertEqual(flt(frappe.db.get_value("Sales Invoice", si.name, "outstanding_amount")), 0)
		self.assert_stock_matches_gl()

	def test_duplicate_submissions_change_nothing(self):
		self.released_fg(60)
		so = self.sales_order(DISTRIBUTOR, 20)
		pl = self.pick(so)
		self.reserve_picked(pl)
		dn = self.deliver(pl)
		si = self.invoice(dn)
		pe = self.pay(si)

		def snapshot():
			return (
				sle_rows(dn.name),
				frappe.db.count(
					"GL Entry", {"voucher_no": ("in", [dn.name, si.name, pe.name]), "is_cancelled": 0}
				),
				flt(frappe.db.get_value("Sales Invoice", si.name, "outstanding_amount")),
				frappe.db.count("Stock Reservation Entry", {"voucher_no": so.name, "docstatus": 1}),
				frappe.db.count("Delivery Note Item", {"against_sales_order": so.name, "docstatus": 1}),
			)

		before = snapshot()
		for doctype, name, user in (
			("Sales Order", so.name, USER_SALES),
			("Pick List", pl.name, USER_STORES),
			("Delivery Note", dn.name, USER_STORES),
			("Sales Invoice", si.name, USER_ACCOUNTS),
			("Payment Entry", pe.name, USER_ACCOUNTS),
		):
			with self.set_user(user):
				frappe.db.savepoint("fo_dup")
				try:
					frappe.client.submit(frappe.get_doc(doctype, name).as_dict())
				except frappe.ValidationError:
					frappe.db.rollback(save_point="fo_dup")  # a failed request rolls back
		self.assertEqual(snapshot(), before)
		# reserving again, or delivering the order again, adds nothing
		with self.set_user(USER_STORES):
			frappe.db.savepoint("fo_dup")
			try:
				frappe.get_doc("Pick List", pl.name).create_stock_reservation_entries()
			except frappe.ValidationError:
				frappe.db.rollback(save_point="fo_dup")
		self.assert_refused(frappe.ValidationError, self.deliver_order, so)
		self.assertEqual(snapshot(), before)

	def test_cancellations_unwind_in_order_and_reconcile(self):
		self.released_fg(60)
		so = self.sales_order(DISTRIBUTOR, 20)
		pl = self.pick(so)
		self.reserve_picked(pl)
		dn = self.deliver(pl)
		si = self.invoice(dn)
		pe = self.pay(si)
		# a Delivery Note cannot be cancelled while its invoice stands
		with self.set_user(USER_STORES):
			self.assert_refused(frappe.ValidationError, frappe.get_doc("Delivery Note", dn.name).cancel)
		with self.set_user(USER_ACCOUNTS):
			frappe.get_doc("Payment Entry", pe.name).cancel()
			self.assertEqual(flt(frappe.db.get_value("Sales Invoice", si.name, "outstanding_amount")), 5000)
			frappe.get_doc("Sales Invoice", si.name).cancel()
		self.assertEqual(flt(frappe.db.get_value("Delivery Note", dn.name, "per_billed")), 0)
		with self.set_user(USER_STORES):
			frappe.get_doc("Delivery Note", dn.name).cancel()
		self.assertEqual(sle_rows(dn.name), [])
		for voucher in (dn.name, si.name, pe.name):
			self.assertEqual(frappe.db.count("GL Entry", {"voucher_no": voucher, "is_cancelled": 0}), 0)
		self.assertEqual(flt(frappe.db.get_value("Sales Order", so.name, "per_delivered")), 0)
		self.assert_stock_matches_gl()
