# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Master Data Import: validate a file, then import it all-or-nothing (D-035).

Status: Draft -> Has Errors | Ready to Import -> Imported | Failed. Import is refused unless the
attached file, as it is now, validates without errors.
"""

import json

import frappe
from frappe import _
from frappe.model.document import Document

from factory_ops.master_data import importer


class MasterDataImport(Document):
	def validate(self):
		if self.has_value_changed("import_file") or self.has_value_changed("import_type"):
			if self.status in ("Has Errors", "Ready to Import"):
				self.status = "Draft"
				self.summary = ""
				self.set("issues", [])
		if self.status == "Imported" and not self.is_new() and self.has_value_changed("import_file"):
			frappe.throw(_("This import is finished; create a new Master Data Import for another file."))

	def _file(self) -> tuple[bytes, str]:
		if not self.import_file:
			frappe.throw(_("Attach the file to import."))
		f = frappe.get_doc("File", {"file_url": self.import_file})
		return f.get_content(), f.file_name or self.import_file

	def _check(self):
		content, filename = self._file()
		rows, issues = importer.validate(self.import_type, content, filename, self.company, self.posting_date)
		self.set("issues", [])
		for i in issues:
			self.append(
				"issues",
				{
					"severity": i.severity,
					"row_no": i.row,
					"column_name": i.column,
					"value": i.value[:140],
					"message": i.message,
				},
			)
		errors = sum(1 for i in issues if i.severity == "Error")
		warnings = len(issues) - errors
		bad_rows = len({i.row for i in issues if i.severity == "Error" and i.row > 1})
		self.status = "Has Errors" if errors else "Ready to Import"
		self.summary = _(
			"{0} data rows checked: {1} rows with errors ({2} errors), {3} warnings. {4}"
		).format(
			len(rows),
			bad_rows,
			errors,
			warnings,
			_("Fix the errors and attach the corrected file; nothing has been imported.")
			if errors
			else _("Ready: the whole file will be imported in one step."),
		)
		return rows, errors

	@frappe.whitelist()
	def validate_file(self):
		self.check_permission("write")
		if self.status == "Imported":
			frappe.throw(_("Already imported."))
		self._check()
		self.save()
		return self.status

	@frappe.whitelist()
	def start_import(self):
		self.check_permission("write")
		if self.status == "Imported":
			frappe.throw(_("Already imported."))
		rows, errors = self._check()  # the file is checked again as it is now
		if errors:
			self.save()
			frappe.throw(_("The file has errors; see the Issues table. Nothing was imported."))
		try:
			created = importer.run(self.import_type, rows, self.company, self.posting_date)
		except importer.ImportFailed as e:
			self.reload()
			self.status = "Failed"
			self.summary = str(e)
			self.append(
				"issues",
				{"severity": "Error", "row_no": 0, "column_name": "", "value": "", "message": str(e)},
			)
			self.save()
			return self.status
		self.status = "Imported"
		self.created_records = json.dumps(created, indent=0)
		self.summary = _("Imported {0} rows: {1} records created.").format(len(rows), len(created))
		self.save()
		return self.status
