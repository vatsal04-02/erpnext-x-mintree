# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""Mintree branding (B3): assets, accent colour, settings, login-page logo.

The page renders themselves are checked in a real browser by ``deploy/dev/ui_check_desk.js``
(screenshots reviewed by hand); these tests cover what can be asserted without one.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from factory_ops.setup import branding
from factory_ops.tests.utils import FactoryOpsTestCase

PUBLIC = Path(frappe.get_app_path("factory_ops")) / "public"
CSS = (PUBLIC / "css" / "mintree.css").read_text()
RULES = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)  # code only: comments may mention anything


def luminance(hex_colour: str) -> float:
	r, g, b = (int(hex_colour.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4))

	def lin(c):
		return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

	return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a: str, b: str) -> float:
	la, lb = sorted((luminance(a), luminance(b)), reverse=True)
	return (la + 0.05) / (lb + 0.05)


class TestAssetsAndStyle(FactoryOpsTestCase):
	def test_logo_assets_are_safe_wellformed_svgs_in_the_brand_colour(self):
		for name in ("mintree-logo.svg", "mintree-mark.svg"):
			text = (PUBLIC / "images" / name).read_text()
			root = ET.fromstring(text)  # well-formed
			self.assertTrue(root.tag.endswith("svg"), name)
			self.assertFalse(re.search(r"<script|onload|onclick|href=|xlink|<image|data:|<style|@import", text, re.I), name)
			self.assertLess(len(text), 20_000, name)
			self.assertIn(branding.BRAND_TEAL.lower(), text.lower(), name)

	def test_mark_is_square_so_it_fits_the_desk_rail_tile(self):
		root = ET.fromstring((PUBLIC / "images" / "mintree-mark.svg").read_text())
		_x, _y, w, h = (float(v) for v in root.attrib["viewBox"].split())
		self.assertEqual(w, h)

	def test_asset_urls_match_the_files(self):
		for url in (branding.WORDMARK, branding.MARK):
			self.assertTrue((PUBLIC / url.removeprefix("/assets/factory_ops/")).is_file(), url)

	def test_button_and_link_colours_have_readable_text_contrast(self):
		"""White label on the accent must reach WCAG AA (4.5:1) for normal text. The raw brand teal
		does not (about 4.4:1), which is why buttons use a darker shade of the same hue."""
		self.assertLess(contrast("#ffffff", branding.BRAND_TEAL), 4.5)  # documents the reason
		shades = set(re.findall(r"--(?:primary|primary-color|btn-primary|bt-solid-bg(?:-hover|-active)?):\s*(#[0-9a-f]{6})", RULES, re.I))
		self.assertEqual(len(shades), 3, shades)  # normal, hover, active
		for shade in shades:
			self.assertGreaterEqual(contrast("#ffffff", shade), 4.5, f"{shade} fails AA with a white label")

	def test_accent_is_light_theme_only_and_leaves_destructive_buttons_alone(self):
		self.assertIn(':root:not([data-theme="dark"])', RULES)
		self.assertIn(".es-button:not([data-theme])", RULES)  # default theme only
		self.assertNotRegex(RULES, r'data-theme="red"|--surface-red|--bt-danger')

	def test_stylesheet_is_registered_for_desk_and_website_pages(self):
		for hook in ("app_include_css", "web_include_css"):
			self.assertIn("/assets/factory_ops/css/mintree.css", frappe.get_hooks(hook, app_name="factory_ops"))

	def test_app_identity_hooks(self):
		self.assertEqual(frappe.get_hooks("app_title", app_name="factory_ops"), [branding.PRODUCT_NAME])
		self.assertEqual(frappe.get_hooks("app_logo_url", app_name="factory_ops"), [branding.MARK])


class TestBrandingSettings(FactoryOpsTestCase):
	def setUp(self):
		super().setUp()
		self.original = {(dt, f): frappe.db.get_single_value(dt, f) for dt, f, _v, _r in branding.BRANDING}

	def tearDown(self):
		for (dt, f), value in self.original.items():
			frappe.db.set_single_value(dt, f, value)
		super().tearDown()

	def test_migrate_applied_the_branding_unless_someone_customised_it(self):
		for dt, field, value, replaceable in branding.BRANDING:
			current = self.original[(dt, field)]
			self.assertTrue(current == value or current not in replaceable, f"{dt}.{field} = {current!r}")

	def test_stock_defaults_are_replaced_and_it_is_idempotent(self):
		for dt, field, _v, _r in branding.BRANDING:
			frappe.db.set_single_value(dt, field, "Frappe" if field == "app_name" else "")
		changed = branding.apply_branding()
		self.assertEqual(len(changed), len(branding.BRANDING))
		self.assertEqual(frappe.db.get_single_value("Website Settings", "app_name"), branding.PRODUCT_NAME)
		self.assertEqual(frappe.db.get_single_value("System Settings", "app_name"), branding.PRODUCT_NAME)
		self.assertEqual(frappe.db.get_single_value("Navbar Settings", "app_logo"), branding.MARK)
		self.assertEqual(frappe.db.get_single_value("Website Settings", "splash_image"), branding.WORDMARK)
		self.assertEqual(branding.apply_branding(), [])  # second run changes nothing

	def test_a_customisation_made_in_desk_is_never_overwritten(self):
		frappe.db.set_single_value("Website Settings", "app_name", "Another Brand")
		frappe.db.set_single_value("Navbar Settings", "app_logo", "/files/their-own-logo.png")
		frappe.db.set_single_value("Website Settings", "splash_image", "/files/their-splash.png")
		self.assertEqual(branding.apply_branding(), [])
		self.assertEqual(frappe.db.get_single_value("Website Settings", "app_name"), "Another Brand")
		self.assertEqual(frappe.db.get_single_value("Navbar Settings", "app_logo"), "/files/their-own-logo.png")
		self.assertEqual(frappe.db.get_single_value("Website Settings", "splash_image"), "/files/their-splash.png")

	def test_migrate_runs_it(self):
		from factory_ops.setup import install

		with patch.object(branding, "apply_branding", return_value=[]) as called:
			install.apply_setup()
		called.assert_called_once()


class TestLoginLogo(FactoryOpsTestCase):
	def setUp(self):
		super().setUp()
		self.saved_request = getattr(frappe.local, "request", None)

	def tearDown(self):
		frappe.local.request = self.saved_request
		super().tearDown()

	def at(self, path: str):
		frappe.local.request = SimpleNamespace(path=path)
		return branding.update_website_context(frappe._dict())

	def test_login_pages_get_the_wordmark(self):
		with patch.object(frappe, "get_website_settings", return_value=None):
			for path in ("/login", "/login/", "/update-password"):
				self.assertEqual(self.at(path), {"logo": branding.WORDMARK}, path)

	def test_other_pages_are_untouched(self):
		with patch.object(frappe, "get_website_settings", return_value=None):
			for path in ("/", "/desk", "/about", "/loginx", "/api/method/ping"):
				self.assertIsNone(self.at(path), path)

	def test_a_logo_set_in_website_settings_wins(self):
		with patch.object(frappe, "get_website_settings", return_value="/files/custom.png"):
			self.assertIsNone(self.at("/login"))

	def test_no_request_is_handled(self):
		frappe.local.request = None
		self.assertIsNone(branding.update_website_context(frappe._dict()))

	def test_hook_is_registered(self):
		self.assertIn(
			"factory_ops.setup.branding.update_website_context",
			frappe.get_hooks("update_website_context", app_name="factory_ops"),
		)
