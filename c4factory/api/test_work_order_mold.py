from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from c4factory.api.work_order_mold import (
	CHANNEL_CONTINUOUS,
	CHANNEL_STANDARD,
	_get_channel_request_quantities,
	_get_mold_material_values,
	_get_remaining_mold_qty,
	validate_and_set_mold_materials,
)


class TestWorkOrderMold(FrappeTestCase):
	def test_mold_bom_is_optional_when_submitting_work_order(self):
		values = {
			"custom_mold_bom_no": None,
			"custom_mold_qty": 0,
			"bom_no": "BOM-FG-001-001",
		}
		work_order = Mock()
		work_order.meta.has_field.return_value = True
		work_order.get.side_effect = values.get
		work_order.bom_no = values["bom_no"]
		work_order.production_item = "FG-001"
		work_order.docstatus = 1
		work_order._action = "submit"

		with patch("c4factory.api.work_order_mold._validate_bom") as validate_bom:
			validate_and_set_mold_materials(work_order)

		validate_bom.assert_called_once_with("BOM-FG-001-001", "FG-001", "Product")
		work_order.set.assert_called_once_with("custom_mold_materials", [])

	def test_scales_and_groups_mold_bom_materials(self):
		bom = frappe._dict({"quantity": 2})
		bom_rows = [
			frappe._dict({"item_code": "RM-1", "stock_qty": 3}),
			frappe._dict({"item_code": "RM-1", "stock_qty": 1}),
		]
		item = frappe._dict(
			{"item_name": "Material 1", "item_group": "Raw", "stock_uom": "Kg"}
		)

		with (
			patch("c4factory.api.work_order_mold.frappe.get_doc", return_value=bom),
			patch("c4factory.api.work_order_mold.frappe.get_all", return_value=bom_rows),
			patch(
				"c4factory.api.work_order_mold.frappe.get_cached_value", return_value=item
			),
			patch(
				"c4factory.api.work_order_mold.get_default_source_warehouse",
				return_value="Stores - C4",
			),
		):
			materials = _get_mold_material_values("BOM-MOLD", 5, "C4", False)

		self.assertEqual(len(materials), 1)
		self.assertEqual(materials[0]["item_code"], "RM-1")
		self.assertEqual(materials[0]["required_qty"], 10)
		self.assertEqual(materials[0]["source_warehouse"], "Stores - C4")

	def test_remaining_mold_qty_uses_shared_channel_coverage(self):
		wo = frappe._dict(
			{
				"name": "WO-1",
				"custom_mold_qty": 10,
			}
		)

		def issued_qty(_work_order, channel, exclude_stock_entry=None):
			return 4 if channel == CHANNEL_CONTINUOUS else 2

		with patch(
			"c4factory.api.work_order_mold._get_channel_issued_qty",
			side_effect=issued_qty,
		):
			remaining = _get_remaining_mold_qty(wo, [object()], [object()])

		self.assertEqual(remaining, 8)
		self.assertNotEqual(CHANNEL_CONTINUOUS, CHANNEL_STANDARD)

	def test_channel_request_only_catches_up_channel_that_is_behind(self):
		wo = frappe._dict({"name": "WO-1", "custom_mold_qty": 10})

		def issued_qty(_work_order, channel, exclude_stock_entry=None):
			return 0 if channel == CHANNEL_CONTINUOUS else 5

		with patch(
			"c4factory.api.work_order_mold._get_channel_issued_qty",
			side_effect=issued_qty,
		):
			quantities = _get_channel_request_quantities(wo, 5, [object()], [object()])

		self.assertEqual(quantities[CHANNEL_CONTINUOUS], 5)
		self.assertEqual(quantities[CHANNEL_STANDARD], 0)
