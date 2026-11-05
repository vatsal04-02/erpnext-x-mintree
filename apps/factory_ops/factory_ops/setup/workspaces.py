"""Role-based Desk workspaces (Milestone 6, Part C; DECISIONS D-036, D-037).

Each workspace is made of Frappe v16's native records, created and kept in step on every migrate:

- **Workspace**: the page (number cards, charts, shortcuts, link cards), limited to its roles;
- **Workspace Sidebar**: the left-hand menu (lists and reports);
- **Desktop Icon**: the tile on the Desk home screen, limited to the same roles.

Access is decided by ERPNext's standard roles and permissions: a workspace only *shows* links, and
every link still opens with the user's own rights, so a workspace can never widen access or bypass
a QC, batch, expiry or dispatch control. Cards and charts for documents a user may not read are
hidden by Frappe.

One role is added: **Management Viewer**, read and report only (no create, submit or approval
rights), for owners and senior management. ERPNext has no read-only "owner" role, and giving
managers Accounts / QA / Stock Manager roles would give them approval powers.
"""

import json

import frappe
from frappe.permissions import add_permission, update_permission_property

ROLE_MANAGEMENT = "Management Viewer"
ROLE_ADMIN = "System Manager"  # the System Administrator: sees every page; rights come from DocPerm, not the page
ROLE_BUSINESS_ADMIN = "Business Administrator"  # the owner: every business page, not the technical one
MODULE = "Factory Ops"

# --------------------------------------------------------------------------- number cards
# label: definition. "method" cards are factory_ops.setup.dashboard_data functions (ledger figures).
CARDS = {
	"FO RM Stock Value": {"label": "Raw Material Stock Value", "method": "rm_stock_value"},
	"FO PM Stock Value": {"label": "Packaging Stock Value", "method": "pm_stock_value"},
	"FO BULK Stock Value": {"label": "Bulk Stock Value", "method": "bulk_stock_value"},
	"FO FG Stock Value": {"label": "Finished Goods Stock Value", "method": "fg_stock_value"},
	"FO Rejected Stock Value": {"label": "Rejected Stock Value", "method": "rejected_stock_value"},
	"FO Wastage 30 Days": {
		"label": "Wastage Written Off (30 days)",
		"method": "wastage_value_30_days",
		"doctype": "Stock Entry",
	},
	"FO Batches Awaiting QC": {
		"label": "Batches Awaiting QC",
		"doctype": "Batch",
		"filters": [["fo_qc_status", "in", ["Hold", "Partly Released"]]],
	},
	"FO QC Releases Pending": {
		"label": "QC Decisions Pending",
		"doctype": "QC Release",
		"filters": [["docstatus", "=", 0]],
	},
	"FO Deviations Pending": {
		"label": "Deviations Awaiting Approval",
		"doctype": "QC Deviation",
		"filters": [["docstatus", "=", 0]],
	},
	"FO Rejected Batches": {
		"label": "Rejected Batches",
		"doctype": "Batch",
		"filters": [["fo_qc_status", "=", "Rejected"]],
	},
	"FO Open Material Requests": {
		"label": "Material Requests to Order",
		"doctype": "Material Request",
		"filters": [["docstatus", "=", 1], ["status", "in", ["Pending", "Partially Ordered"]]],
	},
	"FO Open Purchase Orders": {
		"label": "Purchase Orders to Receive",
		"doctype": "Purchase Order",
		"filters": [["docstatus", "=", 1], ["status", "in", ["To Receive and Bill", "To Receive"]]],
	},
	"FO Purchase Orders Awaiting Approval": {
		"label": "Purchase Orders Awaiting Approval",
		"doctype": "Purchase Order",
		"filters": [["docstatus", "=", 0]],
	},
	# R&D (research doctypes: statuses and outcomes as defined on the DocTypes)
	"FO Formulations In Draft or Review": {
		"label": "Formulations in Draft or Review",
		"doctype": "Formulation",
		"filters": [["status", "in", ["Draft", "Under Review"]]],
	},
	"FO Formulations In Trial": {
		"label": "Formulations in Trial",
		"doctype": "Formulation",
		"filters": [["status", "=", "Trial"]],
	},
	"FO Trials Awaiting Outcome": {
		"label": "Trials Awaiting Outcome",
		"doctype": "Formulation Trial",
		"filters": [["outcome", "=", "Pending"], ["docstatus", "!=", 2]],
	},
	"FO Stability Studies Running": {
		"label": "Stability Studies Running",
		"doctype": "Stability Study",
		"filters": [["status", "in", ["Planned", "In Progress"]]],
	},
	# Maintenance (Asset Repair, Asset and Asset Maintenance Log, ERPNext's own records)
	"FO Open Breakdowns": {
		"label": "Open Breakdowns",
		"doctype": "Asset Repair",
		"filters": [["docstatus", "=", 0], ["repair_status", "=", "Pending"]],
	},
	"FO Machines Under Breakdown": {
		"label": "Machines Under Breakdown",
		"doctype": "Asset",
		"filters": [["docstatus", "=", 1], ["fo_operational_status", "=", "Breakdown"]],
	},
	"FO Maintenance Tasks Due": {
		"label": "Maintenance Due (14 days)",
		"method": "maintenance_tasks_due",
		"doctype": "Asset Maintenance Log",
	},
	"FO Spares Below Reorder": {
		"label": "Spares Below Reorder Level",
		"method": "spares_below_reorder_count",
		"doctype": "Item",
	},
	# Administration (audit trail and users: Frappe's own records)
	"FO Enabled Users": {
		"label": "Enabled Users",
		"doctype": "User",
		"filters": [["enabled", "=", 1], ["user_type", "=", "System User"]],
	},
	"FO Failed Logins 7 Days": {
		"label": "Failed Logins (7 days)",
		"doctype": "Activity Log",
		"filters": [["operation", "=", "Login"], ["status", "=", "Failed"], ["creation", "Timespan", "last 7 days"]],
	},
	"FO Errors 7 Days": {
		"label": "Errors Logged (7 days)",
		"doctype": "Error Log",
		"filters": [["creation", "Timespan", "last 7 days"]],
	},
	"FO Failed Jobs 7 Days": {
		"label": "Failed Scheduled Jobs (7 days)",
		"doctype": "Scheduled Job Log",
		"filters": [["status", "=", "Failed"], ["creation", "Timespan", "last 7 days"]],
	},
	# Procurement, Stores and Sales: figures that need a date or a join
	"FO Purchase Orders Overdue": {
		"label": "Purchase Orders Overdue",
		"method": "purchase_orders_overdue",
		"doctype": "Purchase Order",
	},
	"FO Items Below Reorder": {
		"label": "Items Below Reorder Level",
		"method": "items_below_reorder_count",
		"doctype": "Item",
	},
	"FO Batches Expiring 30 Days": {
		"label": "Batches Expiring (30 days)",
		"method": "batches_expiring_soon",
		"doctype": "Batch",
	},
	"FO Orders Past Delivery Date": {
		"label": "Orders Past Delivery Date",
		"method": "sales_orders_past_delivery_date",
		"doctype": "Sales Order",
	},
	"FO Work Orders In Progress": {
		"label": "Work Orders In Progress",
		"doctype": "Work Order",
		"filters": [["docstatus", "=", 1], ["status", "in", ["Not Started", "In Process"]]],
	},
	"FO Manufacture Entries 30 Days": {
		"label": "Batches Made (30 days)",
		"doctype": "Stock Entry",
		"filters": [
			["docstatus", "=", 1],
			["purpose", "=", "Manufacture"],
			["posting_date", "Timespan", "last 30 days"],  # "last month" = previous calendar month
		],
	},
	"FO Payables Outstanding": {
		"label": "Payables Outstanding",
		"doctype": "Purchase Invoice",
		"function": "Sum",
		"field": "outstanding_amount",
		"filters": [["docstatus", "=", 1], ["outstanding_amount", ">", 0]],
	},
}
# --------------------------------------------------------------------------- charts
CHARTS = {
	"FO Purchase Orders by Week": (
		"Purchase Order",
		"Sum",
		"transaction_date",
		"base_grand_total",
		[["docstatus", "=", 1]],
		"Bar",
	),
	"FO Batches Made by Week": (
		"Stock Entry",
		"Count",
		"posting_date",
		None,
		[["docstatus", "=", 1], ["purpose", "=", "Manufacture"]],
		"Bar",
	),
	"FO QC Decisions by Week": ("QC Release", "Count", "modified", None, [["docstatus", "=", 1]], "Bar"),
	"FO Breakdowns by Month": ("Asset Repair", "Count", "failure_date", None, [["docstatus", "!=", 2]], "Bar"),
	"FO Trials by Week": ("Formulation Trial", "Count", "creation", None, [["docstatus", "!=", 2]], "Bar"),
}

