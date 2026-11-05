import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class MarketplaceChannel(Document):
	def validate(self):
		from factory_ops.marketplace.connectors import connector_for

		if not 0 <= flt(self.allocation_percent) <= 100:
			frappe.throw(_("The share must be between 0 and 100 %."))
		others = frappe.get_all("Marketplace Channel", filters={"name": ["!=", self.name]}, pluck="allocation_percent")
		total = flt(self.allocation_percent) + sum(flt(p) for p in others)
		if total > 100:
			frappe.throw(
				_("All channels together would publish {0}% of available stock; the total may not exceed 100 % (no unit is offered twice).").format(
					flt(total, 2)
				),
				title=_("Stock offered twice"),
			)
		if flt(self.safety_stock) < 0:
			frappe.throw(_("Safety stock cannot be negative."))
		connector = connector_for(self)
		self.access_note = connector.access_note
		# "Connected" is set only by a successful live test; credentials alone never set it
		if self.connection_state not in ("Connected", "Synchronising", "Error") or self.has_value_changed("client_id"):
			self.connection_state = connector.state()
