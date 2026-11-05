# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from factory_ops.constants import ROLE_QA_MANAGER


class QCDeviation(Document):
	def validate(self):
		batch_item = frappe.db.get_value("Batch", self.batch_no, "item")
		if batch_item != self.item_code:
			frappe.throw(
				_("Batch {0} belongs to item {1}, not {2}.").format(self.batch_no, batch_item, self.item_code)
			)

	def before_submit(self):
		# Approval = submission. Only a QA Manager approves, and never their own deviation.
		if ROLE_QA_MANAGER not in frappe.get_roles():
			frappe.throw(_("Only a QA Manager can approve a QC Deviation."), frappe.PermissionError)
		if self.owner == frappe.session.user:
			frappe.throw(
				_("You raised this deviation, so another QA Manager must approve it."), frappe.PermissionError
			)
		self.approved_by = frappe.session.user
		self.approved_on = now_datetime()
