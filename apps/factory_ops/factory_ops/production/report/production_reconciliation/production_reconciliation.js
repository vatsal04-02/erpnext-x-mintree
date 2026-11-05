// Copyright (c) 2026, FlowHQ and contributors
// License: GNU General Public License v3. See license.txt

frappe.query_reports["Production Reconciliation"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company", default: frappe.defaults.get_user_default("Company") },
		{ fieldname: "work_order", label: __("Work Order"), fieldtype: "Link", options: "Work Order" },
		{ fieldname: "production_item", label: __("Item"), fieldtype: "Link", options: "Item" },
		{ fieldname: "status", label: __("Status"), fieldtype: "Select", options: "\nNot Started\nIn Process\nCompleted\nStopped\nClosed" },
		{ fieldname: "from_date", label: __("From"), fieldtype: "Date", default: frappe.datetime.add_months(frappe.datetime.get_today(), -1) },
		{ fieldname: "to_date", label: __("To"), fieldtype: "Date", default: frappe.datetime.get_today() },
		{ fieldname: "detail", label: __("Show components"), fieldtype: "Check" },
		{ fieldname: "only_problems", label: __("Only unreconciled or below minimum yield"), fieldtype: "Check" },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "reconciliation" && data) {
			const cls = { Reconciled: "green", Unreconciled: "red", Open: "gray" }[data.reconciliation];
			if (cls) value = `<span class="indicator-pill ${cls}">${data.reconciliation}</span>`;
		}
		return value;
	},
};
