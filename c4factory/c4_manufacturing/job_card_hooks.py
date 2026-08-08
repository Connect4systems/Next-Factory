import frappe
from frappe.utils import flt


def set_operation_row_reference(doc, method=None):
    """
    Backfill ERPNext's Job Card operation row reference fields from the linked
    Work Order operation. This is needed for C4 Pick List-created Job Cards.
    """
    if not doc.get("work_order") or not doc.get("operation"):
        return

    if doc.get("operation_id") and doc.get("operation_row_number"):
        return

    op_row = _get_work_order_operation_row(
        doc.get("work_order"),
        operation=doc.get("operation"),
        workstation=doc.get("workstation"),
        operation_id=doc.get("operation_id"),
    )

    if not op_row:
        return

    _set_if_field(doc, "operation_id", op_row.name)
    _set_if_field(doc, "operation_row_id", op_row.idx)
    _set_if_field(doc, "operation_row_number", op_row.name)
    _set_if_field(doc, "sequence_id", op_row.sequence_id or op_row.idx)


@frappe.whitelist()
def get_operation_row_reference(work_order, operation=None, workstation=None, operation_id=None):
    op_row = _get_work_order_operation_row(
        work_order,
        operation=operation,
        workstation=workstation,
        operation_id=operation_id,
    )
    if not op_row:
        return None

    return {
        "operation_id": op_row.name,
        "operation_row_id": op_row.idx,
        "operation_row_number": op_row.name,
        "sequence_id": op_row.sequence_id or op_row.idx,
    }


def _get_work_order_operation_row(work_order, operation=None, workstation=None, operation_id=None):
    if not work_order:
        return None

    fields = ["name", "idx", "sequence_id"]

    if operation_id:
        op_row = frappe.db.get_value(
            "Work Order Operation",
            {"name": operation_id, "parent": work_order, "parenttype": "Work Order"},
            fields,
            as_dict=True,
        )
        if op_row:
            return op_row

    if operation:
        filters = {
            "parent": work_order,
            "parenttype": "Work Order",
            "operation": operation,
        }

        if workstation:
            filters["workstation"] = workstation

        op_row = frappe.db.get_value(
            "Work Order Operation",
            filters,
            fields,
            as_dict=True,
            order_by="idx asc",
        )
        if op_row:
            return op_row

        op_row = frappe.db.get_value(
            "Work Order Operation",
            {
                "parent": work_order,
                "parenttype": "Work Order",
                "operation": operation,
            },
            fields,
            as_dict=True,
            order_by="idx asc",
        )
        if op_row:
            return op_row

    return frappe.db.get_value(
        "Work Order Operation",
        {"parent": work_order, "parenttype": "Work Order"},
        fields,
        as_dict=True,
        order_by="idx asc",
    )


def normalize_partial_completion(doc, method=None):
    """
    Prevent auto process loss on partial Job Card completion.

    ERPNext may infer process_loss_qty as (for_quantity - completed_qty)
    when users submit a partially completed Job Card as Completed.
    For C4 flow we treat the remaining quantity as pending work, not loss.
    """
    qty = flt(getattr(doc, "for_quantity", 0))
    completed = flt(getattr(doc, "total_completed_qty", 0))
    process_loss = flt(getattr(doc, "process_loss_qty", 0))

    if qty <= 0 or completed <= 0 or completed >= qty:
        return

    remaining = max(qty - completed, 0.0)

    # Only normalize the common auto-inferred case.
    if abs(process_loss - remaining) > 0.000001:
        return

    doc.process_loss_qty = 0.0

    if hasattr(doc, "process_loss_percentage"):
        doc.process_loss_percentage = 0.0

    # Convert this Job Card into a true partial-complete card.
    # Remaining quantity stays pending on the Work Order.
    doc.for_quantity = completed


def sync_work_order_costing_from_job_card(doc, method=None):
    """
    Keep Work Order c4_operating_cost / c4_total_cost synced with
    actual Job Card operating data whenever Job Cards change.
    """
    wo_name = getattr(doc, "work_order", None)
    if not wo_name:
        return

    try:
        from c4factory.c4_manufacturing.stock_entry_hooks import recompute_work_order_costing

        recompute_work_order_costing(wo_name)
    except Exception:
        # Do not block Job Card save/submit due to costing sync issues.
        frappe.log_error(frappe.get_traceback(), "C4Factory: Job Card costing sync failed")

    sync_pick_list_operation_cost_from_job_card(doc)


def sync_pick_list_operation_cost_from_job_card(doc, method=None):
    pick_list = doc.get("custom_pick_list")
    if not pick_list:
        return

    try:
        from c4factory.api.work_order_flow import update_pick_list_operation_cost

        update_pick_list_operation_cost(pick_list)
    except Exception:
        frappe.log_error(
            frappe.get_traceback(), "C4Factory: Pick List operation cost sync failed"
        )


def _set_if_field(doc, fieldname: str, value) -> None:
    if value is not None and doc.meta.has_field(fieldname):
        doc.set(fieldname, value)
