// Copyright (c) 2026, FlowHQ and contributors
// License: GNU General Public License v3. See license.txt

frappe.ui.form.on("QC Release", {
	refresh(frm) {
		if (frm.doc.docstatus === 0 && !frm.is_new() && !frm.doc.quality_inspection) {
			frm.add_custom_button(__("Create Quality Inspection"), () => {
				frappe
					.call("factory_ops.quality.doctype.qc_release.qc_release.create_quality_inspection", {
						qc_release: frm.doc.name,
					})
					.then((r) => frappe.set_route("Form", "Quality Inspection", r.message));
			});
		}
		if (frm.doc.docstatus === 1 && frm.doc.decision === "Reject" && frappe.user.has_role("QA Manager")) {
			frm.add_custom_button(__("Send for Re-test"), () => {
				frappe.prompt(
					[
						{ fieldname: "reason", fieldtype: "Small Text", label: __("Why re-test"), reqd: 1 },
						{ fieldname: "qty", fieldtype: "Float", label: __("Quantity (blank = all rejected)") },
					],
					(v) =>
						frappe
							.call("factory_ops.quality.doctype.qc_retest.qc_retest.send_for_retest", {
								qc_release: frm.doc.name,
								reason: v.reason,
								qty: v.qty,
							})
							.then((r) => frappe.set_route("Form", "QC Retest", r.message)),
					__("Send for Re-test"),
					__("Send"),
				);
			});
		}
		frm.set_query("source_warehouse", () => ({
			filters: { warehouse_type: "QC Hold", company: frm.doc.company, is_group: 0 },
		}));
		frm.set_query("batch_no", () => ({ filters: { item: frm.doc.item_code } }));
		frm.set_query("qc_deviation", () => ({ filters: { batch_no: frm.doc.batch_no, docstatus: 1 } }));
	},
});
