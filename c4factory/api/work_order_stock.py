import frappe
from frappe.utils import flt
from erpnext.manufacturing.doctype.work_order.work_order import (
    make_stock_entry as erpnext_make_stock_entry,
)


@frappe.whitelist()
def make_stock_entry(work_order_id, purpose, qty=None):
    """
    Override for erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry

    - For purpose != "Manufacture": delegate to standard ERPNext behavior.
    - For purpose == "Manufacture": build Stock Entry from:
        * ACTUAL transfers to WIP (Material Transfer for Manufacture), and
        * Scrap items defined on the Work Order (c4_scrap_items).

    Final behavior:
    - Raw material rows:
        s_warehouse = WIP Warehouse
        t_warehouse = (empty)
    - Scrap rows:
        s_warehouse = (empty)
        t_warehouse = Scrap Warehouse
    - Finished good row:
        s_warehouse = (empty)
        t_warehouse = FG Warehouse
    """
    if purpose != "Manufacture":
        # For Material Transfer, Disassemble, etc: use standard logic
        return erpnext_make_stock_entry(work_order_id, purpose, qty)

    # Custom logic for Manufacture
    wo = frappe.get_doc("Work Order", work_order_id)

    if not wo.wip_warehouse:
        frappe.throw(f"Work Order {wo.name} has no WIP Warehouse set.")

    if not wo.fg_warehouse:
        frappe.throw(f"Work Order {wo.name} has no Finished Goods Warehouse set.")

    # Determine FG quantity
    if qty:
        try:
            fg_qty = float(qty)
        except Exception:
            fg_qty = frappe.utils.flt(qty)
    else:
        # standard behavior: remaining to produce
        fg_qty = (wo.qty or 0) - (wo.produced_qty or 0)

    if fg_qty <= 0:
        frappe.throw(
            f"No remaining quantity to manufacture for Work Order {wo.name}."
        )

    # Collect ACTUAL, not-yet-consumed Pick List transfers to WIP for this WO.
    transferred_items = _get_transferred_items_to_wip(wo.name, wo.wip_warehouse)

    if not transferred_items:
        frappe.throw(
            "No unconsumed Pick List material transfers found "
            f"for Work Order {wo.name}. Please transfer the next Pick List to WIP "
            "before finishing another product."
        )

    # --------------------------------------------------------------------
    # Create Manufacture Stock Entry (header)
    # --------------------------------------------------------------------
    se = frappe.new_doc("Stock Entry")
    se.purpose = "Manufacture"
    se.stock_entry_type = "Manufacture"
    se.company = wo.company
    se.work_order = wo.name
    se.from_bom = 0  # do NOT pull from BOM
    se.use_multi_level_bom = wo.use_multi_level_bom

    # v15: set all manufactured-qty style fields
    se.fg_completed_qty = fg_qty
    if hasattr(se, "manufactured_qty"):
        se.manufactured_qty = fg_qty
    if hasattr(se, "for_quantity"):
        se.for_quantity = fg_qty

    # We'll mark rows with a small flag so we can enforce warehouses AFTER
    # set_missing_values() (because ERPNext may override them).

    # 1) RAW MATERIAL ROWS – consumed from WIP
    for item in transferred_items:
        rate = flt(item.get("valuation_rate") or item.get("basic_rate"))
        amount = flt(item.get("amount")) or (flt(item["qty"]) * rate)
        row = se.append("items", {
            "item_code": item["item_code"],
            "qty": item["qty"],
            "uom": item["stock_uom"],
            "stock_uom": item["stock_uom"],
            "conversion_factor": 1,
            "is_finished_item": 0,
            "is_scrap_item": 0,
            "valuation_rate": rate,
            "basic_rate": rate,
            "amount": amount,
            "basic_amount": amount,
        })
        if item.get("custom_pick_list_item"):
            row.custom_pick_list_item = item["custom_pick_list_item"]
        if item.get("custom_work_order_item") and row.meta.has_field("custom_work_order_item"):
            row.custom_work_order_item = item["custom_work_order_item"]
        row._c4_role = "raw"
        row._c4_expected_qty = item["qty"]
        row._c4_expected_rate = rate

    # 2) SCRAP ITEMS ROWS – created in Scrap Warehouse
    if wo.scrap_warehouse:
        for row in (wo.get("c4_scrap_items") or []):
            qty = (row.get("stock_qty") or 0.0)
            if qty <= 0:
                continue

            scrap_row = se.append("items", {
                "item_code": row.item_code,
                "qty": qty,
                "uom": row.stock_uom,
                "stock_uom": row.stock_uom,
                "conversion_factor": 1,
                "is_scrap_item": 1,
                "is_finished_item": 0,
            })
            scrap_row._c4_role = "scrap"
            scrap_row._c4_expected_qty = qty

    # 3) FINISHED GOOD ROW – into FG warehouse
    fg_row = se.append("items", {
        "item_code": wo.production_item,
        "qty": fg_qty,
        "uom": wo.stock_uom,
        "stock_uom": wo.stock_uom,
        "conversion_factor": 1,
        "is_finished_item": 1,
        "is_scrap_item": 0,
    })
    fg_row._c4_role = "fg"
    fg_row._c4_expected_qty = fg_qty

    # Let ERPNext fill valuation etc.
    se.set_missing_values()

    # ENFORCE WAREHOUSES AFTER set_missing_values
    for row in se.items:
        role = getattr(row, "_c4_role", None)
        expected_qty = float(getattr(row, "_c4_expected_qty", 0) or 0)

        if role == "raw":
            # raw materials: consumed from WIP
            row.s_warehouse = wo.wip_warehouse
            row.t_warehouse = None
            if expected_qty > 0:
                row.qty = expected_qty
            expected_rate = flt(getattr(row, "_c4_expected_rate", 0))
            if expected_rate > 0:
                row.valuation_rate = expected_rate
                row.basic_rate = expected_rate
                row.amount = flt(row.qty) * expected_rate
                row.basic_amount = row.amount

        elif role == "scrap":
            # scrap: created in scrap warehouse, no source
            row.s_warehouse = None
            row.t_warehouse = wo.scrap_warehouse
            if expected_qty > 0:
                row.qty = expected_qty

        elif role == "fg":
            # finished good: created in FG warehouse, no source
            row.s_warehouse = None
            row.t_warehouse = wo.fg_warehouse
            if fg_qty > 0:
                row.qty = fg_qty

        # remove temporary attributes if present
        if hasattr(row, "_c4_expected_qty"):
            delattr(row, "_c4_expected_qty")
        if hasattr(row, "_c4_expected_rate"):
            delattr(row, "_c4_expected_rate")
        if hasattr(row, "_c4_role"):
            delattr(row, "_c4_role")

    # Price the finished good immediately so the draft opened by the user
    # already reflects material valuation + related operation cost.
    from c4factory.c4_manufacturing.stock_entry_hooks import (
        _set_manufacture_finished_item_valuation,
    )

    _set_manufacture_finished_item_valuation(se, wo)

    return se.as_dict()


