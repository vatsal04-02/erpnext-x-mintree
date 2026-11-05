app_name = "factory_ops"
app_title = "Mintree Factory OS"
app_logo_url = "/assets/factory_ops/images/mintree-mark.svg"
app_publisher = "FlowHQ"
app_description = "Mintree Factory OS: factory-specific operations on ERPNext (QC quarantine, approvals, production controls)"
app_email = "dev@flowhq.invalid"
app_license = "gpl-3.0"

required_apps = ["frappe/erpnext"]

# ---------------------------------------------------------------------------
# Installation. Custom fields, roles, permissions and warehouse types are
# declared in code (factory_ops/setup/*) and re-applied on every migrate, so a
# fresh site and an upgraded site end up identical. See docs/DECISIONS.md D-005.
# ---------------------------------------------------------------------------
after_install = "factory_ops.setup.install.after_install"
after_migrate = "factory_ops.setup.install.after_migrate"
before_uninstall = "factory_ops.setup.install.before_uninstall"

# ---------------------------------------------------------------------------
# Server-side QC guards. Every route that can move, reserve or plan stock out of
# a QC Hold or Rejected warehouse is checked here. See
# docs/DATABASE_AND_WORKFLOW_DESIGN.md section 3.3.
# ---------------------------------------------------------------------------
_guard = "factory_ops.inventory.guards"
_recv = "factory_ops.inventory.receiving"
_prod = "factory_ops.production.controls"
_sales = "factory_ops.sales_ops.controls"

# Mintree accent colour (Desk and website pages such as login) and login-page logo
app_include_css = "/assets/factory_ops/css/mintree.css"
web_include_css = "/assets/factory_ops/css/mintree.css"
update_website_context = ["factory_ops.setup.branding.update_website_context"]

# Contextual Desk actions (UX only; every rule and calculation lives in Python)
doctype_js = {
	"Batch": "public/js/batch.js",
	"Work Order": "public/js/work_order.js",
}

