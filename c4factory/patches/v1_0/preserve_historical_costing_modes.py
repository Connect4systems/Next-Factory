import frappe


def execute():
	"""Repair legacy rows seeded by an enabled SQL default, without repricing them.

	New automatic orders always have a Provisional cost status. Orders that have
	used the new flow retain their mode through their status, audit or stock links.
	Only an unused mode flag is cleared; no quantity or financial field is updated.
	"""
	frappe.db.sql("""
		UPDATE `tabWork Order` wo SET wo.custom_continuous_costing=0
		WHERE wo.custom_continuous_costing=1
		  AND COALESCE(wo.custom_continuous_cost_status, '')=''
		  AND COALESCE(wo.custom_continuous_settlement, '')=''
		  AND NOT EXISTS (
			SELECT 1 FROM `tabContinuous Cost Settlement` settlement
			WHERE settlement.work_order=wo.name
		  )
		  AND NOT EXISTS (
			SELECT 1 FROM `tabStock Entry` se
			WHERE se.work_order=wo.name AND se.custom_continuous_costing=1
		  )
	""")
