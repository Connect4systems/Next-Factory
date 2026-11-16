import unittest
from unittest.mock import patch

import frappe

from c4factory.api.work_order_costing import set_new_order_costing_mode


class TestContinuousCostingDefaults(unittest.TestCase):
	def test_new_or_copied_order_is_enabled_and_financial_state_is_reset(self):
		doc = frappe._dict(
			{
				"company": "Factory",
				"custom_continuous_costing": 0,
				"custom_continuous_cost_status": "Cost Closed",
				"custom_continuous_settlement": "OLD-SETTLEMENT",
				"custom_timesheet_cost_closed": 1,
			}
		)
		with (
			patch("frappe.db.get_single_value", return_value="Clearing - F"),
			patch("frappe.get_cached_value", return_value="Factory"),
		):
			set_new_order_costing_mode(doc)
		self.assertEqual(doc.custom_continuous_costing, 1)
		self.assertEqual(doc.custom_continuous_cost_status, "Provisional")
		self.assertIsNone(doc.custom_continuous_settlement)
		self.assertEqual(doc.custom_timesheet_cost_closed, 0)

	def test_does_not_enable_a_different_company_with_the_wrong_clearing_account(self):
		doc = frappe._dict({"company": "Other Factory"})
		with (
			patch("frappe.db.get_single_value", return_value="Clearing - F"),
			patch("frappe.get_cached_value", return_value="Factory"),
		):
			set_new_order_costing_mode(doc)
		self.assertEqual(doc.custom_continuous_costing, 0)

	def test_unconfigured_site_preserves_legacy_mode(self):
		doc = frappe._dict({"company": "Factory"})
		with patch("frappe.db.get_single_value", return_value=None):
			set_new_order_costing_mode(doc)
		self.assertEqual(doc.custom_continuous_costing, 0)
