// Copyright (c) 2026, FlowHQ and contributors
// License: GNU General Public License v3. See license.txt

frappe.query_reports["RM PM Requirements"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company", default: frappe.defaults.get_user_default("Company"), reqd: 1 },
		{ fieldname: "factory_group", label: __("Group"), fieldtype: "Select", options: "\nRM\nPM" },
		{ fieldname: "only_shortfall", label: __("Only items with a shortfall"), fieldtype: "Check" },
	],
};
