import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	create_custom_fields(
		{
			"BOM": [
				{
					"fieldname": "custom_bom_type",
					"label": "BOM Type",
					"fieldtype": "Select",
					"options": "Product\nMold",
					"default": "Product",
					"reqd": 1,
					"insert_after": "item",
				},
			]
		},
		update=True,
	)

	# BOMs created before BOM Type was introduced are Product BOMs.
	frappe.db.sql(
		"""
		UPDATE `tabBOM`
		SET `custom_bom_type` = 'Product'
		WHERE COALESCE(TRIM(`custom_bom_type`), '') = ''
		"""
	)

	frappe.clear_cache(doctype="BOM")
