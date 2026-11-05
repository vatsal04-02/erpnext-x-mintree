// Copyright (c) 2026, FlowHQ and contributors
// License: GNU General Public License v3. See license.txt

frappe.ui.form.on("Batch", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(
			__("Batch Genealogy"),
			() => frappe.set_route("query-report", "Batch Genealogy", { batch_no: frm.doc.name }),
			__("View")
		);
	},
});
