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
        wo = frappe.get_doc("Work Order", work_order)
        if wo.get("custom_continuous_costing") and wo.status == "Completed":
            if not wo.get("custom_timesheet_cost_closed"):
                if not frappe.db.exists("Timesheet", {
                    "custom_work_order": work_order, "docstatus": ("<", 2),
                }):
                    from c4factory.api.work_order_costing import request_settlement

                    request_settlement(wo)
                else:
                    frappe.db.set_value("Work Order", work_order,
                        "custom_continuous_cost_status", "Awaiting Timesheet Finish")
    except Exception:
        frappe.log_error(
            frappe.get_traceback(), "C4Factory: Timesheet Work Order costing sync failed"
        )
        if (frappe.get_meta("Work Order").has_field("custom_continuous_costing")
                and frappe.db.get_value("Work Order", work_order, "custom_continuous_costing")):
            raise
