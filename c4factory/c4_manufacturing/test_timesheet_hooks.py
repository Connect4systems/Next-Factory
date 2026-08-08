from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from c4factory.c4_manufacturing.timesheet_hooks import set_work_order_project
from c4factory.c4_manufacturing.work_order_hooks import _get_work_order_timesheet_cost


class TestWorkOrderTimesheetCosting(FrappeTestCase):
    def test_returns_submitted_linked_timesheet_cost(self):
        meta = MagicMock()
        meta.has_field.side_effect = lambda fieldname: fieldname in {
            "custom_work_order",
            "total_costing_amount",
            "base_total_costing_amount",
        }

        with (
            patch("c4factory.c4_manufacturing.work_order_hooks.frappe.get_meta", return_value=meta),
            patch(
                "c4factory.c4_manufacturing.work_order_hooks.frappe.db.sql",
                return_value=[[375.5]],
            ) as sql,
        ):
            self.assertEqual(_get_work_order_timesheet_cost("WO-0001"), 375.5)

        self.assertIn("docstatus = 1", sql.call_args.args[0])
        self.assertIn("base_total_costing_amount", sql.call_args.args[0])
        self.assertEqual(sql.call_args.args[1], ("WO-0001",))

    def test_sets_timesheet_company_and_project_from_work_order(self):
        first_row = frappe._dict({"project": None})
        second_row = frappe._dict({"project": "PROJECT-OTHER"})
        timesheet = frappe._dict(
            {
                "custom_work_order": "WO-0001",
                "company": None,
                "time_logs": [first_row, second_row],
            }
        )
        timesheet.meta = MagicMock()
        timesheet.meta.has_field.return_value = True

        with patch(
            "c4factory.c4_manufacturing.timesheet_hooks.frappe.db.get_value",
            return_value=frappe._dict({"project": "PROJECT-0001", "company": "C4"}),
        ):
            set_work_order_project(timesheet)

        self.assertEqual(timesheet.company, "C4")
        self.assertEqual(timesheet.parent_project, "PROJECT-0001")
        self.assertEqual(first_row.project, "PROJECT-0001")
        self.assertEqual(second_row.project, "PROJECT-0001")
