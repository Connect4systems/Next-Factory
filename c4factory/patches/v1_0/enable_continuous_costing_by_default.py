import frappe


def execute():
	"""Default new orders to continuous costing without rewriting historical orders."""
	name = frappe.db.get_value(
		"Custom Field", {"dt": "Work Order", "fieldname": "custom_continuous_costing"}, "name"
	)
	if name:
		frappe.db.set_value("Custom Field", name, {"default": "1", "no_copy": 1})
		frappe.clear_cache(doctype="Work Order")
