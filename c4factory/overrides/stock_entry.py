import frappe
from erpnext.stock.doctype.stock_entry.stock_entry import StockEntry as ERPNextStockEntry
from frappe.utils import flt


class StockEntry(ERPNextStockEntry):
	def distribute_additional_costs(self):
		if not self.get("custom_continuous_costing") or self.purpose != "Manufacture":
			return super().distribute_additional_costs()
		# ERPNext 15.120 returns without allocating labor when material cost is
		# zero. A later continuous batch can legitimately have no new raw rows.
		finished = [row for row in self.items if row.is_finished_item and row.t_warehouse]
		quantity = sum(flt(row.transfer_qty) for row in finished)
		self.total_additional_costs = sum(flt(row.base_amount) for row in self.additional_costs)
		for row in self.items:
			row.additional_cost = (
				self.total_additional_costs * flt(row.transfer_qty) / quantity
				if row in finished and quantity
				else 0
			)

	def get_gl_entries(self, warehouse_account):
		finished = [row for row in self.items if row.is_finished_item and row.t_warehouse]
		if (
			not self.get("custom_continuous_costing")
			or self.purpose != "Manufacture"
			or sum(flt(row.basic_amount) for row in finished)
		):
			return super().get_gl_entries(warehouse_account)
		# Older v15 GL allocation uses total row quantity as the zero-cost
		# denominator, including free raw materials. Supply a transient finished
		# quantity basis; stock GL itself continues to use the real ledger values.
		original = [row.basic_amount for row in finished]
		try:
			for row in finished:
				row.basic_amount = flt(row.transfer_qty)
			return super().get_gl_entries(warehouse_account)
		finally:
			for row, amount in zip(finished, original, strict=True):
				row.basic_amount = amount

	def validate_raw_materials_exists(self):
		if self.get("custom_continuous_costing") and self.purpose == "Manufacture" and self.work_order:
			from c4factory.api.work_order_costing import is_continuous

			wo = frappe.get_doc("Work Order", self.work_order)
			if is_continuous(wo) and flt(wo.produced_qty) > 0:
				return
		return super().validate_raw_materials_exists()

	def calculate_rate_and_amount(
		self,
		reset_outgoing_rate=True,
		raise_error_if_no_rate=True,
	):
		if flt(self.get("custom_continuous_manufacture_transfer")) or flt(
			self.get("custom_uses_finish_allocation")
		):
			reset_outgoing_rate = False

		return super().calculate_rate_and_amount(
			reset_outgoing_rate=reset_outgoing_rate,
			raise_error_if_no_rate=raise_error_if_no_rate,
		)

	def check_if_operations_completed(self):
		if self.work_order and flt(
			frappe.db.get_value("Work Order", self.work_order, "custom_disable_operation")
		):
			return

		return super().check_if_operations_completed()
