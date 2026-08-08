import frappe


def execute():
    if not frappe.db.exists("Custom Field", "Work Order-c4_operating_cost"):
        return

    frappe.db.set_value(
        "Custom Field",
        "Work Order-c4_operating_cost",
        {
            "read_only": 1,
            "allow_on_submit": 1,
        },
    )
