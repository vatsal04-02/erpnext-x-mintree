// Copyright (c) 2026, FlowHQ and contributors
// License: GNU General Public License v3. See license.txt

frappe.query_reports["Available to Sell"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company", default: frappe.defaults.get_user_default("Company"), reqd: 1 },
		{ fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item" },
		{ fieldname: "item_group", label: __("Item Group"), fieldtype: "Link", options: "Item Group" },
		{ fieldname: "by_warehouse", label: __("Split by warehouse"), fieldtype: "Check" },
	],
};
