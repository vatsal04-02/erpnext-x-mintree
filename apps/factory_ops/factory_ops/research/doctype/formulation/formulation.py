"""Formulation: versioned ingredient list with a role-checked approval workflow (MINTREE_RND_MODULE.md).

Draft -> Under Review -> Trial -> Approved / Rejected -> Archived. Only a Draft can be edited; every
change after that is a new version (same formulation code, version + 1, linked to the previous
one). Status changes only through `apply_action`, which checks the user's roles on the server.
An approved version can become a BOM, which keeps a link back to the exact version.

No safety or chemical-compatibility rule is invented here: the checks are arithmetic (totals,
quantities), master-data validity and workflow control.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime

ROLE_CHEMIST, ROLE_MANAGER = "R&D Chemist", "R&D Manager"
DEMO_LABEL = "DEMO - NOT REAL - NOT FOR COMMERCIAL PRODUCTION"

# action: (allowed from, to, roles that may do it)
ACTIONS = {
	"send_for_review": ({"Draft"}, "Under Review", {ROLE_CHEMIST, ROLE_MANAGER}),
	"return_to_draft": ({"Under Review"}, "Draft", {ROLE_CHEMIST, ROLE_MANAGER}),
	"start_trial": ({"Under Review"}, "Trial", {ROLE_MANAGER}),
	"approve": ({"Trial"}, "Approved", {ROLE_MANAGER}),
	"reject": ({"Under Review", "Trial"}, "Rejected", {ROLE_MANAGER}),
	"archive": ({"Approved", "Rejected"}, "Archived", {ROLE_MANAGER}),
}
# fields that define the formula; frozen once a version leaves Draft
CONTENT_FIELDS = ("formulation_name", "formulation_code", "batch_size_kg", "target_total", "tolerance", "product_item")


def has_any_role(roles: set[str]) -> bool:
	user_roles = set(frappe.get_roles())
	return "System Manager" in user_roles or bool(user_roles & roles)


def factory_group(item_group: str | None) -> str | None:
	from factory_ops.master_data.importer import _factory_group

	return _factory_group(item_group) if item_group else None


class Formulation(Document):
	def validate(self):
		self.guard_workflow()
		self.validate_ingredients()
		self.compute()
		self.validate_required_from_previous()
		if self.is_demo and DEMO_LABEL not in (self.formulation_name or ""):
			self.formulation_name = f"{self.formulation_name} ({DEMO_LABEL})"

	# -- workflow integrity --------------------------------------------------------------------
	def guard_workflow(self):
		if self.flags.fo_transition:
			return
		if self.is_new():
			self.status = "Draft"
			if not self.version:
				self.version = 1
			return
		before = self.get_doc_before_save()
		if not before:
			return
		if before.status != self.status:
			frappe.throw(_("Status changes only through the workflow actions."), title=_("Not allowed"))
		if before.status != "Draft":
			changed = [f for f in CONTENT_FIELDS if str(before.get(f) or "") != str(self.get(f) or "")]
			if self._rows(before) != self._rows(self):
				changed.append("ingredients")
			if changed:
				frappe.throw(
					_("Only a Draft can be changed ({0}). Create a new version to change {1}.").format(
						before.status, ", ".join(changed)
					),
					title=_("Formulation is locked"),
				)

	@staticmethod
	def _rows(doc) -> list[tuple]:
		return [(r.item_code, flt(r.percentage, 6), int(r.required or 0)) for r in doc.get("ingredients") or []]

	# -- ingredient rules ------------------------------------------------------------------------
	def validate_ingredients(self):
		seen = set()
		for row in self.ingredients:
			if row.item_code in seen:
				frappe.throw(_("Row {0}: {1} is listed twice.").format(row.idx, row.item_code))
			seen.add(row.item_code)
			item = frappe.db.get_value(
				"Item", row.item_code, ["disabled", "is_stock_item", "item_group", "item_name"], as_dict=True
			)
			if not item:
				frappe.throw(_("Row {0}: item {1} does not exist.").format(row.idx, row.item_code))
			if item.disabled:
				frappe.throw(_("Row {0}: {1} is disabled and cannot be used.").format(row.idx, row.item_code))
			if not item.is_stock_item:
				frappe.throw(_("Row {0}: {1} is not a stock item.").format(row.idx, row.item_code))
			if factory_group(item.item_group) != "RM":
				frappe.throw(
					_("Row {0}: {1} is not a raw material (group {2}); only raw materials are ingredients.").format(
						row.idx, row.item_code, item.item_group
					)
				)
			if flt(row.percentage) <= 0:
				frappe.throw(_("Row {0}: the percentage must be above zero.").format(row.idx))

	def compute(self):
		for row in self.ingredients:
			row.quantity_kg = flt(flt(row.percentage) * flt(self.batch_size_kg) / 100, 4)
		self.total_percentage = flt(sum(flt(r.percentage) for r in self.ingredients), 4)

	def total_problem(self) -> str | None:
		if not self.ingredients:
			return _("The formulation has no ingredients.")
		if abs(flt(self.total_percentage) - flt(self.target_total)) > flt(self.tolerance):
			return _("The ingredients add up to {0}%; the formula total must be {1}% (± {2}).").format(
				flt(self.total_percentage, 4), flt(self.target_total), flt(self.tolerance)
			)
		return None

	def validate_required_from_previous(self):
		if not self.previous_version:
			return
		required = frappe.get_all(
			"Formulation Ingredient",
			filters={"parent": self.previous_version, "parenttype": "Formulation", "required": 1},
			pluck="item_code",
		)
		present = {r.item_code for r in self.ingredients}
		missing = [i for i in required if i not in present]
		if missing:
			frappe.throw(
				_("Required ingredients of the previous version are missing: {0}.").format(", ".join(missing)),
				title=_("Required ingredient removed"),
			)

	# -- actions ---------------------------------------------------------------------------------
	@frappe.whitelist()
	def apply_action(self, action: str, note: str | None = None):
		if action not in ACTIONS:
			frappe.throw(_("Unknown action {0}.").format(action))
		allowed_from, target, roles = ACTIONS[action]
		if self.status not in allowed_from:
			frappe.throw(_("{0} is not possible while the formulation is {1}.").format(action.replace("_", " "), self.status))
		if not has_any_role(roles):
			frappe.throw(
				_("Only {0} may {1}.").format(" or ".join(sorted(roles)), action.replace("_", " ")),
				frappe.PermissionError,
			)
		self.check_permission("write")
		if action == "send_for_review":
			if problem := self.total_problem():
				frappe.throw(problem, title=_("Formula total"))
			if self.version > 1 and not (self.change_note or "").strip():
				frappe.throw(_("Describe what changed in this version before sending it for review."))
			self.submitted_by = frappe.session.user
		if action in ("approve", "reject"):
			if self.submitted_by == frappe.session.user and "System Manager" not in frappe.get_roles():
				frappe.throw(_("The person who sent a version for review cannot decide on it."), frappe.PermissionError)
			if action == "reject" and not (note or "").strip():
				frappe.throw(_("Give the reason for rejecting."))
			if action == "approve":
				passed = frappe.db.exists(
					"Formulation Trial", {"formulation": self.name, "docstatus": 1, "outcome": "Pass"}
				)
				if not passed:
					frappe.throw(
						_("Approval needs at least one submitted trial of this version with outcome Pass."),
						title=_("No passed trial"),
					)
			self.reviewed_by = frappe.session.user
			self.decided_on = now_datetime()
			self.decision_note = note
		self.status = target
		self.flags.fo_transition = True
		self.save()
		if action == "approve":
			# one approved version per formulation code: earlier approved versions are archived
			for other in frappe.get_all(
				"Formulation",
				filters={"formulation_code": self.formulation_code, "status": "Approved", "name": ("!=", self.name)},
				pluck="name",
			):
				doc = frappe.get_doc("Formulation", other)
				doc.status = "Archived"
				doc.flags.fo_transition = True
				doc.add_comment("Info", _("Archived: version {0} ({1}) was approved.").format(self.version, self.name))
				doc.save()
		self.add_comment("Info", _("{0}: {1} -> {2}{3}").format(action.replace("_", " "), ", ".join(sorted(allowed_from)), target, f" — {note}" if note else ""))
		return self.status

	@frappe.whitelist()
	def new_version(self) -> str:
		if self.status == "Draft":
			frappe.throw(_("This version is still a Draft; edit it instead."))
		if not has_any_role({ROLE_CHEMIST, ROLE_MANAGER}):
			frappe.throw(_("Only R&D may create a new version."), frappe.PermissionError)
		latest = (
			frappe.get_all(
				"Formulation",
				filters={"formulation_code": self.formulation_code},
				pluck="version",
				order_by="version desc",
				limit=1,
			)
			or [self.version]
		)[0]
		doc = frappe.copy_doc(self)
		doc.update(
			{
				"version": int(latest) + 1,
				"previous_version": self.name,
				"status": "Draft",
				"change_note": "",
				"submitted_by": None,
				"reviewed_by": None,
				"decided_on": None,
				"decision_note": None,
				"bom": None,
			}
		)
		doc.formulation_name = (self.formulation_name or "").replace(f" ({DEMO_LABEL})", "")
		doc.insert()
		return doc.name

	@frappe.whitelist()
	def create_bom(self, company: str | None = None) -> str:
		if self.status != "Approved":
			frappe.throw(_("Only an approved formulation can become a production BOM."), title=_("Not approved"))
		# the BOM is created with the user's own rights: manufacturing takes an approved formula into
		# production (ERPNext's BOM create + submit, e.g. Manufacturing Manager); R&D approves it
		if not (frappe.has_permission("BOM", "create") and frappe.has_permission("BOM", "submit")):
			frappe.throw(_("Creating the BOM needs permission to create and submit BOMs (manufacturing)."), frappe.PermissionError)
		if self.bom:
			frappe.throw(_("BOM {0} was already created from this version.").format(self.bom))
		if self.is_demo and not frappe.conf.get("fo_demo_site"):
			frappe.throw(
				_("Demo formulations can never become production BOMs ({0}).").format(DEMO_LABEL),
				title=_("Demo formulation"),
			)
		if not self.product_item:
			frappe.throw(_("Set the bulk item this formulation makes before creating its BOM."))
		group = factory_group(frappe.db.get_value("Item", self.product_item, "item_group"))
		if group != "BULK":
			frappe.throw(_("{0} is not a bulk item; a formulation makes bulk.").format(self.product_item))
		if problem := self.total_problem():
			frappe.throw(problem)
		company = (
			company
			or frappe.defaults.get_user_default("Company")
			or frappe.db.get_single_value("Global Defaults", "default_company")
		)
		if not company:
			frappe.throw(_("Choose the company the BOM is for."))
		bom = frappe.get_doc(
			{
				"doctype": "BOM",
				"item": self.product_item,
				"company": company,
				"quantity": flt(self.batch_size_kg),
				"rm_cost_as_per": "Valuation Rate",
				"is_active": 1,
				"is_default": 1,
				"fo_formulation": self.name,
				"fo_formulation_version": self.version,
				"remarks": (
					f"From formulation {self.formulation_code} version {self.version} ({self.name})."
					+ (f" {DEMO_LABEL}." if self.is_demo else "")
				),
				"items": [{"item_code": r.item_code, "qty": r.quantity_kg} for r in self.ingredients],
			}
		)
		bom.insert()
		bom.submit()
		self.db_set("bom", bom.name)
		self.add_comment("Info", _("BOM {0} created from this approved version.").format(bom.name))
		return bom.name


def validate_bom_formulation(doc, method=None):
	"""A BOM that claims to come from a formulation must point at an approved version of it."""
	if not doc.get("fo_formulation"):
		return
	status, version, is_demo = frappe.db.get_value(
		"Formulation", doc.fo_formulation, ["status", "version", "is_demo"]
	) or (None, None, None)
	if status not in ("Approved", "Archived"):
		frappe.throw(_("Formulation {0} is not approved; it cannot be the source of a BOM.").format(doc.fo_formulation))
	if doc.is_new() and status != "Approved":
		frappe.throw(_("Formulation {0} is not the approved version.").format(doc.fo_formulation))
	doc.fo_formulation_version = version
	if is_demo and not frappe.conf.get("fo_demo_site"):
		frappe.throw(_("Demo formulations can never become production BOMs."))