R, D, U = "Report", "DocType", "URL"

# the pages the administrator's home links to (see "Administration" below)
DEPARTMENT_ORDER = (
	"Management",
	"Production Floor",
	"Quality Control",
	"Research and Development",
	"Plant Maintenance",
	"Stores and Warehouse",
	"Procurement",
	"Sales and Dispatch",
	"Factory Accounts",
)
ERPNEXT_MODULES = ("Accounting", "Stock", "Buying", "Selling", "Manufacturing", "Quality", "Assets")


def workspace_route(name: str) -> str:
	from frappe.desk.utils import slug

	return f"/desk/{slug(name)}"


def available_shortcuts(ws) -> list[tuple[str, str]]:
	"""The workspace's shortcuts; a URL tile is kept only when its workspace exists on this site."""
	return [
		(to, t) for to, t in ws["shortcuts"] if t != U or frappe.db.exists("Workspace", to)
	]


def shortcut_row(to: str, shortcut_type: str) -> dict:
	if shortcut_type == U:
		return {"type": U, "label": to, "url": workspace_route(to)}
	return {"type": shortcut_type, "link_to": to, "label": to, "doc_view": "List" if shortcut_type == D else ""}


def page_roles(ws) -> list[str]:
	"""Roles shown a workspace and its desktop icon: its own, the System Administrator, and (unless
	the page is technical) the Business Administrator. Showing a page grants nothing; every link opens
	with the user's own permissions (see setup/admin_access.py)."""
	roles = list(ws["roles"])
	for role in (ROLE_ADMIN, None if ws.get("technical") else ROLE_BUSINESS_ADMIN):
		if role and role not in roles:
			roles.append(role)
	return roles


