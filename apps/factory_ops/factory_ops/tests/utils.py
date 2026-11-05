"""Shared test fixtures for factory_ops.

Built on ERPNext's own test bootstrap (``_Test Company``, abbr ``_TC``). The factory
warehouse tree is created with the same ``ensure_warehouse_tree`` the real site uses, so
tests exercise production setup code. Master data created in ``setUpClass`` is committed
(idempotent get-or-create); each test's transactions are rolled back in ``tearDown``.
"""

from contextlib import contextmanager

import frappe
from erpnext.tests.utils import ERPNextTestSuite
from frappe.utils import add_days, nowdate

from factory_ops.constants import ROLE_QA_MANAGER, ROLE_QC_OFFICER
from factory_ops.setup.sample_company import configure_stock_settings, setup_company_masters

COMPANY = "_Test Company"

QC_ITEM = "_FO Test RM Niacinamide"  # QC-required raw material, batch + expiry
FG_ITEM = "_FO Test FG Serum 30 mL"  # QC-required finished good
PLAIN_ITEM = "_FO Test Plain Batch Item"  # batch item without QC requirement (spike)

USER_QC = "fo-qc-officer@example.com"
USER_QA = "fo-qa-manager@example.com"
USER_STORES = "fo-stores@example.com"
USER_PRODUCTION = "fo-production@example.com"
USER_SALES = "fo-sales@example.com"
USER_QC_RECEIVER = "fo-qc-receiver@example.com"  # QC officer who also receives goods
USER_BUYER = "fo-buyer@example.com"  # raises purchase documents, no approval limit
USER_APPROVER_LOW = "fo-approver-low@example.com"  # limit 10,000
USER_APPROVER_HIGH = "fo-approver-high@example.com"  # limit 10,00,000
USER_ACCOUNTS = "fo-accounts@example.com"  # Accounts User + Accounts Manager
USER_SALES_MANAGER = "fo-sales-manager@example.com"  # Sales Manager = credit controller
USER_DISPATCH = "fo-dispatch@example.com"  # Stock User + Delivery User (standard dispatch roles)
USER_MANAGEMENT = "fo-management@example.com"  # Management Viewer (read and report only)
LIMIT_LOW = 10_000
LIMIT_HIGH = 1_000_000


def ensure_approval_limit(user: str, limit: float) -> None:
	if frappe.db.exists("Purchase Approval Limit", user):
		frappe.db.set_value("Purchase Approval Limit", user, {"approval_limit": limit, "enabled": 1})
	else:
		frappe.get_doc({"doctype": "Purchase Approval Limit", "user": user, "approval_limit": limit}).insert(
			ignore_permissions=True
		)


def ensure_user(email: str, roles: list[str]) -> str:
	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
				"user_type": "System User",
			}
		)
		user.insert(ignore_permissions=True)
	user = frappe.get_doc("User", email)
	user.add_roles(*roles)
	return email


def ensure_batch_item(item_code: str, uom: str = "Kg", qc_required: int = 1, shelf_life: int = 365) -> str:
	if not frappe.db.exists("Item", item_code):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": item_code,
				"item_name": item_code,
				"item_group": "_Test Item Group",
				"stock_uom": uom,
				"is_stock_item": 1,
				"has_batch_no": 1,
				"create_new_batch": 1,
				"batch_number_series": "FOTB-.#####",
				"has_expiry_date": 1,
				"shelf_life_in_days": shelf_life,
				"valuation_rate": 100,
				"fo_qc_required": qc_required,
			}
		).insert(ignore_permissions=True)
	return item_code


def ensure_plain_warehouse(name: str) -> str:
	full = f"{name} - _TC"
	if not frappe.db.exists("Warehouse", full):
		frappe.get_doc(
			{
				"doctype": "Warehouse",
				"warehouse_name": name,
				"company": COMPANY,
				"parent_warehouse": "All Warehouses - _TC",
			}
		).insert(ignore_permissions=True)
	return full


def ensure_perpetual_company(name: str, abbr: str) -> str:
	"""A company created the normal ERPNext way (standard chart, perpetual inventory on), as the
	SAMPLE company is. ERPNext's own _Test Company has perpetual inventory off."""
	if not frappe.db.exists("Company", name):
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": name,
				"abbr": abbr,
				"default_currency": "INR",
				"country": "India",
				"create_chart_of_accounts_based_on": "Standard Template",
				"chart_of_accounts": "Standard",
				"enable_perpetual_inventory": 1,
			}
		).insert(ignore_permissions=True)
	return name


