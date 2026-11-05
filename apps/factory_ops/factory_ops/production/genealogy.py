# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Batch genealogy: what went into a batch, and where a batch went.

Read-only. Built on ERPNext's own records (Serial and Batch Bundle / Entry, Stock Entry, Purchase
Receipt, Delivery Note, Sales Invoice): no table of its own, so it cannot drift from the stock ledger.

**Lineage edges** are *transformations* only. A batch is created by a receipt (Purchase Receipt,
Purchase Invoice with stock update, Material Receipt, Stock Reconciliation, Subcontracting Receipt)
or by a Manufacture / Repack Stock Entry. Plain moves (transfers, QC release, transfer to WIP)
keep the batch identity and are not edges.

**Attribution rule.** A Manufacture / Repack entry links *every* batch it consumes to *every*
batch it outputs. That is exact for one finished item per entry, which is how Mintree's two-stage
Work Orders run (one BULK or one FG per entry). If a BOM also yields by-products or scrap in the
same entry, they are linked to the same inputs too; the report shows it as such.

**Not traceable.** An item that is not batch-tracked leaves no batch row, so it cannot appear.

**Permissions.** The caller needs read access to the starting Batch. Any document the caller may
not read is shown as "(restricted)" and its party is hidden, rather than being left out, so the
chain stays visibly connected without leaking customers or suppliers.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt

TRANSFORM_PURPOSES = ("Manufacture", "Repack")
CREATING_VOUCHERS = (
	"Purchase Receipt",
	"Purchase Invoice",
	"Stock Reconciliation",
	"Subcontracting Receipt",
)
CUSTOMER_VOUCHERS = ("Delivery Note", "Sales Invoice", "POS Invoice")
RESTRICTED = _("(restricted)")
MAX_ROWS = 500


def _rows(batch_no: str, inward: bool) -> list[frappe._dict]:
	"""Submitted, non-cancelled bundle rows of a batch, grouped by voucher (qty as a positive number)."""
	sbb = frappe.qb.DocType("Serial and Batch Bundle")
	sbe = frappe.qb.DocType("Serial and Batch Entry")
	data = (
		frappe.qb.from_(sbe)
		.inner_join(sbb)
		.on(sbb.name == sbe.parent)
		.select(
			sbe.batch_no,
			sbe.qty,
			sbb.item_code,
			sbb.voucher_type,
			sbb.voucher_no,
			sbb.posting_datetime,
		)
		.where(sbe.batch_no == batch_no)
		.where(sbb.docstatus == 1)
		.where(sbb.is_cancelled == 0)
		.where(sbb.type_of_transaction == ("Inward" if inward else "Outward"))
		.orderby(sbb.posting_datetime)
	).run(as_dict=True)
	grouped: dict[tuple, frappe._dict] = {}
	for r in data:
		key = (r.voucher_type, r.voucher_no, r.item_code)
		if key in grouped:
			grouped[key].qty += abs(flt(r.qty))
		else:
			grouped[key] = frappe._dict(r, qty=abs(flt(r.qty)))
	return list(grouped.values())


def _voucher_rows(voucher_type: str, voucher_no: str, inward: bool) -> list[frappe._dict]:
	"""Batch rows a voucher moved in (outputs) or out (consumption), grouped per batch."""
	sbb = frappe.qb.DocType("Serial and Batch Bundle")
	sbe = frappe.qb.DocType("Serial and Batch Entry")
	data = (
		frappe.qb.from_(sbe)
		.inner_join(sbb)
		.on(sbb.name == sbe.parent)
		.select(sbe.batch_no, sbe.qty, sbb.item_code)
		.where(sbb.voucher_type == voucher_type)
		.where(sbb.voucher_no == voucher_no)
		.where(sbb.docstatus == 1)
		.where(sbb.is_cancelled == 0)
		.where(sbb.type_of_transaction == ("Inward" if inward else "Outward"))
		.where(sbe.batch_no.isnotnull())
	).run(as_dict=True)
	grouped: dict[str, frappe._dict] = {}
	for r in data:
		if r.batch_no in grouped:
			grouped[r.batch_no].qty += abs(flt(r.qty))
		else:
			grouped[r.batch_no] = frappe._dict(r, qty=abs(flt(r.qty)))
	return list(grouped.values())


