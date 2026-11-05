import frappe
from frappe import _
from frappe.model.document import Document

from factory_ops.marketing.workflow import ROLE_MANAGER, ROLE_USER, apply, guard_status, stamp_decision

B = {ROLE_MANAGER, ROLE_USER}
M = {ROLE_MANAGER}
ACTIONS = {
	"send_for_approval": ({"Draft", "Rejected"}, "Awaiting Approval", B, ()),
	"approve": ({"Awaiting Approval"}, "Approved", M, ("decision",)),
	"reject": ({"Awaiting Approval"}, "Rejected", M, ("decision", "note")),
	"mark_live": ({"Approved", "Paused"}, "Live", B, ()),
	"pause": ({"Live"}, "Paused", B, ()),
	"complete": ({"Live", "Paused", "Approved"}, "Completed", B, ()),
}


class MarketingCampaign(Document):
	def validate(self):
		guard_status(self)
		if self.end_date and self.start_date and self.end_date < self.start_date:
			frappe.throw(_("The campaign ends before it starts."))
		if self.budget is not None and self.budget < 0:
			frappe.throw(_("The budget cannot be negative."))
		if self.is_demo and "DEMO" not in (self.campaign_name or ""):
			self.campaign_name = f"DEMO - {self.campaign_name}"

	@frappe.whitelist()
	def apply_action(self, action: str, note: str | None = None):
		target = apply(self, ACTIONS, action, note, submitter_field="submitted_by")
		if action == "send_for_approval":
			self.submitted_by = frappe.session.user
		if action in ("approve", "reject"):
			stamp_decision(self, note)
		self.status = target
		self.flags.fo_transition = True
		self.save()
		self.add_comment("Info", f"{action.replace('_', ' ')} -> {target}" + (f": {note}" if note else ""))
		return self.status
