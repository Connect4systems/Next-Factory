import unittest
from unittest.mock import MagicMock, patch

import frappe

from c4factory.api.work_order_costing import (
	NATIVE_REPOST_METHOD,
	_apply_costs,
	_load_finishes,
	execute_pending_settlement_reposts,
	queue_pending_settlement_reposts,
)


class TestContinuousRepostDispatch(unittest.TestCase):
	def test_dispatches_without_waiting_for_hourly_scheduler(self):
		with (
			patch("frappe.utils.background_jobs.is_job_enqueued", return_value=False),
			patch(
				"erpnext.stock.doctype.repost_item_valuation.repost_item_valuation.in_configured_timeslot",
				return_value=True,
			),
			patch("c4factory.api.work_order_costing.frappe.db.exists", return_value=True),
			patch("c4factory.api.work_order_costing.frappe.enqueue") as enqueue,
		):
			queue_pending_settlement_reposts()
		self.assertEqual(enqueue.call_args.kwargs["job_id"], f"scheduled_job::{NATIVE_REPOST_METHOD}")
		self.assertTrue(enqueue.call_args.kwargs["deduplicate"])
		self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])

	def test_does_not_overlap_existing_native_runner(self):
		with (
			patch("frappe.utils.background_jobs.is_job_enqueued", return_value=True),
			patch("c4factory.api.work_order_costing.frappe.enqueue") as enqueue,
		):
			queue_pending_settlement_reposts()
		enqueue.assert_not_called()

	def test_respects_configured_reposting_window(self):
		with (
			patch("frappe.utils.background_jobs.is_job_enqueued", return_value=False),
			patch(
				"erpnext.stock.doctype.repost_item_valuation.repost_item_valuation.in_configured_timeslot",
				return_value=False,
			),
			patch("c4factory.api.work_order_costing.frappe.enqueue") as enqueue,
		):
			queue_pending_settlement_reposts()
		enqueue.assert_not_called()

	def test_only_runs_queued_references_from_settlement(self):
		audit = frappe._dict({"reposts": '["RIV-QUEUED", "RIV-DONE", "RIV-RUNNING"]'})
		queued, done, running = (
			MagicMock(status=status) for status in ("Queued", "Completed", "In Progress")
		)
		with (
			patch("c4factory.api.work_order_costing.frappe.get_all", return_value=["AUDIT-1"]),
			patch(
				"c4factory.api.work_order_costing.frappe.get_doc", side_effect=[audit, queued, done, running]
			),
			patch("c4factory.api.work_order_costing.sync_pending_settlements"),
		):
			execute_pending_settlement_reposts()
		queued.repost_now.assert_called_once()
		done.repost_now.assert_not_called()
		running.repost_now.assert_not_called()

	def test_native_amount_recalculation_keeps_automatically_settled_receipt_rate(self):
		if frappe.local.site != "napata.connect4systems.com":
			self.skipTest("Requires the authorized staging ledger")
		wo = frappe.get_doc("Work Order", "MFG-WO-2026-00081")
		entries = _load_finishes(wo)
		_apply_costs(wo, entries)
		for entry in entries:
			finished = next(row for row in entry.items if row.is_finished_item)
			expected = finished.amount
			self.assertTrue(finished.set_basic_rate_manually)
			# Simulate the native ledger updating outgoing material rates before it
			# calls calculate_rate_and_amount on an already settled receipt.
			for row in entry.items:
				if row.s_warehouse:
					row.basic_rate += 7
			entry.calculate_rate_and_amount(reset_outgoing_rate=False, raise_error_if_no_rate=False)
			self.assertAlmostEqual(finished.amount, expected, places=2)