def _links(*cards):
	"""cards: (card label, [(label, link_type, link_to), ...])"""
	return list(cards)


def unique_links(shortcuts, link_groups):
	"""Link cards without the entries that are already shortcuts, so each target has one entry
	point on a page. Cards left empty are dropped."""
	taken = {(link_type, to) for to, link_type in shortcuts}
	kept = []
	for label, items in link_groups:
		rest = [i for i in items if (i[1], i[2]) not in taken]
		if rest:
			kept.append((label, rest))
	return kept


def layout_blocks(key, cards, charts, shortcuts, link_cards):
	"""Workspace content: number cards, shortcuts, link cards, then charts.

	Each kind of widget gets its own rows, separated by spacers. Frappe lays a page out in flex rows
	whose items stretch to the tallest neighbour, so a shortcut placed beside a link card became a
	tall empty box, and an empty chart pushed shortcuts sideways (both seen in the browser). There is
	no title block: the page already shows its name."""
	slug = _slug(key)
	groups = [
		[
			{"id": f"fo-n{i}-{slug}", "type": "number_card", "data": {"number_card_name": c, "col": 3}}
			for i, c in enumerate(cards)
		],
		[
			{"id": f"fo-s{i}-{slug}", "type": "shortcut", "data": {"shortcut_name": to, "col": 3}}
			for i, (to, _t) in enumerate(shortcuts)
		],
		[
			{"id": f"fo-k{i}-{slug}", "type": "card", "data": {"card_name": label, "col": 3 if len(link_cards) == 4 else 4}}
			for i, (label, _items) in enumerate(link_cards)
		],
		[
			{"id": f"fo-c{i}-{slug}", "type": "chart", "data": {"chart_name": c, "col": 6}}
			for i, c in enumerate(charts)
		],
	]
	blocks = []
	for group in (g for g in groups if g):
		if blocks:
			blocks.append({"id": f"fo-sp{len(blocks)}-{slug}", "type": "spacer", "data": {"col": 12}})
		blocks += group
	return blocks


