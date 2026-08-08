from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from c4factory.c4_manufacturing.stock_entry_hooks import set_work_order_project


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
