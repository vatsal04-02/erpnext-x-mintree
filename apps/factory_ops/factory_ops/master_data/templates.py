"""Import templates generated from the same column specification the validator uses (D-035).

    bench --site <site> execute factory_ops.master_data.templates.write_templates

writes ``factory_ops/master_data/templates/<type>.xlsx`` and ``.csv`` (committed to the repository).
The XLSX has a **Data** sheet (required columns marked with *, example rows) and an
**Instructions** sheet (column, required, format, permitted values, rules, duplicate handling).
The Master Data Import form downloads the same XLSX.
"""

import csv
import io
import os

import frappe

from factory_ops.master_data.importer import SPECS

KIND_TEXT = {
	"text": "Text",
	"int": "Whole number",
	"number": "Number (no thousands separators needed)",
	"date": "Date YYYY-MM-DD (DD-MM-YYYY also accepted)",
	"check": "0 or 1",
	"choice": "One of the permitted values",
}

PREPARATION = [
	"Keep the header row exactly as in this template; a * marks a required column. Do not add columns.",
	"Fill one record per row (BOMs: one row per component). Delete the example rows before importing.",
	"Use the exact names that exist in the system for links (item groups, UOMs, warehouses, suppliers ...).",
	"Save as .xlsx (this workbook, Data sheet) or as .csv (UTF-8).",
	"In Desk: Master Data Import > New > choose the type > attach the file > Validate. Fix every error listed",
	"(row, column, reason) and attach the corrected file. Import is enabled only when no errors remain;",
	"it imports the whole file or nothing.",
]


def _header(spec):
	return [c.name + ("*" if c.required else "") for c in spec.columns]


def _examples(spec):
	n = max((len(c.example) for c in spec.columns), default=0)
	return [[(c.example[i] if i < len(c.example) else "") for c in spec.columns] for i in range(n)]


def build_csv(key: str) -> bytes:
	spec = SPECS[key]
	buf = io.StringIO()
	w = csv.writer(buf)
	w.writerow(_header(spec))
	w.writerows(_examples(spec))
	return buf.getvalue().encode("utf-8")


def build_xlsx(key: str) -> bytes:
	from openpyxl import Workbook
	from openpyxl.styles import Font, PatternFill

	spec = SPECS[key]
	wb = Workbook()
	data = wb.active
	data.title = "Data"
	data.append(_header(spec))
	for row in _examples(spec):
		data.append(row)
	bold = Font(bold=True)
	for cell in data[1]:
		cell.font = bold
		if cell.value.endswith("*"):
			cell.fill = PatternFill("solid", fgColor="FFF2CC")
	for i, c in enumerate(spec.columns, start=1):
		data.column_dimensions[data.cell(1, i).column_letter].width = max(14, len(c.name) + 4)
	ins = wb.create_sheet("Instructions")
	ins.append([f"{spec.label}: import template"])
	ins["A1"].font = Font(bold=True, size=13)
	ins.append(
		[
			f"Creates: {spec.doctype}"
			+ (" (select the company on the import form)" if spec.needs_company else "")
		]
	)
	if spec.needs_posting_date:
		ins.append(["Select the posting date (opening date) on the import form."])
	ins.append([])
	ins.append(["Column", "Required", "Format", "Permitted values / rule"])
	for cell in ins[ins.max_row]:
		cell.font = bold
	for c in spec.columns:
		ins.append(
			[
				c.name,
				"Yes" if c.required else "No",
				KIND_TEXT[c.kind],
				", ".join(c.choices) if c.choices else c.help,
			]
		)
	ins.append([])
	ins.append(["Validation rules"])
	ins[ins.max_row][0].font = bold
	for rule in spec.rules:
		ins.append([rule])
	ins.append(
		[
			"Every value is checked before anything is imported; each problem is reported with its row, column and reason."
		]
	)
	ins.append([])
	ins.append(["Duplicates"])
	ins[ins.max_row][0].font = bold
	ins.append([spec.duplicates])
	ins.append([])
	ins.append(["Preparing the file"])
	ins[ins.max_row][0].font = bold
	for line in PREPARATION:
		ins.append([line])
	ins.column_dimensions["A"].width = 32
	ins.column_dimensions["D"].width = 80
	buf = io.BytesIO()
	wb.save(buf)
	return buf.getvalue()


def template_dir() -> str:
	return os.path.join(os.path.dirname(__file__), "templates")


def slug(key: str) -> str:
	return key.lower().replace(" ", "_")


def write_templates() -> list[str]:
	os.makedirs(template_dir(), exist_ok=True)
	written = []
	for key in SPECS:
		for ext, builder in (("xlsx", build_xlsx), ("csv", build_csv)):
			path = os.path.join(template_dir(), f"{slug(key)}.{ext}")
			with open(path, "wb") as f:
				f.write(builder(key))
			written.append(path)
	return written


@frappe.whitelist()
def download_template(import_type: str, file_format: str = "xlsx"):
	if import_type not in SPECS:
		frappe.throw(frappe._("Unknown import type."))
	content = build_xlsx(import_type) if file_format == "xlsx" else build_csv(import_type)
	frappe.local.response.filename = f"{slug(import_type)}_template.{file_format}"
	frappe.local.response.filecontent = content
	frappe.local.response.type = "binary"
