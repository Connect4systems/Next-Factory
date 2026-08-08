from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Stock Entry": [
                {
                    "fieldname": "custom_continuous_start_qty",
                    "label": "Continuous Start Quantity",
                    "fieldtype": "Float",
                    "hidden": 1,
                    "read_only": 1,
                    "no_copy": 1,
                },
            ],
        },
        update=True,
    )
