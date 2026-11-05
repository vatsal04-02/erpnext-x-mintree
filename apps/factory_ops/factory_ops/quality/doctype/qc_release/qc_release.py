# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""QC Release: the only way stock leaves a QC Hold warehouse.

Lifecycle (verified by tests/test_spike_qi_lifecycle.py on ERPNext v16.53.0):

1. Save the QC Release (draft). Server code creates or updates a *draft* Material Transfer
   Stock Entry from the hold warehouse to the destination, linked by ``fo_qc_release``.
2. "Create Quality Inspection" makes a QI with reference = that draft Stock Entry. When the
   QI is submitted, ERPNext writes it onto the Stock Entry row.
3. Submitting the QC Release checks roles, segregation of duties, quantity (under a row
   lock), the QI and any deviation, then submits the Stock Entry in the same transaction.
   For Release and Partial Release the Stock Entry has ``inspection_required = 1``, so
   ERPNext's own check also refuses it unless the QI is submitted and Accepted.
4. Cancelling the QC Release cancels its Stock Entry (reversing ledger rows). ERPNext
   refuses if the released stock has already been consumed.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime

from factory_ops.constants import (
	DECISION_CONDITIONAL,
	DECISION_PARTIAL,
	DECISION_REJECT,
	DECISION_RELEASE,
	QC_ROLES,
	RELEASE_DECISIONS,
	ROLE_QA_MANAGER,
	WH_TYPE_REJECTED,
)
from factory_ops.inventory.guards import RELEASE_FLAG
from factory_ops.inventory.stock_queries import (
	batch_qty_in_warehouse,
	is_qc_hold,
	is_rejected,
	is_restricted,
)
from factory_ops.quality.batch_status import recompute


class QCReleaseError(frappe.ValidationError):
	pass


class SegregationOfDutiesError(frappe.PermissionError):
	pass


