import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


def execute():
    # Existing submitted orders may have no scrap warehouse. Allow the
    # Manufacturing Settings default to be saved through normal validation.
    make_property_setter(
        "Work Order", "scrap_warehouse", "allow_on_submit", 1, "Check"
    )
    frappe.clear_cache(doctype="Work Order")