doc_events = {
	"Item": {
		"validate": "factory_ops.inventory.items.apply_item_group_controls",
	},
	"Purchase Order": {
		"before_submit": "factory_ops.purchase.approval.validate_purchase_order_approval",
	},
	"Stock Entry": {
		"validate": [
			f"{_guard}.validate_stock_entry",
			f"{_prod}.validate_wastage",
			f"{_prod}.validate_wastage_work_order",
		],
		"before_submit": [
			f"{_guard}.validate_stock_entry",
			f"{_prod}.validate_wastage",
			f"{_prod}.validate_wastage_work_order",
		],
		"on_submit": f"{_guard}.on_inward_submit",
		"before_cancel": f"{_guard}.before_cancel_stock_entry",
		"on_cancel": f"{_guard}.on_inward_cancel",
	},
	"Purchase Receipt": {
		"before_validate": f"{_recv}.set_receipt_defaults",
		"validate": [f"{_guard}.validate_purchase_receipt", f"{_recv}.validate_receipt_data"],
		"before_submit": [
			f"{_guard}.validate_purchase_receipt",
			f"{_recv}.validate_receipt_data",
			f"{_recv}.create_supplier_batches",
		],
		"on_submit": f"{_guard}.on_inward_submit",
		"on_cancel": f"{_guard}.on_inward_cancel",
	},
	"Purchase Invoice": {
		"validate": f"{_guard}.validate_purchase_receipt",
		"before_submit": f"{_guard}.validate_purchase_receipt",
	},
	"Delivery Note": {
		"before_validate": [f"{_sales}.inherit_sales_channel", f"{_sales}.assign_eligible_batches"],
		"validate": [
			f"{_guard}.validate_outward_sales_doc",
			f"{_sales}.validate_reserved_batches_dispatchable",
		],
		"before_submit": [f"{_guard}.validate_outward_sales_doc", f"{_sales}.validate_dispatch_details"],
		# batches may be auto-selected (FEFO) during submit, so G6 runs again once bundles exist
		"on_submit": f"{_guard}.validate_shelf_life",
	},
	"Sales Invoice": {
		"before_validate": f"{_sales}.inherit_sales_channel",
		"validate": [f"{_guard}.validate_outward_sales_doc", f"{_sales}.validate_invoice_stock_path"],
		"before_submit": [f"{_guard}.validate_outward_sales_doc", f"{_sales}.validate_invoice_stock_path"],
		# batches may be auto-selected (FEFO) during submit, so G6 runs again once bundles exist
		"on_submit": f"{_guard}.validate_shelf_life",
	},
	"Pick List": {
		"before_validate": f"{_guard}.default_pick_list_warehouse",
		"validate": f"{_guard}.validate_pick_list",
		# ERPNext computes locations in before_save, after validate: replace ineligible suggestions
		# with eligible batches (D-034), then check again
		"before_save": [f"{_sales}.reallocate_pick_list", f"{_guard}.validate_pick_list"],
		"before_submit": f"{_guard}.validate_pick_list",
	},
	"Sales Order": {
		"validate": [f"{_guard}.validate_sales_order", f"{_sales}.validate_sales_channel"],
		"before_submit": f"{_guard}.validate_sales_order",
	},
	"Stock Reservation Entry": {
		"validate": f"{_guard}.validate_stock_reservation",
		"before_submit": [
			f"{_guard}.validate_stock_reservation",
			f"{_sales}.validate_reservation_is_sellable",
		],
	},
	"Work Order": {
		"before_validate": f"{_prod}.set_work_order_header_defaults",
		# component sources are defaulted after ERPNext fills required_items, then guarded
		"validate": [
			f"{_prod}.validate_two_stage",
			f"{_prod}.set_work_order_component_sources",
			f"{_guard}.validate_work_order",
		],
		"before_submit": [f"{_prod}.validate_two_stage", f"{_guard}.validate_work_order"],
	},
	"Asset Maintenance": {
		"before_validate": "factory_ops.maintenance.controls.set_next_due_dates",
	},
	"Asset Repair": {
		"before_validate": "factory_ops.maintenance.controls.fill_spare_rates",
		"validate": "factory_ops.maintenance.controls.validate_repair",
		"before_submit": "factory_ops.maintenance.controls.before_submit_repair",
		"after_insert": "factory_ops.maintenance.controls.after_repair_change",
		"on_update": "factory_ops.maintenance.controls.after_repair_change",
		"on_submit": "factory_ops.maintenance.controls.after_repair_change",
		"on_cancel": "factory_ops.maintenance.controls.after_repair_change",
	},
	"BOM": {
		"validate": [
			f"{_prod}.validate_bom",
			"factory_ops.research.doctype.formulation.formulation.validate_bom_formulation",
		],
	},
	"Stock Entry Type": {
		"validate": f"{_prod}.validate_stock_entry_type",
	},
	"User": {
		"validate": "factory_ops.setup.landing.set_default_workspace",
	},
	"Material Request": {
		"validate": f"{_guard}.validate_material_request",
		"before_submit": f"{_guard}.validate_material_request",
	},
	"Subcontracting Order": {
		"validate": f"{_guard}.validate_subcontracting_order",
		"before_submit": f"{_guard}.validate_subcontracting_order",
	},
	"Stock Reconciliation": {
		"validate": f"{_guard}.validate_stock_reconciliation",
		"before_submit": f"{_guard}.validate_stock_reconciliation",
	},
}

scheduler_events = {
	"daily": [
		"factory_ops.quality.batch_status.nightly_consistency_check",
		"factory_ops.sales_ops.shelf_life.segregate_undispatchable_stock",
	],
	# Marketplaces: retry rate-limited calls after their back-off; push inventory to Connected,
	# sync-enabled channels only (none is Connected until a live API test succeeds)
	"hourly": [
		"factory_ops.marketplace.service.retry_due_operations",
		"factory_ops.marketplace.service.push_inventory_for_connected_channels",
	],
}

# Exported configuration that is edited in Desk and then version-controlled.
fixtures = [
	{"dt": "Workspace", "filters": [["module", "in", ["Factory Ops", "Quality"]]]},
]

# Factory OS front end (apps/web), built into www/factory.html + public/factory; client-side routes
website_route_rules = [
	{"from_route": "/factory/<path:app_path>", "to_route": "factory"},
]
