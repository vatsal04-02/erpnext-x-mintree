# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ManagementAction(Document):
	"""The audit trail of management actions. Written by the Decision Centre only; never edited or deleted."""

	def validate(self):
		if not self.is_new():
			frappe.throw(_("A management action record cannot be changed."), frappe.PermissionError)

	def on_trash(self):
		frappe.throw(_("A management action record cannot be deleted."), frappe.PermissionError)
