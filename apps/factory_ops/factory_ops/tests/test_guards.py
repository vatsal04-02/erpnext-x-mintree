"""Every other route that could bypass QC: each is tried on a held (or rejected) batch and
must be refused server-side, and each has a positive control on released stock so the
guards are shown not to over-block. Pick List, Stock Reservation, Subcontracting Order, sales
returns, Purchase Invoice, manufacturing and repack are in test_lifecycle_routes.py."""

import frappe
from frappe.utils import add_days, nowdate

from factory_ops.inventory.guards import QCGuardError
from factory_ops.tests.utils import COMPANY, FG_ITEM, PLAIN_ITEM, QC_ITEM, FactoryOpsTestCase

CUSTOMER = "_Test Customer"
SUPPLIER = "_Test Supplier"


class TestGuards(FactoryOpsTestCase):
	def setUp(self):
		self.hold = self.wh["RM-QC-HOLD"]
		self.stores = self.wh["RM-STORES"]
		_, self.batch = self.receive(QC_ITEM, self.hold, 100)

	# --- inward ---------------------------------------------------------------
	def test_purchase_receipt_must_land_in_qc_hold(self):
		def pr(warehouse):
			return frappe.get_doc(
				{
					"doctype": "Purchase Receipt",
					"company": COMPANY,
					"supplier": SUPPLIER,
					"items": [
						{
							"item_code": QC_ITEM,
							"qty": 10,
							"rate": 100,
							"warehouse": warehouse,
							"fo_supplier_batch_no": "SAMPLE-LOT-1",
							"fo_mfg_date": add_days(nowdate(), -30),
							"fo_expiry_date": add_days(nowdate(), 700),
						}
					],
				}
			)

		self.assertRaises(QCGuardError, pr(self.stores).insert)
		doc = pr(self.hold).insert()
		doc.submit()
		self.assertEqual(doc.docstatus, 1)

	def test_material_receipt_of_qc_item_into_usable_is_blocked(self):
		self.assertRaises(QCGuardError, self.receive, QC_ITEM, self.stores, 10)

	def test_non_qc_item_is_not_restricted(self):
		_, batch = self.receive(PLAIN_ITEM, self.stores, 10)
		self.assertTrue(batch)

	# --- outward / dispatch -------------------------------------------------------
	def _dn(self, warehouse, batch, qty=5, item=QC_ITEM):
		return frappe.get_doc(
			{
				"doctype": "Delivery Note",
				"company": COMPANY,
				"customer": CUSTOMER,
				"items": [
					{
						"item_code": item,
						"qty": qty,
						"rate": 200,
						"warehouse": warehouse,
						"use_serial_batch_fields": 1,
						"batch_no": batch,
					}
				],
			}
		)

	def test_delivery_note_from_hold_is_blocked_and_from_stores_allowed(self):
		self.assertRaises(QCGuardError, self._dn(self.hold, self.batch).insert)
		self.qc_release(self.batch, "Release")
		dn = self._dn(self.stores, self.batch).insert()
		dn.submit()
		self.assertEqual(dn.docstatus, 1)

	def test_sales_invoice_with_stock_update_from_hold_is_blocked(self):
		si = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"company": COMPANY,
				"customer": CUSTOMER,
				"update_stock": 1,
				"items": [
					{
						"item_code": QC_ITEM,
						"qty": 5,
						"rate": 200,
						"warehouse": self.hold,
						"use_serial_batch_fields": 1,
						"batch_no": self.batch,
					}
				],
			}
		)
		self.assertRaises(QCGuardError, si.insert)

	def test_minimum_shelf_life_for_dispatch(self):
		_, short_batch = self.receive(QC_ITEM, self.hold, 20, expiry_days=30)
		self.qc_release(short_batch, "Release")
		frappe.db.set_single_value("Factory Ops Settings", "min_shelf_life_days_for_dispatch", 180)
		try:
			self.assertRaises(QCGuardError, self._dn(self.stores, short_batch).insert)
			frappe.db.set_single_value("Factory Ops Settings", "min_shelf_life_days_for_dispatch", 0)
			self._dn(self.stores, short_batch).insert()
		finally:
			frappe.db.set_single_value("Factory Ops Settings", "min_shelf_life_days_for_dispatch", 0)

	# --- production and planning --------------------------------------------------
	def _bom(self):
		bom = frappe.get_doc(
			{
				"doctype": "BOM",
				"item": FG_ITEM,
				"company": COMPANY,
				"quantity": 100,
				"items": [{"item_code": QC_ITEM, "qty": 3, "rate": 100}],
			}
		)
		bom.insert()
		bom.submit()
		return bom.name

	def _wo(self, source, fg):
		return frappe.get_doc(
			{
				"doctype": "Work Order",
				"company": COMPANY,
				"production_item": FG_ITEM,
				"bom_no": self._bom(),
				"qty": 100,
				"source_warehouse": source,
				"wip_warehouse": self.wh["WIP-PRODUCTION"],
				"fg_warehouse": fg,
			}
		)

	def test_work_order_cannot_draw_from_hold(self):
		self.assertRaises(QCGuardError, self._wo(self.hold, self.wh["FG-QC-HOLD"]).insert)

	def test_work_order_output_must_go_to_qc_hold(self):
		self.assertRaises(QCGuardError, self._wo(self.stores, self.wh["FG-STORES"]).insert)
		wo = self._wo(self.stores, self.wh["FG-QC-HOLD"]).insert()
		self.assertTrue(wo.name)

	def test_material_request_from_hold_is_blocked(self):
		mr = frappe.get_doc(
			{
				"doctype": "Material Request",
				"company": COMPANY,
				"material_request_type": "Material Transfer",
				"schedule_date": add_days(nowdate(), 1),
				"set_from_warehouse": self.hold,
				"set_warehouse": self.wh["WIP-PRODUCTION"],
				"items": [
					{
						"item_code": QC_ITEM,
						"qty": 5,
						"schedule_date": add_days(nowdate(), 1),
						"from_warehouse": self.hold,
						"warehouse": self.wh["WIP-PRODUCTION"],
					}
				],
			}
		)
		self.assertRaises(QCGuardError, mr.insert)

	def test_stock_reconciliation_cannot_count_held_batch_into_usable(self):
		reco = frappe.get_doc(
			{
				"doctype": "Stock Reconciliation",
				"company": COMPANY,
				"purpose": "Stock Reconciliation",
				"items": [
					{
						"item_code": QC_ITEM,
						"warehouse": self.stores,
						"qty": 100,
						"valuation_rate": 100,
						"use_serial_batch_fields": 1,
						"batch_no": self.batch,
					}
				],
			}
		)
		self.assertRaises(QCGuardError, reco.insert)