def _can_read(doctype: str, name: str) -> bool:
	return bool(frappe.has_permission(doctype, "read", doc=name))


def _stock_entry(name: str) -> frappe._dict:
	return frappe.db.get_value(
		"Stock Entry", name, ["purpose", "posting_date", "work_order", "stock_entry_type"], as_dict=True
	) or frappe._dict()


def _describe(batch_no: str, item_code: str, qty: float, voucher_type: str, voucher_no: str, level: int, event: str, direction: str) -> dict:
	"""One output row. Party and document are masked unless the caller may read the document."""
	readable = _can_read(voucher_type, voucher_no)
	batch = frappe.db.get_value(
		"Batch",
		batch_no,
		["item", "manufacturing_date", "expiry_date", "fo_qc_status", "fo_supplier_batch_no", "supplier"],
		as_dict=True,
	) or frappe._dict()
	party_type = party = posting_date = work_order = None
	if readable:
		if voucher_type == "Stock Entry":
			se = _stock_entry(voucher_no)
			posting_date, work_order = se.get("posting_date"), se.get("work_order")
		elif voucher_type == "Purchase Receipt":
			party_type = "Supplier"
			party, posting_date = frappe.db.get_value("Purchase Receipt", voucher_no, ["supplier", "posting_date"])
		elif voucher_type in CUSTOMER_VOUCHERS:
			party_type = "Customer"
			party, posting_date = frappe.db.get_value(voucher_type, voucher_no, ["customer", "posting_date"])
		else:
			# any other voucher: its own date field if it has one (not every DocType has posting_date)
			meta = frappe.get_meta(voucher_type)
			field = next((f for f in ("posting_date", "transaction_date") if meta.has_field(f)), None)
			posting_date = frappe.db.get_value(voucher_type, voucher_no, field) if field else None
	return {
		"level": level,
		"direction": direction,
		"batch_no": batch_no,
		"item_code": item_code or batch.get("item"),
		"qty": flt(qty, 6),
		"event": event,
		"voucher_type": voucher_type if readable else None,
		"voucher_no": voucher_no if readable else RESTRICTED,
		"posting_date": posting_date,
		"party_type": party_type,
		"party": party if readable else (RESTRICTED if voucher_type in ("Purchase Receipt", *CUSTOMER_VOUCHERS) else None),
		"work_order": work_order,
		"supplier_batch_no": batch.get("fo_supplier_batch_no"),
		"manufacturing_date": batch.get("manufacturing_date"),
		"expiry_date": batch.get("expiry_date"),
		"qc_status": batch.get("fo_qc_status"),
	}


def _creation_events(batch_no: str) -> list[frappe._dict]:
	"""Events that brought this batch into existence (or added to it), not plain moves."""
	events = []
	for r in _rows(batch_no, inward=True):
		if r.voucher_type == "Stock Entry":
			se = _stock_entry(r.voucher_no)
			purpose = se.get("purpose")
			if purpose in TRANSFORM_PURPOSES:
				events.append(frappe._dict(r, kind="transform", event=purpose))
			elif purpose == "Material Receipt":
				events.append(frappe._dict(r, kind="origin", event="Material Receipt"))
		elif r.voucher_type in CREATING_VOUCHERS:
			if r.voucher_type in ("Purchase Receipt", "Purchase Invoice") and frappe.db.get_value(
				r.voucher_type, r.voucher_no, "is_return"
			):
				continue
			events.append(frappe._dict(r, kind="origin", event=r.voucher_type))
	return events


