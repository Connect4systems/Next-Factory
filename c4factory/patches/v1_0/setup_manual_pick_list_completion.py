import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Pick List": [
                {
                    "fieldname": "custom_manually_completed",
                    "label": "Manually Completed",
                    "fieldtype": "Check",
                    "insert_after": "status",
                    "hidden": 1,
                    "read_only": 1,
                    "no_copy": 1,
                }
            ]
        },
        update=True,
    )

    # Existing status recalculation remains authoritative unless a user
    # explicitly uses the new completion action.
    frappe.clear_cache(doctype="Pick List")
