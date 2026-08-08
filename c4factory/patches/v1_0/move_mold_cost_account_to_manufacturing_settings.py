import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	create_custom_fields(
		{
			"Manufacturing Settings": [
				{
					"fieldname": "custom_mold_cost_expense_account",
					"label": "Mold Cost Expense Account",
					"fieldtype": "Link",
					"options": "Account",
					"insert_after": "default_scrap_warehouse",
					"description": "Expense/clearing account used to capitalize issued mold cost into finished products.",
				},
			]
		},
		update=True,
	)

	# Preserve an account already configured using the earlier Company field.
	if not frappe.db.get_single_value(
		"Manufacturing Settings", "custom_mold_cost_expense_account"
	) and frappe.db.has_column("Company", "custom_mold_production_wip_account"):
		configured = frappe.db.sql(
			"""
			SELECT custom_mold_production_wip_account
			FROM `tabCompany`
			WHERE COALESCE(custom_mold_production_wip_account, '') != ''
			ORDER BY modified DESC
			LIMIT 1
			"""
		)
		account = configured[0][0] if configured else None
		if account:
			frappe.db.set_single_value(
				"Manufacturing Settings", "custom_mold_cost_expense_account", account
			)

	# Keep old values recoverable, but remove the obsolete Company field from forms.
	old_field = frappe.db.get_value(
		"Custom Field",
		{
			"dt": "Company",
			"fieldname": "custom_mold_production_wip_account",
		},
		"name",
	)
	if old_field:
		frappe.db.set_value("Custom Field", old_field, "hidden", 1)

	frappe.clear_cache(doctype="Manufacturing Settings")
	frappe.clear_cache(doctype="Company")
