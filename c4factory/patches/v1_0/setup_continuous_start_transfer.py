import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Work Order": [
                {
                    "fieldname": "custom_continuous_start_pending",
                    "label": "Continuous Start Transfer Pending",
                    "fieldtype": "Check",
                    "hidden": 1,
                    "read_only": 1,
                    "no_copy": 1,
                },
                {
                    "fieldname": "custom_continuous_start_request_id",
                    "label": "Continuous Start Request ID",
                    "fieldtype": "Data",
                    "hidden": 1,
                    "read_only": 1,
                    "no_copy": 1,
                },
            ],
            "Stock Entry": [
                {
                    "fieldname": "custom_continuous_manufacture_transfer",
                    "label": "Continuous Manufacture Transfer",
                    "fieldtype": "Check",
                    "hidden": 1,
                    "read_only": 1,
                    "no_copy": 1,
                },
                {
                    "fieldname": "custom_continuous_start_request_id",
                    "label": "Continuous Start Request ID",
                    "fieldtype": "Data",
                    "hidden": 1,
                    "read_only": 1,
                    "no_copy": 1,
                    "unique": 1,
                },
                {
                    "fieldname": "custom_continuous_start_qty",
                    "label": "Continuous Start Quantity",
                    "fieldtype": "Float",
                    "hidden": 1,
                    "read_only": 1,
                    "no_copy": 1,
                },
            ],
            "Pick List": [
                {
                    "fieldname": "custom_continuous_start_request_id",
                    "label": "Continuous Start Request ID",
                    "fieldtype": "Data",
                    "hidden": 1,
                    "read_only": 1,
                    "no_copy": 1,
                    "unique": 1,
                },
            ],
        },
        update=True,
    )
