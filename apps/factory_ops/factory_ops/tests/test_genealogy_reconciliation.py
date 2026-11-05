# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Batch genealogy and production reconciliation, on the real two-stage flow.

The flow is the one in ``test_production.TestProductionE2E`` (released RM and PM -> BULK Work Order
with 2 % process loss -> bulk QC release -> FG Work Order short-filled -> close -> return), because
lineage and reconciliation are only meaningful on real ERPNext documents. Lineage is checked
against an independent oracle: the Stock Entry documents' own Serial and Batch Bundles.
"""

from unittest.mock import patch

import frappe
from erpnext.manufacturing.doctype.work_order.work_order import (
	close_work_order,
	make_stock_return_entry,
)
from frappe.utils import flt

from factory_ops.production import genealogy, reconciliation
from factory_ops.production.controls import WastageReasonError
from factory_ops.setup.sample_company import ensure_boms
from factory_ops.tests.test_production import (
	BULK,
	FG,
	PM,
	REASON,
	RM1,
	RM2,
	ProductionTestCase,
	output_batch,
)
from factory_ops.tests.utils import USER_PRODUCTION, USER_STORES, ensure_user

NOBODY = "fo_nobody@example.com"


def bundle_batches(stock_entry: str, inward: bool) -> dict[str, float]:
	"""Oracle: batches a Stock Entry moved, read through the documents, not through genealogy.py.
	Outputs are rows with a target and no source; consumption / transfers-out are rows with a source."""
	se = frappe.get_doc("Stock Entry", stock_entry)
	found: dict[str, float] = {}
	for row in se.items:
		if not row.serial_and_batch_bundle:
			continue
		if inward != bool(row.t_warehouse and not row.s_warehouse):
			continue
		if not inward and not row.s_warehouse:
			continue
		for e in frappe.get_doc("Serial and Batch Bundle", row.serial_and_batch_bundle).entries:
			found[e.batch_no] = found.get(e.batch_no, 0) + abs(flt(e.qty))
	return found


def unsaved_issue(company, item, warehouse, qty, reason, batch, wastage=True, work_order=None):
	"""A Material Issue document that is not inserted yet (ProductionTestCase.issue inserts)."""
	return frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"purpose": "Material Issue",
			"stock_entry_type": "Wastage" if wastage else "Material Issue",
			"company": company,
			"fo_work_order": work_order,
			"items": [
				{
					"item_code": item,
					"qty": qty,
					"s_warehouse": warehouse,
					"fo_wastage_reason": reason,
					"use_serial_batch_fields": 1,
					"batch_no": batch,
				}
			],
		}
	)


class TestGenealogyAndReconciliation(ProductionTestCase):
	company = "_FO Perpetual Company"
	company_abbr = "_FOPC"

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.boms = ensure_boms(cls.company)
		ensure_user(NOBODY, [])
		frappe.db.commit()

	# ------------------------------------------------------------------ the shared flow
	def run_two_stage_flow(self):
		wh, wip = self.wh, self.wh["WIP-PRODUCTION"]
		f = frappe._dict()
		f.rm1 = self.released_stock(RM1, 20, rate=200)
		f.rm2 = self.released_stock(RM2, 200, rate=20)
		f.pm = self.released_stock(PM, 2000, rate=5)

		f.bulk_wo = self.work_order(BULK, 100)
		f.bulk_transfer = self.stock_entry(f.bulk_wo.name, "Material Transfer for Manufacture", 100)
		f.make_bulk = self.stock_entry(f.bulk_wo.name, "Manufacture", 100)
		f.bulk = output_batch(f.make_bulk.name, BULK)
		self.qc_release(f.bulk, "Release", item_code=BULK, source=wh["BULK-QC-HOLD"])

		f.fg_wo = self.work_order(FG, 1000)
		f.fg_transfer = self.stock_entry(f.fg_wo.name, "Material Transfer for Manufacture", 1000)
		f.make_fg = self.stock_entry(f.fg_wo.name, "Manufacture", 950)
		f.fg = output_batch(f.make_fg.name, FG)
		with self.set_user(USER_PRODUCTION):
			close_work_order(f.fg_wo.name, "Closed")
		f.wip = wip
		return f

	# ------------------------------------------------------------------ genealogy
	def test_back_and_forward_trace_follow_the_real_documents(self):
		f = self.run_two_stage_flow()

		back = genealogy.trace_back(f.fg)["rows"]
		by_batch = {r["batch_no"]: r for r in back}
		# the finished batch was made by the FG manufacture entry, from the released bulk batch
		self.assertEqual((back[0]["batch_no"], back[0]["level"], back[0]["event"]), (f.fg, 0, "Manufacture"))
		self.assertEqual(back[0]["voucher_no"], f.make_fg.name)
		self.assertEqual(back[0]["work_order"], f.fg_wo.name)
		self.assertEqual(flt(back[0]["qty"], 3), 950)
		consumed_by_fg = bundle_batches(f.make_fg.name, inward=False)
		self.assertIn(f.bulk, consumed_by_fg)
		self.assertEqual(flt(by_batch[f.bulk]["qty"], 3), 98)  # the bulk batch as produced
		self.assertEqual(by_batch[f.bulk]["level"], 1)
		self.assertEqual(by_batch[f.bulk]["event"], "Manufacture")
		self.assertEqual(by_batch[f.bulk]["voucher_no"], f.make_bulk.name)
		# every batch the FG entry consumed is in the trace, one level down
		for batch in consumed_by_fg:
			self.assertIn(batch, by_batch, f"{batch} consumed by {f.make_fg.name} is missing")
		# and every raw-material batch the bulk entry consumed is traced to its receipt
		for batch, qty in bundle_batches(f.make_bulk.name, inward=False).items():
			rows = [r for r in back if r["batch_no"] == batch and r["level"] == 2]
			self.assertTrue(rows, f"{batch} consumed by the bulk entry is missing at level 2")
			self.assertEqual(rows[0]["event"], "Material Receipt")
		self.assertTrue({f.rm1, f.rm2, f.pm} & set(by_batch), "released input batches appear in the trace")

		# forward from the bulk batch: consumed in the FG manufacture, producing the FG batch
		fwd = genealogy.trace_forward(f.bulk)["rows"]
		self.assertEqual(fwd[0]["event"], "Consumed in Manufacture")
		self.assertEqual(fwd[0]["voucher_no"], f.make_fg.name)
		self.assertAlmostEqual(flt(fwd[0]["qty"], 3), 28.5)
		self.assertIn(f.fg, [r["batch_no"] for r in fwd if r["level"] == 1])

		# a raw-material batch is traced forward through bulk to the finished batch (two hops)
		rm_batch = next(iter(bundle_batches(f.make_bulk.name, inward=False)))
		chain = [r["batch_no"] for r in genealogy.trace_forward(rm_batch)["rows"]]
		self.assertIn(f.bulk, chain)
		self.assertIn(f.fg, chain)

		# depth limit is reported, not silent
		shallow = genealogy.trace_back(f.fg, max_depth=1)
		self.assertTrue(shallow["truncated"])
		self.assertNotIn(RM1, [r["item_code"] for r in shallow["rows"] if r["level"] > 1])

	def test_cancelled_manufacture_leaves_no_lineage(self):
		f = self.run_two_stage_flow()
		# open the order again by cancelling its manufacture entry; the FG batch stops existing
		with self.set_user(USER_PRODUCTION):
			frappe.get_doc("Stock Entry", f.make_fg.name).cancel()
		self.assertEqual(genealogy.trace_back(f.fg)["rows"], [])
		self.assertNotIn(f.fg, [r["batch_no"] for r in genealogy.trace_forward(f.bulk)["rows"]])

	def test_unreadable_documents_are_masked_not_dropped(self):
		f = self.run_two_stage_flow()

		def deny_stock_entries(doctype, name):
			return doctype != "Stock Entry"

		with patch.object(genealogy, "_can_read", deny_stock_entries):
			rows = genealogy.trace_back(f.fg)["rows"]
		self.assertGreater(len(rows), 1)  # chain still visibly connected
		for r in rows:
			self.assertEqual(r["voucher_no"], genealogy.RESTRICTED)
			self.assertIsNone(r["voucher_type"])
			self.assertIsNone(r["work_order"])

	def test_batch_read_permission_is_enforced_by_the_backend(self):
		f = self.run_two_stage_flow()
		self.assertFalse(frappe.has_permission("Batch", "read", user=NOBODY))
		with self.set_user(NOBODY):
			self.assertRaises(frappe.PermissionError, genealogy.trace_back, f.fg)
			self.assertRaises(frappe.PermissionError, genealogy.trace, f.fg)
			self.assertRaises(frappe.PermissionError, reconciliation.reconcile, f.fg_wo.name)
			self.assertRaises(
				frappe.PermissionError,
				frappe.desk.query_report.run,
				"Batch Genealogy",
				{"batch_no": f.fg},
			)
		with self.set_user(USER_PRODUCTION):
			result = frappe.desk.query_report.run("Batch Genealogy", {"batch_no": f.fg})
			self.assertTrue(any(r["batch_no"] == f.bulk for r in result["result"]))

	# ------------------------------------------------------------------ reconciliation
	def test_yield_variance_and_wip_balance(self):
		f = self.run_two_stage_flow()

		bulk = reconciliation.reconcile(f.bulk_wo.name)
		self.assertEqual(
			(bulk["planned_qty"], bulk["produced_qty"], bulk["process_loss_qty"], bulk["processed_qty"]),
			(100, 98, 2, 100),
		)
		self.assertEqual((bulk["completion_pct"], bulk["yield_pct"]), (100.0, 98.0))
		self.assertEqual((bulk["status"], bulk["reconciliation"]), ("Completed", "Reconciled"))
		rm1 = next(i for i in bulk["items"] if i["item_code"] == RM1)
		self.assertEqual(
			(rm1["required_qty"], rm1["standard_qty"], rm1["consumed_qty"], rm1["variance_qty"], rm1["balance_qty"]),
			(5, 5, 5, 0, 0),
		)

		# FG order was short-filled (950 of 1,000) and closed with material left in WIP
		fg = reconciliation.reconcile(f.fg_wo.name)
		self.assertEqual((fg["produced_qty"], fg["processed_qty"], fg["completion_pct"], fg["yield_pct"]), (950, 950, 95.0, 100.0))
		line = {i["item_code"]: i for i in fg["items"]}
		self.assertEqual((line[BULK]["required_qty"], line[BULK]["standard_qty"], line[BULK]["consumed_qty"]), (30, 28.5, 28.5))
		self.assertEqual(line[BULK]["variance_qty"], 0)
		self.assertEqual(line[BULK]["balance_qty"], 1.5)  # transferred 30, consumed 28.5, not yet returned
		self.assertEqual(line[PM]["balance_qty"], 50)
		self.assertEqual(fg["reconciliation"], "Unreconciled")  # closed but WIP not cleared

		# returning the unused material to stores reconciles the order
		with self.set_user(USER_PRODUCTION):
			ret = make_stock_return_entry(f.fg_wo.name)
			ret.insert()
			ret.submit()
		fg = reconciliation.reconcile(f.fg_wo.name)
		line = {i["item_code"]: i for i in fg["items"]}
		self.assertEqual((line[BULK]["returned_qty"], line[BULK]["balance_qty"]), (1.5, 0))
		self.assertEqual((line[PM]["returned_qty"], line[PM]["balance_qty"]), (50, 0))
		self.assertEqual(fg["reconciliation"], "Reconciled")

		# wastage linked to the order, written off from stores: counted, but not against WIP
		with self.set_user(USER_STORES):
			waste = unsaved_issue(self.company, PM, self.wh["PM-STORES"], 10, REASON, f.pm, work_order=f.fg_wo.name)
			waste.insert()
			waste.submit()
		fg = reconciliation.reconcile(f.fg_wo.name)
		pm = {i["item_code"]: i for i in fg["items"]}[PM]
		self.assertEqual((pm["wastage_qty"], pm["wastage_value"], pm["balance_qty"]), (10, 50, 0))
		self.assertEqual(fg["wastage_value"], 50)
		self.assertEqual(fg["reconciliation"], "Reconciled")

	def test_wastage_from_wip_reduces_the_balance(self):
		f = self.run_two_stage_flow()
		# a second bulk order: transfer 20 kg, write 1 kg of RM2 off from WIP, make 10 kg, close, return
		wo = self.work_order(BULK, 20)
		transfer = self.stock_entry(wo.name, "Material Transfer for Manufacture", 20)
		rm2_in_wip = next(b for b, q in bundle_batches(transfer.name, inward=False).items() if frappe.db.get_value("Batch", b, "item") == RM2)
		with self.set_user(USER_STORES):
			waste = unsaved_issue(self.company, RM2, f.wip, 1, REASON, rm2_in_wip, work_order=wo.name)
			waste.insert()
			waste.submit()
		self.stock_entry(wo.name, "Manufacture", 10)
		with self.set_user(USER_PRODUCTION):
			close_work_order(wo.name, "Closed")
		rec = reconciliation.reconcile(wo.name)
		rm2 = {i["item_code"]: i for i in rec["items"]}[RM2]
		self.assertEqual((rm2["transferred_qty"], rm2["consumed_qty"], rm2["wastage_qty"]), (19, 9.5, 1))
		self.assertEqual(rm2["balance_qty"], 8.5)  # 19 - 9.5 - 0 - 1
		self.assertEqual(rec["reconciliation"], "Unreconciled")
		with self.set_user(USER_PRODUCTION):
			ret = make_stock_return_entry(wo.name)
			# ERPNext proposes transferred - consumed (9.5 kg) and does not know about the write-off,
			# so it would return more than WIP holds and the ledger refuses (negative batch stock).
			# The operator returns what is actually left: 8.5 kg.
			rm2_row = next(r for r in ret.items if r.item_code == RM2)
			self.assertEqual(flt(rm2_row.qty, 3), 9.5)
			rm2_row.qty = rm2_row.transfer_qty = 8.5
			ret.insert()
			ret.submit()
		rec = reconciliation.reconcile(wo.name)
		rm2 = {i["item_code"]: i for i in rec["items"]}[RM2]
		self.assertEqual((rm2["returned_qty"], rm2["consumed_qty"], rm2["balance_qty"]), (8.5, 9.5, 0))
		self.assertEqual(rec["reconciliation"], "Reconciled")

	def test_minimum_yield_flag_is_only_set_when_configured(self):
		f = self.run_two_stage_flow()
		settings = "Factory Ops Settings"
		try:
			frappe.db.set_single_value(settings, "min_yield_percent", 0)
			frappe.clear_document_cache(settings, settings)
			self.assertFalse(reconciliation.reconcile(f.bulk_wo.name)["below_min_yield"])  # blank: no check
			frappe.db.set_single_value(settings, "min_yield_percent", 99)
			self.assertTrue(reconciliation.reconcile(f.bulk_wo.name)["below_min_yield"])  # 98 < 99
			frappe.db.set_single_value(settings, "min_yield_percent", 98)
			self.assertFalse(reconciliation.reconcile(f.bulk_wo.name)["below_min_yield"])  # 98 is not below 98
		finally:
			frappe.db.set_single_value(settings, "min_yield_percent", 0)

	def test_report_summary_and_detail_modes(self):
		f = self.run_two_stage_flow()
		with self.set_user(USER_PRODUCTION):
			summary = frappe.desk.query_report.run(
				"Production Reconciliation", {"company": self.company, "work_order": f.bulk_wo.name}
			)
			detail = frappe.desk.query_report.run(
				"Production Reconciliation",
				{"company": self.company, "work_order": f.bulk_wo.name, "detail": 1},
			)
			problems = frappe.desk.query_report.run(
				"Production Reconciliation", {"company": self.company, "only_problems": 1}
			)
		self.assertEqual([r["work_order"] for r in summary["result"]], [f.bulk_wo.name])
		self.assertEqual(summary["result"][0]["yield_pct"], 98.0)
		self.assertEqual({r["item_code"] for r in detail["result"]}, {RM1, RM2})
		# the closed FG order still has material in WIP, so it is listed; the reconciled bulk order is not
		listed = [r["work_order"] for r in problems["result"]]
		self.assertIn(f.fg_wo.name, listed)
		self.assertNotIn(f.bulk_wo.name, listed)

	# ------------------------------------------------------------------ wastage link validation
	def test_work_order_link_is_only_for_wastage_on_a_submitted_order_of_the_same_company(self):
		f = self.run_two_stage_flow()
		stores = self.wh["PM-STORES"]
		def issue(**kw):
			return unsaved_issue(self.company, PM, stores, 1, kw.pop("reason", REASON), f.pm, **kw)

		with self.set_user(USER_STORES):
			# not a wastage entry
			self.assertRaises(WastageReasonError, issue(reason=None, wastage=False, work_order=f.fg_wo.name).insert)
			# a draft (unsubmitted) order
			draft = self.work_order(FG, 10, submit=False)
			self.assertRaises(WastageReasonError, issue(work_order=draft.name).insert)
			# unknown order
			self.assertRaises((WastageReasonError, frappe.LinkValidationError), issue(work_order="WO-DOES-NOT-EXIST").insert)
			# the valid case
			se = issue(work_order=f.fg_wo.name)
			se.insert()
			self.assertEqual(se.fo_work_order, f.fg_wo.name)
		# a Work Order of another company is refused
		other = frappe.db.get_value("Company", {"name": ("!=", self.company)}, "name")
		if other:
			foreign = frappe.db.get_value("Work Order", {"company": other, "docstatus": 1}, "name")
			if foreign:
				with self.set_user(USER_STORES):
					self.assertRaises(WastageReasonError, issue(work_order=foreign).insert)

	# ------------------------------------------------------------------ Desk actions are served
	def test_desk_buttons_are_delivered_with_the_doctypes(self):
		frappe.set_user("Administrator")
		for doctype, marker in (("Batch", "Batch Genealogy"), ("Work Order", "Production Reconciliation")):
			frappe.response["docs"] = []
			frappe.desk.form.load.getdoctype(doctype)
			js = frappe.response["docs"][0].get("__js") or ""
			self.assertIn(marker, js, f"{doctype} form does not carry its button")

	def test_every_report_role_can_actually_run_its_report(self):
		"""Frappe also needs the 'report' right on the reference DocType; a role listed on the report
		without it would see the report in the list and get a permission error on open."""
		for name in ("Batch Genealogy", "Production Reconciliation"):
			report = frappe.get_doc("Report", name)
			for row in report.roles:
				email = f"fo-role-{row.role.lower().replace(' ', '-')}@example.com"
				ensure_user(email, [row.role])
				self.assertTrue(
					frappe.has_permission(report.ref_doctype, "report", user=email),
					f"{row.role} is listed on {name} but has no report right on {report.ref_doctype}",
				)
