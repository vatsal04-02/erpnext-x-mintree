"""The "Sales and Dispatch" dashboard, built from standard Number Cards and Dashboard Charts.

Created (and kept in step) on install and on every migrate. All figures are live queries on
submitted documents; nothing is stored. Not standard (is_standard 0), so Desk users may add to it.
"""

import json

import frappe

DASHBOARD = "Sales and Dispatch"

# label: (doctype, function, field for Sum, filters)
NUMBER_CARDS = {
	"Open Sales Orders": (
		"Sales Order",
		"Count",
		None,
		[["docstatus", "=", 1], ["status", "in", ["To Deliver and Bill", "To Deliver"]]],
	),
	"Orders to Dispatch (value)": (
		"Sales Order",
		"Sum",
		"base_grand_total",
		[["docstatus", "=", 1], ["per_delivered", "<", 100], ["status", "not in", ["Closed", "On Hold"]]],
	),
	"Pick Lists Open": (
		"Pick List",
		"Count",
		None,
		[["docstatus", "<", 2], ["status", "in", ["Draft", "Open"]]],
	),
	"Dispatches Not Billed": (
		"Delivery Note",
		"Count",
		None,
		[["docstatus", "=", 1], ["is_return", "=", 0], ["per_billed", "<", 100]],
	),
	"Receivables Outstanding": (
		"Sales Invoice",
		"Sum",
		"outstanding_amount",
		[["docstatus", "=", 1], ["outstanding_amount", ">", 0]],
	),
	"Overdue Invoices": ("Sales Invoice", "Count", None, [["docstatus", "=", 1], ["status", "=", "Overdue"]]),
}

# name: (doctype, chart type, date field, value field, filters, chart style)
CHARTS = {
	"Sales Orders by Week": (
		"Sales Order",
		"Sum",
		"transaction_date",
		"base_grand_total",
		[["docstatus", "=", 1]],
		"Bar",
	),
	"Dispatches by Week": (
		"Delivery Note",
		"Count",
		"posting_date",
		None,
		[["docstatus", "=", 1], ["is_return", "=", 0]],
		"Line",
	),
	"Invoiced by Week": (
		"Sales Invoice",
		"Sum",
		"posting_date",
		"base_grand_total",
		[["docstatus", "=", 1], ["is_return", "=", 0]],
		"Bar",
	),
	"Payments Received by Week": (
		"Payment Entry",
		"Sum",
		"posting_date",
		"base_paid_amount",
		[["docstatus", "=", 1], ["payment_type", "=", "Receive"]],
		"Line",
	),
}


def _filters(doctype, filters):
	return json.dumps([[doctype, f, op, v, False] for f, op, v in filters])


def ensure_sales_dashboard():
	for label, (doctype, function, field, filters) in NUMBER_CARDS.items():
		values = {
			"label": label,
			"type": "Document Type",
			"document_type": doctype,
			"function": function,
			"aggregate_function_based_on": field,
			"filters_json": _filters(doctype, filters),
			"is_public": 1,
			"show_percentage_stats": 0,
		}
		_upsert("Number Card", label, values)
	for name, (doctype, chart_type, based_on, value_field, filters, style) in CHARTS.items():
		values = {
			"chart_name": name,
			"chart_type": chart_type,
			"document_type": doctype,
			"based_on": based_on,
			"value_based_on": value_field,
			"timeseries": 1,
			"timespan": "Last Quarter",
			"time_interval": "Weekly",
			"type": style,
			"filters_json": _filters(doctype, filters),
			"is_public": 1,
		}
		_upsert("Dashboard Chart", name, values)
	if frappe.db.exists("Dashboard", DASHBOARD):
		dash = frappe.get_doc("Dashboard", DASHBOARD)
	else:
		dash = frappe.new_doc("Dashboard")
		dash.dashboard_name = DASHBOARD
	present_cards = {c.card for c in dash.cards}
	for label in NUMBER_CARDS:
		if label not in present_cards:
			dash.append("cards", {"card": label})
	present_charts = {c.chart for c in dash.charts}
	for name in CHARTS:
		if name not in present_charts:
			dash.append("charts", {"chart": name, "width": "Half"})
	dash.save(ignore_permissions=True) if not dash.is_new() else dash.insert(ignore_permissions=True)


def _upsert(doctype, name, values):
	if frappe.db.exists(doctype, name):
		doc = frappe.get_doc(doctype, name)
		doc.update(values)
		doc.save(ignore_permissions=True)
	else:
		frappe.get_doc({"doctype": doctype, **values}).insert(ignore_permissions=True)
