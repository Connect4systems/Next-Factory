import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Work Order Item": [
                {
                    "fieldname": "custom_additional_material_qty",
                    "label": "Additional Material Qty",
                    "fieldtype": "Float",
                    "insert_after": "required_qty",
                    "hidden": 1,
                    "read_only": 1,
                    "allow_on_submit": 1,
                    "no_copy": 1,
                },
            ],
            "Stock Entry": [
                {
                    "fieldname": "custom_is_additional_material",
                    "label": "Additional Material",
                    "fieldtype": "Check",
                    "insert_after": "pick_list",
                    "hidden": 1,
                    "read_only": 1,
                    "no_copy": 1,
                },
                {
                    "fieldname": "custom_additional_material_pick_list",
                    "label": "Additional Material Pick List",
                    "fieldtype": "Link",
                    "options": "Pick List",
                    "insert_after": "custom_is_additional_material",
                    "hidden": 1,
                    "read_only": 1,
                    "no_copy": 1,
                },
                {
                    "fieldname": "custom_sub_pick_list",
                    "label": "Sub Pick List",
                    "fieldtype": "Link",
                    "options": "Sub Pick List",
                    "insert_after": "custom_additional_material_pick_list",
                    "read_only": 1,
                    "no_copy": 1,
                },
            ],
            "Stock Entry Detail": [
                {
                    "fieldname": "custom_additional_required_qty",
                    "label": "Additional Required Qty",
                    "fieldtype": "Float",
                    "insert_after": "custom_work_order_item",
                    "hidden": 1,
                    "read_only": 1,
                    "no_copy": 1,
                },
                {
                    "fieldname": "custom_additional_transferred_qty_applied",
                    "label": "Additional Transferred Qty Applied",
                    "fieldtype": "Float",
                    "insert_after": "custom_additional_required_qty",
                    "hidden": 1,
                    "read_only": 1,
                    "no_copy": 1,
                },
                {
                    "fieldname": "custom_sub_pick_list_item",
                    "label": "Sub Pick List Item",
                    "fieldtype": "Link",
                    "options": "Sub Pick List Item",
                    "insert_after": "custom_additional_transferred_qty_applied",
                    "read_only": 1,
                    "no_copy": 1,
                },
            ],
        },
        update=True,
    )

    for doctype in (
        "Work Order",
        "Work Order Item",
        "Stock Entry",
        "Stock Entry Detail",
    ):
        frappe.clear_cache(doctype=doctype)
