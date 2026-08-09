from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from c4factory.c4_manufacturing.stock_entry_hooks import (
    set_work_order_project,
    validate_finish_material_allocation,
)


class TestStockEntryProject(FrappeTestCase):
    def test_sets_project_from_work_order(self):
        stock_entry = frappe._dict(
            {"work_order": "WO-0001", "project": "PROJECT-OTHER"}
        )

        with patch(
            "c4factory.c4_manufacturing.stock_entry_hooks.frappe.db.get_value",
            return_value="PROJECT-0001",
        ) as get_value:
            set_work_order_project(stock_entry)

        self.assertEqual(stock_entry.project, "PROJECT-0001")
        get_value.assert_called_once_with("Work Order", "WO-0001", "project")

    def test_ignores_stock_entry_without_work_order(self):
        stock_entry = frappe._dict({"work_order": None, "project": "PROJECT-0001"})

        with patch(
            "c4factory.c4_manufacturing.stock_entry_hooks.frappe.db.get_value"
        ) as get_value:
            set_work_order_project(stock_entry)

        self.assertEqual(stock_entry.project, "PROJECT-0001")
        get_value.assert_not_called()


class TestFinishAllocationValidation(FrappeTestCase):
    def test_restores_generated_source_quantity_on_save(self):
        meta = frappe._dict({"has_field": lambda fieldname: True})
        raw_row = frappe._dict(
            {
                "item_code": "RM-1",
                "qty": 0.35,
                "transfer_qty": 0.35,
                "is_finished_item": 0,
                "is_scrap_item": 0,
                "custom_source_transfer_detail": "SED-SOURCE-1",
                "meta": meta,
            }
        )
        finished_row = frappe._dict(
            {
                "item_code": "FG-1",
                "qty": 20,
                "transfer_qty": 20,
                "is_finished_item": 1,
                "is_scrap_item": 0,
                "meta": meta,
            }
        )
        stock_entry = frappe._dict(
            {
                "name": "new-stock-entry-test",
                "work_order": "WO-1",
                "purpose": "Manufacture",
                "stock_entry_type": "Manufacture",
                "custom_uses_finish_allocation": 1,
                "custom_is_final_finish": 1,
                "items": [raw_row, finished_row],
                "additional_costs": [],
            }
        )
        work_order = frappe._dict(
            {"name": "WO-1", "qty": 20, "produced_qty": 0}
        )
        expected_rows = [
            {
                "source_transfer_detail": "SED-SOURCE-1",
                "item_code": "RM-1",
                "qty": 0.351,
                "valuation_rate": 10,
            }
        ]

        with (
            patch(
                "c4factory.c4_manufacturing.stock_entry_hooks.frappe.get_doc",
                return_value=work_order,
            ),
            patch(
                "c4factory.api.work_order_stock._get_finish_material_allocations",
                return_value=expected_rows,
            ),
            patch(
                "c4factory.api.work_order_stock._get_scrap_allocations",
                return_value=[],
            ),
            patch(
                "c4factory.api.work_order_stock._get_allocated_mold_cost_rows",
                return_value={},
            ),
        ):
            validate_finish_material_allocation(stock_entry)

        self.assertEqual(raw_row.qty, 0.351)
        self.assertEqual(raw_row.transfer_qty, 0.351)
        self.assertEqual(raw_row.amount, 3.51)
