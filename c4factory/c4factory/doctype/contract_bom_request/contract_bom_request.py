# Copyright (c) 2025, Connect 4 Systems and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ContractBOMRequest(Document):
	pass


@frappe.whitelist()
def create_bom_for_item(item, qty=1, company=None, contract_bom_request=None, contract_bom_item=None):
	if not item:
		frappe.throw("Please set Item before creating BOM.")

	if not company:
		company = frappe.defaults.get_default("company")

	item_doc = frappe.get_cached_doc("Item", item)

	bom = frappe.get_doc({
		"doctype": "BOM",
		"item": item,
		"quantity": 1,
		"company": company,
		"items": [
			{
				"item_code": item,
				"qty": 1,
				"uom": item_doc.stock_uom,
			}
		],
	})
	bom.insert(ignore_permissions=False)

	if contract_bom_request and contract_bom_item:
		frappe.has_permission("Contract BOM Request", doc=contract_bom_request, throw=True)
		row = frappe.db.get_value(
			"Contract BOM Item",
			contract_bom_item,
			["parent", "parenttype"],
			as_dict=True,
		)
		if not row or row.parenttype != "Contract BOM Request" or row.parent != contract_bom_request:
			frappe.throw("Invalid Contract BOM row selected.")

		# Persist link on child row so dashboard internal link can resolve exact BOM names.
		frappe.db.set_value("Contract BOM Item", contract_bom_item, "bom", bom.name, update_modified=False)

	frappe.db.commit()
	return bom.name
