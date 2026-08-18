import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


def execute():
	work_order_fields = ("qty", "required_items")
	work_order_item_fields = (
		"item_code",
		"source_warehouse",
		"required_qty",
	)

	for fieldname in work_order_fields:
		make_property_setter(
			"Work Order",
			fieldname,
			"allow_on_submit",
			1,
			"Check",
		)

	for fieldname in work_order_item_fields:
		make_property_setter(
			"Work Order Item",
			fieldname,
			"allow_on_submit",
			1,
			"Check",
		)

	frappe.clear_cache(doctype="Work Order")
	frappe.clear_cache(doctype="Work Order Item")