# --------------------------------------------------------------------------- workspaces
WORKSPACES = {
	"Management": {
		"icon": "chart-column",
		"roles": [ROLE_MANAGEMENT, "System Manager"],
		"cards": [
			# what is waiting for a decision or is in progress, then the position
			"FO Purchase Orders Awaiting Approval",
			"FO QC Releases Pending",
			"FO Deviations Pending",
			"FO Work Orders In Progress",
			"Total Stock Value",
			"FO RM Stock Value",
			"FO PM Stock Value",
			"FO BULK Stock Value",
			"FO FG Stock Value",
			"FO Batches Awaiting QC",
			"FO Rejected Stock Value",
			"FO Manufacture Entries 30 Days",
			"Open Sales Orders",
			"Orders to Dispatch (value)",
			"Dispatches Not Billed",
			"Receivables Outstanding",
		],
		"charts": [
			"Sales Orders by Week",
			"Invoiced by Week",
			"Dispatches by Week",
			"FO Batches Made by Week",
		],
		"shortcuts": [
			("Stock Balance", R),
			("Available to Sell", R),
			("Accounts Receivable Summary", R),
			("Profit and Loss Statement", R),
		],
		"links": _links(
			(
				"Stock and quality",
				[
					("Stock Balance", R, "Stock Balance"),
					("Stock by QC Status", R, "Stock by QC Status"),
					("Batch Genealogy", R, "Batch Genealogy"),
					("Available to Sell", R, "Available to Sell"),
					("Stock and Account Value Comparison", R, "Stock and Account Value Comparison"),
				],
			),
			(
				"Production",
				[
					("Work Order Summary", R, "Work Order Summary"),
					("Production Reconciliation", R, "Production Reconciliation"),
					("Process Loss Report", R, "Process Loss Report"),
					("Wastage by Reason", R, "Wastage by Reason"),
				],
			),
			(
				"Sales",
				[
					("Sales Analytics", R, "Sales Analytics"),
					("Sales Order Analysis", R, "Sales Order Analysis"),
					("Dispatch Register", R, "Dispatch Register"),
				],
			),
			(
				"Finance (actual, from the ledger)",
				[
					("Accounts Receivable Summary", R, "Accounts Receivable Summary"),
					("Accounts Payable Summary", R, "Accounts Payable Summary"),
					("Profit and Loss Statement", R, "Profit and Loss Statement"),
					("Balance Sheet", R, "Balance Sheet"),
				],
			),
		),
	},
	"Procurement": {
		"icon": "shopping-cart",
		"roles": ["Purchase User", "Purchase Manager"],
		"cards": [
			"FO Open Material Requests",
			"FO Purchase Orders Awaiting Approval",
			"FO Open Purchase Orders",
			"FO Purchase Orders Overdue",
		],
		"charts": ["FO Purchase Orders by Week"],
		"shortcuts": [
			("Material Request", D),
			("Purchase Order", D),
			("Purchase Receipt", D),
			("Supplier", D),
		],
		"links": _links(
			(
				"Documents",
				[
					("Material Request", D, "Material Request"),
					("Request for Quotation", D, "Request for Quotation"),
					("Supplier Quotation", D, "Supplier Quotation"),
					("Purchase Order", D, "Purchase Order"),
					("Purchase Receipt", D, "Purchase Receipt"),
				],
			),
			(
				"Suppliers",
				[
					("Supplier", D, "Supplier"),
					("Supplier Group", D, "Supplier Group"),
					("Purchase Approval Limit", D, "Purchase Approval Limit"),
				],
			),
			(
				"Reports",
				[
					("RM / PM Requirements", R, "RM PM Requirements"),
					("Purchase Order Analysis", R, "Purchase Order Analysis"),
					("Procurement Tracker", R, "Procurement Tracker"),
					("Item-wise Purchase History", R, "Item-wise Purchase History"),
					("Supplier Quotation Comparison", R, "Supplier Quotation Comparison"),
				],
			),
		),
	},
	"Stores and Warehouse": {
		"icon": "package",
		"roles": ["Stock User", "Stock Manager"],
		"cards": [
			"Total Stock Value",
			"FO Batches Awaiting QC",
			"FO Batches Expiring 30 Days",
			"FO Items Below Reorder",
		],
		"charts": [],
		"shortcuts": [("Stock Entry", D), ("Purchase Receipt", D), ("Pick List", D), ("Stock Balance", R)],
		"links": _links(
			(
				"Stock",
				[
					("Stock Balance", R, "Stock Balance"),
					("Stock Ledger", R, "Stock Ledger"),
					("Stock by QC Status", R, "Stock by QC Status"),
					("Available to Sell", R, "Available to Sell"),
					(
						"Warehouse wise Item Balance Age and Value",
						R,
						"Warehouse wise Item Balance Age and Value",
					),
				],
			),
			(
				"Batches and expiry",
				[
					("Batch", D, "Batch"),
					("Batch-Wise Balance History", R, "Batch-Wise Balance History"),
					("Batch Item Expiry Status", R, "Batch Item Expiry Status"),
					("Stock Ageing", R, "Stock Ageing"),
				],
			),
			(
				"Movements",
				[
					("Stock Entry", D, "Stock Entry"),
					("Stock Reservation Entry", D, "Stock Reservation Entry"),
					("Pick List", D, "Pick List"),
					("Delivery Note", D, "Delivery Note"),
				],
			),
			(
				"Setup and opening stock",
				[
					("Warehouse", D, "Warehouse"),
					("Item", D, "Item"),
					("Master Data Import", D, "Master Data Import"),
					("Stock Reconciliation", D, "Stock Reconciliation"),
				],
			),
		),
	},
	"Production Floor": {
		"icon": "factory",
		"roles": ["Manufacturing User", "Manufacturing Manager"],
		"cards": [
			"FO Work Orders In Progress",
			"FO Manufacture Entries 30 Days",
			"FO Wastage 30 Days",
			"FO Batches Awaiting QC",
		],
		"charts": ["FO Batches Made by Week"],
		"shortcuts": [
			("Work Order", D),
			("Stock Entry", D),
			("Production Reconciliation", R),
			("Batch Genealogy", R),
		],
		"links": _links(
			(
				"Production",
				[
					("Work Order", D, "Work Order"),
					("BOM", D, "BOM"),
					("Material Request", D, "Material Request"),
					("Stock Entry", D, "Stock Entry"),
				],
			),
			(
				"Reports",
				[
					("Production Reconciliation", R, "Production Reconciliation"),
					("Batch Genealogy", R, "Batch Genealogy"),
					("Work Order Summary", R, "Work Order Summary"),
					("Production Analytics", R, "Production Analytics"),
					("Process Loss Report", R, "Process Loss Report"),
					("Work Order Stock Report", R, "Work Order Stock Report"),
					("Wastage by Reason", R, "Wastage by Reason"),
					("RM / PM Requirements", R, "RM PM Requirements"),
				],
			),
			(
				"Setup",
				[
					("Wastage Reason", D, "Wastage Reason"),
					("Workstation", D, "Workstation"),
					("Operation", D, "Operation"),
				],
			),
		),
	},
	"Quality Control": {
		"icon": "shield-check",
		"roles": ["QC Officer", "QA Manager"],
		"cards": [
			"FO Batches Awaiting QC",
			"FO QC Releases Pending",
			"FO Deviations Pending",
			"FO Rejected Batches",
		],
		"charts": ["FO QC Decisions by Week"],
		"shortcuts": [("QC Queue", R), ("QC Release", D), ("Quality Inspection", D), ("QC Deviation", D)],
		"links": _links(
			(
				"Inspection and release",
				[
					("QC Queue", R, "QC Queue"),
					("QC Release", D, "QC Release"),
					("Quality Inspection", D, "Quality Inspection"),
					("QC Deviation", D, "QC Deviation"),
					("QC Retest", D, "QC Retest"),
				],
			),
			(
				"Stock under QC",
				[
					("Stock by QC Status", R, "Stock by QC Status"),
					("Batch Genealogy", R, "Batch Genealogy"),
					("Batch", D, "Batch"),
					("Quality Inspection Summary", R, "Quality Inspection Summary"),
				],
			),
			(
				"Setup",
				[
					("Quality Inspection Template", D, "Quality Inspection Template"),
					("Factory Ops Settings", D, "Factory Ops Settings"),
				],
			),
		),
	},
	"Research and Development": {
		"icon": "flask-conical",
		"roles": ["R&D Chemist", "R&D Manager"],
		"cards": [
			"FO Formulations In Draft or Review",
			"FO Formulations In Trial",
			"FO Trials Awaiting Outcome",
			"FO Stability Studies Running",
		],
		"charts": ["FO Trials by Week"],
		"shortcuts": [
			("Formulation", D),
			("Formulation Trial", D),
			("Stability Study", D),
			("Research Project", D),
		],
		"links": _links(
			(
				"Reference data",
				[
					("Ingredient Category", D, "Ingredient Category"),
				],
			),
		),
	},
	"Plant Maintenance": {
		"icon": "wrench",
		"roles": ["Maintenance User", "Maintenance Manager"],
		"cards": [
			"FO Open Breakdowns",
			"FO Machines Under Breakdown",
			"FO Maintenance Tasks Due",
			"FO Spares Below Reorder",
		],
		"charts": ["FO Breakdowns by Month"],
		"shortcuts": [
			("Asset Repair", D),
			("Asset Maintenance Log", D),
			("Asset", D),
			("Asset Maintenance", D),
		],
		"links": _links(
			(
				"Equipment and teams",
				[
					("Asset Category", D, "Asset Category"),
					("Location", D, "Location"),
					("Asset Maintenance Team", D, "Asset Maintenance Team"),
				],
			),
		),
	},
	"Sales and Dispatch": {
		"icon": "truck",
		"roles": ["Sales User", "Sales Manager", "Delivery User"],
		"cards": [
			"Orders to Dispatch (value)",
			"FO Orders Past Delivery Date",
			"Pick Lists Open",
			"Dispatches Not Billed",
		],
		"charts": ["Sales Orders by Week", "Dispatches by Week"],
		"shortcuts": [("Sales Order", D), ("Pick List", D), ("Delivery Note", D), ("Available to Sell", R)],
		"links": _links(
			(
				"Orders",
				[
					("Customer", D, "Customer"),
					("Sales Order", D, "Sales Order"),
					("Available to Sell", R, "Available to Sell"),
					("Sales Order Analysis", R, "Sales Order Analysis"),
				],
			),
			(
				"Dispatch",
				[
					("Pick List", D, "Pick List"),
					("Delivery Note", D, "Delivery Note"),
					("Packing Slip", D, "Packing Slip"),
					("Dispatch Register", R, "Dispatch Register"),
					("Delivery Note Trends", R, "Delivery Note Trends"),
				],
			),
			(
				"Billing",
				[("Sales Invoice", D, "Sales Invoice"), ("Accounts Receivable", R, "Accounts Receivable")],
			),
		),
	},
	"Factory Accounts": {
		"icon": "landmark",
		"roles": ["Accounts User", "Accounts Manager"],
		"cards": [
			"Receivables Outstanding",
			"Overdue Invoices",
			"FO Payables Outstanding",
		],
		"charts": ["Invoiced by Week", "Payments Received by Week"],
		"shortcuts": [
			("Sales Invoice", D),
			("Purchase Invoice", D),
			("Payment Entry", D),
			("General Ledger", R),
		],
		"links": _links(
			(
				"Documents",
				[
					("Sales Invoice", D, "Sales Invoice"),
					("Purchase Invoice", D, "Purchase Invoice"),
					("Payment Entry", D, "Payment Entry"),
					("Journal Entry", D, "Journal Entry"),
				],
			),
			(
				"Receivables and payables",
				[
					("Accounts Receivable", R, "Accounts Receivable"),
					("Accounts Receivable Summary", R, "Accounts Receivable Summary"),
					("Accounts Payable", R, "Accounts Payable"),
					("Accounts Payable Summary", R, "Accounts Payable Summary"),
				],
			),
			(
				"Ledgers and statements",
				[
					("General Ledger", R, "General Ledger"),
					("Trial Balance", R, "Trial Balance"),
					("Profit and Loss Statement", R, "Profit and Loss Statement"),
					("Balance Sheet", R, "Balance Sheet"),
				],
			),
			(
				"Stock in the books",
				[
					("Stock and Account Value Comparison", R, "Stock and Account Value Comparison"),
					("Sales Register", R, "Sales Register"),
					("Purchase Register", R, "Purchase Register"),
				],
			),
		),
	},

}

