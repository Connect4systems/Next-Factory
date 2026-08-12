import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	create_custom_fields(
		{
			"Work Order": [
				{
					"fieldname": "custom_mold_bom_no",
					"label": "Mold BOM No",
					"fieldtype": "Link",
					"options": "BOM",
					"insert_after": "bom_no",
					"reqd": 0,
					"description": "Optional. Select a Mold BOM only when mold material is required.",
				},
				{
					"fieldname": "custom_mold_qty",
					"label": "Mold QTY",
					"fieldtype": "Float",
					"insert_after": "qty",
					"bold": 1,
					"non_negative": 1,
					"reqd": 0,
					"description": "Required only when a Mold BOM is selected.",
				},
				{
					"fieldname": "custom_mold_material_section",
					"label": "Mold Material",
					"fieldtype": "Section Break",
					"insert_after": "required_items",
				},
				{
					"fieldname": "custom_mold_materials",
					"label": "Mold Material",
					"fieldtype": "Table",
					"options": "Mold Material",
					"insert_after": "custom_mold_material_section",
					"read_only": 1,
				},
				{
					"fieldname": "custom_mold_issue_pending",
					"label": "Mold Issue Pending",
					"fieldtype": "Check",
					"hidden": 1,
					"read_only": 1,
					"no_copy": 1,
				},
				{
					"fieldname": "custom_mold_issue_request_id",
					"label": "Mold Issue Request ID",
					"fieldtype": "Data",
					"hidden": 1,
					"read_only": 1,
					"no_copy": 1,
				},
			],
			"Stock Entry": [
				{
					"fieldname": "custom_is_mold_material_issue",
					"label": "Mold Material Issue",
					"fieldtype": "Check",
					"insert_after": "work_order",
					"hidden": 1,
					"read_only": 1,
					"no_copy": 1,
				},
				{
					"fieldname": "custom_mold_issue_channel",
					"label": "Mold Issue Channel",
					"fieldtype": "Select",
					"options": "\nContinuous\nStandard",
					"insert_after": "custom_is_mold_material_issue",
					"hidden": 1,
					"read_only": 1,
					"no_copy": 1,
				},
				{
					"fieldname": "custom_mold_issue_qty",
					"label": "Mold Issue Qty",
					"fieldtype": "Float",
					"insert_after": "custom_mold_issue_channel",
					"hidden": 1,
					"read_only": 1,
					"no_copy": 1,
				},
				{
					"fieldname": "custom_mold_issue_request_id",
					"label": "Mold Issue Request ID",
					"fieldtype": "Data",
					"insert_after": "custom_mold_issue_qty",
					"hidden": 1,
					"read_only": 1,
					"no_copy": 1,
				},
			],
			"Stock Entry Detail": [
				{
					"fieldname": "custom_mold_material",
					"label": "Mold Material",
					"fieldtype": "Link",
					"options": "Mold Material",
					"insert_after": "custom_work_order_item",
					"hidden": 1,
					"read_only": 1,
					"no_copy": 1,
				},
			],
		},
		update=True,
	)

	for doctype in ("Work Order", "Mold Material", "Stock Entry", "Stock Entry Detail"):
		frappe.clear_cache(doctype=doctype)
