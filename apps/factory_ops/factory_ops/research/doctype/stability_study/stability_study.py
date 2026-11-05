import frappe
from frappe import _
from frappe.model.document import Document

from factory_ops.research.doctype.formulation.formulation import DEMO_LABEL


class StabilityStudy(Document):
	def validate(self):
		if frappe.db.get_value("Formulation", self.formulation, "is_demo"):
			self.is_demo = 1
		if self.is_demo and "DEMO" not in (self.title or ""):
			self.title = f"{self.title} ({DEMO_LABEL})"
		if self.duration_days is not None and self.duration_days <= 0:
			frappe.throw(_("The planned duration must be above zero days."))
		if self.status == "Completed":
			if self.result == "Pending":
				frappe.throw(_("A completed study needs a result (Pass or Fail)."))
			if not self.readings:
				frappe.throw(_("A completed study needs at least one reading."))
