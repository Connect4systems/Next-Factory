import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	create_custom_fields(
		{
			"Company": [
				{
					"fieldname": "custom_mold_production_wip_account",
					"label": "Mold Production/WIP Clearing Account",
					"fieldtype": "Link",
					"options": "Account",
					"insert_after": "default_expense_account",
					"description": "Clearing account used to capitalize issued mold cost into finished products.",
				},
			],
			"Stock Entry": [
				{
					"fieldname": "custom_uses_finish_allocation",
					"label": "Uses Finish Material Allocation",
					"fieldtype": "Check",
					"insert_after": "custom_mold_issue_request_id",
					"hidden": 1,
					"read_only": 1,
					"no_copy": 1,
				},
				{
					"fieldname": "custom_material_allocation_qty",
					"label": "Material Allocation Production Qty",
					"fieldtype": "Float",
					"insert_after": "custom_uses_finish_allocation",
					"hidden": 1,
					"read_only": 1,
					"no_copy": 1,
				},
				{
					"fieldname": "custom_is_final_finish",
					"label": "Final Finish Reconciliation",
					"fieldtype": "Check",
					"insert_after": "custom_material_allocation_qty",
					"hidden": 1,
					"read_only": 1,
					"no_copy": 1,
				},
				{
					"fieldname": "custom_allocated_mold_cost",
					"label": "Allocated Mold Cost",
					"fieldtype": "Currency",
					"insert_after": "custom_is_final_finish",
					"hidden": 1,
					"read_only": 1,
					"no_copy": 1,
				},
				{
					"fieldname": "custom_allocated_operation_cost",
					"label": "Allocated Operation Cost",
					"fieldtype": "Currency",
					"insert_after": "custom_allocated_mold_cost",
					"hidden": 1,
					"read_only": 1,
					"no_copy": 1,
				},
			],
			"Stock Entry Detail": [
				{
					"fieldname": "custom_source_transfer_detail",
					"label": "Source Transfer Detail",
					"fieldtype": "Link",
					"options": "Stock Entry Detail",
					"insert_after": "custom_mold_material",
					"hidden": 1,
					"read_only": 1,
					"no_copy": 1,
				},
			],
		},
		update=True,
	)

	# Existing additional-material transfers predate allocation coverage. Spread
	# their remaining balance prospectively over the Work Order quantity that is
	# still unfinished when this migration is installed.
	frappe.db.sql(
		"""
		UPDATE `tabStock Entry` se
		INNER JOIN `tabWork Order` wo ON wo.name = se.work_order
		SET se.custom_material_allocation_qty = GREATEST(wo.qty - wo.produced_qty, 0)
		WHERE COALESCE(se.custom_is_additional_material, 0) = 1
		  AND se.docstatus = 1
		  AND COALESCE(se.custom_material_allocation_qty, 0) = 0
		"""
	)

	for doctype in ("Company", "Stock Entry", "Stock Entry Detail"):
		frappe.clear_cache(doctype=doctype)