def _get_transferred_items_to_wip(work_order_name, wip_warehouse):
    """
    Get unconsumed Pick List material moved INTO WIP for this Work Order.

    Returns list of dicts:
    [
        {"item_code": ..., "stock_uom": ..., "qty": ..., "custom_pick_list_item": ...},
        ...
    ]
    """
    transfer_entries = frappe.get_all(
        "Stock Entry",
        filters={
            "work_order": work_order_name,
            "docstatus": 1,
            "stock_entry_type": "Material Transfer for Manufacture",
        },
        fields=[
            "name",
            "custom_is_additional_material",
            "custom_continuous_manufacture_transfer",
        ],
    )

    if not transfer_entries:
        return []

    se_names = [entry.name for entry in transfer_entries]
    additional_se_names = {
        entry.name
        for entry in transfer_entries
        if flt(entry.get("custom_is_additional_material"))
    }
    continuous_se_names = {
        entry.name
        for entry in transfer_entries
        if flt(entry.get("custom_continuous_manufacture_transfer"))
    }

    sed_meta = frappe.get_meta("Stock Entry Detail")
    fields = [
        "name",
        "parent",
        "item_code",
        "stock_uom",
        "qty",
        "transfer_qty",
        "basic_rate",
        "valuation_rate",
        "basic_amount",
        "amount",
        "custom_pick_list_item",
    ]
    if sed_meta.has_field("custom_work_order_item"):
        fields.append("custom_work_order_item")

    rows = frappe.get_all(
        "Stock Entry Detail",
        filters={
            "parent": ["in", se_names],
            "t_warehouse": wip_warehouse,
        },
        fields=fields,
        order_by="creation asc, idx asc",
    )

    if not rows:
        return []

    transferred_by_pl_item = {}
    for r in rows:
        pl_item = r.get("custom_pick_list_item")
        is_additional = r.get("parent") in additional_se_names
        is_continuous = r.get("parent") in continuous_se_names
        if not pl_item and not is_additional and not is_continuous:
            continue

        # Additional and continuous material have no Pick List Item row by
        # design. Keep each channel distinct for consumption and costing.
        if is_additional:
            material_key = "__additional_material__"
        elif is_continuous:
            material_key = (
                f"__continuous__:{r.get('custom_work_order_item') or r.name}"
            )
        else:
            material_key = pl_item
        key = (material_key, r["item_code"], r["stock_uom"])
        transferred_by_pl_item.setdefault(key, {"qty": 0.0, "amount": 0.0})

        row_qty = flt(r.get("transfer_qty")) or flt(r.get("qty"))
        if row_qty <= 0:
            continue

        rate = flt(r.get("valuation_rate")) or flt(r.get("basic_rate"))
        amount = flt(r.get("basic_amount")) or flt(r.get("amount"))
        if amount <= 0 and row_qty > 0 and rate > 0:
            amount = row_qty * rate

        transferred_by_pl_item[key]["qty"] += row_qty
        transferred_by_pl_item[key]["amount"] += amount
        transferred_by_pl_item[key]["custom_pick_list_item"] = pl_item
        transferred_by_pl_item[key]["custom_work_order_item"] = r.get(
            "custom_work_order_item"
        )

    consumed_qty_map = _get_consumed_pick_list_material_qty(work_order_name)
    legacy_consumed_by_item = _get_legacy_consumed_material_qty(work_order_name)
    aggregated = {}
    ordered_transfers = sorted(
        transferred_by_pl_item.items(),
        key=lambda entry: (
            0
            if entry[0][0] == "__additional_material__"
            else 1
            if entry[0][0].startswith("__continuous__:")
            else 2
        ),
    )
    for key, values in ordered_transfers:
        pl_item, item_code, _stock_uom = key
        total_qty = flt(values["qty"])
        consumed_qty = (
            0.0
            if pl_item == "__additional_material__"
            else flt(consumed_qty_map.get((pl_item, item_code)))
        )
        remaining_qty = max(total_qty - consumed_qty, 0.0)
        legacy_consumed_qty = min(
            flt(legacy_consumed_by_item.get(item_code)), remaining_qty
        )
        if legacy_consumed_qty > 0:
            remaining_qty -= legacy_consumed_qty
            legacy_consumed_by_item[item_code] -= legacy_consumed_qty
        if remaining_qty <= 0:
            continue

        total_amount = flt(values["amount"])
        remaining_amount = (
            total_amount * remaining_qty / total_qty
            if total_qty > 0
            else 0.0
        )

        aggregated[key] = {
            "qty": remaining_qty,
            "amount": remaining_amount,
            "custom_pick_list_item": values.get("custom_pick_list_item"),
            "custom_work_order_item": values.get("custom_work_order_item"),
        }

    result = []
    for (_pl_item, item_code, stock_uom), values in aggregated.items():
        total_qty = flt(values["qty"])
        if total_qty <= 0:
            continue
        total_amount = flt(values["amount"])
        weighted_rate = (total_amount / total_qty) if total_amount > 0 else 0.0
        result.append({
            "item_code": item_code,
            "stock_uom": stock_uom,
            "qty": total_qty,
            "valuation_rate": weighted_rate,
            "basic_rate": weighted_rate,
            "amount": total_amount,
            "custom_pick_list_item": values.get("custom_pick_list_item"),
            "custom_work_order_item": values.get("custom_work_order_item"),
        })

    return result


