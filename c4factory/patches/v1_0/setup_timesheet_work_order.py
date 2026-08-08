import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Timesheet": [
                {
                    "fieldname": "custom_work_order",
                    "label": "Work Order",
                    "fieldtype": "Link",
                    "options": "Work Order",
                    "insert_after": "company",
                    "in_standard_filter": 1,
                }
            ]
        },
        update=True,
    )
    frappe.clear_cache(doctype="Timesheet")
