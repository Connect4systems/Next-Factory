import unittest

import frappe

from c4factory.patches.v1_0.preserve_historical_costing_modes import execute


class TestHistoricalCostingModes(unittest.TestCase):
	def setUp(self):
		if frappe.local.site != "napata.connect4systems.com":
			self.skipTest("Requires the authorized staging copy")
		frappe.db.savepoint("historical_costing_test")
		self.legacy = frappe.db.sql("""
			SELECT wo.name FROM `tabWork Order` wo
			WHERE wo.custom_continuous_costing=0
			  AND NOT EXISTS (SELECT 1 FROM `tabContinuous Cost Settlement` s WHERE s.work_order=wo.name)
			  AND NOT EXISTS (SELECT 1 FROM `tabStock Entry` se WHERE se.work_order=wo.name AND se.custom_continuous_costing=1)
			LIMIT 1
		""")[0][0]

	def tearDown(self):
		frappe.db.rollback(save_point="historical_costing_test")

	def test_clears_only_legacy_flag_without_changing_production(self):
		before = frappe.db.get_value("Work Order", self.legacy, ["qty", "produced_qty", "status"])
		frappe.db.set_value(
			"Work Order",
			self.legacy,
			{
				"custom_continuous_costing": 1,
				"custom_continuous_cost_status": None,
				"custom_continuous_settlement": None,
			},
		)
		execute()
		self.assertEqual(frappe.db.get_value("Work Order", self.legacy, "custom_continuous_costing"), 0)
		self.assertEqual(
			frappe.db.get_value("Work Order", self.legacy, ["qty", "produced_qty", "status"]), before
		)

	def test_retains_automatic_new_order_with_provisional_status(self):
		frappe.db.set_value(
			"Work Order",
			self.legacy,
			{
				"custom_continuous_costing": 1,
				"custom_continuous_cost_status": "Provisional",
			},
		)
		execute()
		self.assertEqual(frappe.db.get_value("Work Order", self.legacy, "custom_continuous_costing"), 1)

	def test_retains_existing_settled_order_even_without_status_markers(self):
		name = "MFG-WO-2026-00081"
		frappe.db.set_value(
			"Work Order",
			name,
			{
				"custom_continuous_costing": 1,
				"custom_continuous_cost_status": None,
				"custom_continuous_settlement": None,
			},
		)
		execute()
		self.assertEqual(frappe.db.get_value("Work Order", name, "custom_continuous_costing"), 1)
