"""Milestone 6, Part A: one batch-eligibility rule everywhere (DECISIONS D-034).

The minimum remaining shelf life must give the same answer in Available to Sell, Sales Order
reservation, Pick List suggestions and Delivery Note, immediately after the setting changes, and
without relying on the daily segregation job (which these tests never run).
"""

import frappe
from frappe.utils import add_days, flt, nowdate

from factory_ops.inventory.batch_eligibility import (
	check_batch,
	check_batch_no,
	eligible_batches,
	min_shelf_life_days,
)
from factory_ops.inventory.guards import QCGuardError
from factory_ops.sales_ops.report.available_to_sell.available_to_sell import execute as ats_report
from factory_ops.tests.test_sales import DISTRIBUTOR, FG, SalesTestCase


def shipped_batches(voucher_no: str) -> dict[str, float]:
	rows = frappe.db.sql(
		"""select sbe.batch_no, sum(sbe.qty) as qty from `tabSerial and Batch Entry` sbe
		inner join `tabSerial and Batch Bundle` sbb on sbb.name = sbe.parent
		where sbb.voucher_no = %s and sbb.is_cancelled = 0 group by sbe.batch_no""",
		voucher_no,
		as_dict=True,
	)
	return {r.batch_no: abs(flt(r.qty)) for r in rows}


def pinned_batches(sales_order: str) -> dict[str, float]:
	rows = frappe.db.sql(
		"""select sbe.batch_no, sum(sbe.qty) as qty from `tabSerial and Batch Entry` sbe
		inner join `tabStock Reservation Entry` sre on sre.name = sbe.parent
		where sre.voucher_no = %s and sre.docstatus = 1 group by sbe.batch_no""",
		sales_order,
		as_dict=True,
	)
	return {r.batch_no: flt(r.qty) for r in rows}


class TestEligibilityRule(SalesTestCase):
	"""The rule itself, at and around the threshold (no stock needed)."""

	def test_threshold_boundaries(self):
		today = nowdate()
		self.set_min_shelf_life(90)
		self.assertEqual(min_shelf_life_days(), 90)
		above = check_batch(add_days(today, 91))
		at = check_batch(add_days(today, 90))
		below = check_batch(add_days(today, 89))
		expired = check_batch(add_days(today, -1))
		self.assertEqual((above.eligible, above.reason, above.remaining_days), (True, None, 91))
		self.assertEqual((at.eligible, at.reason, at.remaining_days), (True, None, 90))  # exactly: eligible
		self.assertEqual((below.eligible, below.reason), (False, "short_dated"))
		self.assertEqual((expired.eligible, expired.reason), (False, "expired"))
		self.assertTrue(check_batch(None).eligible)  # no expiry tracking
		self.assertTrue(check_batch(add_days(today, 0)).eligible is False)  # 0 days left < 90

	def test_zero_minimum_keeps_erpnext_behaviour(self):
		today = nowdate()
		self.set_min_shelf_life(0)
		self.assertTrue(check_batch(add_days(today, 1)).eligible)
		self.assertTrue(check_batch(today).eligible)  # expires today: still in date
		self.assertEqual(check_batch(add_days(today, -1)).reason, "expired")

	def test_transaction_date_is_used(self):
		self.set_min_shelf_life(30)
		expiry = add_days(nowdate(), 40)
		self.assertTrue(check_batch(expiry).eligible)  # today: 40 days left
		self.assertFalse(check_batch(expiry, on_date=add_days(nowdate(), 20)).eligible)  # 20 left


