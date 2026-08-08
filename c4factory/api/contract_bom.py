# c4napata/api/contract_bom.py
from __future__ import annotations
import frappe
from frappe.model.mapper import get_mapped_doc
from frappe.utils import today

@frappe.whitelist()
def make_contract_bom_request(sales_order: str):
    """
    Build (unsaved) Contract BOM Request from a Sales Order.

    - Sets Contract BOM Request.sales_order = <SO>
    - Copies Sales Order Item -> Contract BOM Item
      (item, item_name, description, qty)
    """
    def _postprocess(source, target):
        target.sales_order = source.name
        if not target.get("date"):
            target.date = today()

    doc = get_mapped_doc(
        "Sales Order",
        sales_order,
        {
            "Sales Order": {"doctype": "Contract BOM Request"},
            "Sales Order Item": {
                "doctype": "Contract BOM Item",
                "field_map": {
                    "item_code": "item",
                    "item_name": "item_name",
                    "description": "description",
                    "qty": "qty",
                },
            },
        },
        target_doc=None,
        postprocess=_postprocess,
    )
    return doc
