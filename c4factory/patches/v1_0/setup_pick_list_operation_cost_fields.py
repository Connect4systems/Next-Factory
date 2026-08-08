from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
import frappe


def execute():
    create_custom_fields(
        {
            "Pick List": [
                {
                    "fieldname": "custom_operation_cost",
                    "label": "Real Operation Cost",
                    "fieldtype": "Currency",
                    "insert_after": "work_order",
                    "read_only": 1,
                },
            ],
        },
        update=True,
    )

    try:
        from c4factory.api.work_order_flow import update_pick_list_operation_cost

        pick_lists = frappe.get_all(
            "Pick List",
            filters={"work_order": ["!=", ""]},
            pluck="name",
        )
        for pick_list in pick_lists:
            update_pick_list_operation_cost(pick_list)
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "C4Factory: backfill Pick List operation cost failed",
        )
