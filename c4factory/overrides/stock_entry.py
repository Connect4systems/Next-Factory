import frappe
from frappe.utils import flt
from erpnext.stock.doctype.stock_entry.stock_entry import StockEntry as ERPNextStockEntry


class StockEntry(ERPNextStockEntry):
    def calculate_rate_and_amount(
        self,
        reset_outgoing_rate=True,
        raise_error_if_no_rate=True,
    ):
        if flt(self.get("custom_continuous_manufacture_transfer")):
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
