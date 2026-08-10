from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from c4factory.c4_manufacturing.item_group_warehouse import (
	set_warehouses_from_item_group,
)
from c4factory.c4_manufacturing.work_order_hooks import (
	_get_default_warehouse_from_item_group_defaults,
)


class TestItemGroupSourceWarehouse(FrappeTestCase):
	def test_company_default_does_not_fall_back_to_another_company(self):
		group = frappe._dict(
			{
				"defaults": [
					frappe._dict(
						{"company": "Other Company", "default_warehouse": "Other Stores"}
					)
				]
			}
		)

		warehouse = _get_default_warehouse_from_item_group_defaults(group, "C4")

		self.assertIsNone(warehouse)

	def test_sets_stock_entry_source_and_preserves_existing_warehouse(self):
		doc = frappe._dict(
			{
				"doctype": "Stock Entry",
				"company": "C4",
				"purpose": "Material Transfer",
				"items": [
					frappe._dict({"item_code": "RM-1", "s_warehouse": None}),
					frappe._dict({"item_code": "RM-2", "s_warehouse": "Manual - C4"}),
				],
			}
		)

		with patch(
			"c4factory.c4_manufacturing.item_group_warehouse.get_default_source_warehouse",
			return_value="Group Stores - C4",
		) as get_warehouse:
			set_warehouses_from_item_group(doc)

		self.assertEqual(doc.items[0].s_warehouse, "Group Stores - C4")
		self.assertEqual(doc.items[1].s_warehouse, "Manual - C4")
		get_warehouse.assert_called_once_with(
			item_code="RM-1", company="C4", item_group=None
		)

	def test_sets_material_transfer_request_source_warehouse(self):
		doc = frappe._dict(
			{
				"doctype": "Material Request",
				"company": "C4",
				"material_request_type": "Material Transfer",
				"items": [frappe._dict({"item_code": "RM-1", "from_warehouse": None})],
			}
		)

		with patch(
			"c4factory.c4_manufacturing.item_group_warehouse.get_default_source_warehouse",
			return_value="Group Stores - C4",
		):
			set_warehouses_from_item_group(doc)

		self.assertEqual(doc.items[0].from_warehouse, "Group Stores - C4")

	def test_sets_purchase_receipt_item_warehouse(self):
		doc = frappe._dict(
			{
				"doctype": "Purchase Receipt",
				"company": "C4",
				"items": [frappe._dict({"item_code": "RM-1", "warehouse": None})],
			}
		)

		with patch(
			"c4factory.c4_manufacturing.item_group_warehouse.get_default_source_warehouse",
			return_value="Group Stores - C4",
		):
			set_warehouses_from_item_group(doc)

		self.assertEqual(doc.items[0].warehouse, "Group Stores - C4")

	def test_does_not_set_source_for_purchase_request_or_material_receipt(self):
		documents = [
			frappe._dict(
				{
					"doctype": "Material Request",
					"material_request_type": "Purchase",
					"items": [frappe._dict({"item_code": "RM-1"})],
				}
			),
			frappe._dict(
				{
					"doctype": "Stock Entry",
					"purpose": "Material Receipt",
					"items": [frappe._dict({"item_code": "RM-1"})],
				}
			),
		]

		with patch(
			"c4factory.c4_manufacturing.item_group_warehouse.get_default_source_warehouse"
		) as get_warehouse:
			for doc in documents:
				set_warehouses_from_item_group(doc)

		get_warehouse.assert_not_called()
