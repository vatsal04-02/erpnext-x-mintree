import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

REQUIRED = ("business_id", "ad_account_id", "app_id", "app_secret", "system_user_token")
NOTE = (
	"Needs a Meta app with Marketing API access approved for the business, a system user token with "
	"ads_read (and ads_management only if publishing is ever approved), and the ad account ID. To be "
	"verified against Meta's current documentation when a real account is provided."
)


class MetaConnection(Document):
	def _has(self, f):
		if self.meta.get_field(f).fieldtype == "Password":
			return bool(self.get_password(f, raise_exception=False))
		return bool(self.get(f))

	def validate(self):
		present = [f for f in REQUIRED if self._has(f)]
		self.access_note = NOTE
		if self.connection_state != "Connected":
			self.connection_state = "Not configured" if not present else ("Awaiting credentials" if len(present) < len(REQUIRED) else "Not implemented")

	@frappe.whitelist()
	def test_connection(self):
		self.check_permission("write")
		self.db_set({"last_tested_at": now_datetime()})
		msg = _(
			"Not sent. State: {0}. No Meta API call is implemented yet; it is written only after Meta's current "
			"documentation is verified with a real, authorised ad account. No figures are fetched or invented."
		).format(self.connection_state)
		self.db_set("last_error", msg)
		return {"status": "Skipped", "message": msg}
