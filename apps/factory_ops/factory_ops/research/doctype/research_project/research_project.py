import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from factory_ops.research.doctype.formulation.formulation import DEMO_LABEL, ROLE_MANAGER, has_any_role

APPROVED_STATES = ("Approved", "In Progress", "Completed")


class ResearchProject(Document):
	def validate(self):
		before = None if self.is_new() else self.get_doc_before_save()
		was = before.status if before else "Proposed"
		if self.status in APPROVED_STATES and was not in APPROVED_STATES:
			if not has_any_role({ROLE_MANAGER}):
				frappe.throw(_("Only an R&D Manager may approve a research project."), frappe.PermissionError)
			self.approved_by = frappe.session.user
			self.approved_on = now_datetime()
		if self.is_demo and "DEMO" not in (self.title or ""):
			self.title = f"{self.title} ({DEMO_LABEL})"