class TestEligibilityAcrossWorkflows(SalesTestCase):
	def setUp(self):
		super().setUp()
		# isolate from FG stock left by other test classes in this company
		self.base = self.ats()

	def stock(self):
		"""good: 600 days left, at: exactly 90, short: 60, expired."""
		good = self.released_fg(50, expiry_days=600)
		at = self.released_fg(30, expiry_days=600)
		short = self.released_fg(40, expiry_days=600)
		expired = self.released_fg(20, expiry_days=600)
		self.age_batch(at, 90)
		self.age_batch(short, 60)
		self.age_batch(expired, -3)
		return good, at, short, expired

	def test_available_to_sell_counts_eligible_batches_only(self):
		good, at, short, expired = self.stock()
		self.set_min_shelf_life(90)
		self.assertEqual(flt(self.ats() - self.base), 80)  # good 50 + at-threshold 30
		elig = {b.batch_no for b in eligible_batches(self.company, FG, self.fg_stores)}
		self.assertTrue({good, at} <= elig)
		self.assertFalse({short, expired} & elig)
		_, rows = ats_report({"company": self.company, "item_code": FG})
		self.assertEqual(flt(rows[0]["available"]), self.ats())

	def test_zero_minimum_sells_short_dated_but_never_expired(self):
		_good, _at, short, expired = self.stock()
		self.set_min_shelf_life(0)
		self.assertEqual(flt(self.ats() - self.base), 120)  # all but the expired 20
		so = self.sales_order(DISTRIBUTOR, self.ats())
		pl = self.pick(so)
		picked = {r.batch_no for r in pl.locations}
		self.assertIn(short, picked)  # ERPNext's FEFO, unchanged
		self.assertNotIn(expired, picked)

	def test_raising_the_minimum_applies_immediately_without_segregation(self):
		_good, _at, short, expired = self.stock()
		self.set_min_shelf_life(0)
		self.assertEqual(flt(self.ats() - self.base), 120)
		self.assertTrue(check_batch_no(short).eligible)
		# the client raises the minimum during the day; the daily job has not run
		self.set_min_shelf_life(90)
		self.assertFalse(check_batch_no(short).eligible)
		self.assertEqual(flt(self.ats() - self.base), 80)
		# Pick List suggestions follow at once
		pl = self.pick(self.sales_order(DISTRIBUTOR, 60))
		self.assertNotIn(short, {r.batch_no for r in pl.locations})
		self.assertEqual(sum(flt(r.stock_qty) for r in pl.locations), 60)
		# a reservation from the order pins only eligible batches
		so = self.sales_order(DISTRIBUTOR, 10)
		self.reserve_order(so)
		self.assertNotIn(short, pinned_batches(so.name))
		# a Delivery Note made from an order chooses only eligible batches
		so2 = self.sales_order(DISTRIBUTOR, 5)
		dn = self.deliver_order(so2)
		self.assertTrue(shipped_batches(dn.name))
		self.assertFalse({short, expired} & set(shipped_batches(dn.name)))
		# nothing was moved by the segregation job
		self.assertFalse(
			frappe.db.exists(
				"Stock Entry",
				{"remarks": ("like", "Shelf-life segregation%"), "docstatus": 1, "company": self.company},
			)
		)

	def test_available_to_sell_and_pick_list_agree(self):
		_good, _at, _short, _expired = self.stock()
		self.set_min_shelf_life(90)
		ats = self.ats()
		# an order for everything that is available is picked in full, from eligible batches only
		pl = self.pick(self.sales_order(DISTRIBUTOR, ats))
		self.assertEqual(sum(flt(r.stock_qty) for r in pl.locations), ats)
		self.assertTrue(all(check_batch_no(r.batch_no).eligible for r in pl.locations))
		self.assertEqual(self.ats(), 0)  # everything is now picked
		# one more unit cannot be picked from eligible stock
		pl2 = self.pick(self.sales_order(DISTRIBUTOR, 1))
		self.assertFalse(
			[r for r in pl2.locations if flt(r.stock_qty) and check_batch_no(r.batch_no).eligible]
		)

	def test_delivery_note_rejects_an_ineligible_batch(self):
		_good, at, short, expired = self.stock()
		self.set_min_shelf_life(90)
		so = self.sales_order(DISTRIBUTOR, 5)
		from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note

		for batch, reason in ((short, "shelf life"), (expired, "expired")):
			dn = make_delivery_note(so.name)
			self._dispatch_details(dn)
			dn.items[0].use_serial_batch_fields = 1
			dn.items[0].batch_no = batch
			err = self.assert_refused(frappe.ValidationError, dn.insert)
			self.assertIn(reason, str(err).lower())
		dn = make_delivery_note(so.name)
		self._dispatch_details(dn)
		dn.items[0].use_serial_batch_fields = 1
		dn.items[0].batch_no = at  # exactly at the threshold: allowed
		dn.insert()
		dn.submit()
		self.assertEqual(shipped_batches(dn.name), {at: 5})

	def test_reservations_and_picks_are_subtracted_once(self):
		_good, _at, _short, _expired = self.stock()
		self.set_min_shelf_life(90)
		start = self.ats()
		# reservation from the order (batch-pinned)
		so1 = self.sales_order(DISTRIBUTOR, 10)
		self.reserve_order(so1)
		self.assertEqual(flt(start - self.ats()), 10)
		# pick, then reserve the picked batches: picked and reserved are the same 20 units
		so2 = self.sales_order(DISTRIBUTOR, 20)
		pl = self.pick(so2)
		self.assertEqual(flt(start - self.ats()), 30)
		self.reserve_picked(pl)
		self.assertEqual(flt(start - self.ats()), 30)
		# quantity-only reservation (no batch pinned) is subtracted per warehouse
		frappe.db.set_single_value("Stock Settings", "auto_reserve_serial_and_batch", 0)
		so3 = self.sales_order(DISTRIBUTOR, 5)
		self.reserve_order(so3)
		self.assertEqual(
			frappe.db.get_value(
				"Stock Reservation Entry", {"voucher_no": so3.name, "docstatus": 1}, "reservation_based_on"
			),
			"Qty",
		)
		self.assertEqual(flt(start - self.ats()), 35)
		# delivering the picked order releases its reservation and its stock together
		self.deliver(pl)
		self.assertEqual(flt(start - self.ats()), 35)
		# a reservation can never take more than is available
		self.assert_refused(
			frappe.ValidationError, self.reserve_order, self.sales_order(DISTRIBUTOR, self.ats() + 1)
		)

	def test_reserved_batch_that_becomes_ineligible_is_refused_at_dispatch(self):
		batch = self.released_fg(20, expiry_days=600)
		so = self.sales_order(DISTRIBUTOR, 20)
		self.reserve_order(so)
		self.assertIn(batch, pinned_batches(so.name))
		self.age_batch(batch, 30)
		self.set_min_shelf_life(90)
		err = self.assert_refused(QCGuardError, self.deliver_order, so)
		self.assertIn("reserved for this order", str(err))
