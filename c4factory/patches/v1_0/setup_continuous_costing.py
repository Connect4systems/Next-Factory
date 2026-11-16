import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	create_custom_fields(
		{
			"Work Order": [
				{
					"fieldname": "custom_continuous_costing",
					"label": "Continuous Production Costing",
					"fieldtype": "Check",
					"insert_after": "update_consumed_material_cost_in_project",
					"allow_on_submit": 1,
					"default": "0",
					"no_copy": 1,
					"description": "Pool actual transfers and Timesheets across all finished batches.",
				},
				{
					"fieldname": "custom_continuous_cost_status",
					"label": "Production Cost Status",
					"fieldtype": "Data",
					"insert_after": "custom_continuous_costing",
					"read_only": 1,
					"allow_on_submit": 1,
					"no_copy": 1,
				},
				{
					"fieldname": "custom_continuous_settlement",
					"label": "Latest Cost Settlement",
					"fieldtype": "Link",
					"options": "Continuous Cost Settlement",
					"insert_after": "custom_continuous_cost_status",
					"read_only": 1,
					"allow_on_submit": 1,
					"no_copy": 1,
				},
				{
					"fieldname": "custom_timesheet_cost_closed",
					"label": "Timesheet Cost Closed",
					"fieldtype": "Check",
					"insert_after": "custom_continuous_settlement",
					"read_only": 1,
					"allow_on_submit": 1,
					"no_copy": 1,
				},
			],
			"Manufacturing Settings": [
				{
					"fieldname": "custom_continuous_cost_account",
					"label": "Continuous Cost Redistribution Account",
					"fieldtype": "Link",
					"options": "Account",
					"insert_after": "custom_operation_cost_account",
					"description": "Non-stock clearing account for differences between production batches.",
				},
			],
			"Stock Entry": [
				{
					"fieldname": "custom_continuous_costing",
					"label": "Continuous Production Costing",
					"fieldtype": "Check",
					"insert_after": "custom_uses_finish_allocation",
					"read_only": 1,
					"hidden": 1,
					"no_copy": 1,
				},
			],
			"Stock Entry Detail": [
				{
					"fieldname": "custom_work_order_item",
					"label": "Work Order Material Row",
					"fieldtype": "Link",
					"options": "Work Order Item",
					"insert_after": "custom_pick_list_item",
					"read_only": 1,
					"no_copy": 1,
				},
			],
		},
		update=True,
	)
	for doctype in ("Work Order", "Manufacturing Settings", "Stock Entry", "Stock Entry Detail"):
		frappe.clear_cache(doctype=doctype)
