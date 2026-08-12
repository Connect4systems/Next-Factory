from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from c4factory.c4_manufacturing.bom_hooks import _build_bom_name, autoname


class TestBOMNaming(FrappeTestCase):
	def test_product_keeps_standard_erpnext_name(self):
		doc = frappe._dict(
			{
				"item": "FG-001",
				"custom_bom_type": "Product",
				"name": "BOM-FG-001-001",
			}
		)

		autoname(doc)

		self.assertEqual(doc.name, "BOM-FG-001-001")

	def test_blank_type_defaults_to_product(self):
		doc = frappe._dict(
			{"item": "FG-001", "custom_bom_type": "", "name": "BOM-FG-001-001"}
		)

		autoname(doc)

		self.assertEqual(doc.custom_bom_type, "Product")
		self.assertEqual(doc.name, "BOM-FG-001-001")

	@patch("c4factory.c4_manufacturing.bom_hooks.frappe.get_all")
	def test_mold_uses_mld_series(self, get_all):
		get_all.return_value = ["MLD-FG-001-001", "MLD-FG-001-002"]
		doc = frappe._dict({"item": "FG-001", "custom_bom_type": "Mold"})
		doc.get_index_for_bom = Mock(return_value=3)

		autoname(doc)

		self.assertEqual(doc.name, "MLD-FG-001-003")
		doc.get_index_for_bom.assert_called_once_with(get_all.return_value)

	def test_mld_name_obeys_bom_name_length_limit(self):
		name = _build_bom_name("MLD", "A" * 200, 1)

		self.assertEqual(len(name), 140)
		self.assertTrue(name.startswith("MLD-"))
		self.assertTrue(name.endswith("-001"))
