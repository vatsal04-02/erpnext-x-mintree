"""Demo data safety: the seeder refuses non-demo sites and every synthetic record is labelled."""

import frappe
from frappe.tests import IntegrationTestCase

from factory_ops.demo import data as D
from factory_ops.demo import seed


class TestDemoSafety(IntegrationTestCase):
	def test_seeder_refuses_a_site_not_marked_as_demo(self):
		self.assertFalse(frappe.conf.get("fo_demo_site"))
		with self.assertRaises(frappe.ValidationError):
			seed.seed_demo_site()
		self.assertFalse(frappe.db.exists("Item", "DEMO-RM-0001"))

	def test_every_synthetic_record_is_labelled_not_real(self):
		for code, (name, *_rest) in D.ITEMS.items():
			self.assertTrue(code.startswith("DEMO-"), code)
			self.assertIn("NOT REAL", name, code)
		for name in [*D.SUPPLIERS, D.CUSTOMER[0], D.SUPPLIER_GROUP, *D.QI_TEMPLATES]:
			self.assertIn("NOT REAL", name)
		self.assertTrue(all(p.startswith("DEMO ") for p in D.QI_PARAMETERS))
		self.assertTrue(all(o[4] == "" or o[4].startswith("DEMO-") for o in D.OPENING_STOCK))

	def test_demo_boms_use_only_demo_materials(self):
		_qty, _loss, components = D.BULK_BOM
		self.assertTrue(all(c in D.ITEMS and D.ITEMS[c][1] == "RM" for c, _ in components))
		self.assertTrue(all(p in D.ITEMS and D.ITEMS[p][1] == "PM" for p in D.FG_PACK))
		for code, (_n, _g, _u, _e, _s, supplier, rate) in D.ITEMS.items():
			if D.ITEMS[code][1] in ("RM", "PM"):
				self.assertIn(supplier, D.SUPPLIER_KEY)
				self.assertGreater(rate, 0)
