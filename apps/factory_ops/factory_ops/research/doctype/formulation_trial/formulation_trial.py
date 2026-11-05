import frappe
from frappe import _
from frappe.model.document import Document

from factory_ops.research.doctype.formulation.formulation import DEMO_LABEL


class FormulationTrial(Document):
	def validate(self):
		status = frappe.db.get_value("Formulation", self.formulation, "status")
		if status not in ("Under Review", "Trial", "Approved"):
			frappe.throw(
				_("Trials are recorded for formulations under review or in trial; {0} is {1}.").format(
					self.formulation, status
				)
			)
		if frappe.db.get_value("Formulation", self.formulation, "is_demo"):
			self.is_demo = 1
		if self.is_demo and "DEMO" not in (self.title or ""):
			self.title = f"{self.title} ({DEMO_LABEL})"

	def before_submit(self):
		if self.outcome == "Pending":
			frappe.throw(_("Record the outcome (Pass, Fail or Inconclusive) before submitting the trial."))
		if not (self.observations or "").strip():
			frappe.throw(_("Record the observations before submitting the trial."))
