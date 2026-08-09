from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from c4factory.api.work_order_flow import _get_pick_list_balances_map
from c4factory.api.work_order_pick_list import get_allocated_pick_list_qty


class TestPickListProductionAllocation(FrappeTestCase):
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
