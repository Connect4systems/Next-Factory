import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

from c4factory.c4_manufacturing.work_order_hooks import SALES_ORDER_ITEM_SPEC_FIELDS


def execute():
    for fieldname in SALES_ORDER_ITEM_SPEC_FIELDS:
        make_property_setter("Work Order", fieldname, "read_only", 0, "Check")
        make_property_setter("Work Order", fieldname, "allow_on_submit", 1, "Check")
    frappe.clear_cache(doctype="Work Order")
