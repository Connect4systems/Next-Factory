import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


def execute():
	make_property_setter(
		"Work Order Item",
		"amount",
		"allow_on_submit",
		1,
		"Check",
	)
	frappe.clear_cache(doctype="Work Order Item")
