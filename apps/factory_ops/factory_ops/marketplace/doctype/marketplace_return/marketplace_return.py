import frappe
from frappe import _
from frappe.model.document import Document

from factory_ops.constants import WH_TYPE_QC_HOLD


class MarketplaceReturn(Document):
	def validate(self):
		channel = frappe.db.get_value("Marketplace Order", self.marketplace_order, "channel")
		if channel and channel != self.channel:
			frappe.throw(_("Order {0} belongs to {1}.").format(self.marketplace_order, channel))
		# returned goods are quarantined like any return (D-012): never straight back to sellable stock
		if self.received_into and frappe.db.get_value("Warehouse", self.received_into, "warehouse_type") != WH_TYPE_QC_HOLD:
			frappe.throw(_("Returned goods go into a QC Hold store (e.g. RETURNS-QC-HOLD), not {0}.").format(self.received_into))
		if self.status == "Refunded" and not self.refund_amount:
			frappe.throw(_("Record the refund amount."))
