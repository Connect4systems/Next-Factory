import frappe
from frappe import _
from frappe.model.document import Document


class ContinuousCostSettlement(Document):
	def validate(self):
		if not frappe.flags.get("continuous_cost_service"):
			frappe.throw(_("Cost settlements can only be created by the Work Order costing service."))
