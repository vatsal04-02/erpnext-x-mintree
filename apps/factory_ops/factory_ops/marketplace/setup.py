"""The four marketplace channels, created once with no credentials and no stock share."""

import frappe

CHANNELS = {
	"Amazon": "Amazon SP-API",
	"Flipkart": "Flipkart Seller API",
	"Myntra": "Myntra Partner API",
	"Nykaa": "Nykaa Seller API",
}


def ensure_marketplace_channels():
	if not frappe.db.exists("DocType", "Marketplace Channel"):
		return
	for name, provider in CHANNELS.items():
		if not frappe.db.exists("Marketplace Channel", name):
			frappe.get_doc({"doctype": "Marketplace Channel", "channel_name": name, "provider": provider}).insert(
				ignore_permissions=True
			)
