"""Shelf-life segregation of finished goods (Milestone 5, DECISIONS D-028).

ERPNext's FEFO batch selection (reservations, Pick Lists, Delivery Notes) skips *expired* batches
but knows nothing of a *minimum remaining shelf life for dispatch*: it would offer a batch with 20
days left first, and the dispatch guard (G6) would then refuse the whole document. Instead of
changing ERPNext's picker, batches of sales items that are expired or below the minimum shelf
life are moved, by an ordinary stock transfer, out of the sellable (Usable) warehouses into the
company's NOT-FOR-DISPATCH warehouse. FEFO then only ever sees dispatchable batches.

Runs daily (batches cross the threshold at day boundaries) and on demand. Stock already
reserved for a Sales Order is left where it is (reported, not moved); the dispatch guard still
refuses to ship it if it is short-dated.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, flt, nowdate

from factory_ops.constants import WH_TYPE_NOT_FOR_DISPATCH, WH_TYPE_USABLE
from factory_ops.inventory.batch_eligibility import min_shelf_life_days
from factory_ops.inventory.stock_queries import LEDGER_BUNDLE

REMARK = "Shelf-life segregation: expired or below the minimum shelf life for dispatch (factory_ops)"


def undispatchable_batches(company: str) -> list[frappe._dict]:
	"""Batches of sales items in Usable warehouses that expire before today + minimum shelf life."""
	cutoff = add_days(nowdate(), min_shelf_life_days())
	return frappe.db.sql(
		f"""
		select sbb.item_code, sbe.warehouse, sbe.batch_no, b.expiry_date, sum(sbe.qty) as qty
		from `tabSerial and Batch Entry` sbe
		inner join `tabSerial and Batch Bundle` sbb on sbb.name = sbe.parent
		inner join `tabWarehouse` w on w.name = sbe.warehouse
		inner join `tabBatch` b on b.name = sbe.batch_no
		inner join `tabItem` i on i.name = sbb.item_code
		where sbb.docstatus = 1 and sbb.is_cancelled = 0 and {LEDGER_BUNDLE} and w.company = %(company)s
			and w.warehouse_type = %(usable)s and i.is_sales_item = 1
			and b.expiry_date is not null and b.expiry_date < %(cutoff)s
		group by sbb.item_code, sbe.warehouse, sbe.batch_no
		having sum(sbe.qty) > 0
		order by b.expiry_date
		""",
		{"company": company, "usable": WH_TYPE_USABLE, "cutoff": cutoff},
		as_dict=True,
	)


def _reserved(company: str) -> tuple[dict, dict]:
	"""(qty reserved per pinned batch+warehouse, qty reserved per item+warehouse in total)."""
	pinned = defaultdict(float)
	for r in frappe.db.sql(
		"""select sbe.batch_no, sbe.warehouse, sum(sbe.qty - sbe.delivered_qty) as qty
		from `tabSerial and Batch Entry` sbe
		inner join `tabStock Reservation Entry` sre on sre.name = sbe.parent
		where sbe.parenttype = 'Stock Reservation Entry' and sre.docstatus = 1 and sre.company = %s
			and sre.status not in ('Delivered', 'Cancelled')
		group by sbe.batch_no, sbe.warehouse""",
		company,
		as_dict=True,
	):
		pinned[(r.batch_no, r.warehouse)] += flt(r.qty)
	total = defaultdict(float)
	for r in frappe.db.sql(
		"""select item_code, warehouse, sum(reserved_qty - delivered_qty) as qty
		from `tabStock Reservation Entry`
		where docstatus = 1 and company = %s and status not in ('Delivered', 'Cancelled')
		group by item_code, warehouse""",
		company,
		as_dict=True,
	):
		total[(r.item_code, r.warehouse)] += flt(r.qty)
	return pinned, total


def segregate_company(company: str) -> str | None:
	target = frappe.db.get_value(
		"Warehouse", {"company": company, "warehouse_type": WH_TYPE_NOT_FOR_DISPATCH, "is_group": 0}, "name"
	)
	if not target:
		return None
	pinned, reserved_total = _reserved(company)
	free = {}  # unreserved qty per item+warehouse, from the warehouse balance
	rows = []
	for b in undispatchable_batches(company):
		key = (b.item_code, b.warehouse)
		if key not in free:
			actual = flt(
				frappe.db.get_value("Bin", {"item_code": b.item_code, "warehouse": b.warehouse}, "actual_qty")
			)
			free[key] = actual - reserved_total.get(key, 0)
		qty = min(flt(b.qty) - pinned.get((b.batch_no, b.warehouse), 0), free[key])
		if qty <= 0:
			continue  # reserved for an order: left in place, the dispatch guard still applies
		free[key] -= qty
		rows.append(
			{
				"item_code": b.item_code,
				"qty": qty,
				"s_warehouse": b.warehouse,
				"t_warehouse": target,
				"use_serial_batch_fields": 1,
				"batch_no": b.batch_no,
			}
		)
	if not rows:
		return None
	se = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"purpose": "Material Transfer",
			"stock_entry_type": "Material Transfer",
			"company": company,
			"remarks": REMARK,
			"items": rows,
		}
	)
	se.flags.ignore_permissions = True
	se.insert()
	se.submit()
	return se.name


def segregate_undispatchable_stock(company: str | None = None) -> list[str]:
	"""Daily job (and on demand): one transfer per company that has undispatchable stock."""
	entries = []
	for co in [company] if company else frappe.get_all("Company", pluck="name"):
		try:
			name = segregate_company(co)
		except Exception:
			if company:
				raise
			frappe.log_error(title=f"Shelf-life segregation failed for {co}")
			continue
		if name:
			entries.append(name)
	return entries


@frappe.whitelist(methods=["POST"])
def segregate_now(company: str) -> list[str]:
	"""Desk action for Stock Managers, e.g. after changing the minimum shelf life."""
	if "Stock Manager" not in frappe.get_roles() and "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only a Stock Manager can segregate short-dated stock."), frappe.PermissionError)
	return segregate_undispatchable_stock(company)
