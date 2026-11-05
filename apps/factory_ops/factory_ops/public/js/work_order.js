// Copyright (c) 2026, FlowHQ and contributors
// License: GNU General Public License v3. See license.txt

frappe.ui.form.on("Work Order", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1) return;
		frm.add_custom_button(
			__("Production Reconciliation"),
			() =>
				frappe.set_route("query-report", "Production Reconciliation", {
					company: frm.doc.company,
					work_order: frm.doc.name,
					from_date: "",
					to_date: "",
					detail: 1,
				}),
			__("View")
		);
	},
});
