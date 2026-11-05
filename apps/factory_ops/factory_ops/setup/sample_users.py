"""SAMPLE users for a development or UAT site, one per factory role.

Development only. Passwords are random and written to the site's private folder
(``sites/<site>/private/sample_users.json``), never to the repository.

    bench --site factory.localhost execute factory_ops.setup.sample_users.create_sample_users
"""

import json
import os
import secrets

import frappe

from factory_ops.constants import ROLE_QA_MANAGER, ROLE_QC_OFFICER

# email: (first name, roles, purchase approval limit or None)
SAMPLE_USERS = {
	"sample.buyer@sample.invalid": ("SAMPLE Buyer", ["Purchase User"], None),
	"sample.approver@sample.invalid": (
		"SAMPLE Purchase Approver",
		["Purchase Manager", "Purchase User"],
		500_000,
	),
	"sample.stores@sample.invalid": ("SAMPLE Stores", ["Stock User", "Stock Manager"], None),
	"sample.qc@sample.invalid": ("SAMPLE QC Officer", [ROLE_QC_OFFICER], None),
	"sample.qa@sample.invalid": ("SAMPLE QA Manager", [ROLE_QC_OFFICER, ROLE_QA_MANAGER], None),
	"sample.production@sample.invalid": (
		"SAMPLE Production",
		["Manufacturing User", "Manufacturing Manager", "Stock User"],
		None,
	),
	"sample.dispatch@sample.invalid": ("SAMPLE Dispatch", ["Stock User", "Delivery User"], None),
	"sample.management@sample.invalid": ("SAMPLE Management (read only)", ["Management Viewer"], None),
	"sample.salesmanager@sample.invalid": (
		"SAMPLE Sales Manager",
		["Sales Manager", "Sales User", "Stock User"],
		None,
	),
	"sample.sales@sample.invalid": ("SAMPLE Sales", ["Sales User", "Stock User"], None),
	"sample.accounts@sample.invalid": ("SAMPLE Accounts", ["Accounts User", "Accounts Manager"], None),
	# Owns master data: the only SAMPLE user who may import Items, Suppliers, Customers, BOMs,
	# opening stock and prices (the importer runs with the user's own permissions, D-035).
	# R&D (MINTREE_RND_MODULE.md): the chemist drafts and records trials; the manager decides
	"sample.rnd@sample.invalid": ("SAMPLE R&D Chemist", ["R&D Chemist"], None),
	"sample.rndmanager@sample.invalid": ("SAMPLE R&D Manager", ["R&D Manager"], None),
	# Maintenance on ERPNext's Asset DocTypes (MINTREE_MAINTENANCE_MODULE.md); Stock User so a closed
	# breakdown can issue its spare parts from stock
	"sample.maintenance@sample.invalid": ("SAMPLE Maintenance Technician", ["Maintenance User", "Stock User"], None),
	"sample.maintmanager@sample.invalid": ("SAMPLE Maintenance Manager", ["Maintenance Manager", "Maintenance User", "Stock User"], None),
	# Marketplaces (MINTREE_ECOMMERCE_MODULE.md): Sales User so a marketplace order becomes a sales order
	"sample.marketplace@sample.invalid": ("SAMPLE Marketplace Manager", ["Marketplace Manager", "Marketplace User", "Sales User"], None),
	# Brand and marketing (MINTREE_META_MARKETING_MODULE.md): the executive drafts, the manager approves
	"sample.marketingexec@sample.invalid": ("SAMPLE Marketing Executive", ["Marketing User"], None),
	"sample.marketing@sample.invalid": ("SAMPLE Marketing Manager", ["Marketing Manager", "Marketing User"], None),
	"sample.masterdata@sample.invalid": (
		"SAMPLE Master Data",
		["Item Manager", "Purchase Master Manager", "Sales Master Manager", "Stock Manager", "Stock User"],
		None,
	),
}


def create_sample_users() -> str:
	# development sites, or a local demo site marked by deploy/demo/reset-demo.sh; never a real site
	if not (frappe.conf.get("developer_mode") or frappe.conf.get("fo_demo_site")):
		frappe.throw("Sample users are for development and demo sites only (developer_mode is off).")
	credentials = {}
	for email, (first_name, roles, limit) in SAMPLE_USERS.items():
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": first_name,
					"send_welcome_email": 0,
					"user_type": "System User",
				}
			).insert(ignore_permissions=True)
		user = frappe.get_doc("User", email)
		user.add_roles(*roles)
		password = secrets.token_urlsafe(12)
		user.new_password = password
		user.save(ignore_permissions=True)
		credentials[email] = password
		if limit is not None:
			if frappe.db.exists("Purchase Approval Limit", email):
				frappe.db.set_value("Purchase Approval Limit", email, {"approval_limit": limit, "enabled": 1})
			else:
				frappe.get_doc(
					{"doctype": "Purchase Approval Limit", "user": email, "approval_limit": limit}
				).insert(ignore_permissions=True)
	path = frappe.get_site_path("private", "sample_users.json")
	fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
	with os.fdopen(fd, "w") as f:
		json.dump(credentials, f, indent=1)
	os.chmod(path, 0o600)
	frappe.db.commit()
	return path
