"""Production controls on top of ERPNext's standard BOM, Work Order and Stock Entry.

The production flow itself is ERPNext's (DECISIONS D-020): a BULK Work Order in kg, then a
fill-and-pack FG Work Order in units that consumes the released bulk. factory_ops adds:

- **Warehouse defaults** for Work Orders, all read from configuration (D-021):
  WIP = ``Company.fo_wip_warehouse``; output = the item's default warehouse (its QC Hold);
  each component = the "Production Source Warehouse" on the item's Item Default for the company,
  else on its item group's, else the QC release destination of its default warehouse.
  ERPNext fills component sources from the item-group default, which for QC groups is the QC
  Hold warehouse; only rows ERPNext filled itself are redirected. A source entered by a user is
  kept, and the QC guards (``inventory/guards.py``) refuse it if it is restricted.
- **BOM guard**: a BOM line may not name a QC Hold or Rejected source warehouse.
- **Wastage** (D-022): a write-off is a Stock Entry whose Stock Entry Type is flagged
  "Wastage write-off" (seeded type: "Wastage", purpose Material Issue). Every row needs an
  enabled Wastage Reason and posts to ``Company.fo_wastage_account``. Ordinary Material Issues
  and manufacturing consumption need no reason, and a reason on them is refused so wastage is
  never misfiled.
"""

import frappe
from frappe import _
from frappe.utils import cint

from factory_ops.inventory.guards import QCGuardError
from factory_ops.inventory.stock_queries import is_qc_hold, is_restricted

WASTAGE_TYPE = "Wastage"


class WastageReasonError(frappe.ValidationError):
	pass


# ---------------------------------------------------------------------------
# Warehouse resolution (configuration, not code)
# ---------------------------------------------------------------------------
def _item_default(parenttype: str, parent: str | None, company: str, field: str) -> str | None:
	if not parent:
		return None
	return frappe.db.get_value(
		"Item Default", {"parenttype": parenttype, "parent": parent, "company": company}, field
	)


def default_item_warehouse(item_code: str, company: str) -> str | None:
	"""Item-level default warehouse for the company, else the item group's."""
	group = frappe.get_cached_value("Item", item_code, "item_group")
	return _item_default("Item", item_code, company, "default_warehouse") or _item_default(
		"Item Group", group, company, "default_warehouse"
	)


def production_source(item_code: str, company: str) -> str | None:
	"""Where production draws an item from (configured on Item Default, item before group)."""
	group = frappe.get_cached_value("Item", item_code, "item_group")
	configured = _item_default("Item", item_code, company, "fo_production_warehouse") or _item_default(
		"Item Group", group, company, "fo_production_warehouse"
	)
	if configured:
		return configured
	default = default_item_warehouse(item_code, company)
	if default and is_qc_hold(default):
		return frappe.get_cached_value("Warehouse", default, "fo_release_warehouse")
	return None if is_restricted(default) else default


def wip_warehouse(company: str) -> str | None:
	return frappe.get_cached_value("Company", company, "fo_wip_warehouse")


# ---------------------------------------------------------------------------
# Work Order
# ---------------------------------------------------------------------------
def qc_subassemblies(bom_no: str | None) -> list[str]:
	"""Components of a BOM that are made in-house (have a BOM) and need QC release, e.g. bulk."""
	if not bom_no:
		return []
	return [
		r.item_code
		for r in frappe.get_all(
			"BOM Item", filters={"parent": bom_no, "bom_no": ("is", "set")}, fields=["item_code"]
		)
		if cint(frappe.get_cached_value("Item", r.item_code, "fo_qc_required"))
	]


def set_work_order_header_defaults(doc, method=None):
	"""before_validate: WIP and output warehouses, before ERPNext validates them. Also remember
	which component rows the user supplied, so only rows ERPNext adds are redirected."""
	doc.flags.fo_user_rows = {id(row) for row in doc.get("required_items") or []}
	if not doc.get("company") or not doc.get("production_item"):
		return
	if not doc.get("bom_no"):
		# Desk fetches the default BOM in the browser; API and data-import users get it here
		doc.bom_no = frappe.db.get_value(
			"BOM",
			{
				"item": doc.production_item,
				"company": doc.company,
				"is_default": 1,
				"is_active": 1,
				"docstatus": 1,
			},
			"name",
		)
	# Two-stage production: ERPNext defaults use_multi_level_bom to 1, which would explode the bulk
	# into its raw materials and skip bulk QC. New orders consume the released sub-assembly.
	if doc.is_new() and qc_subassemblies(doc.get("bom_no")):
		doc.use_multi_level_bom = 0
	if not doc.get("wip_warehouse") and not doc.get("skip_transfer"):
		doc.wip_warehouse = wip_warehouse(doc.company)
	if not doc.get("fg_warehouse"):
		doc.fg_warehouse = default_item_warehouse(doc.production_item, doc.company)


