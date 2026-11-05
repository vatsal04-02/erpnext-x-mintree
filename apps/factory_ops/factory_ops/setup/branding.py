# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Mintree branding for Desk and the login page, through Frappe's own settings.

- Desk rail logo: ``Navbar Settings.app_logo`` = the square mark (a monogram cut from the supplied
  wordmark: the wide wordmark is unreadable in the 32 px rail tile).
- Login page logo: the full wordmark, through the ``update_website_context`` hook, so the page keeps
  working if someone later sets their own ``Website Settings.app_logo``.
- Product name: ``Website Settings.app_name`` and ``System Settings.app_name``.
- Splash screen shown while Desk loads: ``Website Settings.splash_image`` = the wordmark.

The ``app_logo_url`` hook cannot do this: with three apps defining it Frappe takes the first
(its own) logo. Values are replaced **only when they are empty or Frappe/ERPNext defaults** (or
already ours), so a customisation made in Desk is never overwritten.

Brand assets come from Mintree (the supplied wordmark). The square mark is derived from it and is
pending Mintree's approval; no favicon is set because none was supplied.
"""

import frappe

PRODUCT_NAME = "Mintree Factory OS"
WORDMARK = "/assets/factory_ops/images/mintree-logo.svg"
MARK = "/assets/factory_ops/images/mintree-mark.svg"
BRAND_TEAL = "#01877A"

STOCK_NAMES = (None, "", "Frappe", "ERPNext", PRODUCT_NAME)
STOCK_LOGOS = (
	None,
	"",
	"/assets/erpnext/images/erpnext-logo.svg",
	"/assets/frappe/images/frappe-framework-logo.svg",
	MARK,
	WORDMARK,
)

# (doctype, field, value, values that may be replaced)
BRANDING = (
	("Website Settings", "app_name", PRODUCT_NAME, STOCK_NAMES),
	("Website Settings", "splash_image", WORDMARK, STOCK_LOGOS),
	("System Settings", "app_name", PRODUCT_NAME, STOCK_NAMES),
	("Navbar Settings", "app_logo", MARK, STOCK_LOGOS),
)


def apply_branding() -> list[str]:
	"""Idempotent. Returns the settings it changed (for tests and migrate logs)."""
	changed = []
	for doctype, field, value, replaceable in BRANDING:
		current = frappe.db.get_single_value(doctype, field)
		if current in replaceable and current != value:
			frappe.db.set_single_value(doctype, field, value)
			changed.append(f"{doctype}.{field}")
	return changed


LOGIN_PATHS = ("/login", "/update-password")


def update_website_context(context):
	"""Hook: the login and reset-password pages show the wordmark (Desk keeps the square mark).
	Skipped if someone has set their own Website Settings logo."""
	request = getattr(frappe.local, "request", None)
	path = (getattr(request, "path", "") or "").rstrip("/")
	if path in LOGIN_PATHS and not frappe.get_website_settings("app_logo"):
		return {"logo": WORDMARK}
