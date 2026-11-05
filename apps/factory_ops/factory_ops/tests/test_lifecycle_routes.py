"""QC guards exercised through full ERPNext document lifecycles (insert / submit through the
standard mappers and controllers), never by calling the guard function directly.

Also covers rollback: a refused movement leaves no stock ledger, GL or bundle rows behind.
"""

import frappe
from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry as wo_stock_entry
from erpnext.selling.doctype.sales_order.sales_order import create_pick_list
from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_return
from frappe.utils import add_days, nowdate

from factory_ops.inventory.guards import QCGuardError
from factory_ops.inventory.stock_queries import batch_qty_in_warehouse
from factory_ops.tests.utils import (
	COMPANY,
	FG_ITEM,
	QC_ITEM,
	USER_QA,
	FactoryOpsTestCase,
)

CUSTOMER = "_Test Customer"
SUPPLIER = "_Test Supplier"


def ledger_rows(voucher_no: str) -> dict[str, int]:
	return {
		"sle": frappe.db.count("Stock Ledger Entry", {"voucher_no": voucher_no}),
		"gle": frappe.db.count("GL Entry", {"voucher_no": voucher_no}),
		"bundle": frappe.db.count("Serial and Batch Bundle", {"voucher_no": voucher_no, "docstatus": 1}),
	}


