// Copyright (c) 2026, FlowHQ and contributors
// License: GNU General Public License v3. See license.txt

frappe.ui.form.on("Master Data Import", {
	refresh(frm) {
		if (frm.doc.import_type) {
			frm.add_custom_button(__("Download Template"), () => {
				window.open(
					`/api/method/factory_ops.master_data.templates.download_template?import_type=${encodeURIComponent(frm.doc.import_type)}&file_format=xlsx`
				);
			});
		}
		if (frm.is_new() || frm.doc.status === "Imported") return;
		if (frm.doc.import_file) {
			frm.add_custom_button(__("Validate"), () =>
				frm.call("validate_file").then(() => frm.reload_doc())
			);
		}
		if (frm.doc.status === "Ready to Import") {
			frm.add_custom_button(
				__("Import"),
				() =>
					frappe.confirm(__("Import the whole file now? It is imported completely or not at all."), () =>
						frm.call({ method: "start_import", freeze: true, freeze_message: __("Importing ...") }).then(() =>
							frm.reload_doc()
						)
					),
				null
			).addClass("btn-primary");
		}
		const colour = { "Has Errors": "red", "Ready to Import": "blue", Imported: "green", Failed: "red" }[frm.doc.status];
		if (colour) frm.dashboard.set_headline_alert(frm.doc.summary || "", colour);
	},
});
