from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from c4factory.c4_manufacturing.work_order_hooks import (
    SALES_ORDER_ITEM_SPEC_FIELDS,
    copy_sales_order_item_specs,
)


class TestWorkOrderSalesOrderItemSpecs(FrappeTestCase):
    def test_copies_specs_from_exact_sales_order_item(self):
        work_order = MagicMock()
        work_order.get.side_effect = {
            "docstatus": 0,
            "sales_order_item": "SO-ITEM-0001",
        }.get
        values = frappe._dict(
            {
                "custom_priority_": "1",
                "custom_finish": "Matte",
                "custom_dimitions": "20 x 30",
                "custom_color": "Blue",
            }
        )

        with patch(
            "c4factory.c4_manufacturing.work_order_hooks.frappe.db.get_value",
            return_value=values,
        ) as get_value:
            copy_sales_order_item_specs(work_order)

        get_value.assert_called_once_with(
            "Sales Order Item",
            "SO-ITEM-0001",
            SALES_ORDER_ITEM_SPEC_FIELDS,
            as_dict=True,
        )
        for fieldname in SALES_ORDER_ITEM_SPEC_FIELDS:
            work_order.set.assert_any_call(fieldname, values.get(fieldname))

    def test_does_nothing_without_sales_order_item(self):
        work_order = MagicMock()
        work_order.get.side_effect = {"docstatus": 0}.get

        with patch(
            "c4factory.c4_manufacturing.work_order_hooks.frappe.db.get_value"
        ) as get_value:
            copy_sales_order_item_specs(work_order)

        get_value.assert_not_called()
        work_order.set.assert_not_called()

    def test_does_not_change_a_submitted_work_order(self):
        work_order = MagicMock()
        work_order.get.side_effect = {
            "docstatus": 1,
            "sales_order_item": "SO-ITEM-0001",
        }.get

        with patch(
            "c4factory.c4_manufacturing.work_order_hooks.frappe.db.get_value"
        ) as get_value:
            copy_sales_order_item_specs(work_order)

        get_value.assert_not_called()
        work_order.set.assert_not_called()