class FactoryOpsTestCase(ERPNextTestSuite):
	company = COMPANY

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		if cls.company != COMPANY:
			ensure_perpetual_company(cls.company, cls.company_abbr)
		cls.wh = setup_company_masters(cls.company)  # same code as the SAMPLE dev site
		ensure_batch_item(QC_ITEM)
		ensure_batch_item(FG_ITEM, uom="Nos")
		ensure_batch_item(PLAIN_ITEM, qc_required=0)
		ensure_user(USER_QC, [ROLE_QC_OFFICER])
		ensure_user(USER_QA, [ROLE_QC_OFFICER, ROLE_QA_MANAGER])
		ensure_user(USER_STORES, ["Stock User", "Stock Manager", "Purchase User"])
		ensure_user(USER_PRODUCTION, ["Manufacturing User", "Stock User"])
		ensure_user(USER_SALES, ["Sales User", "Stock User"])
		ensure_user(USER_QC_RECEIVER, [ROLE_QC_OFFICER, "Stock User"])
		ensure_user(USER_BUYER, ["Purchase User"])
		ensure_user(USER_APPROVER_LOW, ["Purchase Manager", "Purchase User"])
		ensure_user(USER_APPROVER_HIGH, ["Purchase Manager", "Purchase User"])
		ensure_user(USER_ACCOUNTS, ["Accounts User", "Accounts Manager"])
		ensure_user(USER_SALES_MANAGER, ["Sales Manager", "Sales User", "Stock User"])
		ensure_user(USER_DISPATCH, ["Stock User", "Delivery User"])
		ensure_user(USER_MANAGEMENT, ["Management Viewer"])
		ensure_approval_limit(USER_APPROVER_LOW, LIMIT_LOW)
		ensure_approval_limit(USER_APPROVER_HIGH, LIMIT_HIGH)
		# the factory's Stock Settings, as on the real site, except the valuation method: ERPNext's
		# test bootstrap saves FIFO on every run and refuses to change it once transactions exist,
		# so a different method here made every later run on the same site fail (D-023)
		configure_stock_settings(skip=("valuation_method",))
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.flags.pop("fo_qc_release_in_progress", None)
		super().tearDown()

	# ---- transaction helpers -------------------------------------------------
	def receive(
		self,
		item_code: str,
		warehouse: str,
		qty: float,
		user: str = USER_STORES,
		rate: float = 100,
		expiry_days: int | None = None,
	) -> tuple[str, str]:
		"""Material Receipt of a new batch. Returns (stock entry name, batch no)."""
		batch_no = None
		if expiry_days is not None:
			batch_no = (
				frappe.get_doc(
					{
						"doctype": "Batch",
						"item": item_code,
						"expiry_date": add_days(nowdate(), expiry_days),
						"manufacturing_date": add_days(nowdate(), -10),
					}
				)
				.insert(ignore_permissions=True)
				.name
			)
		with self.set_user(user):
			se = frappe.get_doc(
				{
					"doctype": "Stock Entry",
					"purpose": "Material Receipt",
					"stock_entry_type": "Material Receipt",
					"company": self.company,
					"items": [
						{
							"item_code": item_code,
							"qty": qty,
							"t_warehouse": warehouse,
							"basic_rate": rate,
							"use_serial_batch_fields": 1 if batch_no else 0,
							"batch_no": batch_no,
						}
					],
				}
			)
			se.insert()
			se.submit()
		if not batch_no:
			se.reload()  # the bundle name is written during submit, not on the in-memory doc
			bundle = se.items[0].serial_and_batch_bundle
			self.assertTrue(bundle, "receipt created no Serial and Batch Bundle")
			batch_no = frappe.get_value("Serial and Batch Entry", {"parent": bundle}, "batch_no")
		self.assertTrue(batch_no, "receipt created no batch")
		return se.name, batch_no

	def transfer(self, item_code, batch_no, qty, src, tgt, submit=True, **extra):
		purpose = extra.pop("purpose", "Material Transfer")
		se = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"purpose": purpose,
				"stock_entry_type": purpose,
				"company": self.company,
				**extra,
				"items": [
					{
						"item_code": item_code,
						"qty": qty,
						"s_warehouse": src,
						"t_warehouse": tgt,
						"use_serial_batch_fields": 1,
						"batch_no": batch_no,
					}
				],
			}
		)
		se.insert()
		if submit:
			se.submit()
		return se

	def qc_release(
		self,
		batch_no: str,
		decision: str,
		qty: float | None = None,
		user: str = USER_QC,
		qi_status: str = "Accepted",
		source: str | None = None,
		submit: bool = True,
		item_code: str = QC_ITEM,
		**extra,
	):
		"""Create a QC Release with its Quality Inspection, as ``user``."""
		from factory_ops.quality.doctype.qc_release.qc_release import create_quality_inspection

		with self.set_user(user):
			doc = frappe.get_doc(
				{
					"doctype": "QC Release",
					"company": self.company,
					"item_code": item_code,
					"batch_no": batch_no,
					"source_warehouse": source or self.wh["RM-QC-HOLD"],
					"decision": decision,
					"qty": qty or 0,
					**extra,
				}
			)
			doc.insert()
			qi = frappe.get_doc("Quality Inspection", create_quality_inspection(doc.name))
			qi.manual_inspection = 1
			qi.status = qi_status
			qi.save()
			qi.submit()
			doc.reload()
			if submit:
				doc.submit()
		return doc


@contextmanager
def production_mode():
	"""Run code with Frappe's test-mode shortcuts switched off, as in production.

	Under the test runner Frappe skips behaviour that matters for the checks made here:
	``Document._save`` sets ``ignore_version = frappe.in_test`` (so no Version history is ever written,
	and a test database has no Version rows at all), and ``disable_saving_as_public()`` lets anyone edit
	public workspaces. Keep the block narrow: other code also reads ``frappe.in_test``."""
	was = frappe.in_test
	frappe.in_test = False
	try:
		yield
	finally:
		frappe.in_test = was


production_auditing = production_mode  # same switch, named for what a test is checking
