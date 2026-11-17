import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


def execute():
	for property_name, value in (("read_only", 0), ("allow_on_submit", 1)):
		make_property_setter(
			"Work Order", "custom_mold_materials", property_name, value, "Check"
		)
	frappe.clear_cache(doctype="Work Order")
	frappe.clear_cache(doctype="Mold Material")
