"""Idempotent install/migrate setup for factory_ops.

Everything here can run any number of times: on install, on every migrate and from tests.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.permissions import add_permission, update_permission_property

from factory_ops.constants import FACTORY_ROLES, FACTORY_WAREHOUSE_TYPES, QC_ROLES, ROLE_QA_MANAGER, ROLE_QC_OFFICER
from factory_ops.setup.custom_fields import CUSTOM_FIELDS


def after_install():
	apply_setup()


def after_migrate():
	apply_setup()


def before_uninstall():
	# Custom fields stay in place on uninstall so historical documents keep their data.
	pass


def apply_setup():
	create_roles()
	create_warehouse_types()
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True, update=True)
	grant_qc_permissions()
	create_stock_entry_types()
	from factory_ops.setup.dashboards import ensure_sales_dashboard

	ensure_sales_dashboard()
	from factory_ops.setup.workspaces import ensure_workspaces

	ensure_workspaces()
	from factory_ops.setup.admin_access import ensure_admin_access

	ensure_admin_access()  # after workspaces and reports exist: read-only visibility, the two admin roles
	from factory_ops.setup.audit_trail import ensure_audit_trails

	ensure_audit_trails()
	from factory_ops.setup.landing import apply_to_existing_users

	apply_to_existing_users()
	from factory_ops.setup.branding import apply_branding

	apply_branding()
	ensure_single_defaults("Factory Ops Settings")
	from factory_ops.marketplace.setup import ensure_marketplace_channels

	ensure_marketplace_channels()
	if frappe.db.exists("DocType", "Meta Connection") and not frappe.db.exists("Meta Connection", "Meta Business"):
		frappe.get_doc({"doctype": "Meta Connection", "connection_name": "Meta Business"}).insert(ignore_permissions=True)
	frappe.clear_cache()


def ensure_single_defaults(doctype: str) -> None:
	"""A field added to an existing Single gets no row in tabSingles, so its JSON default never
	applies and a Check reads as 0. Write the default for every field that has never been set."""
	present = set(frappe.db.sql_list("select field from `tabSingles` where doctype=%s", doctype))
	for df in frappe.get_meta(doctype).fields:
		if (
			df.default is not None
			and df.fieldname not in present
			and df.fieldtype not in ("Section Break", "Column Break")
		):
			frappe.db.set_single_value(doctype, df.fieldname, df.default)


def create_stock_entry_types():
	"""The wastage write-off type (production/controls.py). Created once; editable in Desk."""
	if not frappe.db.exists("Stock Entry Type", "Wastage"):
		frappe.get_doc(
			{
				"doctype": "Stock Entry Type",
				"name": "Wastage",
				"purpose": "Material Issue",
				"fo_is_wastage": 1,
			}
		).insert(ignore_permissions=True)


def create_roles():
	for role in FACTORY_ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc(
				{"doctype": "Role", "role_name": role, "desk_access": 1, "two_factor_auth": 0}
			).insert(ignore_permissions=True)


def create_warehouse_types():
	for wh_type in FACTORY_WAREHOUSE_TYPES:
		if not frappe.db.exists("Warehouse Type", wh_type):
			frappe.get_doc({"doctype": "Warehouse Type", "name": wh_type}).insert(ignore_permissions=True)


# (doctype, role, permlevel 0 rights). Custom DocPerm rows; the standard ERPNext
# permissions on these DocTypes are left as they are.
QC_DOCPERMS = (
	("Quality Inspection", ROLE_QC_OFFICER, ("read", "write", "create", "submit", "print", "report")),
	(
		"Quality Inspection",
		ROLE_QA_MANAGER,
		("read", "write", "create", "submit", "cancel", "print", "report"),
	),
	("Quality Inspection Template", ROLE_QC_OFFICER, ("read",)),
	("Quality Inspection Template", ROLE_QA_MANAGER, ("read", "write", "create")),
	("Batch", ROLE_QC_OFFICER, ("read", "report")),
	("Batch", ROLE_QA_MANAGER, ("read", "report")),
	("Item", ROLE_QC_OFFICER, ("read",)),
	("Item", ROLE_QA_MANAGER, ("read",)),
	("Warehouse", ROLE_QC_OFFICER, ("read",)),
	("Warehouse", ROLE_QA_MANAGER, ("read",)),
	("Stock Entry", ROLE_QC_OFFICER, ("read",)),
	("Stock Entry", ROLE_QA_MANAGER, ("read",)),
	# report filters and link fields resolve the Company; without read the QC Queue opens
	# with a permission error (found in the browser check)
	("Company", ROLE_QC_OFFICER, ("read",)),
	("Company", ROLE_QA_MANAGER, ("read",)),
	# read-only ledger visibility for QC (Stock by QC Status, Stock Ledger reports)
	("Stock Ledger Entry", ROLE_QC_OFFICER, ("read", "report")),
	("Stock Ledger Entry", ROLE_QA_MANAGER, ("read", "report")),
	# ERPNext v16 lets only Item Manager create Batches, but a batch is auto-created when
	# stock is received or produced. Receivers and producers need create (not write).
	("Batch", "Stock User", ("read", "create")),
	("Batch", "Manufacturing User", ("read", "create")),
	# Batch Genealogy is a read-only report over batches these roles can already read; Frappe
	# also requires the "report" right on the report's reference DocType.
	("Batch", "Stock User", ("report",)),
	("Batch", "Manufacturing User", ("report",)),
	# Submitting the QC Release's transfer creates and cancels Serial and Batch Bundles, and
	# ERPNext checks the session user's rights on them. Same rights as Stock User has.
	("Serial and Batch Bundle", ROLE_QC_OFFICER, ("read", "create", "write", "submit", "cancel")),
	("Serial and Batch Bundle", ROLE_QA_MANAGER, ("read", "create", "write", "submit", "cancel")),
	# Milestone 5: sales operations import manually collected D2C / marketplace orders with Data
	# Import (ERPNext allows only System Managers by default). Importing still needs Sales Order
	# create rights, and the duplicate-order rule applies to every imported row.
	("Data Import", "Sales Manager", ("read", "write", "create")),
)

# Maintenance on ERPNext's asset DocTypes, for ERPNext's own Maintenance Manager / User roles
# (MINTREE_MAINTENANCE_MODULE.md). ERPNext grants these DocTypes to Manufacturing and Quality roles
# only. A technician reports and works breakdowns and completes scheduled maintenance; the
# manager also plans schedules and approves breakdown closure (submit).
MAINT_DOCPERMS = (
	("Asset", "Maintenance User", ("read", "report")),
	("Asset", "Maintenance Manager", ("read", "write", "create", "submit", "report")),
	# submitting an Asset makes its first Asset Movement (placement at its location) as the user
	("Asset Movement", "Maintenance Manager", ("read", "write", "create", "submit")),
	("Asset Movement", "Maintenance User", ("read",)),
	("Asset Category", "Maintenance Manager", ("read", "create", "write")),
	("Asset Category", "Maintenance User", ("read",)),
	("Location", "Maintenance Manager", ("read", "create", "write")),
	("Location", "Maintenance User", ("read",)),
	# completing a task (submitting its log) makes ERPNext save the schedule's last-done and next-due
	# dates with the technician's rights
	("Asset Maintenance", "Maintenance User", ("read", "write", "report")),
	("Asset Maintenance", "Maintenance Manager", ("read", "write", "create", "report")),
	("Asset Maintenance Team", "Maintenance User", ("read",)),
	("Asset Maintenance Team", "Maintenance Manager", ("read", "write", "create")),
	# ... and insert the task's next occurrence
	("Asset Maintenance Log", "Maintenance User", ("read", "write", "create", "submit", "report")),
	("Asset Maintenance Log", "Maintenance Manager", ("read", "write", "create", "submit", "cancel", "report")),
	("Asset Repair", "Maintenance User", ("read", "write", "create", "report")),
	("Asset Repair", "Maintenance Manager", ("read", "write", "create", "submit", "cancel", "report")),
	("Item", "Maintenance User", ("read",)),
	("Item", "Maintenance Manager", ("read",)),
	("Company", "Maintenance User", ("read",)),
	("Company", "Maintenance Manager", ("read",)),
	("Warehouse", "Maintenance User", ("read",)),
	("Warehouse", "Maintenance Manager", ("read",)),
	("Bin", "Maintenance User", ("read",)),
	("Bin", "Maintenance Manager", ("read",)),
)

# Read access to permlevel-1 fields (Batch.fo_qc_status). Nobody is given write at
# level 1, so the derived QC status can only be set by server code.
LEVEL1_READ = (
	("Batch", ROLE_QC_OFFICER),
	("Batch", ROLE_QA_MANAGER),
	("Batch", "Stock User"),
	("Batch", "Stock Manager"),
	("Batch", "Item Manager"),
	("Batch", "Manufacturing User"),
	("Batch", "Sales User"),
)


def _ensure_perm_row(doctype: str, role: str, permlevel: int) -> None:
	if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": permlevel}):
		add_permission(doctype, role, permlevel)


def grant_qc_permissions():
	for doctype, role, rights in QC_DOCPERMS:
		if not frappe.db.exists("DocType", doctype):
			continue
		_ensure_perm_row(doctype, role, 0)
		for right in rights:
			update_permission_property(doctype, role, 0, right, 1, validate=False)
	for doctype, role, rights in MAINT_DOCPERMS:
		if not frappe.db.exists("DocType", doctype) or not frappe.db.exists("Role", role):
			continue
		_ensure_perm_row(doctype, role, 0)
		for right in rights:
			update_permission_property(doctype, role, 0, right, 1, validate=False)
	for doctype, role in LEVEL1_READ:
		_ensure_perm_row(doctype, role, 1)
		update_permission_property(doctype, role, 1, "read", 1, validate=False)
