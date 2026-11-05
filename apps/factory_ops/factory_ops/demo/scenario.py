# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""DEMO - NOT REAL: one end-to-end run of the twelve demo products through every factory workflow.

Runs on a demo site only (``fo_demo_site``). Every step is a normal ERPNext / factory_ops document created
and submitted **as the SAMPLE user of that department** (never Administrator, never ``ignore_permissions``),
so permissions, approval limits, QC guards and stock/accounting validations all apply:

    Purchase Order (buyer) -> approval (approver) -> Purchase Receipt into QC Hold (stores, with supplier
    batch and COA) -> QC Release (QC officer) -> Purchase Invoice and Payment (accounts)
    -> bulk Work Order, transfer, manufacture, bulk QC release (production / QC)
    -> finished-goods Work Order, transfer, manufacture, FG QC release
    -> Sales Order (sales) -> Pick List and reservation (stores) -> Delivery Note and Packing Slip
    (dispatch) -> Sales Invoice and Payment Entry (accounts)

and then checks that the stages are really connected (batch genealogy, ledgers vs. accounts, available to
sell, reconciliation, reports, dashboard cards) and that the controls refuse what they must. Each check is
recorded as passed or failed with its evidence; nothing is skipped silently. The report is written to the
site's ``private/demo_scenario_report.json``.

    bench --site demo.localhost execute factory_ops.demo.scenario.run
