import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

DEMO_SOURCE = "DEMO - NOT REAL"
META_SOURCE = "Meta API"


class CampaignPerformance(Document):
	def validate(self):
		if self.period_end < self.period_start:
			frappe.throw(_("The period ends before it starts."))
		if self.source == META_SOURCE and not self.flags.from_meta_sync:
			frappe.throw(_("Only a verified Meta connection may record figures as coming from the Meta API; none exists."))
		campaign_demo = frappe.db.get_value("Marketing Campaign", self.campaign, "is_demo")
		if (self.source == DEMO_SOURCE) != bool(campaign_demo):
			frappe.throw(_("DEMO figures belong only to DEMO campaigns, and a DEMO campaign has only DEMO figures."))
		self.is_demo = 1 if campaign_demo else 0
		for f in ("spend", "impressions", "reach", "clicks", "leads", "purchases", "purchase_value"):
			if flt(self.get(f)) < 0:
				frappe.throw(_("{0} cannot be negative.").format(self.meta.get_label(f)))
		# ratios only when their inputs exist; otherwise left empty, never zero-filled
		self.ctr = flt(self.clicks) / flt(self.impressions) * 100 if flt(self.impressions) else None
		self.cpc = flt(self.spend) / flt(self.clicks) if flt(self.clicks) and self.spend is not None else None
		self.cost_per_lead = flt(self.spend) / flt(self.leads) if flt(self.leads) and self.spend is not None else None
		self.roas = flt(self.purchase_value) / flt(self.spend) if flt(self.spend) and self.purchase_value is not None else None
