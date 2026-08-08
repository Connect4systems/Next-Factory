from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Stock Entry": [
                {
                    "fieldname": "custom_sub_pick_list",
                    "label": "Sub Pick List",
                    "fieldtype": "Link",
                    "options": "Sub Pick List",
                    "insert_after": "custom_additional_material_pick_list",
                    "read_only": 1,
                    "no_copy": 1,
                }
            ],
            "Stock Entry Detail": [
                {
                    "fieldname": "custom_sub_pick_list_item",
                    "label": "Sub Pick List Item",
                    "fieldtype": "Link",
                    "options": "Sub Pick List Item",
                    "insert_after": "custom_additional_transferred_qty_applied",
                    "read_only": 1,
                    "no_copy": 1,
                }
            ],
        },
        update=True,
    )
