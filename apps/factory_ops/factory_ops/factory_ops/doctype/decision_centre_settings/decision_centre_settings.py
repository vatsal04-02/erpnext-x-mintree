# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class DecisionCentreSettings(Document):
	def validate(self):
		from factory_ops.decisions.sources import all_kinds

		known = all_kinds()
		for row in self.rules or []:
			if row.kind not in known:
				frappe.throw(_("Row {0}: {1} is not a decision kind. Known kinds: {2}").format(row.idx, row.kind, ", ".join(sorted(known))))
			if row.enabled and not (row.notify_role or row.notify_user or (row.escalate_after_hours and row.escalate_to)):
				frappe.throw(_("Row {0}: an enabled rule needs someone to notify, or a priority to raise to after some hours.").format(row.idx))
			if row.escalate_after_hours and row.escalate_after_hours < 0:
				frappe.throw(_("Row {0}: hours cannot be negative.").format(row.idx))
