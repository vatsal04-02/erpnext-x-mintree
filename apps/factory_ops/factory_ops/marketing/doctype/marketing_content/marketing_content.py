import frappe
from frappe import _
from frappe.model.document import Document

from factory_ops.marketing.workflow import ROLE_MANAGER, ROLE_USER, apply, guard_status, stamp_decision

B = {ROLE_MANAGER, ROLE_USER}
M = {ROLE_MANAGER}
ACTIONS = {
	"start_production": ({"Idea", "Rejected"}, "In Production", B, ()),
	"send_for_review": ({"In Production", "Idea"}, "In Review", B, ()),
	"approve": ({"In Review"}, "Approved", M, ("decision",)),
	"reject": ({"In Review"}, "Rejected", M, ("decision", "note")),
	"schedule": ({"Approved"}, "Scheduled", B, ()),
	"mark_published": ({"Scheduled", "Approved"}, "Published", B, ()),
}


class MarketingContent(Document):
	def validate(self):
		guard_status(self)
		if self.is_demo and "DEMO" not in (self.title or ""):
			self.title = f"DEMO - {self.title}"

	@frappe.whitelist()
	def apply_action(self, action: str, note: str | None = None):
		if action == "send_for_review" and not (self.caption or self.creative):
			frappe.throw(_("Add the creative or the draft caption before review."))
		if action == "mark_published" and not self.published_url:
			frappe.throw(_("Enter where it was published (URL). Publishing is done by hand: no publishing connection exists."))
		target = apply(self, ACTIONS, action, note, submitter_field="assigned_to")
		if action in ("approve", "reject"):
			stamp_decision(self, note)
		self.status = target
		self.flags.fo_transition = True
		self.save()
		return self.status