class QCRelease(Document):
	# ------------------------------------------------------------------ validate
	def validate(self):
		self.validate_batch()
		self.validate_source()
		self.set_available_qty()
		self.set_default_target()
		self.validate_target()
		self.validate_qty()
		self.validate_reason()
		self.set_origin()

	def validate_batch(self):
		batch_item = frappe.db.get_value("Batch", self.batch_no, "item")
		if batch_item != self.item_code:
			frappe.throw(
				_("Batch {0} belongs to item {1}, not {2}.").format(
					self.batch_no, batch_item, self.item_code
				),
				QCReleaseError,
			)

	def validate_source(self):
		if not is_qc_hold(self.source_warehouse):
			frappe.throw(_("{0} is not a QC Hold warehouse.").format(self.source_warehouse), QCReleaseError)
		wh_company = frappe.get_cached_value("Warehouse", self.source_warehouse, "company")
		if wh_company != self.company:
			frappe.throw(_("Warehouse {0} belongs to {1}.").format(self.source_warehouse, wh_company))

	def set_available_qty(self):
		self.available_qty = batch_qty_in_warehouse(self.batch_no, self.source_warehouse)

	def set_default_target(self):
		if self.target_warehouse:
			return
		if self.decision == DECISION_REJECT:
			self.target_warehouse = frappe.db.get_value(
				"Warehouse",
				{"company": self.company, "warehouse_type": WH_TYPE_REJECTED, "is_group": 0, "disabled": 0},
				"name",
			)
		else:
			self.target_warehouse = frappe.get_cached_value(
				"Warehouse", self.source_warehouse, "fo_release_warehouse"
			)

	def validate_target(self):
		if not self.target_warehouse:
			frappe.throw(_("Set a Destination Warehouse."), QCReleaseError)
		if self.decision == DECISION_REJECT:
			if not is_rejected(self.target_warehouse):
				frappe.throw(_("Rejected stock must go to a Rejected warehouse."), QCReleaseError)
		elif is_restricted(self.target_warehouse):
			frappe.throw(
				_("Released stock must go to a usable warehouse, not {0}.").format(self.target_warehouse),
				QCReleaseError,
			)

	def validate_qty(self):
		if self.decision in (DECISION_RELEASE, DECISION_CONDITIONAL) and not flt(self.qty):
			self.qty = self.available_qty
		if flt(self.qty) <= 0:
			frappe.throw(_("Quantity must be greater than zero."), QCReleaseError)
		if flt(self.qty) > flt(self.available_qty):
			frappe.throw(
				_("Only {0} of batch {1} is in {2}; cannot decide on {3}.").format(
					self.available_qty, self.batch_no, self.source_warehouse, self.qty
				),
				QCReleaseError,
			)
		if self.decision == DECISION_RELEASE and flt(self.qty) != flt(self.available_qty):
			frappe.throw(
				_("A full Release covers the whole {0} in hold. Use Partial Release for {1}.").format(
					self.available_qty, self.qty
				),
				QCReleaseError,
			)
		if self.decision == DECISION_PARTIAL and flt(self.qty) >= flt(self.available_qty):
			frappe.throw(_("A Partial Release must be less than the quantity in hold."), QCReleaseError)

	def validate_reason(self):
		if self.decision in (DECISION_REJECT, DECISION_CONDITIONAL) and not (self.reason or "").strip():
			frappe.throw(_("Give a reason for a {0}.").format(_(self.decision)), QCReleaseError)

	def set_origin(self):
		"""The first inward movement of the batch: who received or produced it."""
		origin = frappe.db.sql(
			"""
			select sbe.voucher_type, sbe.voucher_no
			from `tabSerial and Batch Entry` sbe
			inner join `tabSerial and Batch Bundle` sbb on sbb.name = sbe.parent
			where sbe.batch_no = %s and sbb.docstatus = 1 and sbb.is_cancelled = 0
				and sbb.type_of_transaction = 'Inward'
			order by sbb.posting_datetime asc, sbb.creation asc
			limit 1
			""",
			(self.batch_no,),
			as_dict=True,
		)
		if origin:
			self.origin_doctype = origin[0].voucher_type
			self.origin_document = origin[0].voucher_no
			self.origin_owner = frappe.db.get_value(self.origin_doctype, self.origin_document, "owner")

	# ------------------------------------------------------------ draft upkeep
	def on_update(self):
		if self.docstatus == 0:
			self.sync_stock_entry()

	def _inspection_required(self) -> int:
		return 1 if self.decision in (DECISION_RELEASE, DECISION_PARTIAL) else 0

	def sync_stock_entry(self):
		"""Create or update the draft transfer in place (keeping its row, which the QI points to)."""
		if self.stock_entry and frappe.db.get_value("Stock Entry", self.stock_entry, "docstatus") == 0:
			se = frappe.get_doc("Stock Entry", self.stock_entry)
		else:
			se = frappe.new_doc("Stock Entry")
			se.update(
				{
					"purpose": "Material Transfer",
					"stock_entry_type": "Material Transfer",
					"company": self.company,
					"fo_qc_release": self.name,
				}
			)
			se.append("items", {})
		se.inspection_required = self._inspection_required()
		se.remarks = _("QC {0} of batch {1} by {2}").format(_(self.decision), self.batch_no, self.name)
		row = se.items[0]
		row.update(
			{
				"item_code": self.item_code,
				"qty": self.qty,
				"s_warehouse": self.source_warehouse,
				"t_warehouse": self.target_warehouse,
				"use_serial_batch_fields": 1,
				"batch_no": self.batch_no,
			}
		)
		if self.quality_inspection:
			row.quality_inspection = self.quality_inspection
		se.flags.ignore_permissions = True
		se.save()
		if self.stock_entry != se.name:
			self.db_set("stock_entry", se.name, update_modified=False)

	# ----------------------------------------------------------------- submit
	def before_submit(self):
		self.check_roles()
		frappe.db.get_value("Batch", self.batch_no, "name", for_update=True)  # serialise decisions per batch
		self.set_available_qty()
		self.validate_qty()
		self.check_segregation()
		self.pull_quality_inspection()
		self.check_quality_inspection()
		self.check_deviation()
		self.sync_stock_entry()
		self.approved_by = frappe.session.user
		self.released_on = now_datetime()

	def check_roles(self):
		roles = set(frappe.get_roles())
		if not roles.intersection(QC_ROLES):
			frappe.throw(
				_("Only QC Officers and QA Managers can decide on held stock."), frappe.PermissionError
			)
		if self.decision == DECISION_CONDITIONAL and ROLE_QA_MANAGER not in roles:
			frappe.throw(_("Only a QA Manager can authorise a Conditional Release."), frappe.PermissionError)

	def check_segregation(self):
		if self.decision not in RELEASE_DECISIONS:
			return  # rejecting your own receipt is allowed; releasing it is not
		user = frappe.session.user
		blocked = {self.origin_owner}
		if self.origin_doctype == "Stock Entry" and self.origin_document:
			work_order = frappe.db.get_value("Stock Entry", self.origin_document, "work_order")
			if work_order:
				blocked.add(frappe.db.get_value("Work Order", work_order, "owner"))
		if user in blocked:
			frappe.throw(
				_("You received or produced batch {0}, so another QC officer must release it.").format(
					self.batch_no
				),
				SegregationOfDutiesError,
			)

	def pull_quality_inspection(self):
		if not self.quality_inspection and self.stock_entry:
			self.quality_inspection = frappe.db.get_value(
				"Stock Entry Detail", {"parent": self.stock_entry}, "quality_inspection"
			)

	def check_quality_inspection(self):
		if not self.quality_inspection:
			frappe.throw(_("Attach a submitted Quality Inspection before deciding."), QCReleaseError)
		qi = frappe.db.get_value(
			"Quality Inspection",
			self.quality_inspection,
			["docstatus", "status", "item_code", "batch_no", "reference_type", "reference_name"],
			as_dict=True,
		)
		if not qi or qi.docstatus != 1:
			frappe.throw(
				_("Quality Inspection {0} is not submitted.").format(self.quality_inspection), QCReleaseError
			)
		if qi.item_code != self.item_code or (qi.batch_no and qi.batch_no != self.batch_no):
			frappe.throw(
				_("Quality Inspection {0} is for a different item or batch.").format(self.quality_inspection),
				QCReleaseError,
			)
		if qi.reference_type != "Stock Entry" or qi.reference_name != self.stock_entry:
			frappe.throw(
				_("Quality Inspection {0} must reference this QC Release's Stock Entry {1}.").format(
					self.quality_inspection, self.stock_entry
				),
				QCReleaseError,
			)
		if self.decision in (DECISION_RELEASE, DECISION_PARTIAL) and qi.status != "Accepted":
			frappe.throw(
				_("Quality Inspection {0} is {1}; only Accepted stock can be released.").format(
					self.quality_inspection, qi.status
				),
				QCReleaseError,
			)
		if self.decision == DECISION_REJECT and qi.status != "Rejected":
			frappe.throw(
				_("Quality Inspection {0} is {1}; a rejection needs a Rejected inspection.").format(
					self.quality_inspection, qi.status
				),
				QCReleaseError,
			)

	def check_deviation(self):
		if self.decision != DECISION_CONDITIONAL:
			return
		if not self.qc_deviation:
			frappe.throw(_("A Conditional Release needs an approved QC Deviation."), QCReleaseError)
		dev = frappe.db.get_value("QC Deviation", self.qc_deviation, ["docstatus", "batch_no"], as_dict=True)
		if not dev or dev.docstatus != 1:
			frappe.throw(_("QC Deviation {0} is not approved.").format(self.qc_deviation), QCReleaseError)
		if dev.batch_no != self.batch_no:
			frappe.throw(
				_("QC Deviation {0} is for another batch.").format(self.qc_deviation), QCReleaseError
			)

	def on_submit(self):
		se = frappe.get_doc("Stock Entry", self.stock_entry)
		se.flags.ignore_permissions = True
		frappe.flags[RELEASE_FLAG] = self.name
		try:
			se.submit()
		finally:
			frappe.flags.pop(RELEASE_FLAG, None)
		recompute(self.batch_no)

	# ----------------------------------------------------------------- cancel
	def on_cancel(self):
		if self.stock_entry and frappe.db.get_value("Stock Entry", self.stock_entry, "docstatus") == 1:
			se = frappe.get_doc("Stock Entry", self.stock_entry)
			se.flags.ignore_permissions = True
			frappe.flags[RELEASE_FLAG] = self.name
			try:
				se.cancel()
			finally:
				frappe.flags.pop(RELEASE_FLAG, None)
		recompute(self.batch_no)

	def on_trash(self):
		if not self.stock_entry:
			return
		if frappe.db.get_value("Stock Entry", self.stock_entry, "docstatus") != 0:
			return
		for qi in frappe.get_all(
			"Quality Inspection",
			filters={"reference_type": "Stock Entry", "reference_name": self.stock_entry},
			fields=["name", "docstatus"],
		):
			if qi.docstatus == 1:
				frappe.throw(
					_("Cancel Quality Inspection {0} before deleting this QC Release.").format(qi.name)
				)
			frappe.delete_doc("Quality Inspection", qi.name, ignore_permissions=True)
		frappe.delete_doc("Stock Entry", self.stock_entry, ignore_permissions=True)


