import frappe
from frappe.model.mapper import get_mapped_doc


@frappe.whitelist()
def make_timesheet(source_name: str, target_doc=None):
    """Create a Timesheet linked to a Work Order and prefill its project."""

    def set_missing_values(source, target):
        target.custom_work_order = source.name
        target.company = source.company
        if source.project:
            target.parent_project = source.project
            target.append("time_logs", {"project": source.project})

    return get_mapped_doc(
        "Work Order",
        source_name,
        {"Work Order": {"doctype": "Timesheet"}},
        target_doc,
        set_missing_values,
    )