# The Mintree Administration page. It is NOT a workspace of its own: it is the content of ERPNext's own
# Setup/Home workspace (``Home``), applied as a site customization by setup/admin_home.py. These are its
# cards, tiles and link groups, kept here beside the other pages' definitions.
ADMIN_HOME = {
	"icon": "settings-2",
	"technical": True,  # users, roles, settings, audit and integrations: the System Administrator only
	"roles": [ROLE_ADMIN],
	"cards": [
		"FO Enabled Users",
		"FO Failed Logins 7 Days",
		"FO Errors 7 Days",
		"FO Failed Jobs 7 Days",
	],
	"charts": [],
	# one tile per department page, then ERPNext's own module pages: the administrator moves
	# between them without changing accounts. Targets are workspace routes, not DocTypes.
	"shortcuts": [(name, U) for name in DEPARTMENT_ORDER] + [(name, U) for name in ERPNEXT_MODULES],
	"links": _links(
		(
			"Users and access",
			[
				("User", D, "User"),
				("Role", D, "Role"),
				("Role Profile", D, "Role Profile"),
				("Module Profile", D, "Module Profile"),
				("User Permission", D, "User Permission"),
			],
		),
		(
			"Audit trail",
			[
				("Activity Log", D, "Activity Log"),
				("Version", D, "Version"),
				("Access Log", D, "Access Log"),
				("Error Log", D, "Error Log"),
				("Scheduled Job Log", D, "Scheduled Job Log"),
			],
		),
		(
			"Settings and integrations",
			[
				("System Settings", D, "System Settings"),
				("Factory Ops Settings", D, "Factory Ops Settings"),
				("Integration Request", D, "Integration Request"),
				("Webhook", D, "Webhook"),
				("Marketplace Channel", D, "Marketplace Channel"),
				("Meta Connection", D, "Meta Connection"),
				("Setup Onboarding", D, "Module Onboarding"),  # ERPNext's checklist, no longer the landing panel
			],
		),
	),
}