class TestLifecycleRoutes(FactoryOpsTestCase):
	def setUp(self):
		self.hold = self.wh["RM-QC-HOLD"]
		self.stores = self.wh["RM-STORES"]
		_, self.held = self.receive(QC_ITEM, self.hold, 100)
		_, self.released = self.receive(QC_ITEM, self.hold, 100)
		self.qc_release(self.released, "Release")

	# ------------------------------------------------------------------ selling
	def _so(self, warehouse, qty=5, submit=True):
		so = frappe.get_doc(
			{
				"doctype": "Sales Order",
				"company": COMPANY,
				"customer": CUSTOMER,
				"delivery_date": add_days(nowdate(), 5),
				"items": [{"item_code": QC_ITEM, "qty": qty, "rate": 200, "warehouse": warehouse}],
			}
		)
		so.insert()
		if submit:
			so.submit()
		return so

	def test_sales_order_cannot_target_qc_hold(self):
		self.assertRaises(QCGuardError, self._so, self.hold)
		self.assertEqual(self._so(self.stores).docstatus, 1)

	def test_pick_list_from_sales_order_picks_only_usable_stock(self):
		so = self._so(self.stores, qty=150)  # more than released stock, so hold stock would be tempting
		pl = create_pick_list(so.name)
		pl.insert()
		self.assertEqual(
			pl.parent_warehouse,
			frappe.db.get_value("Warehouse", {"warehouse_name": "USABLE", "company": COMPANY}),
		)
		self.assertTrue(pl.locations)
		# 150 ordered, 100 released and 100 held: only released stock in usable warehouses is suggested
		held_wh = {self.hold, self.wh["REJECTED"]}
		for loc in pl.locations:
			self.assertNotIn(loc.warehouse, held_wh)
			self.assertNotEqual(loc.batch_no, self.held)
		self.assertLessEqual(sum(loc.qty for loc in pl.locations), 150)

	def test_manual_pick_list_from_hold_is_refused_on_save(self):
		so = self._so(self.stores)
		pl = create_pick_list(so.name)
		pl.pick_manually = 1
		for loc in pl.locations:
			loc.warehouse = self.hold
			loc.batch_no = self.held
		self.assertRaises(QCGuardError, pl.insert)

	def test_stock_reservation_from_hold_is_refused(self):
		frappe.db.set_single_value("Stock Settings", "enable_stock_reservation", 1)
		so = self._so(self.stores)
		row = so.items[0]
		details = {"sales_order_item": row.name, "item_code": QC_ITEM, "qty_to_reserve": 5}
		self.assertRaises(
			QCGuardError, so.create_stock_reservation_entries, [{**details, "warehouse": self.hold}]
		)
		self.assertFalse(frappe.db.exists("Stock Reservation Entry", {"voucher_no": so.name, "docstatus": 1}))
		so.reload()
		so.create_stock_reservation_entries([{**details, "warehouse": self.stores}])
		sre = frappe.get_all(
			"Stock Reservation Entry", {"voucher_no": so.name, "docstatus": 1}, ["warehouse", "reserved_qty"]
		)
		self.assertEqual(len(sre), 1)
		self.assertEqual(sre[0].warehouse, self.stores)

	def test_sales_return_must_come_back_into_qc_hold(self):
		dn = frappe.get_doc(
			{
				"doctype": "Delivery Note",
				"company": COMPANY,
				"customer": CUSTOMER,
				"items": [
					{
						"item_code": QC_ITEM,
						"qty": 10,
						"rate": 200,
						"warehouse": self.stores,
						"use_serial_batch_fields": 1,
						"batch_no": self.released,
					}
				],
			}
		).insert()
		dn.submit()
		ret = make_sales_return(dn.name)
		self.assertEqual(ret.items[0].warehouse, self.stores)  # ERPNext maps it back to the source
		self.assertRaises(QCGuardError, ret.insert)
		ret = make_sales_return(dn.name)
		ret.items[0].warehouse = self.wh["RETURNS-QC-HOLD"]
		ret.insert()
		ret.submit()
		self.assertEqual(batch_qty_in_warehouse(self.released, self.wh["RETURNS-QC-HOLD"]), 10)

	# ----------------------------------------------------------------- purchase
	def _pi(self, warehouse):
		return frappe.get_doc(
			{
				"doctype": "Purchase Invoice",
				"company": COMPANY,
				"supplier": SUPPLIER,
				"update_stock": 1,
				"items": [{"item_code": QC_ITEM, "qty": 10, "rate": 100, "warehouse": warehouse}],
			}
		)

	def test_purchase_invoice_with_stock_update(self):
		"""QC items must be received with a Purchase Receipt (supplier batch, dates, COA), so a
		stock-updating invoice is refused for them in any warehouse. Non-QC items are unaffected."""
		self.assertRaises(QCGuardError, self._pi(self.stores).insert)
		self.assertRaises(QCGuardError, self._pi(self.hold).insert)
		pi = self._pi(self.stores)
		pi.items[0].item_code = "_Test Item"
		pi.insert()
		pi.submit()
		self.assertEqual(pi.docstatus, 1)
		self.assertTrue(frappe.db.count("GL Entry", {"voucher_no": pi.name}))

	# --------------------------------------------------------------- production
	def _work_order(self, transfer=True):
		bom = frappe.get_doc(
			{
				"doctype": "BOM",
				"item": FG_ITEM,
				"company": COMPANY,
				"quantity": 100,
				"items": [{"item_code": QC_ITEM, "qty": 3, "rate": 100}],
			}
		).insert()
		bom.submit()
		wo = frappe.get_doc(
			{
				"doctype": "Work Order",
				"company": COMPANY,
				"production_item": FG_ITEM,
				"bom_no": bom.name,
				"qty": 100,
				"source_warehouse": self.stores,
				"wip_warehouse": self.wh["WIP-PRODUCTION"],
				"fg_warehouse": self.wh["FG-QC-HOLD"],
			}
		).insert()
		wo.submit()
		if not transfer:
			return wo
		transfer = frappe.get_doc(wo_stock_entry(wo.name, "Material Transfer for Manufacture", 100))
		for row in transfer.items:
			row.use_serial_batch_fields = 1
			row.batch_no = self.released
		transfer.insert()
		transfer.submit()
		return wo

	def test_manufacture_output_must_land_in_qc_hold(self):
		wo = self._work_order()
		se = frappe.get_doc(wo_stock_entry(wo.name, "Manufacture", 100))
		fg_row = next(r for r in se.items if r.is_finished_item)
		self.assertEqual(fg_row.t_warehouse, self.wh["FG-QC-HOLD"])
		fg_row.t_warehouse = self.wh["FG-STORES"]
		self.assertRaises(QCGuardError, se.insert)
		se = frappe.get_doc(wo_stock_entry(wo.name, "Manufacture", 100))
		se.insert()
		se.submit()
		se.reload()
		fg_row = next(r for r in se.items if r.is_finished_item)
		fg_batch = frappe.db.get_value(
			"Serial and Batch Entry", {"parent": fg_row.serial_and_batch_bundle}, "batch_no"
		)
		self.assertEqual(batch_qty_in_warehouse(fg_batch, self.wh["FG-QC-HOLD"]), 100)
		self.assertEqual(frappe.db.get_value("Batch", fg_batch, "fo_qc_status"), "Hold")

	def test_production_cannot_consume_held_material(self):
		wo = self._work_order(transfer=False)
		se = frappe.get_doc(wo_stock_entry(wo.name, "Material Transfer for Manufacture", 10))
		for row in se.items:
			row.s_warehouse = self.hold
			row.use_serial_batch_fields = 1
			row.batch_no = self.held
		self.assertRaises(QCGuardError, se.insert)

	def _repack(self, source_wh, source_batch, out_wh):
		return frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"purpose": "Repack",
				"stock_entry_type": "Repack",
				"company": COMPANY,
				"items": [
					{
						"item_code": QC_ITEM,
						"qty": 10,
						"s_warehouse": source_wh,
						"use_serial_batch_fields": 1,
						"batch_no": source_batch,
					},
					{"item_code": FG_ITEM, "qty": 10, "t_warehouse": out_wh, "is_finished_item": 1},
				],
			}
		)

	def test_repack(self):
		# output of a QC item into usable stock is refused; consuming held stock is refused
		self.assertRaises(QCGuardError, self._repack(self.stores, self.released, self.wh["FG-STORES"]).insert)
		self.assertRaises(QCGuardError, self._repack(self.hold, self.held, self.wh["FG-QC-HOLD"]).insert)
		se = self._repack(self.stores, self.released, self.wh["FG-QC-HOLD"]).insert()
		se.submit()
		self.assertEqual(se.docstatus, 1)

	# --------------------------------------------------------- subcontracting
	def test_subcontracting_order_cannot_reserve_supplied_items_in_hold(self):
		from erpnext.controllers.tests.test_subcontracting_controller import (
			get_subcontracting_order,
			make_bom_for_subcontracted_items,
			make_raw_materials,
			make_service_items,
			make_subcontracted_items,
		)

		make_subcontracted_items()
		make_raw_materials()
		make_service_items()
		make_bom_for_subcontracted_items()
		# ERPNext's helper submits the subcontracting PO as its creator; approval limits are not
		# what this test is about, so they are switched off for it (the change is audited).
		with self.change_settings("Factory Ops Settings", enforce_purchase_approval=0):
			sco = get_subcontracting_order(do_not_save=True)
			sco.set_reserve_warehouse = self.hold
			for row in sco.get("supplied_items") or []:
				row.reserve_warehouse = self.hold
			self.assertRaises(QCGuardError, sco.insert)

	# ---------------------------------------------------------------- rollback
	def test_refused_movement_writes_nothing(self):
		"""Guards run in validate/before_submit, before ERPNext writes any ledger row, so even
		inside the open transaction nothing has been written for the refused voucher."""
		sle_before = frappe.db.count("Stock Ledger Entry")
		gle_before = frappe.db.count("GL Entry")
		se = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"purpose": "Material Transfer",
				"stock_entry_type": "Material Transfer",
				"company": COMPANY,
				"fo_qc_release": None,
				"items": [
					{
						"item_code": QC_ITEM,
						"qty": 100,
						"s_warehouse": self.hold,
						"t_warehouse": self.stores,
						"use_serial_batch_fields": 1,
						"batch_no": self.held,
					}
				],
			}
		)
		self.assertRaises(QCGuardError, se.insert)
		self.assertEqual(frappe.db.count("Stock Ledger Entry"), sle_before)
		self.assertEqual(frappe.db.count("GL Entry"), gle_before)
		self.assertEqual(batch_qty_in_warehouse(self.held, self.hold), 100)

	def test_failure_after_ledger_rows_rolls_back_completely(self):
		"""The shelf-life rule also runs in on_submit (batches can be auto-picked during submit),
		i.e. after ERPNext has written ledger rows. A failure there must leave nothing behind once
		the transaction is rolled back, as Frappe does for every failed request."""
		_, short = self.receive(QC_ITEM, self.hold, 20, expiry_days=10)
		self.qc_release(short, "Release")
		# Milestone 6 (D-034): where eligible stock exists, the Delivery Note is given an eligible
		# batch up front. To reach a failure *after* ledger rows, the short-dated batch is the only
		# stock in its own usable warehouse, so ERPNext still picks it during submit.
		alone = self.wh["BULK-STORES"]
		self.transfer(QC_ITEM, short, 20, self.stores, alone)
		frappe.db.set_single_value("Factory Ops Settings", "min_shelf_life_days_for_dispatch", 30)
		try:

			def dn_from(warehouse):
				return frappe.get_doc(
					{
						"doctype": "Delivery Note",
						"company": COMPANY,
						"customer": CUSTOMER,
						"items": [{"item_code": QC_ITEM, "qty": 5, "rate": 200, "warehouse": warehouse}],
					}
				)

			dn = dn_from(alone)
			dn.insert()
			frappe.db.savepoint("before_submit")
			# no batch chosen and nothing eligible: ERPNext picks FEFO during submit, i.e. the
			# short-dated batch, and the guard refuses it in on_submit, after the ledger rows
			with self.assertRaises(QCGuardError):
				dn.submit()
			frappe.db.rollback(save_point="before_submit")
			self.assertEqual(ledger_rows(dn.name), {"sle": 0, "gle": 0, "bundle": 0})
			self.assertEqual(frappe.db.get_value("Delivery Note", dn.name, "docstatus"), 0)
			self.assertEqual(batch_qty_in_warehouse(short, alone), 20)
			# where eligible stock exists, the eligible batch is chosen and the short one is not
			ok = dn_from(self.stores)
			ok.insert()
			self.assertTrue(all(r.batch_no and r.batch_no != short for r in ok.items))
		finally:
			frappe.db.set_single_value("Factory Ops Settings", "min_shelf_life_days_for_dispatch", 0)

	def test_failed_qc_release_leaves_release_draft_and_stock_in_hold(self):
		draft = self.qc_release(self.held, "Release", submit=False)
		# someone moves part of the batch is impossible (guarded), so simulate a stale quantity:
		frappe.db.set_value("QC Release", draft.name, "qty", 150)
		doc = frappe.get_doc("QC Release", draft.name)
		frappe.db.savepoint("before_release")
		with self.set_user(USER_QA):
			self.assertRaises(frappe.ValidationError, doc.submit)
		frappe.db.rollback(save_point="before_release")
		self.assertEqual(frappe.db.get_value("QC Release", draft.name, "docstatus"), 0)
		self.assertEqual(frappe.db.get_value("Stock Entry", draft.stock_entry, "docstatus"), 0)
		self.assertEqual(batch_qty_in_warehouse(self.held, self.hold), 100)
