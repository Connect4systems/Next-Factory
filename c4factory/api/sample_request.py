from __future__ import annotations
import frappe

@frappe.whitelist()
def create_work_order_from_sample(sample_request_name: str) -> str:
    sr = frappe.get_doc("Sample Request", sample_request_name)
    if sr.docstatus != 1:
        frappe.throw("Submit the Sample Request first.")

    product = sr.get("product")
    bom_no  = sr.get("bom")
    if not product:
        frappe.throw("Please set Product on the Sample Request.")
    if not bom_no:
        frappe.throw("Please set BOM on the Sample Request.")

    # validate BOM
    bom_info = frappe.db.get_value("BOM", bom_no, ["item", "is_active"], as_dict=True)
    if not bom_info:
        frappe.throw(f"BOM {bom_no} was not found.")
    if bom_info.item != product:
        frappe.throw(f"Selected BOM {bom_no} belongs to Item {bom_info.item}, but Product is {product}.")
    if not bom_info.is_active:
        frappe.throw(f"Selected BOM {bom_no} is not Active.")

    # company
    company = sr.get("company") or frappe.db.get_single_value("Global Defaults", "default_company")
    if not company:
        frappe.throw("Company not found. Set Sample Request.company or Default Company in Global Defaults.")

    # create WO
    wo = frappe.new_doc("Work Order")
    wo.company = company
    wo.production_item = product
    wo.bom_no = bom_no
    wo.qty = 1

    # ---- pull operations from BOM (so they exist immediately) ----
    bom = frappe.get_doc("BOM", bom_no)
    wo.set("operations", [])
    for op in (bom.operations or []):
        wo.append("operations", {
            "operation": op.operation,
            "workstation": getattr(op, "workstation", None),
            "hour_rate": getattr(op, "hour_rate", 0),
            "time_in_mins": getattr(op, "time_in_mins", 0),
        })
    # --------------------------------------------------------------

    wo.insert(ignore_permissions=True)   # saved as Draft with operations populated
    return wo.name
