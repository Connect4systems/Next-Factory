from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from c4factory.c4factory.report.finished_product_cost_report.finished_product_cost_report import (
	execute,
	get_conditions,
	get_stock_entry_date_conditions,
)


class TestFinishedProductCostReport(FrappeTestCase):
	def test_filters_are_parameterized_and_applied_to_the_correct_documents(self):
		filters = frappe._dict(
			{
				"from_date": "2026-08-01",
				"to_date": "2026-08-31",
				"work_order": "WO-0001",
				"sales_order": "SO-0001",
				"customer": "Customer 1",
				"project": "Project 1",
			}
		)

		conditions, values = get_conditions(filters)
		date_conditions = get_stock_entry_date_conditions(filters)

		self.assertIn("wo.name = %(work_order)s", conditions)
		self.assertIn("so.customer = %(customer)s", conditions)
		self.assertIn("se.posting_date >= %(from_date)s", date_conditions)
		self.assertIn("se.posting_date <= %(to_date)s", date_conditions)
		self.assertEqual(values["work_order"], "WO-0001")
		self.assertEqual(values["from_date"], "2026-08-01")
		self.assertEqual(values["to_date"], "2026-08-31")

	def test_query_aggregates_manufacture_entries_once_per_work_order(self):
		with patch(
			"c4factory.c4factory.report.finished_product_cost_report.finished_product_cost_report.frappe.db.sql",
			return_value=[],
		) as sql:
			execute({"from_date": "2026-08-01", "to_date": "2026-08-31"})

		query = sql.call_args.args[0]
		self.assertIn("se.docstatus = 1", query)
		self.assertIn("se.stock_entry_type = 'Manufacture'", query)
		self.assertIn("GROUP BY se.work_order", query)
		self.assertIn("manufacture.manufactured_value / manufacture.finished_item_qty", query)
		self.assertIn("SUM(ABS(COALESCE(sed.amount, 0))) AS value", query)
		self.assertIn("company.default_currency AS currency", query)
		self.assertIn("linked_soi.base_rate", query)
		self.assertIn("finished.item_code = manufacture_wo.production_item", query)

	def test_rejects_reversed_date_range(self):
		with self.assertRaises(frappe.ValidationError):
			execute({"from_date": "2026-09-01", "to_date": "2026-08-01"})
