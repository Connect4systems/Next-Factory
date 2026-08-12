import frappe


def execute():
	field_updates = {
		"custom_mold_bom_no": {
			"reqd": 0,
			"mandatory_depends_on": None,
			"description": "Optional. Select a Mold BOM only when mold material is required.",
		},
		"custom_mold_qty": {
			"reqd": 0,
			"mandatory_depends_on": None,
			"description": "Required only when a Mold BOM is selected.",
		},
	}

	for fieldname, values in field_updates.items():
		custom_field = frappe.db.get_value(
			"Custom Field",
			{"dt": "Work Order", "fieldname": fieldname},
			"name",
		)
		if custom_field:
			frappe.db.set_value("Custom Field", custom_field, values, update_modified=False)

	frappe.clear_cache(doctype="Work Order")
