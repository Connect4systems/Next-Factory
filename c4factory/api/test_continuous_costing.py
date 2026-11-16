"""Integration regression on the supplied staging Work Order, always rolled back.

Run only on a staging copy containing MFG-WO-2026-00081. Other sites skip these
tests; do not enable or run the acceptance tests against production.
"""

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from c4factory.api.work_order_costing import (
	finish_timesheet,
	run_settlement,
	sync_pending_settlements,
	timesheet_context,
)
from c4factory.api.work_order_stock import make_stock_entry


class TestContinuousCostingAcceptance(FrappeTestCase):
	def setUp(self):
		if frappe.local.site != "napata.connect4systems.com":
			self.skipTest("Acceptance tests require the explicitly authorized staging site")
		frappe.set_user("Administrator")
		self.wo = frappe.get_doc("Work Order", "MFG-WO-2026-00081")
		if self.wo.produced_qty != 14 or self.wo.qty != 23:
			self.skipTest("Staging fixture must start with 14 of 23 units produced")
		account = frappe.db.get_single_value("Manufacturing Settings", "custom_operation_cost_account")
		frappe.db.set_single_value("Manufacturing Settings", "custom_continuous_cost_account", account)
		self.wo.db_set("custom_continuous_costing", 1)
		self.before = self._quantities()

	def tearDown(self):
		frappe.db.rollback()

	def _quantities(self):
		return frappe.db.sql(
			"""
			SELECT sed.name, sed.qty, sed.transfer_qty FROM `tabStock Entry Detail` sed
			INNER JOIN `tabStock Entry` se ON se.name=sed.parent
			WHERE se.work_order=%s AND se.docstatus=1 ORDER BY sed.name
		""",
			self.wo.name,
		)

	def _settle(self, name):
		run_settlement(name)
		audit = frappe.get_doc("Continuous Cost Settlement", name)
		for ref in json.loads(audit.reposts):
			repost = frappe.get_doc("Repost Item Valuation", ref)
			repost.repost_now()
			self.assertEqual(frappe.db.get_value("Repost Item Valuation", ref, "status"), "Completed")
		sync_pending_settlements()
		self.wo.reload()
		self.assertEqual(frappe.db.get_value("Continuous Cost Settlement", name, "status"), "Completed")
		return audit

	def _finish(self, qty):
		doc = frappe.get_doc(make_stock_entry(self.wo.name, "Manufacture", qty))
		for row in doc.items:
			self.assertGreater(row.transfer_qty, 0)
		doc.insert()
		with patch("frappe.enqueue"):
			doc.submit()
		self.wo.reload()
		self._settle(self.wo.custom_continuous_settlement)
		return doc

	def test_incident_finish_reposts_all_receipts_without_changing_old_quantities(self):
		self._finish(9)
		self.assertEqual(self.wo.status, "Completed")
		self.assertEqual(self.wo.produced_qty, 23)
		self.assertTrue(timesheet_context(self.wo)["finish_timesheet"])
		after = dict((row[0], row[1:]) for row in self._quantities())
		for name, qty, stock_qty in self.before:
			self.assertEqual(after[name], (qty, stock_qty))
		rows = frappe.db.sql(
			"""
			SELECT sed.transfer_qty, sed.amount FROM `tabStock Entry Detail` sed
			INNER JOIN `tabStock Entry` se ON se.name=sed.parent
			WHERE se.work_order=%s AND se.docstatus=1 AND sed.is_finished_item=1
		""",
			self.wo.name,
		)
		average = sum(row[1] for row in rows) / 23
		for qty, amount in rows:
			self.assertAlmostEqual(amount / qty, average, places=2)
		with patch("frappe.enqueue"):
			result = finish_timesheet(self.wo.name)
		self._settle(result["settlement"])
		self.assertEqual(self.wo.status, "Completed")
		self.assertEqual(self.wo.custom_continuous_cost_status, "Cost Closed")
		self.assertFalse(timesheet_context(self.wo)["finish_timesheet"])
		with self.assertRaises(frappe.ValidationError):
			finish_timesheet(self.wo.name)

	def test_second_batch_can_finish_without_new_material_transfer(self):
		self._finish(5)
		self.assertEqual(self.wo.status, "In Process")
		doc = self._finish(4)
		self.assertEqual(self.wo.status, "Completed")
		self.assertFalse([row for row in doc.items if row.s_warehouse])
		self.assertEqual(self.wo.produced_qty, 23)

	def test_no_timesheets_does_not_block_delivery_or_show_finish_timesheet(self):
		# Remove the labor association within this rolled-back test transaction only.
		frappe.db.sql(
			"UPDATE `tabTimesheet` SET custom_work_order=NULL WHERE custom_work_order=%s", self.wo.name
		)
		self._finish(9)
		self.assertEqual(self.wo.status, "Completed")
		self.assertEqual(self.wo.custom_continuous_cost_status, "Material Cost Closed")
		self.assertFalse(timesheet_context(self.wo)["finish_timesheet"])

	def test_late_timesheet_updates_all_batches_and_locks_after_finish(self):
		self._finish(9)
		before = self.wo.c4_total_cost
		source = frappe.db.get_value(
			"Timesheet",
			{
				"custom_work_order": self.wo.name,
				"docstatus": 1,
			},
			"name",
		)
		late = frappe.copy_doc(frappe.get_doc("Timesheet", source))
		late.set("time_logs", [late.time_logs[0]])
		row = late.time_logs[0]
		row.from_time = "2030-01-01 08:00:00"
		row.to_time = "2030-01-01 09:00:00"
		row.hours = 1
		late.insert()
		late.submit()
		self.assertGreater(late.total_costing_amount, 0)
		with patch("frappe.enqueue"):
			result = finish_timesheet(self.wo.name)
		audit = self._settle(result["settlement"])
		self.assertAlmostEqual(audit.total_cost - before, late.total_costing_amount, places=2)
		self.assertEqual(self.wo.status, "Completed")
		with self.assertRaises(frappe.ValidationError):
			late.cancel()

	def test_additional_material_outside_original_requirements_enters_pool(self):
		from c4factory.api.work_order_flow import make_additional_material_stock_entry

		pick_list = frappe.db.get_value(
			"Pick List",
			{
				"work_order": self.wo.name,
				"docstatus": 1,
			},
			"name",
		)
		self.assertTrue(pick_list)
		required = [row.item_code for row in self.wo.required_items]
		warehouse = self.wo.required_items[0].source_warehouse
		candidates = frappe.db.sql(
			"""
			SELECT bin.item_code FROM `tabBin` bin INNER JOIN `tabItem` item ON item.name=bin.item_code
			WHERE bin.warehouse=%s AND bin.actual_qty>1 AND bin.valuation_rate>0
			  AND item.has_batch_no=0 AND item.has_serial_no=0 AND item.disabled=0
			  AND bin.item_code NOT IN %s LIMIT 1
		""",
			(warehouse, tuple(required)),
		)
		self.assertTrue(candidates, "Staging needs one available additional material")
		item_code = candidates[0][0]
		extra = frappe.get_doc(make_additional_material_stock_entry(pick_list))
		extra.append("items", {"item_code": item_code, "qty": 1, "s_warehouse": warehouse})
		extra.insert()
		extra.submit()
		doc = self._finish(9)
		self.assertIn(item_code, [row.item_code for row in doc.items if row.s_warehouse])
		self.assertEqual(self.wo.status, "Completed")

	def test_native_repost_validation_failure_leaves_receipt_costs_unchanged(self):
		doc = frappe.get_doc(make_stock_entry(self.wo.name, "Manufacture", 9))
		doc.insert()
		with patch("frappe.enqueue"):
			doc.submit()
		self.wo.reload()
		before = frappe.db.sql(
			"""
			SELECT sed.name, sed.amount, sed.valuation_rate FROM `tabStock Entry Detail` sed
			INNER JOIN `tabStock Entry` se ON se.name=sed.parent
			WHERE se.work_order=%s ORDER BY sed.name
		""",
			self.wo.name,
		)
		with (
			patch(
				"erpnext.stock.doctype.repost_item_valuation.repost_item_valuation.RepostItemValuation.validate",
				side_effect=frappe.ValidationError("Accounting period is closed"),
			),
			self.assertRaises(frappe.ValidationError),
		):
			run_settlement(self.wo.custom_continuous_settlement)
		after = frappe.db.sql(
			"""
			SELECT sed.name, sed.amount, sed.valuation_rate FROM `tabStock Entry Detail` sed
			INNER JOIN `tabStock Entry` se ON se.name=sed.parent
			WHERE se.work_order=%s ORDER BY sed.name
		""",
			self.wo.name,
		)
		self.assertEqual(before, after)
		self.wo.reload()
		self.assertEqual(self.wo.custom_continuous_cost_status, "Failed")
