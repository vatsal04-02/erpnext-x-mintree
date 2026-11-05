import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime


class MarketplaceSettlement(Document):
	def validate(self):
		if self.period_from and self.period_to and self.period_from > self.period_to:
			frappe.throw(_("The period ends before it starts."))
		deductions = sum(flt(self.get(f)) for f in ("commission", "fees", "shipping_fees", "refunds", "tax_withheld"))
		if self.gross_sales and not self.net_payout:
			self.net_payout = flt(self.gross_sales) - deductions

	@frappe.whitelist()
	def reconcile(self):
		"""Compare the statement's gross sales with what ERPNext invoiced for this channel's orders
		placed in the period (submitted Sales Invoices against their sales orders)."""
		self.check_permission("write")
		orders = frappe.get_all(
			"Marketplace Order",
			filters={"channel": self.channel, "order_date": ["between", [self.period_from, f"{self.period_to} 23:59:59"]], "sales_order": ["is", "set"]},
			pluck="sales_order",
		)
		invoiced = 0.0
		if orders:
			invoiced = flt(
				frappe.db.sql(
					"""select coalesce(sum(sii.amount), 0) from `tabSales Invoice Item` sii
					join `tabSales Invoice` si on si.name = sii.parent
					where si.docstatus = 1 and sii.sales_order in %(orders)s""",
					{"orders": orders},
				)[0][0]
			)
		self.erp_invoiced = invoiced
		self.difference = flt(self.gross_sales) - invoiced
		self.reconciliation_status = "Matched" if abs(self.difference) < 0.5 else "Difference"
		self.reconciled_at = now_datetime()
		self.save()
		return self.reconciliation_status