# Read-only rights for Management Viewer (permlevel 0 read + report). No create, write, submit.
MANAGEMENT_READ = (
	"Company", "Item", "Item Group", "Warehouse", "Batch", "Bin", "Stock Ledger Entry", "Stock Entry",
	"Stock Reservation Entry", "Pick List", "Delivery Note", "Sales Order", "Sales Invoice", "Customer",
	"Purchase Order", "Purchase Receipt", "Purchase Invoice", "Supplier", "Work Order", "BOM",
	"QC Release", "QC Deviation", "Quality Inspection", "GL Entry", "Account", "Payment Entry",
	"Cost Center", "Fiscal Year", "Material Request",
)  # fmt: skip
MANAGEMENT_LEVEL1_READ = ("Batch",)
MANAGEMENT_REPORTS = sorted(
	{link[2] for card in WORKSPACES["Management"]["links"] for link in card[1] if link[1] == R}
	| {s[0] for s in WORKSPACES["Management"]["shortcuts"] if s[1] == R}
)


def card_name(key: str) -> str:
	"""Number Cards are named after their label (Frappe), so a key maps to its label."""
	return CARDS[key]["label"] if key in CARDS else key


def ensure_workspaces():
	ensure_management_role()
	for name, definition in CARDS.items():
		_number_card(name, definition)
	for name, chart in CHARTS.items():
		_chart(name, *chart)
	remove_renamed_workspaces()
	for name, ws in WORKSPACES.items():
		ensure_navigation_module(name)
		_workspace(name, ws)
		_sidebar(name, ws)
		_desktop_icon(name, ws)
	from factory_ops.setup.admin_home import ensure_admin_home, retire_standalone_administration

	retire_standalone_administration()  # the Administration page is now ERPNext's own Setup/Home
	ensure_admin_home()


def ensure_management_role():
	if not frappe.db.exists("Role", ROLE_MANAGEMENT):
		frappe.get_doc({"doctype": "Role", "role_name": ROLE_MANAGEMENT, "desk_access": 1}).insert(
			ignore_permissions=True
		)
	for doctype in MANAGEMENT_READ:
		if not frappe.db.exists("DocType", doctype):
			continue
		if not frappe.db.exists(
			"Custom DocPerm", {"parent": doctype, "role": ROLE_MANAGEMENT, "permlevel": 0}
		):
			add_permission(doctype, ROLE_MANAGEMENT, 0)
		for right in ("read", "report"):
			update_permission_property(doctype, ROLE_MANAGEMENT, 0, right, 1, validate=False)
		for right in ("write", "create", "submit", "cancel", "delete", "amend"):
			update_permission_property(doctype, ROLE_MANAGEMENT, 0, right, 0, validate=False)
	# permlevel-1 fields its cards filter on (Batch.fo_qc_status for "Batches Awaiting QC"):
	# read only, like every other level-1 grant (setup/install.py LEVEL1_READ)
	for doctype in MANAGEMENT_LEVEL1_READ:
		if not frappe.db.exists(
			"Custom DocPerm", {"parent": doctype, "role": ROLE_MANAGEMENT, "permlevel": 1}
		):
			add_permission(doctype, ROLE_MANAGEMENT, 1)
		update_permission_property(doctype, ROLE_MANAGEMENT, 1, "read", 1, validate=False)
		update_permission_property(doctype, ROLE_MANAGEMENT, 1, "write", 0, validate=False)
	for report in MANAGEMENT_REPORTS:
		grant_report(report, ROLE_MANAGEMENT)


def grant_report(report: str, role: str):
	"""Add a role to a report without removing its existing roles (Custom Role replaces the
	report's own list, so the standard roles are copied in first)."""
	if not frappe.db.exists("Report", report):
		return
	name = frappe.db.get_value("Custom Role", {"report": report}, "name")
	if name:
		doc = frappe.get_doc("Custom Role", name)
	else:
		doc = frappe.get_doc(
			{
				"doctype": "Custom Role",
				"report": report,
				"ref_doctype": frappe.db.get_value("Report", report, "ref_doctype"),
			}
		)
		for r in frappe.get_all("Has Role", filters={"parent": report, "parenttype": "Report"}, pluck="role"):
			doc.append("roles", {"role": r})
	if role not in {r.role for r in doc.roles}:
		doc.append("roles", {"role": role})
	doc.save(ignore_permissions=True) if not doc.is_new() else doc.insert(ignore_permissions=True)