"""

import io
import json
import math
import traceback
from contextlib import contextmanager

import frappe
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry as wo_stock_entry
from erpnext.selling.doctype.sales_order.sales_order import create_pick_list
from erpnext.stock.doctype.delivery_note.delivery_note import make_packing_slip
from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice as dn_to_si
from erpnext.stock.doctype.pick_list.pick_list import create_delivery_note
from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice
from frappe.utils import add_days, flt, nowdate

from factory_ops.demo import catalogue as C
from factory_ops.demo import data as D
from factory_ops.demo.seed import COMPANY, ABBR, require_demo_site

U = {
	"buyer": "sample.buyer@sample.invalid",
	"approver": "sample.approver@sample.invalid",
	"stores": "sample.stores@sample.invalid",
	"qc": "sample.qc@sample.invalid",
	"production": "sample.production@sample.invalid",
	"sales": "sample.sales@sample.invalid",
	"dispatch": "sample.dispatch@sample.invalid",
	"accounts": "sample.accounts@sample.invalid",
	"management": "sample.management@sample.invalid",
}
_DRY = {"on": False}  # a dry run keeps everything in one transaction and rolls it back at the end
FG_UNITS = 600  # per product
PO_VALUE_CAP = 400_000  # one Purchase Order stays under the approver's limit (500,000 on the SAMPLE users)
REFUSALS = (frappe.ValidationError, frappe.PermissionError)


def commit():
	if not _DRY["on"]:
		frappe.db.commit()


def wh(name: str) -> str:
	return f"{name} - {ABBR}"


@contextmanager
def as_user(role: str):
	frappe.set_user(U[role])
	try:
		yield
	finally:
		frappe.set_user("Administrator")


class Run:
	def __init__(self):
		self.checks: list[dict] = []
		self.facts: dict = {}

	def check(self, name: str, ok: bool, detail=""):
		self.checks.append({"check": name, "ok": bool(ok), "detail": str(detail)[:400]})
		print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))

	def refused(self, name: str, fn, expect=REFUSALS):
		"""``fn`` must be refused by a control; whatever it started is rolled back, as a failed request."""
		frappe.db.savepoint("demo_refused")
		try:
			fn()
		except expect as exc:
			frappe.db.rollback(save_point="demo_refused")
			frappe.clear_messages()
			self.check(name, True, f"{type(exc).__name__}: {str(exc)[:160]}")
			return
		except Exception as exc:  # a different failure is a failed check, not a pass
			frappe.db.rollback(save_point="demo_refused")
			self.check(name, False, f"unexpected {type(exc).__name__}: {str(exc)[:200]}")
			return
		frappe.db.rollback(save_point="demo_refused")
		self.check(name, False, "the action was NOT refused")


# --------------------------------------------------------------------------- planning
def plan() -> dict:
	"""Quantities from the BOMs and the number of units planned: nothing typed in per material."""
	bulk_need: dict[str, float] = {}
	for code, _n, _cat, bulk, ml, _p in C.PRODUCTS:
		bulk_need[bulk] = bulk_need.get(bulk, 0) + ml * FG_UNITS / 1000
	bulk_wo: dict[str, int] = {}
	rm_need: dict[str, float] = {}
	for bulk, need in bulk_need.items():
		loss = C.BULKS[bulk][1] / 100
		qty = math.ceil(need / (1 - loss) / 10) * 10
		bulk_wo[bulk] = qty
		bom = frappe.get_doc("BOM", frappe.db.get_value("BOM", {"item": bulk, "is_default": 1, "docstatus": 1}, "name"))
		for row in bom.items:
			rm_need[row.item_code] = rm_need.get(row.item_code, 0) + qty * row.qty / bom.quantity
	pm_need: dict[str, float] = {}
	for code, _n, _cat, bulk, ml, _p in C.PRODUCTS:
		for item, qty in C.fg_components(code, bulk, ml)[1:]:
			pm_need[item] = pm_need.get(item, 0) + qty * FG_UNITS / C.UNITS_PER_BOM
	need = {k: math.ceil(v * 1.05) for k, v in {**rm_need, **pm_need}.items()}  # 5 % spare
	need[C.BOTTLE[100]] += 50  # bottles for the wastage step
	return {"bulk_need": bulk_need, "bulk_wo": bulk_wo, "buy": need}


# --------------------------------------------------------------------------- procurement
def demo_coa() -> str:
	from pypdf import PdfWriter

	writer = PdfWriter()
	writer.add_blank_page(width=200, height=200)
	writer.add_metadata({"/Title": "DEMO COA - NOT REAL"})
	buf = io.BytesIO()
	writer.write(buf)
	f = frappe.new_doc("File")
	f.file_name = "DEMO-COA-NOT-REAL.pdf"
	f.is_private = 1
	f.content = buf.getvalue()
	f.insert(ignore_permissions=True)
	return f.file_url


def purchase_orders(run: Run, buy: dict[str, int], controls: bool = True) -> list[str]:
	"""One PO per supplier and value chunk: raised by the buyer, approved by the approver."""
	rates = {
		i: flt(frappe.db.get_value("Item Price", {"item_code": i, "price_list": "Standard Buying"}, "price_list_rate"))
		for i in buy
	}
	by_supplier: dict[str, list[tuple[str, int]]] = {}
	for item, qty in sorted(buy.items()):
		supplier = D.SUPPLIER_KEY["chem" if item.startswith("DEMO-RM") else "pack"]
		by_supplier.setdefault(supplier, []).append((item, qty))
	pos = []
	for supplier, rows in by_supplier.items():
		chunks, current, value = [], [], 0.0
		for item, qty in rows:
			line = qty * rates[item]
			if current and value + line > PO_VALUE_CAP:
				chunks.append(current)
				current, value = [], 0.0
			current.append((item, qty))
			value += line
		chunks.append(current)
		for chunk in chunks:
			with as_user("buyer"):
				po = frappe.get_doc(
					{
						"doctype": "Purchase Order",
						"company": COMPANY,
						"supplier": supplier,
						"schedule_date": add_days(nowdate(), 5),
						"items": [
							{"item_code": i, "qty": q, "rate": rates[i], "schedule_date": add_days(nowdate(), 5)}
							for i, q in chunk
						],
					}
				).insert()
			total = flt(po.base_grand_total)
			if controls and pos == []:  # first order: the buyer may not approve their own order
				run.refused(
					"Procurement: the buyer cannot approve their own Purchase Order",
					lambda n=po.name: _submit_as("buyer", "Purchase Order", n),
				)
			with as_user("approver"):
				frappe.get_doc("Purchase Order", po.name).submit()
			pos.append(po.name)
			run.facts.setdefault("po_totals", {})[po.name] = total
	if not controls:
		return pos
	# an order above the approver's limit is refused
	with as_user("buyer"):
		big = frappe.get_doc(
			{
				"doctype": "Purchase Order",
				"company": COMPANY,
				"supplier": D.SUPPLIER_KEY["chem"],
				"schedule_date": add_days(nowdate(), 5),
				"items": [{"item_code": "DEMO-RM-0006", "qty": 1000, "rate": 2200, "schedule_date": add_days(nowdate(), 5)}],
			}
		).insert()
	run.refused("Procurement: a Purchase Order above the approver's limit is refused", lambda: _submit_as("approver", "Purchase Order", big.name))
	frappe.delete_doc("Purchase Order", big.name, force=True, ignore_permissions=True)
	return pos


def _submit_as(role: str, doctype: str, name: str):
	with as_user(role):
		frappe.get_doc(doctype, name).submit()


def receive(run: Run, pos: list[str]) -> list[dict]:
	"""Purchase Receipts into QC Hold with supplier batch, dates and COA (raw materials)."""
	coa = demo_coa()
	received = []
	first = not run.facts.get("coa_check_done")
	for po in pos:
		pr = make_purchase_receipt(po)
		for row in pr.items:
			is_rm = row.item_code.startswith("DEMO-RM")
			row.fo_supplier_batch_no = f"DEMO-LOT-{row.item_code[-6:]}"
			row.fo_mfg_date = add_days(nowdate(), -20)
			if frappe.db.get_value("Item", row.item_code, "has_expiry_date"):
				row.fo_expiry_date = add_days(nowdate(), 700 if is_rm else 365)
			if is_rm and not first:
				row.fo_coa = coa
		if first and any(r.item_code.startswith("DEMO-RM") for r in pr.items):
			first = False
			run.facts["coa_check_done"] = True

			def no_coa(pr=pr):  # a raw material without a supplier COA is refused
				with as_user("stores"):
					doc = frappe.get_doc(pr.as_dict()).insert()
					doc.submit()
			run.refused("Receiving: a raw-material receipt without a supplier COA is refused", no_coa)
			for row in pr.items:
				if row.item_code.startswith("DEMO-RM"):
					row.fo_coa = coa
		with as_user("stores"):
			doc = frappe.get_doc(pr.as_dict()).insert()
			doc.submit()
		doc.reload()
		for row in doc.items:
			received.append({"pr": doc.name, "po": po, "item": row.item_code, "batch": row.batch_no, "warehouse": row.warehouse, "qty": flt(row.qty)})
	return received


def qc_release(run: Run, item: str, batch: str, source: str, decision: str = "Release", status: str = "Accepted", qty: float = 0, reason: str = ""):
	from factory_ops.quality.doctype.qc_release.qc_release import create_quality_inspection

	with as_user("qc"):
		doc = frappe.get_doc(
			{"doctype": "QC Release", "company": COMPANY, "item_code": item, "batch_no": batch, "source_warehouse": source, "decision": decision, "qty": qty, "reason": reason}
		).insert()
		qi = frappe.get_doc("Quality Inspection", create_quality_inspection(doc.name))
		qi.manual_inspection = 1
		qi.status = status
		qi.save()
		qi.submit()
		doc.reload()
		doc.submit()
	return doc


# --------------------------------------------------------------------------- production
def output_batch(stock_entry: str, item_code: str) -> str:
	return frappe.db.sql(
		"""select sbe.batch_no from `tabSerial and Batch Entry` sbe
		inner join `tabSerial and Batch Bundle` sbb on sbb.name = sbe.parent
		where sbb.voucher_type = 'Stock Entry' and sbb.voucher_no = %s and sbb.item_code = %s
		and sbe.qty > 0 and sbb.is_cancelled = 0""",
		(stock_entry, item_code),
	)[0][0]


def work_order(item: str, qty: float):
	with as_user("production"):
		bom = frappe.db.get_value("BOM", {"item": item, "company": COMPANY, "is_default": 1, "docstatus": 1}, "name")
		wo = frappe.get_doc({"doctype": "Work Order", "company": COMPANY, "production_item": item, "bom_no": bom, "qty": qty}).insert()
		wo.submit()
	return wo


def stock_entry(wo: str, purpose: str, qty: float):
	with as_user("production"):
		se = frappe.get_doc(wo_stock_entry(wo, purpose, qty))
		se.insert()
		se.submit()
	return se


def produce(run: Run, item: str, qty: float, label: str) -> dict:
	wo = work_order(item, qty)
	transfer = stock_entry(wo.name, "Material Transfer for Manufacture", qty)
	make = stock_entry(wo.name, "Manufacture", qty)
	batch = output_batch(make.name, item)
	return {"work_order": wo.name, "transfer": transfer.name, "manufacture": make.name, "batch": batch, "item": item}


# --------------------------------------------------------------------------- stages
def run_wo(wo, qty: float) -> dict:
	transfer = stock_entry(wo.name, "Material Transfer for Manufacture", qty)
	make = stock_entry(wo.name, "Manufacture", qty)
	return {
		"work_order": wo.name, "transfer": transfer.name, "manufacture": make.name,
		"batch": output_batch(make.name, wo.production_item), "item": wo.production_item,
	}  # fmt: skip


def blocked_fill(fg_wo: str, bulk: str, batch: str):
	with as_user("production"):
		se = frappe.get_doc(wo_stock_entry(fg_wo, "Material Transfer for Manufacture", FG_UNITS))
		for row in se.items:
			if row.item_code == bulk:
				row.s_warehouse = wh("BULK-QC-HOLD")
				row.use_serial_batch_fields = 1
				row.batch_no = batch
		se.insert()


def batch_info(batch: str) -> dict:
	from factory_ops.inventory.stock_queries import batch_qty_by_warehouse

	status = frappe.db.get_value("Batch", batch, "fo_qc_status")
	return {"status": status, "qty": {k: flt(v, 3) for k, v in batch_qty_by_warehouse(batch).items() if v}}


def procurement_stage(r: Run, p: dict) -> list[dict]:
	pos = purchase_orders(r, p["buy"])
	r.check("Procurement: every item planned from the BOMs was ordered on an approved PO", all(frappe.db.get_value("Purchase Order", n, "docstatus") == 1 for n in pos), f"{len(pos)} POs")
	received = receive(r, pos)
	commit()
	bad = [x for x in received if batch_info(x["batch"])["status"] != "Hold" or not batch_info(x["batch"])["qty"].get(x["warehouse"])]
	r.check("Receiving: every received batch sits in its QC Hold warehouse with status Hold", not bad, bad[:2])
	with_supplier = [x for x in received if x["item"].startswith("DEMO-RM") and frappe.db.get_value("Batch", x["batch"], "fo_supplier_batch_no")]
	r.check("Receiving: raw-material batches carry the supplier batch number and COA", len(with_supplier) == len([x for x in received if x["item"].startswith("DEMO-RM")]))
	# a lot that QC will reject
	reject_po = purchase_orders(r, {"DEMO-RM-0003": 20}, controls=False)
	reject_lot = receive(r, reject_po)[0]
	commit()
	for x in received:
		qc_release(r, x["item"], x["batch"], x["warehouse"])
	rej = qc_release(r, reject_lot["item"], reject_lot["batch"], reject_lot["warehouse"], decision="Reject", status="Rejected", qty=reject_lot["qty"], reason="DEMO: lot failed the incoming check (NOT REAL)")
	commit()
	bad = [x["batch"] for x in received if batch_info(x["batch"])["status"] != "Released" or batch_info(x["batch"])["qty"].get(x["warehouse"])]
	r.check("QC: every accepted incoming batch is Released and has left QC Hold", not bad, bad[:3])
	info = batch_info(reject_lot["batch"])
	r.check("QC: the rejected lot is Rejected and sits in the Rejected warehouse", info["status"] == "Rejected" and wh("REJECTED") in info["qty"], info)
	r.facts["reject_lot"] = reject_lot

	def issue_rejected():
		with as_user("production"):
			se = frappe.get_doc(
				{
					"doctype": "Stock Entry", "purpose": "Material Transfer", "stock_entry_type": "Material Transfer", "company": COMPANY,
					"items": [{"item_code": reject_lot["item"], "qty": 5, "s_warehouse": wh("REJECTED"), "t_warehouse": wh("WIP-PRODUCTION"), "use_serial_batch_fields": 1, "batch_no": reject_lot["batch"]}],
				}
			)
			se.insert()
			se.submit()

	r.refused("QC: rejected material cannot be issued to production", issue_rejected)
	# accounts: supplier invoice and payment for every receipt
	for pr in sorted({x["pr"] for x in received + [reject_lot]}):
		with as_user("accounts"):
			pi = make_purchase_invoice(pr)
			pi.bill_no = f"DEMO-BILL-{pr[-5:]}"
			pi.bill_date = nowdate()
			pi.insert()
			pi.submit()
			pe = get_payment_entry("Purchase Invoice", pi.name)
			pe.reference_no, pe.reference_date = f"DEMO-UTR-{pr[-5:]}", nowdate()
			pe.insert()
			pe.submit()
	commit()
	r.facts["purchase_receipts"] = sorted({x["pr"] for x in received + [reject_lot]})
	unpaid = frappe.db.count("Purchase Invoice", {"company": COMPANY, "docstatus": 1, "outstanding_amount": (">", 0.005)})
	r.check("Accounts: every supplier invoice is paid (no payable left)", unpaid == 0, f"{unpaid} unpaid")
	return received


def production_stage(r: Run, p: dict, received: list[dict]) -> dict:
	out = {"bulk": {}, "fg": {}}
	by_bulk: dict[str, list] = {}
	for prod in C.PRODUCTS:
		by_bulk.setdefault(prod[3], []).append(prod)
	for n, (bulk, products) in enumerate(sorted(by_bulk.items())):
		qty = p["bulk_wo"][bulk]
		first_fg = products[0]
		fg_wo_first = None
		bwo = work_order(bulk, qty)
		b = run_wo(bwo, qty)
		info = batch_info(b["batch"])
		loss = C.BULKS[bulk][1] / 100
		produced = flt(frappe.db.get_value("Work Order", bwo.name, "produced_qty"), 3)
		r.check(f"Bulk {bulk}: made with the BOM's process loss and held for QC", info["status"] == "Hold" and abs(produced - qty * (1 - loss)) < 0.01 and wh("BULK-QC-HOLD") in info["qty"], f"{info}, produced {produced}")
		fg_wo_first = work_order(first_fg[0], FG_UNITS)
		r.refused(f"Bulk {bulk}: held bulk cannot go into filling", lambda w=fg_wo_first.name, bu=bulk, ba=b["batch"]: blocked_fill(w, bu, ba))
		qc_release(r, bulk, b["batch"], wh("BULK-QC-HOLD"))
		commit()
		out["bulk"][bulk] = {**b, "produced": produced}
		for prod in products:
			code = prod[0]
			wo = fg_wo_first if prod is first_fg else work_order(code, FG_UNITS)
			f = run_wo(wo, FG_UNITS)
			fi = batch_info(f["batch"])
			r.check(f"FG {code}: filled and held for QC", fi["status"] == "Hold" and fi["qty"].get(wh("FG-QC-HOLD")) == FG_UNITS, fi)
			qc_release(r, code, f["batch"], wh("FG-QC-HOLD"))
			commit()
			out["fg"][code] = f
	return out


def sales_stage(r: Run, produced: dict) -> list[dict]:
	from factory_ops.sales_ops.available_to_sell import available_to_sell

	customer = D.CUSTOMER[0]
	orders = []
	codes = [p[0] for p in C.PRODUCTS]
	prices = {p[0]: p[5] for p in C.PRODUCTS}
	# one extra, never-released lot: it must not count as sellable and must not be dispatchable
	held_wo = work_order(codes[0], 20)
	before = available_to_sell(COMPANY, codes[0], wh("FG-STORES"))
	held = run_wo(held_wo, 20)
	after = available_to_sell(COMPANY, codes[0], wh("FG-STORES"))
	r.check("Sales: a produced-but-unreleased lot adds nothing to available-to-sell", before == after == FG_UNITS, f"{before} -> {after}")

	def ship_held():
		with as_user("dispatch"):
			dn = frappe.get_doc(
				{
					"doctype": "Delivery Note", "company": COMPANY, "customer": customer,
					"items": [{"item_code": codes[0], "qty": 10, "rate": prices[codes[0]], "warehouse": wh("FG-QC-HOLD"), "use_serial_batch_fields": 1, "batch_no": held["batch"]}],
				}
			)
			dn.insert()

	r.refused("Sales: a lot still in QC Hold cannot be put on a Delivery Note", ship_held)
	for i in range(3):
		chunk = codes[i * 4 : i * 4 + 4]
		with as_user("sales"):
			so = frappe.get_doc(
				{
					"doctype": "Sales Order", "company": COMPANY, "customer": customer, "fo_sales_channel": "B2B",
					"delivery_date": add_days(nowdate(), 7), "set_warehouse": wh("FG-STORES"),
					"items": [{"item_code": c, "qty": 100, "rate": prices[c], "warehouse": wh("FG-STORES")} for c in chunk],
				}
			).insert()
			so.submit()
		with as_user("stores"):
			pl = create_pick_list(so.name)
			pl.insert()
			pl.submit()
			frappe.get_doc("Pick List", pl.name).create_stock_reservation_entries()
		with as_user("dispatch"):
			dn = create_delivery_note(pl.name)
			dn.transporter_name, dn.lr_no, dn.lr_date, dn.vehicle_no = "DEMO Transport (NOT REAL)", f"DEMO-LR-{i + 1:04d}", nowdate(), "DEMO-00-0000"
			dn.save()
			ps = make_packing_slip(dn.name)
			for row in ps.items:
				row.net_weight = 0.2
			ps.insert()
			ps.submit()
			dn.reload()
			dn.submit()
		with as_user("accounts"):
			si = dn_to_si(dn.name)
			si.insert()
			si.submit()
			pe = get_payment_entry("Sales Invoice", si.name)
			pe.reference_no, pe.reference_date = f"DEMO-RECEIPT-{i + 1:04d}", nowdate()
			pe.insert()
			pe.submit()
		commit()
		orders.append({"so": so.name, "pick_list": pl.name, "dn": dn.name, "si": si.name, "pe": pe.name, "items": chunk})
	return orders


# --------------------------------------------------------------------------- verification
def verify(r: Run, p: dict, received: list[dict], produced: dict, orders: list[dict]):
	from factory_ops.production import genealogy, reconciliation

	fg_codes = [x[0] for x in C.PRODUCTS]
	for code in fg_codes:
		batch = produced["fg"][code]["batch"]
		info = batch_info(batch)
		sold = 100 if any(code in o["items"] for o in orders) else 0
		r.check(f"FG {code}: released, {FG_UNITS - sold} left in FG-STORES after dispatch", info["status"] == "Released" and info["qty"].get(wh("FG-STORES")) == FG_UNITS - sold, info)

	# genealogy: each finished batch traces back through bulk to supplier receipts
	for code, _n, _c, bulk, ml, _p in C.PRODUCTS:
		back = genealogy.trace_back(produced["fg"][code]["batch"])
		items = {row["item_code"] for row in back["rows"]}
		bulk_bom = frappe.db.get_value("BOM", {"item": bulk, "is_default": 1, "docstatus": 1}, "name")
		expected = {bulk, C.BOTTLE[ml], C.CAP, C.CARTON, C.label_code(code)} | {x.item_code for x in frappe.get_doc("BOM", bulk_bom).items}
		events = {row["event"] for row in back["rows"]}
		missing = expected - items
		r.check(f"Genealogy {code}: back-trace reaches bulk, packaging and every raw material, down to the receipts", not missing and "Purchase Receipt" in events and not back["truncated"], f"missing {sorted(missing)}, events {sorted(events)}")
	surfactant = next(x for x in received if x["item"] == "DEMO-RM-0001")
	fwd = genealogy.trace_forward(surfactant["batch"])
	reached = {row["item_code"] for row in fwd["rows"]}
	delivered = [row for row in fwd["rows"] if row["voucher_type"] == "Delivery Note"]
	r.check("Genealogy: a surfactant batch traces forward to cleanser bulk, its finished goods and the customer's Delivery Note", "DEMO-BULK-0001" in reached and {"DEMO-FG-001", "DEMO-FG-002", "DEMO-FG-003"} <= reached and bool(delivered), f"reached {sorted(reached)[:8]} dn rows {len(delivered)}")

	# reconciliation of every work order
	bad = []
	for b, v in produced["bulk"].items():
		rec = reconciliation.reconcile(v["work_order"])
		if abs(flt(rec.get("produced_qty")) - flt(v["produced"])) > 0.01:
			bad.append((b, rec.get("produced_qty"), v["produced"]))
	r.check("Reconciliation: each bulk Work Order's reconciliation reports the quantity actually produced", not bad, bad)
	r.facts["sample_reconciliation"] = {k: v for k, v in reconciliation.reconcile(next(iter(produced["bulk"].values()))["work_order"]).items() if not isinstance(v, (list, dict))}

	# ledgers
	sle = flt(frappe.db.sql("select coalesce(sum(stock_value_difference),0) from `tabStock Ledger Entry` where company=%s and is_cancelled=0", COMPANY)[0][0], 2)
	gl = flt(frappe.db.sql("select coalesce(sum(g.debit-g.credit),0) from `tabGL Entry` g join `tabAccount` a on a.name=g.account where g.company=%s and g.is_cancelled=0 and a.account_type='Stock'", COMPANY)[0][0], 2)
	r.check("Accounting: the stock ledger value equals the Stock accounts in the General Ledger", abs(sle - gl) < 0.05, f"stock ledger {sle}, GL {gl}")
	dr, cr = frappe.db.sql("select coalesce(sum(debit),0), coalesce(sum(credit),0) from `tabGL Entry` where company=%s and is_cancelled=0", COMPANY)[0]
	r.check("Accounting: the General Ledger balances (debits = credits)", abs(flt(dr) - flt(cr)) < 0.05, f"{dr} vs {cr}")
	unbalanced = frappe.db.sql("select voucher_no from `tabGL Entry` where company=%s and is_cancelled=0 group by voucher_no having abs(sum(debit)-sum(credit)) > 0.05", COMPANY)
	r.check("Accounting: every voucher balances on its own", not unbalanced, unbalanced[:3])

	# sales chain
	for o in orders:
		so_status = frappe.db.get_value("Sales Order", o["so"], "status")
		si = frappe.db.get_value("Sales Invoice", o["si"], ["status", "outstanding_amount", "grand_total"], as_dict=True)
		dn = frappe.db.get_value("Delivery Note", o["dn"], ["docstatus", "transporter_name", "lr_no"], as_dict=True)
		r.check(f"Sales {o['so']}: delivered with transporter and LR no, invoiced, paid; order Completed", so_status == "Completed" and dn.docstatus == 1 and dn.lr_no and si.status == "Paid" and flt(si.outstanding_amount) == 0, f"{so_status} {dict(si)} {dict(dn)}")
	reserved = frappe.db.count("Stock Reservation Entry", {"docstatus": 1, "voucher_type": "Sales Order", "status": ("not in", ("Delivered", "Cancelled"))})
	r.check("Sales: no reservation is left open after delivery", reserved == 0, reserved)

	# available to sell by product
	from factory_ops.sales_ops.available_to_sell import available_to_sell

	wrong = {c: available_to_sell(COMPANY, c, wh("FG-STORES")) for c in fg_codes if available_to_sell(COMPANY, c, wh("FG-STORES")) != FG_UNITS - (100 if any(c in o["items"] for o in orders) else 0)}
	r.check("Available-to-sell equals released stock minus what was delivered, for all 12 products", not wrong, wrong)

	# wastage
	bottle = next(x for x in received if x["item"] == C.BOTTLE[100])
	def no_reason():
		with as_user("stores"):
			se = frappe.get_doc({"doctype": "Stock Entry", "purpose": "Material Issue", "stock_entry_type": "Wastage", "company": COMPANY, "items": [{"item_code": bottle["item"], "qty": 10, "s_warehouse": wh("PM-STORES"), "use_serial_batch_fields": 1, "batch_no": bottle["batch"]}]})
			se.insert()

	r.refused("Wastage: a write-off without a reason is refused", no_reason)
	with as_user("stores"):
		se = frappe.get_doc({"doctype": "Stock Entry", "purpose": "Material Issue", "stock_entry_type": "Wastage", "company": COMPANY, "items": [{"item_code": bottle["item"], "qty": 10, "s_warehouse": wh("PM-STORES"), "fo_wastage_reason": "Damaged in handling", "use_serial_batch_fields": 1, "batch_no": bottle["batch"]}]})
		se.insert()
		se.submit()
	commit()
	r.check("Wastage: a write-off with a reason posted and balanced", frappe.db.get_value("Stock Entry", se.name, "docstatus") == 1)

	# permissions
	r.check("Permissions: production staff cannot create QC Releases; the QC officer can", not frappe.has_permission("QC Release", "create", user=U["production"]) and bool(frappe.has_permission("QC Release", "create", user=U["qc"])))
	r.check("Permissions: the read-only management user cannot create or approve anything", not any(frappe.has_permission(d, t, user=U["management"]) for d in ("Purchase Order", "Sales Order", "Work Order", "QC Release") for t in ("create", "submit")))

	# reports and dashboard cards, as the read-only management user
	from factory_ops.inventory.report.stock_by_qc_status.stock_by_qc_status import execute as qc_stock
	from factory_ops.production.report.batch_genealogy.batch_genealogy import execute as genealogy_report
	from factory_ops.production.report.production_reconciliation.production_reconciliation import execute as recon_report
	from factory_ops.production.report.wastage_by_reason.wastage_by_reason import execute as wastage_report
	from factory_ops.quality.report.qc_queue.qc_queue import execute as qc_queue
	from factory_ops.sales_ops.report.available_to_sell.available_to_sell import execute as ats_report
	from factory_ops.sales_ops.report.dispatch_register.dispatch_register import execute as dispatch_report

	one_batch = produced["fg"]["DEMO-FG-007"]["batch"]
	reports = {
		"Stock by QC Status": (qc_stock, {"company": COMPANY}),
		"QC Queue": (qc_queue, {"company": COMPANY}),
		"Production Reconciliation": (recon_report, {"company": COMPANY}),
		"Wastage by Reason": (wastage_report, {"company": COMPANY, "from_date": add_days(nowdate(), -30), "to_date": nowdate()}),
		"Available to Sell": (ats_report, {"company": COMPANY}),
		"Dispatch Register": (dispatch_report, {"company": COMPANY, "from_date": add_days(nowdate(), -30), "to_date": nowdate()}),
		"Batch Genealogy": (genealogy_report, {"batch_no": one_batch}),
	}
	for name, (fn, filters) in reports.items():
		try:
			with as_user("management"):
				result = fn(frappe._dict(filters))
			rows = result[1] if isinstance(result, (list, tuple)) and len(result) > 1 else []
			r.check(f"Report {name}: opens for the management user and shows rows", len(rows) > 0, f"{len(rows)} rows")
		except Exception as exc:
			frappe.clear_messages()
			r.check(f"Report {name}: opens for the management user and shows rows", False, f"{type(exc).__name__}: {str(exc)[:200]}")
	from frappe.desk.doctype.number_card.number_card import get_result

	for key in ("FO Purchase Orders Awaiting Approval", "FO QC Releases Pending", "FO Work Orders In Progress", "FO Batches Awaiting QC", "Total Stock Value", "Open Sales Orders", "Receivables Outstanding"):
		try:
			from factory_ops.setup.workspaces import card_name

			card = frappe.get_doc("Number Card", card_name(key))
			with as_user("management"):
				value = get_result(card.as_dict(), card.filters_json)
			r.check(f"Dashboard card '{card.label}' computes for the management user", True, value)
			r.facts.setdefault("cards", {})[card.label] = value
		except Exception as exc:
			frappe.clear_messages()
			r.check(f"Dashboard card {key} computes for the management user", False, f"{type(exc).__name__}: {str(exc)[:200]}")


def _run(r: Run):
	require_demo_site()
	r.facts["catalogue"] = {k: v for k, v in C.seed_catalogue().items() if k != "boms"}
	commit()
	p = plan()
	r.facts["plan"] = p
	received = procurement_stage(r, p)
	produced = production_stage(r, p, received)
	orders = sales_stage(r, produced)
	r.facts["documents"] = {"purchase_receipts": r.facts.get("purchase_receipts"), "orders": orders, "bulk": produced["bulk"], "fg": produced["fg"]}
	verify(r, p, received, produced, orders)


def run(dry: bool = False) -> dict:
	"""``dry``: do everything in one transaction, report, and roll it all back (for development)."""
	require_demo_site()
	_DRY["on"] = bool(dry)
	if frappe.db.exists("Work Order", {"production_item": ("like", "DEMO-FG-%")}):
		frappe.throw("The demo scenario has already run on this site (Work Orders for DEMO-FG items exist).")
	r = Run()
	try:
		_run(r)
	except Exception:
		frappe.db.rollback()
		print(traceback.format_exc())
		r.check("the scenario ran to the end without an unexpected error", False, traceback.format_exc()[-900:])
	finally:
		frappe.set_user("Administrator")
		if dry:
			frappe.db.rollback()
		else:
			frappe.db.commit()
	failed = [c for c in r.checks if not c["ok"]]
	report = {"passed": len(r.checks) - len(failed), "failed": len(failed), "checks": r.checks, "facts": r.facts}
	path = frappe.get_site_path("private", "demo_scenario_report.json")
	with open(path, "w") as fh:
		json.dump(report, fh, indent=1, default=str)
	print(f"\n{report['passed']} passed, {report['failed']} failed. Report: {path}")
	return {"passed": report["passed"], "failed": [c["check"] for c in failed]}
