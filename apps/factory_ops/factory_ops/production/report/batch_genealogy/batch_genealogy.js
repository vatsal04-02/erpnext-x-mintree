// Copyright (c) 2026, FlowHQ and contributors
// License: GNU General Public License v3. See license.txt

frappe.query_reports["Batch Genealogy"] = {
	filters: [
		{ fieldname: "batch_no", label: __("Batch"), fieldtype: "Link", options: "Batch", reqd: 1 },
		{ fieldname: "direction", label: __("Direction"), fieldtype: "Select", options: "Both\nBackward\nForward", default: "Both" },
		{ fieldname: "max_depth", label: __("Max depth"), fieldtype: "Int", default: 10 },
	],
};
