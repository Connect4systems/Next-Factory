import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Work Order": [
                {
                    "fieldname": "custom_additional_materials",
                    "label": "Additional Materials",
                    "fieldtype": "Section Break",
                    "insert_after": "required_items",
                },
                {
                    "fieldname": "custom_additional_material",
                    "label": "Additional Material",
                    "fieldtype": "Table",
                    "options": "Work Order Item",
                    "insert_after": "custom_additional_materials",
                    "allow_on_submit": 1,
                },
            ],
        },
        update=True,
    )
    frappe.clear_cache(doctype="Work Order")
