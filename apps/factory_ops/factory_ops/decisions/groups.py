# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Factory item group (RM / PM / BULK / FG) of many items, from two queries in total."""

import frappe

from factory_ops.constants import INSPECTION_TYPE_BY_GROUP

FACTORY_GROUPS = tuple(INSPECTION_TYPE_BY_GROUP)  # RM, PM, BULK, FG


def factory_groups_of(item_codes) -> dict[str, str | None]:
	"""{item_code: factory group or None}. One query for the items, one for the item group tree."""
	item_codes = tuple({c for c in item_codes if c})
	if not item_codes:
		return {}
	item_group = {
		r.name: r.item_group
		for r in frappe.get_all("Item", filters={"name": ["in", item_codes]}, fields=["name", "item_group"], limit_page_length=0)
	}
	parent = {
		g.name: g.parent_item_group
		for g in frappe.get_all("Item Group", fields=["name", "parent_item_group"], limit_page_length=0)
	}

	def resolve(group):
		for _ in range(12):
			if not group:
				return None
			if group in FACTORY_GROUPS:
				return group
			group = parent.get(group)
		return None

	return {code: resolve(item_group.get(code)) for code in item_codes}
