import frappe


def set_work_order_project(doc, method=None):
    """Keep Timesheet company and projects aligned with the linked Work Order."""
    work_order = doc.get("custom_work_order")
    if not work_order:
        return

    work_order_values = frappe.db.get_value(
        "Work Order", work_order, ["project", "company"], as_dict=True
    )
    if not work_order_values:
        return

    if work_order_values.company:
        doc.company = work_order_values.company

    if work_order_values.project:
        if doc.meta.has_field("parent_project"):
            doc.parent_project = work_order_values.project
        for row in doc.get("time_logs") or []:
            row.project = work_order_values.project


def sync_work_order_costing(doc, method=None):
    """Recompute linked Work Order costing after Timesheet state changes."""
    work_order = doc.get("custom_work_order")
    if not work_order:
        return

    try:
        from c4factory.c4_manufacturing.stock_entry_hooks import recompute_work_order_costing

        recompute_work_order_costing(work_order)
    except Exception:
        frappe.log_error(
            frappe.get_traceback(), "C4Factory: Timesheet Work Order costing sync failed"
        )
