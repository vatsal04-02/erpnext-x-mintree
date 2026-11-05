# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class EscalationEvent(Document):
	"""One row per (queue entry, rule) already notified: what stops the same escalation being sent
	again every hour. Deleted when the underlying issue is resolved, so a recurrence notifies afresh."""
