from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from c4factory.c4_manufacturing.stock_entry_hooks import (
    _get_allocated_operation_cost,
    _set_manufacture_finished_item_valuation,
    _set_operation_cost_additional_cost,
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


class TestOperationCostAllocation(FrappeTestCase):
    def test_partial_finish_allocates_work_order_operation_cost_by_quantity(self):
        doc = frappe._dict(
            {
                "name": "new-stock-entry-test",
                "custom_is_final_finish": 0,
                "meta": frappe._dict({"has_field": lambda fieldname: True}),
            }
        )
        work_order = frappe._dict(
            {"name": "WO-1", "qty": 10, "produced_qty": 0}
        )

        with (
            patch(
                "c4factory.c4_manufacturing.work_order_hooks._get_work_order_operating_cost",
                return_value=100,
            ),
            patch(
                "c4factory.c4_manufacturing.stock_entry_hooks.frappe.db.sql",
                side_effect=[[[0]], [frappe._dict({"fg_value": 0, "raw_value": 0})]],
            ),
        ):
            allocated = _get_allocated_operation_cost(doc, work_order, 5)

        self.assertEqual(allocated, 50)

    def test_adds_operation_cost_accounting_row(self):
        doc = MagicMock()
        doc.meta.has_field.return_value = True
        doc.get.return_value = []
        work_order = frappe._dict({"company": "Napata Company"})

        with (
            patch(
                "c4factory.c4_manufacturing.stock_entry_hooks._get_allocated_operation_cost",
                return_value=75,
            ),
            patch(
                "c4factory.c4_manufacturing.stock_entry_hooks._get_operation_cost_account",
                return_value="Operation Cost - NC",
            ),
        ):
            _set_operation_cost_additional_cost(doc, work_order, 5)

        doc.append.assert_called_once_with(
            "additional_costs",
            {
                "expense_account": "Operation Cost - NC",
                "description": "Operation Cost",
                "amount": 75,
            },
        )
        doc.calculate_rate_and_amount.assert_called_once_with(
            reset_outgoing_rate=False,
            raise_error_if_no_rate=False,
        )

    def test_finished_value_includes_operation_additional_cost_once(self):
        raw_row = frappe._dict(
            {
                "item_code": "RM-1",
                "qty": 10,
                "basic_amount": 100,
                "is_finished_item": 0,
                "is_scrap_item": 0,
            }
        )
        finished_row = frappe._dict(
            {
                "item_code": "FG-1",
                "qty": 10,
                "additional_cost": 50,
                "is_finished_item": 1,
                "is_scrap_item": 0,
                "valuation_rate": 0,
            }
        )
        doc = frappe._dict(
            {
                "purpose": "Manufacture",
                "items": [raw_row, finished_row],
            }
        )

        _set_manufacture_finished_item_valuation(doc, frappe._dict({"name": "WO-1"}))

        self.assertEqual(finished_row.basic_amount, 100)
        self.assertEqual(finished_row.amount, 150)
        self.assertEqual(finished_row.valuation_rate, 15)