def trace_back(batch_no: str, max_depth: int = 10, max_rows: int = MAX_ROWS) -> dict:
	"""What went into ``batch_no``, recursively, down to receipts."""
	frappe.has_permission("Batch", "read", doc=batch_no, throw=True)
	out: list[dict] = []
	truncated = False

	def walk(batch: str, level: int, path: tuple[str, ...]):
		nonlocal truncated
		for ev in _creation_events(batch):
			if len(out) >= max_rows:
				truncated = True
				return
			out.append(
				_describe(batch, ev.item_code, ev.qty, ev.voucher_type, ev.voucher_no, level, ev.event, "back")
			)
			if ev.kind == "transform" and level < max_depth:
				for inp in _voucher_rows(ev.voucher_type, ev.voucher_no, inward=False):
					if inp.batch_no in path or inp.batch_no == batch:
						continue  # cycle guard
					walk(inp.batch_no, level + 1, path + (batch,))
			elif ev.kind == "transform":
				truncated = True

	walk(batch_no, 0, ())
	return {"batch_no": batch_no, "direction": "back", "rows": out, "truncated": truncated}


def trace_forward(batch_no: str, max_depth: int = 10, max_rows: int = MAX_ROWS) -> dict:
	"""Where ``batch_no`` went: consumed into other batches (recursively), delivered, issued, returned."""
	frappe.has_permission("Batch", "read", doc=batch_no, throw=True)
	out: list[dict] = []
	truncated = False

	def walk(batch: str, level: int, path: tuple[str, ...], made: frappe._dict | None = None):
		nonlocal truncated
		if made:  # a batch produced from the one above is a node of its own, even if nothing left it yet
			out.append(_describe(batch, made.item_code, made.qty, "Stock Entry", made.voucher_no, level, f"Produced by {made.purpose}", "forward"))
		for r in _rows(batch, inward=False):
			if len(out) >= max_rows:
				truncated = True
				return
			if r.voucher_type == "Stock Entry":
				purpose = _stock_entry(r.voucher_no).get("purpose")
				if purpose in TRANSFORM_PURPOSES:
					out.append(_describe(batch, r.item_code, r.qty, "Stock Entry", r.voucher_no, level, f"Consumed in {purpose}", "forward"))
					if level < max_depth:
						for produced in _voucher_rows("Stock Entry", r.voucher_no, inward=True):
							if produced.batch_no in path or produced.batch_no == batch:
								continue
							walk(
								produced.batch_no,
								level + 1,
								path + (batch,),
								frappe._dict(produced, voucher_no=r.voucher_no, purpose=purpose),
							)
					else:
						truncated = True
				elif purpose == "Material Issue":
					out.append(_describe(batch, r.item_code, r.qty, "Stock Entry", r.voucher_no, level, "Issued (Material Issue)", "forward"))
				# transfers, QC release and transfer-to-WIP move the batch; they end nothing
			elif r.voucher_type in CUSTOMER_VOUCHERS:
				out.append(_describe(batch, r.item_code, r.qty, r.voucher_type, r.voucher_no, level, "Delivered", "forward"))
			elif r.voucher_type in ("Purchase Receipt", "Purchase Invoice"):
				out.append(_describe(batch, r.item_code, r.qty, r.voucher_type, r.voucher_no, level, "Returned to supplier", "forward"))
			else:
				out.append(_describe(batch, r.item_code, r.qty, r.voucher_type, r.voucher_no, level, r.voucher_type, "forward"))

	walk(batch_no, 0, ())
	return {"batch_no": batch_no, "direction": "forward", "rows": out, "truncated": truncated}


@frappe.whitelist()
def trace(batch_no: str, direction: str = "both", max_depth: int = 10) -> dict:
	"""Whitelisted entry for Desk. ``direction``: back, forward or both."""
	if direction not in ("back", "forward", "both"):
		frappe.throw(_("Direction must be back, forward or both."))
	depth = max(1, min(int(max_depth), 25))
	result = {"batch_no": batch_no, "back": None, "forward": None}
	if direction in ("back", "both"):
		result["back"] = trace_back(batch_no, depth)
	if direction in ("forward", "both"):
		result["forward"] = trace_forward(batch_no, depth)
	return result


def summarise_by_event(rows: list[dict]) -> dict[str, int]:
	counts: dict[str, int] = defaultdict(int)
	for r in rows:
		counts[r["event"]] += 1
	return dict(counts)
