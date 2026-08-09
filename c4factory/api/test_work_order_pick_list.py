from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from c4factory.api.work_order_flow import (
    _get_pick_list_balances_map,
    _get_source_warehouse_allocations,
    _recompute_wo_produced_qty,
)
from c4factory.api.work_order_pick_list import (
    _get_pick_list_source_warehouse,
    get_allocated_pick_list_qty,
)


class TestPickListProductionAllocation(FrappeTestCase):
    def test_continuous_item_group_manufacture_warehouse_overrides_work_order_source(self):
        work_order = frappe._dict(
            {"company": "Test Company", "source_warehouse": "WO Warehouse"}
        )
        work_order_item = frappe._dict(
            {
                "item_code": "RM-CONT",
                "item_group": "Continuous Materials",
                "source_warehouse": "Old Warehouse",
            }
        )

        with patch(
            "c4factory.api.work_order_pick_list.get_source_warehouse_details",
            return_value={
                "warehouse": "Manufacture Warehouse",
                "override_existing": True,
            },
        ):
            warehouse = _get_pick_list_source_warehouse(
                work_order, work_order_item
            )

        self.assertEqual(warehouse, "Manufacture Warehouse")

    def test_recomputes_work_order_produced_qty_from_submitted_finish_rows(self):
        status_calls = []
        work_order = frappe._dict(
            {
                "name": "WO-1",
                "produced_qty": 0,
                "set_status": lambda: status_calls.append(True),
            }
        )

        with (
            patch(
                "c4factory.api.work_order_flow.frappe.db.sql",
                return_value=[[5]],
            ),
            patch(
                "c4factory.api.work_order_flow.frappe.db.set_value"
            ) as set_value,
            patch(
                "c4factory.api.work_order_flow.frappe.get_doc",
                return_value=work_order,
            ),
        ):
            produced_qty = _recompute_wo_produced_qty("WO-1")

        self.assertEqual(produced_qty, 5)
        self.assertEqual(work_order.produced_qty, 5)
        self.assertEqual(status_calls, [True])
        set_value.assert_called_once_with(
            "Work Order", "WO-1", "produced_qty", 5, update_modified=False
        )

    def test_group_warehouse_is_split_across_leaf_warehouses(self):
        warehouse = frappe._dict(
            {"is_group": 1, "lft": 10, "rgt": 20, "company": "Test Company"}
        )
        leaf_rows = [
            frappe._dict({"name": "Leaf A", "available_qty": 6}),
            frappe._dict({"name": "Leaf B", "available_qty": 5}),
        ]

        with (
            patch(
                "c4factory.api.work_order_flow.frappe.db.get_value",
                return_value=warehouse,
            ),
            patch(
                "c4factory.api.work_order_flow.frappe.db.sql",
                return_value=leaf_rows,
            ),
        ):
            allocations = _get_source_warehouse_allocations(
                "RM-1", "Warehouse Group", "Test Company", 9
            )

        self.assertEqual(allocations, [("Leaf A", 6), ("Leaf B", 3)])

    def test_manually_finished_pick_list_keeps_transferable_balance(self):
        pick_list = frappe._dict(
            {
                "name": "PL-CONT",
                "custom_manually_completed": 1,
                "locations": [
                    frappe._dict(
                        {
                            "name": "PLI-1",
                            "item_code": "RM-1",
                            "item_name": "Raw Material",
                            "qty": 10,
                            "custom_pl_qty": 10,
                        }
                    )
                ],
            }
        )
        submitted_rows = [
            frappe._dict({"custom_pick_list_item": "PLI-1", "total_qty": 4})
        ]

        with patch(
            "c4factory.api.work_order_flow.frappe.db.sql",
            side_effect=[submitted_rows, []],
        ):
            balances = _get_pick_list_balances_map(pick_list)

        self.assertEqual(balances["PLI-1"]["transferred"], 4)
        self.assertEqual(balances["PLI-1"]["balance"], 6)

    def test_start_pair_reserves_finished_quantity_once(self):
        rows = [
            frappe._dict(
                {
                    "name": "PL-CONT",
                    "for_qty": 10,
                    "custom_continuous_start_request_id": "START-1",
                }
            ),
            frappe._dict(
                {
                    "name": "PL-NORMAL",
                    "for_qty": 10,
                    "custom_continuous_start_request_id": "START-1",
                }
            ),
            frappe._dict(
                {
                    "name": "PL-STANDALONE",
                    "for_qty": 4,
                    "custom_continuous_start_request_id": None,
                }
            ),
        ]
        meta = frappe._dict(
            {"has_field": lambda fieldname: fieldname == "custom_continuous_start_request_id"}
        )

        with (
            patch("c4factory.api.work_order_pick_list.frappe.get_meta", return_value=meta),
            patch("c4factory.api.work_order_pick_list.frappe.get_all", return_value=rows),
        ):
            allocated = get_allocated_pick_list_qty("WO-1", docstatus=1)

        self.assertEqual(allocated, 14)

    def test_excluding_one_pick_list_excludes_its_whole_start_pair(self):
        rows = [
            frappe._dict(
                {
                    "name": "PL-CONT",
                    "for_qty": 10,
                    "custom_continuous_start_request_id": "START-1",
                }
            ),
            frappe._dict(
                {
                    "name": "PL-NORMAL",
                    "for_qty": 10,
                    "custom_continuous_start_request_id": "START-1",
                }
            ),
        ]
        meta = frappe._dict(
            {"has_field": lambda fieldname: fieldname == "custom_continuous_start_request_id"}
        )

        with (
            patch("c4factory.api.work_order_pick_list.frappe.get_meta", return_value=meta),
            patch("c4factory.api.work_order_pick_list.frappe.get_all", return_value=rows),
            patch(
                "c4factory.api.work_order_pick_list.frappe.db.get_value",
                return_value="START-1",
            ),
        ):
            allocated = get_allocated_pick_list_qty(
                "WO-1", docstatus=1, exclude_pick_list="PL-NORMAL"
            )

        self.assertEqual(allocated, 0)
