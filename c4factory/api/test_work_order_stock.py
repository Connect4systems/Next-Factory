from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from c4factory.api.work_order_stock import _get_finish_material_allocations


class TestFinishMaterialAllocation(FrappeTestCase):
	def _work_order(self):
		return frappe._dict(
			{
				"name": "WO-1",
				"qty": 10,
				"produced_qty": 0,
				"required_items": [
					frappe._dict(
						{
							"name": "WOI-1",
							"item_code": "RM-1",
							"required_qty": 100,
							"custom_additional_material_qty": 0,
						}
					)
				],
			}
		)

	def test_partial_finish_consumes_only_proportional_material(self):
		sources = [
			frappe._dict(
				{
					"name": "SED-1",
					"item_code": "RM-1",
					"stock_uom": "Kg",
					"total_qty": 100,
					"total_amount": 1000,
					"remaining_qty": 100,
					"custom_work_order_item": "WOI-1",
					"custom_pick_list_item": "PLI-1",
					"is_additional": False,
					"allocation_coverage_qty": 0,
				}
			)
		]
		with patch(
			"c4factory.api.work_order_stock._get_available_transfer_sources",
			return_value=sources,
		):
			rows = _get_finish_material_allocations(self._work_order(), 5, False)

		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["qty"], 50)
		self.assertEqual(rows[0]["amount"], 500)

	def test_transfer_covering_five_is_fully_consumed_by_finish_five(self):
		sources = [
			frappe._dict(
				{
					"name": "SED-1",
					"item_code": "RM-1",
					"stock_uom": "Kg",
					"total_qty": 50,
					"total_amount": 500,
					"remaining_qty": 50,
					"custom_work_order_item": "WOI-1",
					"custom_pick_list_item": "PLI-1",
					"is_additional": False,
					"allocation_coverage_qty": 0,
				}
			)
		]
		with patch(
			"c4factory.api.work_order_stock._get_available_transfer_sources",
			return_value=sources,
		):
			rows = _get_finish_material_allocations(self._work_order(), 5, False)

		self.assertEqual(rows[0]["qty"], 50)

	def test_additional_material_is_spread_over_its_remaining_coverage(self):
		sources = [
			frappe._dict(
				{
					"name": "SED-1",
					"item_code": "RM-1",
					"stock_uom": "Kg",
					"total_qty": 100,
					"total_amount": 1000,
					"remaining_qty": 100,
					"custom_work_order_item": "WOI-1",
					"custom_pick_list_item": "PLI-1",
					"is_additional": False,
					"allocation_coverage_qty": 0,
				}
			),
			frappe._dict(
				{
					"name": "SED-ADD",
					"item_code": "RM-2",
					"stock_uom": "Kg",
					"total_qty": 20,
					"total_amount": 200,
					"remaining_qty": 20,
					"custom_work_order_item": None,
					"custom_pick_list_item": None,
					"is_additional": True,
					"allocation_coverage_qty": 10,
				}
			),
		]
		with patch(
			"c4factory.api.work_order_stock._get_available_transfer_sources",
			return_value=sources,
		):
			rows = _get_finish_material_allocations(self._work_order(), 5, False)

		qty_by_source = {row["source_transfer_detail"]: row["qty"] for row in rows}
		self.assertEqual(qty_by_source["SED-1"], 50)
		self.assertEqual(qty_by_source["SED-ADD"], 10)

	def test_final_finish_consumes_every_remaining_source_balance(self):
		wo = self._work_order()
		wo.produced_qty = 5
		sources = [
			frappe._dict(
				{
					"name": "SED-1",
					"item_code": "RM-1",
					"stock_uom": "Kg",
					"total_qty": 110,
					"total_amount": 1100,
					"remaining_qty": 60,
					"custom_work_order_item": "WOI-1",
					"custom_pick_list_item": "PLI-1",
					"is_additional": False,
					"allocation_coverage_qty": 0,
				}
			)
		]
		with patch(
			"c4factory.api.work_order_stock._get_available_transfer_sources",
			return_value=sources,
		):
			rows = _get_finish_material_allocations(wo, 5, True)

		self.assertEqual(rows[0]["qty"], 60)
