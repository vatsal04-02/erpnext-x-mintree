// Copyright (c) 2026, FlowHQ and contributors
// License: GNU General Public License v3. See license.txt

frappe.query_reports["QC Queue"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company", default: frappe.defaults.get_user_default("Company"), reqd: 1 },
		{ fieldname: "warehouse", label: __("QC Hold Warehouse"), fieldtype: "Link", options: "Warehouse", get_query: () => ({ filters: { warehouse_type: "QC Hold", is_group: 0 } }) },
		{ fieldname: "overdue_only", label: __("Overdue only"), fieldtype: "Check" },
	],
};