def _number_card(name, d):
	values = {"label": d["label"], "is_public": 1, "module": MODULE, "show_percentage_stats": 0}
	if "method" in d:
		# a Custom card is readable only by users who can read its document_type (Frappe), so it
		# names the table its figure comes from: Bin for stock values, Stock Entry for wastage
		values.update(
			{
				"type": "Custom",
				"method": f"factory_ops.setup.dashboard_data.{d['method']}",
				"document_type": d.get("doctype", "Bin"),
			}
		)
	else:
		values.update(
			{
				"type": "Document Type",
				"document_type": d["doctype"],
				"function": d.get("function", "Count"),
				"aggregate_function_based_on": d.get("field"),
				"filters_json": json.dumps([[d["doctype"], f, op, v, False] for f, op, v in d["filters"]]),
			}
		)
	_upsert("Number Card", d["label"], values)


def _chart(name, doctype, chart_type, based_on, value_field, filters, style):
	_upsert(
		"Dashboard Chart",
		name,
		{
			"chart_name": name,
			"chart_type": chart_type,
			"document_type": doctype,
			"based_on": based_on,
			"value_based_on": value_field,
			"timeseries": 1,
			"timespan": "Last Quarter",
			"time_interval": "Weekly",
			"type": style,
			"filters_json": json.dumps([[doctype, f, op, v, False] for f, op, v in filters]),
			"is_public": 1,
			"module": MODULE,
		},
	)


def _workspace(name, ws):
	doc = (
		frappe.get_doc("Workspace", name)
		if frappe.db.exists("Workspace", name)
		else frappe.new_doc("Workspace")
	)
	doc.update(
		{
			"label": name,
			"title": name,
			"icon": ws["icon"],
			"module": name,  # its own navigation module (see ensure_navigation_module)
			"public": 1,
			"is_hidden": 0,
			"type": "Workspace",
		}
	)
	shortcuts = available_shortcuts(ws)
	doc.set("roles", [{"role": r} for r in page_roles(ws)])
	doc.set("number_cards", [{"number_card_name": card_name(c), "label": card_name(c)} for c in ws["cards"]])
	doc.set("charts", [{"chart_name": c, "label": c.removeprefix("FO ")} for c in ws["charts"]])
	doc.set("shortcuts", [shortcut_row(to, t) for to, t in shortcuts])
	link_cards = unique_links(shortcuts, ws["links"])
	links = []
	for card_label, items in link_cards:
		links.append({"type": "Card Break", "label": card_label, "link_count": len(items)})
		for label, link_type, link_to in items:
			links.append(
				{
					"type": "Link",
					"label": label,
					"link_type": link_type,
					"link_to": link_to,
					"is_query_report": int(link_type == R and _is_query_report(link_to)),
				}
			)
	doc.set("links", links)
	blocks = layout_blocks(name, [card_name(c) for c in ws["cards"]], ws["charts"], shortcuts, link_cards)
	doc.content = json.dumps(blocks)
	doc.flags.ignore_permissions = True
	doc.save() if not doc.is_new() else doc.insert()


def ensure_navigation_module(name: str):
	"""A database-only (custom) Module Def per role workspace, holding nothing but navigation.

	Frappe v16 routes a workspace through its module, and the sidebar that answers for a module
	is the one named after it. With all seven pages in one module, every page opened inside the
	same, first shell of that module (seen in the browser check). One navigation module each gives
	every workspace its own shell and URL. No code, DocType or report lives in these modules."""
	existing = frappe.db.get_value("Module Def", name, ["custom", "app_name"], as_dict=True)
	if existing and not (existing.custom and existing.app_name == "factory_ops"):
		# Frappe binds a sidebar and its pages to the module of the same name, so reusing another app's
		# module would take over that module's navigation (this happened with "Maintenance", which is
		# ERPNext's own module: Maintenance Schedule and Visit)
		frappe.throw(
			f"The workspace name {name!r} is the name of a module of {existing.app_name!r}. "
			"Choose a different name so it does not take over that module's navigation."
		)
	if not existing:
		frappe.get_doc(
			{"doctype": "Module Def", "module_name": name, "custom": 1, "app_name": "factory_ops"}
		).insert(ignore_permissions=True)


