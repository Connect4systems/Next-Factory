import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	create_custom_fields(
		{
			"Manufacturing Settings": [
				{
					"fieldname": "custom_operation_cost_account",
					"label": "Operation Cost Account",
					"fieldtype": "Link",
					"options": "Account",
					"insert_after": "custom_mold_cost_expense_account",
					"description": "Expense/clearing account used to capitalize Work Order operating cost into finished products.",
				},
			]
		},
		update=True,
	)

	# Use the requested account automatically when it exists, while keeping the
	# patch portable to companies whose account abbreviation is not NC.
	if not frappe.db.get_single_value(
		"Manufacturing Settings", "custom_operation_cost_account"
	) and frappe.db.exists("Account", "Operation Cost - NC"):
		frappe.db.set_single_value(
			"Manufacturing Settings",
			"custom_operation_cost_account",
			"Operation Cost - NC",
		)

	frappe.clear_cache(doctype="Manufacturing Settings")
