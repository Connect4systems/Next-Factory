from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt

from c4factory.c4_manufacturing.work_order_hooks import get_default_source_warehouse


def _resolve_work_order_arg(
    work_order: str | None = None,
    source_name: str | None = None,
    work_order_id: str | None = None,
    name: str | None = None,
    **extras,
) -> str:
    doc = extras.get("doc")
    if isinstance(doc, dict):
        name = name or doc.get("name")

    return (work_order or source_name or work_order_id or name or "").strip()


def _get_component_rows(wo):
    return wo.get("required_items") or wo.get("items") or []


def get_remaining_pick_list_qty(wo, exclude_pick_list: str | None = None) -> float:
    """
    Return finished-goods quantity not already allocated to a submitted PL.

    Open and Completed Pick Lists are both submitted documents and reserve their
    production quantity. Cancelled and draft Pick Lists do not reserve quantity.
    """
    allocated_qty = flt(
        frappe.db.sql(
            """
            SELECT COALESCE(SUM(for_qty), 0)
            FROM `tabPick List`
            WHERE work_order = %(work_order)s
              AND docstatus = 1
              AND name != %(exclude_pick_list)s
            """,
            {
                "work_order": wo.name,
                "exclude_pick_list": exclude_pick_list or "",
            },
        )[0][0]
    )

    already_covered = max(allocated_qty, flt(wo.produced_qty))
    return max(flt(wo.qty) - already_covered, 0.0)


@frappe.whitelist()
def create_pick_list(
    work_order: str | None = None,
    source_name: str | None = None,
    for_qty: float | None = None,
    **kwargs,
):
    """
    Create Pick List from Work Order required items and C4 default warehouses.
    """
    wo_name = _resolve_work_order_arg(
        work_order=work_order,
        source_name=source_name,
        **kwargs,
    )
    if not wo_name:
        frappe.throw(_("Work Order is required."))

    wo = frappe.get_doc("Work Order", wo_name)
    if wo.docstatus != 1:
        frappe.throw(_("Work Order must be submitted before creating a Pick List."))

    # Reconcile legacy/custom partial transfers before ERPNext's next Pick List
    # is built. Existing entries may predate fg_completed_qty population.
    from c4factory.api.work_order_flow import (
        _recompute_wo_material_transfer_from_pls,
    )

    _recompute_wo_material_transfer_from_pls(wo.name)
    wo.reload()

    rows = _get_component_rows(wo)
    if not rows:
        frappe.throw(_("Work Order has no required items."))

    remaining_qty = get_remaining_pick_list_qty(wo)
    requested_qty = flt(for_qty)
    # ERPNext's dialog defaults to the production remainder and does not know
    # about quantities already reserved by Pick Lists. Cap that default to the
    # actual unallocated balance while still honoring any smaller user quantity.
    fg_qty = min(requested_qty, remaining_qty) if requested_qty else remaining_qty
    if fg_qty <= 0:
        frappe.throw(
            _("No unallocated quantity remains for Work Order {0}.").format(wo.name)
        )

    qty_scale = fg_qty / (flt(wo.qty) or 1.0)

    pl = frappe.new_doc("Pick List")
    pl.company = wo.company
    pl.purpose = "Material Transfer for Manufacture"
    pl.work_order = wo.name
    if hasattr(pl, "pick_manually"):
        # Preserve every required row even when no stock is currently available.
        pl.pick_manually = 1

    for fieldname in (
        "qty_of_finished_goods_item",
        "qty_of_finished_goods",
        "for_qty",
    ):
        if hasattr(pl, fieldname):
            pl.set(fieldname, fg_qty)

    count = 0
    item_group_cache = {}
    continuous_group_cache = {}
    for wo_item in rows:
        item_code = wo_item.get("item_code")
        if not item_code:
            continue

        if is_continuous_manufacture_item(
            wo_item,
            item_group_cache=item_group_cache,
            continuous_group_cache=continuous_group_cache,
        ):
            continue

        required_qty = flt(wo_item.get("required_qty") or wo_item.get("qty"))
        row_qty = required_qty * qty_scale
        if row_qty <= 0:
            continue

        warehouse = _get_pick_list_source_warehouse(wo, wo_item)
        if not warehouse:
            frappe.throw(_("Source Warehouse is required for item {0}.").format(item_code))

        stock_uom = (
            wo_item.get("stock_uom")
            or wo_item.get("uom")
            or frappe.db.get_value("Item", item_code, "stock_uom")
        )
        item_name = (
            wo_item.get("item_name")
            or frappe.db.get_value("Item", item_code, "item_name")
            or item_code
        )

        pl_row = pl.append(
            "locations",
            {
                "item_code": item_code,
                "item": item_code,
                "item_name": item_name,
                "uom": stock_uom,
                "stock_uom": stock_uom,
                "conversion_factor": 1,
                "qty": row_qty,
                "stock_qty": row_qty,
                "qty_in_stock_uom": row_qty,
                "warehouse": warehouse,
                "work_order": wo.name,
            },
        )
        _set_if_present(pl_row, "custom_pl_qty", row_qty)
        _set_if_present(pl_row, "custom_work_order_item", wo_item.name)
        _set_if_present(pl_row, "custom_wip_warehouse", wo.get("wip_warehouse"))
        count += 1

    if count == 0:
        frappe.throw(
            _("No required items are eligible for a Pick List for Work Order {0}.").format(
                wo.name
            )
        )

    return pl.as_dict()


def _get_pick_list_source_warehouse(wo, wo_item) -> str | None:
    item_code = wo_item.get("item_code")
    item_group = wo_item.get("item_group")
    if not item_group and item_code:
        item_group = frappe.db.get_value("Item", item_code, "item_group")

    return (
        wo_item.get("source_warehouse")
        or wo_item.get("from_warehouse")
        or get_default_source_warehouse(
            item_code=item_code,
            item_group=item_group,
            company=wo.get("company"),
        )
        or wo.get("source_warehouse")
    )


def is_continuous_manufacture_item(
    wo_item,
    item_group_cache: dict | None = None,
    continuous_group_cache: dict | None = None,
) -> bool:
    """Return whether a Work Order item must be excluded from Pick Lists."""
    item_group_cache = item_group_cache if item_group_cache is not None else {}
    continuous_group_cache = (
        continuous_group_cache if continuous_group_cache is not None else {}
    )

    item_code = wo_item.get("item_code")
    item_group = wo_item.get("item_group") or item_group_cache.get(item_code)
    if item_group is None and item_code:
        item_group = frappe.db.get_value("Item", item_code, "item_group")
        item_group_cache[item_code] = item_group

    if not item_group:
        return False

    if item_group not in continuous_group_cache:
        continuous_group_cache[item_group] = cint(
            frappe.db.get_value(
                "Item Group",
                item_group,
                "custom_continues_manufacture",
            )
        )

    return bool(continuous_group_cache[item_group])


def _set_if_present(doc, fieldname: str, value) -> None:
    if hasattr(doc, fieldname):
        doc.set(fieldname, value)
