import frappe
from frappe import _
from frappe.model.document import Document


class MarketplaceListing(Document):
	def validate(self):
		self.marketplace_sku = (self.marketplace_sku or "").strip()
		for field, label in (("marketplace_sku", _("marketplace SKU")), ("item_code", _("ERPNext item"))):
			dup = frappe.db.get_value(
				"Marketplace Listing", {"channel": self.channel, field: self.get(field), "name": ["!=", self.name]}
			)
			if dup:
				frappe.throw(_("{0} already has a listing for this {1} ({2}).").format(self.channel, label, dup))
		item = frappe.db.get_value("Item", self.item_code, ["is_sales_item", "disabled", "has_variants"], as_dict=True)
		if not item or item.disabled or not item.is_sales_item:
			frappe.throw(_("{0} is not an active sales item.").format(self.item_code))
		if item.has_variants:
			frappe.throw(_("{0} is a template; list the sellable variant item instead.").format(self.item_code))
