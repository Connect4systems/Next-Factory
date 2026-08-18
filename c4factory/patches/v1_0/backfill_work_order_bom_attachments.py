import frappe

from c4factory.c4_manufacturing.work_order_hooks import attach_public_bom_files


def execute():
    """Attach public BOM files to all existing submitted Work Orders."""
    work_orders = frappe.get_all(
        "Work Order",
        filters={"docstatus": 1},
        fields=["name", "bom_no", "custom_mold_bom_no"],
    )

    for work_order in work_orders:
        attach_public_bom_files(work_order)