def set_work_order_component_sources(doc, method=None):
	"""validate (after ERPNext's own validate filled required_items from the BOM)."""
	if not doc.get("company") or doc.get("source_warehouse"):
		return  # a header source applies to every row and is guarded as entered
	user_rows = doc.flags.get("fo_user_rows") or set()
	bom_sources = {
		r.item_code: r.source_warehouse
		for r in frappe.get_all(
			"BOM Item", filters={"parent": doc.bom_no}, fields=["item_code", "source_warehouse"]
		)
		if r.source_warehouse
	}
	for row in doc.get("required_items") or []:
		if id(row) in user_rows or row.item_code in bom_sources:
			continue  # entered by a user, or set on the BOM: keep it (guarded)
		# ERPNext filled this row from a plain default warehouse; the configured production source
		# wins over that, whether or not ERPNext's choice happens to be restricted
		row.source_warehouse = production_source(row.item_code, doc.company) or row.source_warehouse


def validate_two_stage(doc, method=None):
	"""validate / before_submit: a QC-controlled sub-assembly must be consumed as released stock."""
	if not cint(doc.get("use_multi_level_bom")):
		return
	items = qc_subassemblies(doc.get("bom_no"))
	if items:
		frappe.throw(
			_(
				"{0} is made in a separate Work Order and must pass QC before use. Turn off 'Use Multi-Level BOM' so this order consumes released {0} instead of its raw materials."
			).format(", ".join(items)),
			exc=QCGuardError,
			title=_("Blocked by QC control"),
		)


# ---------------------------------------------------------------------------
# BOM
# ---------------------------------------------------------------------------
def validate_bom(doc, method=None):
	for row in doc.get("items") or []:
		if is_restricted(row.get("source_warehouse")):
			frappe.throw(
				_("Row #{0}: {1} cannot be drawn from {2} (QC Hold or Rejected stock).").format(
					row.idx, row.item_code, row.source_warehouse
				),
				exc=QCGuardError,
				title=_("Blocked by QC control"),
			)


# ---------------------------------------------------------------------------
# Wastage
# ---------------------------------------------------------------------------
def is_wastage_entry(doc) -> bool:
	return bool(doc.get("stock_entry_type")) and bool(
		cint(frappe.get_cached_value("Stock Entry Type", doc.stock_entry_type, "fo_is_wastage"))
	)


def validate_stock_entry_type(doc, method=None):
	if cint(doc.get("fo_is_wastage")) and doc.purpose != "Material Issue":
		frappe.throw(_("A wastage write-off type must have the purpose Material Issue."), WastageReasonError)


def validate_wastage(doc, method=None):
	if not is_wastage_entry(doc):
		for row in doc.items:
			if row.get("fo_wastage_reason"):
				frappe.throw(
					_(
						"Row #{0}: a Wastage Reason is set but this is not a wastage entry. Use the Stock Entry Type {1} to write off wastage."
					).format(row.idx, WASTAGE_TYPE),
					WastageReasonError,
				)
		return
	account = frappe.get_cached_value("Company", doc.company, "fo_wastage_account")
	for row in doc.items:
		if not row.s_warehouse or row.t_warehouse:
			frappe.throw(
				_("Row #{0}: wastage is written off from a warehouse.").format(row.idx), WastageReasonError
			)
		reason = row.get("fo_wastage_reason")
		if not reason:
			frappe.throw(
				_("Row #{0}: select a Wastage Reason for writing off {1} from {2}.").format(
					row.idx, row.item_code, row.s_warehouse
				),
				WastageReasonError,
			)
		if not frappe.db.get_value("Wastage Reason", reason, "enabled"):
			frappe.throw(
				_("Row #{0}: Wastage Reason {1} is disabled.").format(row.idx, reason), WastageReasonError
			)
		if account:
			row.expense_account = account


def validate_wastage_work_order(doc, method=None):
	"""validate / before_submit: ``fo_work_order`` belongs only on a wastage write-off, and must be a
	live submitted Work Order of the same company (it feeds Production Reconciliation)."""
	work_order = doc.get("fo_work_order")
	if not work_order:
		return
	if not is_wastage_entry(doc):
		frappe.throw(
			_("Production Work Order can only be set on a wastage write-off."), WastageReasonError
		)
	wo = frappe.db.get_value("Work Order", work_order, ["company", "docstatus", "status"], as_dict=True)
	if not wo or wo.docstatus != 1 or wo.status == "Cancelled":
		frappe.throw(_("Work Order {0} is not a submitted, active Work Order.").format(work_order), WastageReasonError)
	if wo.company != doc.company:
		frappe.throw(
			_("Work Order {0} belongs to {1}, not {2}.").format(work_order, wo.company, doc.company),
			WastageReasonError,
		)
