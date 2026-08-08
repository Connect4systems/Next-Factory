import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    """
    Extra custom fields needed for C4Factory WO → Pick List → Partial Stock Entry flow.
    Safe to run multiple times (update=True).
    """

    custom_fields = {
        # ---------------------------
        # Work Order (parent)
        # ---------------------------
        "Work Order": [
            {
                "fieldname": "custom_total_transferred_qty",
                "label": "Total Transferred Qty",
                "fieldtype": "Float",
                "insert_after": "material_transferred_for_manufacturing",
            },
            {
                "fieldname": "custom_total_consumed_qty",
                "label": "Total Consumed Qty",
                "fieldtype": "Float",
                "insert_after": "custom_total_transferred_qty",
            },
        ],

        # ---------------------------
        # Work Order Item (child)
        # ---------------------------
        "Work Order Item": [
            {
                "fieldname": "custom_balance_to_transfer",
                "label": "Balance To Transfer",
                "fieldtype": "Float",
                "insert_after": "transferred_qty",
                "read_only": 1,
            },
            {
                "fieldname": "custom_balance_to_consume",
                "label": "Balance To Consume",
                "fieldtype": "Float",
                "insert_after": "consumed_qty",
                "read_only": 1,
            },
        ],

        # ---------------------------
        # Pick List Item (child)
        # ---------------------------
        "Pick List Item": [
            {
                # حتى لو أنت عملته يدويًا، update=True هيعدّل أو يسيبه كما هو
                "fieldname": "custom_pl_qty",
                "label": "PL Qty",
                "fieldtype": "Float",
                "insert_after": "qty",
            },
            {
                "fieldname": "custom_work_order_item",
                "label": "Work Order Item",
                "fieldtype": "Link",
                "options": "Work Order Item",
                "insert_after": "item_code",
            },
            {
                "fieldname": "custom_wip_warehouse",
                "label": "WIP Warehouse",
                "fieldtype": "Link",
                "options": "Warehouse",
                "insert_after": "warehouse",
            },
        ],

        # ---------------------------
        # Stock Entry Detail (child)
        # ---------------------------
        "Stock Entry Detail": [
            {
                "fieldname": "custom_pick_list_item",
                "label": "Pick List Item",
                "fieldtype": "Data",  # أو Link إلى Pick List Item لو حابب
                "insert_after": "item_code",
            },
            {
                "fieldname": "custom_work_order_item",
                "label": "Work Order Item",
                "fieldtype": "Link",
                "options": "Work Order Item",
                "insert_after": "custom_pick_list_item",
            },
        ],

        # ---------------------------
        # Stock Entry (parent)  (اختياري لكن يدعم التقارير)
        # ---------------------------
        "Stock Entry": [
            {
                "fieldname": "custom_work_order",
                "label": "Work Order (C4)",
                "fieldtype": "Link",
                "options": "Work Order",
                "insert_after": "work_order",
            },
            {
                "fieldname": "custom_pick_list",
                "label": "Pick List (C4)",
                "fieldtype": "Link",
                "options": "Pick List",
                "insert_after": "custom_work_order",
            },
        ],

        # ---------------------------
        # Job Card (parent)
        # ---------------------------
        "Job Card": [
            {
                "fieldname": "custom_pick_list",
                "label": "Pick List (C4)",
                "fieldtype": "Link",
                "options": "Pick List",
                "insert_after": "work_order",
                "read_only": 1,
            },
        ],
    }

    create_custom_fields(custom_fields, update=True)
