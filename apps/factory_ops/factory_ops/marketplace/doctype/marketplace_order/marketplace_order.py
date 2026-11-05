import frappe
from frappe import _
from frappe.model.document import Document


class MarketplaceOrder(Document):
	def validate(self):
		from factory_ops.marketplace.service import resolve_items

		self.external_order_id = (self.external_order_id or "").strip()
		dup = frappe.db.get_value(
			"Marketplace Order", {"channel": self.channel, "external_order_id": self.external_order_id, "name": ["!=", self.name]}
		)
		if dup:
			frappe.throw(_("Order {0} from {1} is already recorded as {2}.").format(self.external_order_id, self.channel, dup), title=_("Duplicate order"))
		for row in self.items:
			if not row.qty or row.qty <= 0:
				frappe.throw(_("Row {0}: quantity must be above zero.").format(row.idx))
		resolve_items(self)
		if self.is_demo and "DEMO" not in self.external_order_id:
			frappe.throw(_("Demo orders carry DEMO in their order ID so they can never pass for real ones."))

	@frappe.whitelist()
	def make_sales_order(self, company: str | None = None) -> str:
		from factory_ops.marketplace.service import create_sales_order

		return create_sales_order(self.name, company)
