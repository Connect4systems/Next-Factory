import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Pick List": [
                {
                    "fieldname": "custom_continuous_production",
                    "label": "Continuous Production",
                    "fieldtype": "Check",
                    "insert_after": "work_order",
                    "read_only": 1,
                    "no_copy": 1,
                    "default": "0",
                    "description": (
                        "Checked automatically for the continuous-production "
                        "Pick List created by Work Order Start."
                    ),
                },
                {
                    "fieldname": "custom_continuous_start_request_id",
                    "label": "Continuous Start Request ID",
                    "fieldtype": "Data",
                    "hidden": 1,
                    "read_only": 1,
                    "no_copy": 1,
                    # A Start request now creates up to two Pick Lists.
                    "unique": 0,
                },
            ]
        },
        update=True,
    )
    frappe.clear_cache(doctype="Pick List")
    # Apply the changed `unique` property immediately so both documents in a
    # Start pair can store the same request ID during this migration.
    frappe.db.updatedb("Pick List")
