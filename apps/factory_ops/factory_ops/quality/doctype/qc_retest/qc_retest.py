# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""QC Retest ("Send for Re-test"): the only way rejected stock goes back into QC Hold.

A QA Manager decision. Submitting it moves the quantity from the Rejected warehouse into a QC
Hold warehouse in one transaction. From there it is held stock like any other: it becomes usable
only through a new QC Release with a new, accepted Quality Inspection (the old rejected QI is
bound to the old transfer and cannot be reused).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime

from factory_ops.constants import DECISION_REJECT, ROLE_QA_MANAGER
from factory_ops.inventory.guards import RETEST_FLAG
from factory_ops.inventory.stock_queries import batch_qty_in_warehouse, is_qc_hold, is_rejected
from factory_ops.quality.batch_status import recompute


class QCRetestError(frappe.ValidationError):
	pass


class QCRetest(Document):
	def validate(self):
		batch_item = frappe.db.get_value("Batch", self.batch_no, "item")
		if batch_item != self.item_code:
			frappe.throw(_("Batch {0} belongs to item {1}.").format(self.batch_no, batch_item), QCRetestError)
		if not is_rejected(self.rejected_warehouse):
			frappe.throw(_("{0} is not a Rejected warehouse.").format(self.rejected_warehouse), QCRetestError)
		self.set_rejection()
		if not self.target_warehouse and self.rejection:
			self.target_warehouse = frappe.db.get_value("QC Release", self.rejection, "source_warehouse")
		if not self.target_warehouse or not is_qc_hold(self.target_warehouse):
			frappe.throw(_("Re-tested material must go to a QC Hold warehouse."), QCRetestError)
		self.available_qty = batch_qty_in_warehouse(self.batch_no, self.rejected_warehouse)
		if flt(self.qty) <= 0 or flt(self.qty) > flt(self.available_qty):
			frappe.throw(
				_("Quantity must be between 0 and the {0} of batch {1} in {2}.").format(
					self.available_qty, self.batch_no, self.rejected_warehouse
				),
				QCRetestError,
			)
		if not (self.reason or "").strip():
			frappe.throw(_("Give the reason for re-testing."), QCRetestError)

	def set_rejection(self):
		self.rejection = frappe.db.get_value(
			"QC Release",
			{"batch_no": self.batch_no, "decision": DECISION_REJECT, "docstatus": 1},
			"name",
			order_by="creation desc",
		)

	def before_submit(self):
		if ROLE_QA_MANAGER not in frappe.get_roles():
			frappe.throw(
				_("Only a QA Manager can send rejected material for re-test."), frappe.PermissionError
			)
		frappe.db.get_value("Batch", self.batch_no, "name", for_update=True)
		self.validate()
		self.requested_by = frappe.session.user
		self.requested_on = now_datetime()

	def on_submit(self):
		se = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"purpose": "Material Transfer",
				"stock_entry_type": "Material Transfer",
				"company": self.company,
				"fo_qc_retest": self.name,
				"remarks": _("Sent for re-test by {0}: {1}").format(self.name, self.reason),
				"items": [
					{
						"item_code": self.item_code,
						"qty": self.qty,
						"s_warehouse": self.rejected_warehouse,
						"t_warehouse": self.target_warehouse,
						"use_serial_batch_fields": 1,
						"batch_no": self.batch_no,
					}
				],
			}
		)
		se.flags.ignore_permissions = True
		frappe.flags[RETEST_FLAG] = self.name
		try:
			se.insert()
			se.submit()
		finally:
			frappe.flags.pop(RETEST_FLAG, None)
		self.db_set("stock_entry", se.name)
		recompute(self.batch_no)

	def on_cancel(self):
		if self.stock_entry and frappe.db.get_value("Stock Entry", self.stock_entry, "docstatus") == 1:
			se = frappe.get_doc("Stock Entry", self.stock_entry)
			se.flags.ignore_permissions = True
			frappe.flags[RETEST_FLAG] = self.name
			try:
				se.cancel()
			finally:
				frappe.flags.pop(RETEST_FLAG, None)
		recompute(self.batch_no)


@frappe.whitelist()
def send_for_retest(qc_release: str, reason: str, qty: float | None = None) -> str:
	""" "Send for Re-test" action on a submitted Reject QC Release. QA Manager only."""
	if ROLE_QA_MANAGER not in frappe.get_roles():
		frappe.throw(_("Only a QA Manager can send rejected material for re-test."), frappe.PermissionError)
	rel = frappe.get_doc("QC Release", qc_release)
	if rel.docstatus != 1 or rel.decision != DECISION_REJECT:
		frappe.throw(_("Only a submitted rejection can be sent for re-test."), QCRetestError)
	doc = frappe.get_doc(
		{
			"doctype": "QC Retest",
			"company": rel.company,
			"item_code": rel.item_code,
			"batch_no": rel.batch_no,
			"rejected_warehouse": rel.target_warehouse,
			"target_warehouse": rel.source_warehouse,
			"qty": flt(qty) or batch_qty_in_warehouse(rel.batch_no, rel.target_warehouse),
			"reason": reason,
		}
	)
	doc.insert()
	doc.submit()
	return doc.name