def _sidebar(name, ws):
	"""The workspace's left-hand navigation: a Frappe v16 ``Sidebar`` named after (and owned by) its
	navigation module, at ``/desk/<name>``.

	It holds one item, the role-restricted workspace page; the page carries the shortcuts, cards
	and links. Frappe drops a sidebar in which a user can open nothing, so the shell appears only to
	holders of the workspace's roles. (Listing documents here would show the shell to anyone who can
	read one of them, e.g. a buyer seeing the accounts menu.)

	``Workspace Sidebar`` is v16's archive of the old navigation; one is kept per workspace, created
	only during migrate, because a Desktop Icon links to one by name (see ``icon_link``)."""
	if frappe.db.exists("Sidebar", name) and frappe.db.get_value("Sidebar", name, "module") != name:
		frappe.delete_doc("Sidebar", name, ignore_permissions=True, force=True)  # was under Factory Ops
	header = {"title": name, "module": name, "header_icon": ws["icon"], "standard": 0}
	items = [{"type": "Link", "label": name, "link_type": "Workspace", "link_to": name, "icon": ws["icon"]}]
	doc = frappe.get_doc("Sidebar", name) if frappe.db.exists("Sidebar", name) else frappe.new_doc("Sidebar")
	if not doc.is_new() and _sidebar_matches(doc, header, items):
		# Frappe v16 lets an app's Sidebar be saved only in developer mode or during
		# install/migrate. Setup also runs outside migrate (tests, setup_dev_site), so an
		# unchanged sidebar is left alone; a changed one is updated by the next migrate.
		pass
	else:
		doc.update(header)
		doc.set("items", items)
		doc.flags.ignore_permissions = True
		doc.save() if not doc.is_new() else doc.insert()
	archive_name = icon_link(name)
	if not frappe.db.exists("Workspace Sidebar", archive_name) and (
		frappe.flags.in_migrate or frappe.flags.in_install
	):
		archive = frappe.new_doc("Workspace Sidebar")
		archive.update(
			{"title": archive_name, "header_icon": ws["icon"], "module": MODULE, "app": "factory_ops"}
		)
		archive.append("items", {"type": "Link", "label": "Home", "link_type": "Workspace", "link_to": name})
		archive.flags.ignore_permissions = True
		archive.insert()


def _sidebar_matches(doc, header: dict, items: list[dict]) -> bool:
	if not all(_same(doc.get(k), v) for k, v in header.items()):
		return False
	if len(doc.items) != len(items):
		return False
	return all(
		all(_same(row.get(k), v) for k, v in want.items()) for row, want in zip(doc.items, items, strict=True)
	)


def remove_renamed_workspaces():
	"""Names changed while building Milestone 6: "Production" (a sidebar may not carry another
	module's name) and "Warehouse" (a workspace's URL may not clash with a DocType's)."""
	for old in ("Production", "Warehouse"):
		if frappe.db.get_value("Workspace", old, "module") == MODULE:
			for doctype in ("Desktop Icon", "Workspace", "Sidebar"):
				if frappe.db.exists(doctype, old) and (
					doctype != "Sidebar" or frappe.db.get_value("Sidebar", old, "module") == MODULE
				):
					frappe.delete_doc(doctype, old, ignore_permissions=True, force=True)
			if (
				frappe.db.exists("Workspace Sidebar", old)
				and frappe.db.get_value("Workspace Sidebar", old, "module") == MODULE
			):
				frappe.delete_doc("Workspace Sidebar", old, ignore_permissions=True, force=True)
	# archives that carried a workspace's own name (icons now link to "<name> Menu", see icon_link)
	for name in WORKSPACES:
		if (
			frappe.db.exists("Workspace Sidebar", name)
			and frappe.db.get_value("Workspace Sidebar", name, "module") == MODULE
		):
			frappe.delete_doc("Workspace Sidebar", name, ignore_permissions=True, force=True)


def icon_link(name: str) -> str:
	"""What a desktop icon links to: a Workspace Sidebar (v16 archive) record that is *not* a
	Workspace name. Frappe stamps an icon with the module of the Workspace its link names, and all
	seven role workspaces share the Factory Ops module, so every icon would open the same, first
	Factory Ops shell. An icon without a module is resolved by its label instead: its own shell."""
	return f"{name} Menu"


def _desktop_icon(name, ws):
	if not frappe.db.exists("Workspace Sidebar", icon_link(name)):
		return  # created on the next migrate, with its archive record (see _sidebar)
	doc = (
		frappe.get_doc("Desktop Icon", name)
		if frappe.db.exists("Desktop Icon", name)
		else frappe.new_doc("Desktop Icon")
	)
	doc.update(
		{
			"label": name,
			"icon": ws["icon"],
			"icon_type": "Link",
			"link_type": "Workspace Sidebar",
			"link_to": icon_link(name),
			"hidden": 0,
			"app": "factory_ops",
		}
	)
	doc.set("roles", [{"role": r} for r in page_roles(ws)])
	doc.flags.ignore_permissions = True
	doc.save() if not doc.is_new() else doc.insert()


def _is_query_report(report: str) -> bool:
	return frappe.db.get_value("Report", report, "report_type") in ("Script Report", "Query Report")


def _slug(text: str) -> str:
	return "".join(ch for ch in text.lower() if ch.isalnum())[:12]


def _same(old, new) -> bool:
	"""Field equality that treats None, "" and 0 as Frappe stores them."""
	if old in (None, "") and new in (None, ""):
		return True
	if isinstance(new, (int, float)) and not isinstance(new, bool):
		return str(old) == str(new) or (old in (None, "", 0) and new == 0)
	return old == new


def _upsert(doctype, name, values):
	if frappe.db.exists(doctype, name):
		doc = frappe.get_doc(doctype, name)
		if all(_same(doc.get(k), v) for k, v in values.items()):
			# unchanged: do not save, so re-running setup does not touch `modified` (and, in
			# developer mode, does not re-export a standard document into the app folder)
			return
		doc.update(values)
		doc.flags.ignore_permissions = True
		doc.save()
	else:
		doc = frappe.get_doc({"doctype": doctype, "name": name, **values})
		doc.flags.ignore_permissions = True
		doc.insert()