def _get_consumed_pick_list_material_qty(work_order_name):
    """Return quantities already consumed by submitted finish entries per PL row."""
    rows = frappe.db.sql(
        """
        SELECT
            sed.custom_pick_list_item,
            sed.item_code,
            COALESCE(SUM(ABS(sed.qty)), 0) AS qty
        FROM `tabStock Entry Detail` sed
        INNER JOIN `tabStock Entry` se
            ON se.name = sed.parent
        WHERE
            se.docstatus = 1
            AND se.work_order = %s
            AND (se.stock_entry_type IN ('Manufacture', 'Process Loss')
                 OR se.purpose IN ('Manufacture', 'Process Loss'))
            AND COALESCE(sed.is_finished_item, 0) = 0
            AND COALESCE(sed.is_scrap_item, 0) = 0
            AND sed.custom_pick_list_item IS NOT NULL
            AND sed.custom_pick_list_item != ''
        GROUP BY sed.custom_pick_list_item, sed.item_code
        """,
        (work_order_name,),
        as_dict=True,
    )

    return {
        (row.custom_pick_list_item, row.item_code): flt(row.qty)
        for row in rows
    }


def _get_legacy_consumed_material_qty(work_order_name):
    """
    Return consumed quantities from older finish entries that did not save PL row links.
    """
    rows = frappe.db.sql(
        """
        SELECT
            sed.item_code,
            COALESCE(SUM(ABS(sed.qty)), 0) AS qty
        FROM `tabStock Entry Detail` sed
        INNER JOIN `tabStock Entry` se
            ON se.name = sed.parent
        WHERE
            se.docstatus = 1
            AND se.work_order = %s
            AND (se.stock_entry_type IN ('Manufacture', 'Process Loss')
                 OR se.purpose IN ('Manufacture', 'Process Loss'))
            AND COALESCE(sed.is_finished_item, 0) = 0
            AND COALESCE(sed.is_scrap_item, 0) = 0
            AND (sed.custom_pick_list_item IS NULL OR sed.custom_pick_list_item = '')
        GROUP BY sed.item_code
        """,
        (work_order_name,),
        as_dict=True,
    )

    return {row.item_code: flt(row.qty) for row in rows}