@frappe.whitelist()
def create_quality_inspection(qc_release: str, inspection_type: str | None = None) -> str:
	"""Create a draft Quality Inspection against the QC Release's draft Stock Entry. Without an
	explicit type: Incoming for RM / PM, In Process for BULK, Outgoing for FG (by item group)."""
	doc = frappe.get_doc("QC Release", qc_release)
	doc.check_permission("write")
	if not inspection_type:
		from factory_ops.constants import INSPECTION_TYPE_BY_GROUP
		from factory_ops.master_data.importer import _factory_group

		group = _factory_group(frappe.get_cached_value("Item", doc.item_code, "item_group"))
		inspection_type = INSPECTION_TYPE_BY_GROUP.get(group, "Incoming")
	if doc.docstatus != 0:
		frappe.throw(_("QC Release {0} is already decided.").format(doc.name))
	if not doc.stock_entry:
		doc.sync_stock_entry()
	qi = frappe.get_doc(
		{
			"doctype": "Quality Inspection",
			"inspection_type": inspection_type,
			"reference_type": "Stock Entry",
			"reference_name": doc.stock_entry,
			"item_code": doc.item_code,
			"batch_no": doc.batch_no,
			"sample_size": 1,
			"inspected_by": frappe.session.user,
			"quality_inspection_template": frappe.get_cached_value(
				"Item", doc.item_code, "quality_inspection_template"
			),
		}
	)
	qi.insert()
	doc.db_set("quality_inspection", qi.name)
	return qi.name
