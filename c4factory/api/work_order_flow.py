from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, nowdate

from c4factory.c4_manufacturing.work_order_hooks import get_default_source_warehouse


# ================================================================
# Work Order helpers and hooks
# ================================================================


def _get_wo_items(wo_doc):
    """Return the Work Order items table (v15 uses required_items)."""
    return wo_doc.get("required_items") or wo_doc.get("items") or []


def _update_wo_item_balances(wo_doc):
    """
    Update internal balance fields for Work Order items only.
    Does NOT write to the database directly.
    """
    total_transferred = 0.0
    total_consumed = 0.0

    for row in _get_wo_items(wo_doc):
        req = flt(row.get("required_qty"))
        transferred = flt(row.get("transferred_qty"))
        consumed = flt(row.get("consumed_qty"))

        total_transferred += transferred
        total_consumed += consumed

        # Optional custom fields on Work Order Item
        if hasattr(row, "custom_balance_to_transfer"):
            row.custom_balance_to_transfer = max(req - transferred, 0.0)
        if hasattr(row, "custom_balance_to_consume"):
            row.custom_balance_to_consume = max(req - consumed, 0.0)

    # Optional summary fields on Work Order header
    if hasattr(wo_doc, "custom_total_transferred_qty"):
        wo_doc.custom_total_transferred_qty = total_transferred
    if hasattr(wo_doc, "custom_total_consumed_qty"):
        wo_doc.custom_total_consumed_qty = total_consumed


def _update_wo_status(wo_doc):
    """Call set_status if available."""
    try:
        if hasattr(wo_doc, "set_status"):
            wo_doc.set_status()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "C4Factory: update_wo_status error")


def on_work_order_validate(doc, method=None):
    """Hook: Work Order.validate"""
    _update_wo_item_balances(doc)


def on_work_order_submit(doc, method=None):
    """Hook: Work Order.on_submit"""
    _update_wo_item_balances(doc)
    _update_wo_status(doc)


# ================================================================
# Pick List balance map
# ================================================================


def _get_pick_list_balances_map(pl_doc_or_name):
    """
    Build a balance map for each Pick List Item row.

    Returns dict:
      {
        "<pl_item_name>": {
            "pl_qty": float,
            "transferred": float,
            "balance": float,
            "item_code": str,
            "item_name": str,
        },
        ...
      }
    """
    if isinstance(pl_doc_or_name, str):
        pl = frappe.get_doc("Pick List", pl_doc_or_name)
    else:
        pl = pl_doc_or_name

    locations = pl.get("locations") or []
    if not locations:
        return {}

    result = {}

    # Base quantities from Pick List
    for row in locations:
        # Prefer custom_pl_qty if present, otherwise use qty
        pl_qty = flt(getattr(row, "custom_pl_qty", None)) or flt(row.get("qty"))
        result[row.name] = {
            "pl_qty": pl_qty,
            "transferred": 0.0,
            "balance": pl_qty,
            "item_code": row.item_code,
            "item_name": row.item_name,
        }

    # Sum transferred qty from submitted Stock Entry Detail
    # using custom_pick_list_item field.
    transferred_rows = frappe.db.sql(
        """
        SELECT sed.custom_pick_list_item, COALESCE(SUM(sed.qty), 0) AS total_qty
        FROM `tabStock Entry Detail` sed
        INNER JOIN `tabStock Entry` se ON se.name = sed.parent
        WHERE se.docstatus = 1
          AND se.pick_list = %(pick_list)s
          AND COALESCE(se.custom_is_additional_material, 0) = 0
          AND sed.custom_pick_list_item IS NOT NULL
        GROUP BY sed.custom_pick_list_item
        """,
        {"pick_list": pl.name},
        as_dict=True,
    )

    for row in transferred_rows:
        pl_item_name = row.custom_pick_list_item
        if pl_item_name in result:
            transferred = flt(row.total_qty)
            result[pl_item_name]["transferred"] = transferred

    # Backward compatibility path:
    # some Stock Entries are linked to Pick List only at header level
    # (se.pick_list) without row-level custom_pick_list_item.
    # In that case, distribute unlinked transferred qty by item_code
    # across matching Pick List rows in row order.
    unlinked_rows = frappe.db.sql(
        """
        SELECT sed.item_code, COALESCE(SUM(sed.qty), 0) AS total_qty
        FROM `tabStock Entry Detail` sed
        INNER JOIN `tabStock Entry` se ON se.name = sed.parent
        WHERE se.docstatus = 1
          AND se.pick_list = %(pick_list)s
          AND COALESCE(se.custom_is_additional_material, 0) = 0
          AND (sed.custom_pick_list_item IS NULL OR sed.custom_pick_list_item = '')
        GROUP BY sed.item_code
        """,
        {"pick_list": pl.name},
        as_dict=True,
    )

    rows_by_item_code = {}
    for row in locations:
        if row.name not in result:
            continue
        rows_by_item_code.setdefault(row.item_code, []).append(row.name)

    for row in unlinked_rows:
        item_code = row.item_code
        remaining = flt(row.total_qty)
        if remaining <= 0:
            continue

        for pl_item_name in rows_by_item_code.get(item_code, []):
            info = result.get(pl_item_name)
            if not info:
                continue

            free_qty = max(flt(info["pl_qty"]) - flt(info["transferred"]), 0.0)
            if free_qty <= 0:
                continue

            alloc = min(remaining, free_qty)
            info["transferred"] = flt(info["transferred"]) + alloc
            remaining -= alloc

            if remaining <= 0.000001:
                break

    for info in result.values():
        info["balance"] = max(flt(info["pl_qty"]) - flt(info["transferred"]), 0.0)

    return result


def _update_pick_list_status_from_db(pick_list_name: str):
    """
    Update Pick List.status based on current balances:
    - If all balances are zero -> "Completed"
    - Otherwise -> "Open"
    """
    if not pick_list_name:
        return

    pl = frappe.get_doc("Pick List", pick_list_name)
    if pl.docstatus == 2:
        new_status = "Cancelled"
    elif flt(pl.get("custom_manually_completed")):
        new_status = "Completed"
    else:
        balances = _get_pick_list_balances_map(pl)
        has_balance = any(
            flt(info["balance"]) > 0.000001 for info in balances.values()
        )
        new_status = "Open" if has_balance else "Completed"

    try:
        frappe.db.set_value("Pick List", pl.name, "status", new_status)
        pl.status = new_status
    except Exception:
        frappe.log_error(
            frappe.get_traceback(), "C4Factory: update_pick_list_status_from_db error"
        )


# ================================================================
# Public API: get_pick_list_balance_rows
# ================================================================


@frappe.whitelist()
def get_pick_list_balance_rows(pick_list: str):
    """
    Return rows for the partial transfer dialog.

    Each row:
      {
        "pl_item_name": str,
        "item_code": str,
        "item_name": str,
        "balance_qty": float
      }
    """
    if not pick_list:
        frappe.throw(_("Pick List is required"))

    pl = frappe.get_doc("Pick List", pick_list)
    if pl.docstatus != 1:
        frappe.throw(_("Pick List {0} must be submitted").format(pl.name))

    balances = _get_pick_list_balances_map(pl)
    rows = []

    for pl_item_name, info in balances.items():
        balance = flt(info["balance"])
        if balance <= 0.000001:
            continue

        rows.append(
            {
                "pl_item_name": pl_item_name,
                "item_code": info.get("item_code"),
                "item_name": info.get("item_name"),
                "balance_qty": balance,
            }
        )

    return rows


@frappe.whitelist()
def complete_pick_list(pick_list: str) -> dict:
    """
    Operationally finish a submitted Pick List without waiving its remaining balance.

    Actual Stock Entry quantities are not changed. This only closes the
    untransferred remainder remains available for later partial transfers.
    """
    if not pick_list:
        frappe.throw(_("Pick List is required"))

    pl = frappe.get_doc("Pick List", pick_list)
    pl.check_permission("write")

    if pl.docstatus != 1:
        frappe.throw(_("Pick List {0} must be submitted").format(pl.name))

    if not pl.get("work_order"):
        frappe.throw(
            _("Pick List {0} is not linked to a Work Order").format(pl.name)
        )

    if not pl.meta.has_field("custom_manually_completed"):
        frappe.throw(
            _(
                "Manual Pick List completion is not installed. "
                "Please run bench migrate and try again."
            )
        )

    frappe.db.set_value(
        "Pick List",
        pl.name,
        {
            "custom_manually_completed": 1,
            "status": "Completed",
        },
    )
    _recompute_wo_material_transfer_from_pls(pl.work_order)

    return {
        "pick_list": pl.name,
        "status": "Completed",
        "work_order": pl.work_order,
    }


@frappe.whitelist()
def make_additional_material_stock_entry(pick_list: str) -> dict:
    """
    Return an unsaved Additional Material transfer for this Pick List's Work Order.

    Validation keeps the originating Pick List, Work Order, transfer type, and
    WIP target fixed when the user saves or submits the Stock Entry.
    """
    if not pick_list:
        frappe.throw(_("Pick List is required"))

    pl = frappe.get_doc("Pick List", pick_list)
    pl.check_permission("read")

    if pl.docstatus != 1:
        frappe.throw(_("Pick List {0} must be submitted").format(pl.name))

    if not pl.get("work_order"):
        frappe.throw(
            _("Pick List {0} is not linked to a Work Order").format(pl.name)
        )

    if not frappe.has_permission("Stock Entry", "create"):
        frappe.throw(
            _("You do not have permission to create a Stock Entry"),
            frappe.PermissionError,
        )

    wo = frappe.get_doc("Work Order", pl.work_order)
    _validate_work_order_for_additional_material(wo)

    required_custom_fields = {
        "Stock Entry": {
            "custom_is_additional_material",
            "custom_additional_material_pick_list",
        },
        "Stock Entry Detail": {
            "custom_work_order_item",
            "custom_additional_required_qty",
            "custom_additional_transferred_qty_applied",
        },
        "Work Order Item": {"custom_additional_material_qty"},
    }
    fields_missing = any(
        not frappe.get_meta(doctype).has_field(fieldname)
        for doctype, fieldnames in required_custom_fields.items()
        for fieldname in fieldnames
    )
    if fields_missing:
        frappe.throw(
            _(
                "Additional Material is not installed. "
                "Please run bench migrate and try again."
            )
        )

    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = "Material Transfer for Manufacture"
    se.purpose = "Material Transfer for Manufacture"
    se.company = wo.company
    se.work_order = wo.name
    se.pick_list = pl.name
    se.custom_is_additional_material = 1
    se.custom_additional_material_pick_list = pl.name

    if se.meta.has_field("custom_work_order"):
        se.custom_work_order = wo.name
    if se.meta.has_field("custom_pick_list"):
        se.custom_pick_list = pl.name
    if se.meta.has_field("to_warehouse"):
        se.to_warehouse = wo.wip_warehouse

    return se.as_dict()


def _validate_work_order_for_additional_material(wo) -> None:
    if wo.docstatus != 1:
        frappe.throw(_("Work Order {0} must be submitted").format(wo.name))

    if (wo.get("status") or "").strip() in {
        "Stopped",
        "Closed",
        "Completed",
        "Cancelled",
    }:
        frappe.throw(
            _(
                "Additional material cannot be added to Work Order {0} "
                "with status {1}"
            ).format(wo.name, wo.status)
        )

    if not wo.get("wip_warehouse"):
        frappe.throw(_("Work Order {0} has no WIP Warehouse set").format(wo.name))
# ---------------------------------------------------------
# Pick List – validate hook (called from hooks.py)
# ---------------------------------------------------------

def on_pick_list_validate(doc, method=None):
    """
    Keep eligible Pick List rows aligned with Work Order required items and
    their Item Group warehouse rules.
    """
    action = getattr(doc, "_action", None)
    if doc.docstatus != 0 or action in {"update_after_submit", "cancel"}:
        return

    set_pick_list_warehouses_from_item_group(doc)
    sync_pick_list_items_from_work_order(doc)
    validate_pick_list_matches_work_order(doc)


def sync_pick_list_items_from_work_order(doc) -> None:
    """Restore eligible Work Order material rows without checking availability."""
    work_order = doc.get("work_order")
    if not work_order:
        return

    if doc.meta.has_field("pick_manually"):
        doc.pick_manually = 1

    from c4factory.api.work_order_pick_list import (
        _get_pick_list_source_warehouse,
        get_remaining_pick_list_qty,
        is_continuous_manufacture_item,
    )

    wo = frappe.get_doc("Work Order", work_order)
    pick_qty = (
        flt(doc.get("qty_of_finished_goods_item"))
        or flt(doc.get("qty_of_finished_goods"))
        or flt(doc.get("for_qty"))
        or max(flt(wo.qty) - flt(wo.produced_qty), 0.0)
    )
    remaining_qty = get_remaining_pick_list_qty(wo, exclude_pick_list=doc.name)
    if pick_qty > remaining_qty + 0.000001:
        frappe.throw(
            _(
                "Pick List quantity {0} exceeds the unallocated Work Order "
                "quantity {1}. Open and Completed Pick Lists are already reserved."
            ).format(pick_qty, remaining_qty)
        )
    qty_scale = pick_qty / (flt(wo.qty) or 1.0)

    expected = {}
    continuous_production = bool(flt(doc.get("custom_continuous_production")))
    item_group_cache = {}
    continuous_group_cache = {}
    for wo_row in _get_wo_items(wo):
        item_code = wo_row.get("item_code")
        required_qty = flt(wo_row.get("required_qty") or wo_row.get("qty"))
        row_qty = required_qty * qty_scale
        if not item_code or row_qty <= 0:
            continue

        row_is_continuous = is_continuous_manufacture_item(
            wo_row,
            item_group_cache=item_group_cache,
            continuous_group_cache=continuous_group_cache,
        )
        if row_is_continuous != continuous_production:
            continue

        stock_uom = (
            wo_row.get("stock_uom")
            or wo_row.get("uom")
            or frappe.db.get_value("Item", item_code, "stock_uom")
        )
        expected[wo_row.name] = {
            "item_code": item_code,
            "item": item_code,
            "item_name": (
                wo_row.get("item_name")
                or frappe.db.get_value("Item", item_code, "item_name")
                or item_code
            ),
            "uom": stock_uom,
            "stock_uom": stock_uom,
            "conversion_factor": 1,
            "qty": row_qty,
            "stock_qty": row_qty,
            "qty_in_stock_uom": row_qty,
            "warehouse": _get_pick_list_source_warehouse(wo, wo_row),
            "work_order": wo.name,
            "custom_pl_qty": row_qty,
            "custom_work_order_item": wo_row.name,
            "custom_wip_warehouse": wo.get("wip_warehouse"),
        }

    current_by_wo_item = {}
    unused_expected = set(expected)
    for row in list(doc.get("locations") or []):
        wo_item = row.get("custom_work_order_item")
        if not wo_item or wo_item not in unused_expected:
            candidates = [
                name
                for name in unused_expected
                if expected[name]["item_code"] == row.get("item_code")
                and (expected[name].get("warehouse") or "")
                == (row.get("warehouse") or "")
            ]
            if not candidates:
                candidates = [
                    name
                    for name in unused_expected
                    if expected[name]["item_code"] == row.get("item_code")
                ]
            wo_item = candidates[0] if len(candidates) == 1 else None

        if not wo_item:
            doc.remove(row)
            continue

        row.custom_work_order_item = wo_item
        current_by_wo_item[wo_item] = row
        unused_expected.discard(wo_item)

    for wo_item, values in expected.items():
        row = current_by_wo_item.get(wo_item)
        if not row:
            row = doc.append("locations", {})
        for fieldname, value in values.items():
            if row.meta.has_field(fieldname) or fieldname == "custom_work_order_item":
                row.set(fieldname, value)


def validate_pick_list_matches_work_order(doc) -> None:
    """
    Keep a manufacturing Pick List identical to its eligible Work Order rows.

    Availability is deliberately not considered: zero-stock materials remain
    on the Pick List so shortages stay visible and can be transferred later.
    The Continuous Production flag selects the matching Work Order material channel.
    """
    work_order = doc.get("work_order")
    if not work_order:
        return

    wo = frappe.get_doc("Work Order", work_order)
    wo_rows = _get_wo_items(wo)
    if not wo_rows:
        frappe.throw(_("Work Order {0} has no required items").format(wo.name))

    pick_qty = (
        flt(doc.get("qty_of_finished_goods_item"))
        or flt(doc.get("qty_of_finished_goods"))
        or flt(doc.get("for_qty"))
    )
    if pick_qty <= 0:
        pick_qty = max(flt(wo.qty) - flt(wo.produced_qty), 0.0)

    qty_scale = pick_qty / (flt(wo.qty) or 1.0)
    from c4factory.api.work_order_pick_list import is_continuous_manufacture_item

    expected = {}
    continuous_production = bool(flt(doc.get("custom_continuous_production")))
    item_group_cache = {}
    continuous_group_cache = {}
    for wo_row in wo_rows:
        item_code = wo_row.get("item_code")
        required_qty = flt(wo_row.get("required_qty") or wo_row.get("qty"))
        row_qty = required_qty * qty_scale
        if not item_code or row_qty <= 0:
            continue

        row_is_continuous = is_continuous_manufacture_item(
            wo_row,
            item_group_cache=item_group_cache,
            continuous_group_cache=continuous_group_cache,
        )
        if row_is_continuous != continuous_production:
            continue

        expected[wo_row.name] = {
            "item_code": item_code,
            "qty": row_qty,
        }

    actual = {}
    for row in doc.get("locations") or []:
        wo_item = row.get("custom_work_order_item")
        if not wo_item or wo_item not in expected:
            frappe.throw(
                _(
                    "Pick List items are fetched from Work Order {0} and "
                    "cannot be added or replaced manually"
                ).format(wo.name)
            )
        if wo_item in actual:
            frappe.throw(
                _("Work Order Item {0} appears more than once in the Pick List").format(
                    wo_item
                )
            )

        expected_row = expected[wo_item]
        actual_qty = flt(row.get("custom_pl_qty")) or flt(row.get("qty"))
        if row.get("item_code") != expected_row["item_code"] or abs(
            actual_qty - expected_row["qty"]
        ) > 0.000001:
            frappe.throw(
                _(
                    "Item and quantity for {0} must match Work Order {1}. "
                    "Edit the Work Order Required Items instead."
                ).format(expected_row["item_code"], wo.name)
            )

        if abs(flt(row.get("qty")) - expected_row["qty"]) > 0.000001:
            frappe.throw(
                _("Pick List quantity for {0} cannot be changed").format(
                    expected_row["item_code"]
                )
            )
        actual[wo_item] = row

    missing = [row for row in expected if row not in actual]
    if missing:
        frappe.throw(
            _(
                "Required materials cannot be removed from this Pick List. "
                "All eligible Work Order items must remain, including unavailable items."
            )
        )


def set_pick_list_warehouses_from_item_group(doc):
    """
    For each Pick List Item, prefer Item Group Defaults -> default_warehouse.

    ERPNext may prefill Pick List Item.warehouse from stock availability. For
    manufacturing picks we want the warehouse mapping to follow the Item Group
    defaults, matching the Work Order source warehouse behavior.
    """
    locations = doc.get("locations") or []
    if not locations:
        return

    company = doc.get("company")
    item_group_cache = {}
    warehouse_cache = {}

    for row in locations:
        item_code = row.get("item_code")
        if not item_code:
            continue

        item_group = row.get("item_group") or item_group_cache.get(item_code)
        if item_group is None:
            item_group = frappe.db.get_value("Item", item_code, "item_group")
            item_group_cache[item_code] = item_group

        if not item_group:
            continue

        cache_key = (item_code, item_group, company)
        warehouse = warehouse_cache.get(cache_key)
        if warehouse is None:
            warehouse = get_default_source_warehouse(
                item_code=item_code,
                item_group=item_group,
                company=company,
            )
            warehouse_cache[cache_key] = warehouse

        if warehouse:
            row.warehouse = warehouse


# ================================================================
# Public API: create partial Stock Entry from Pick List
# ================================================================


@frappe.whitelist()
def make_partial_stock_entry_from_pick_list(pick_list: str, items_json: str) -> str:
    """
    Create a Material Transfer for Manufacture Stock Entry from a Pick List
    using partial quantities per row (<= balance).

    - Uses balance calculated by _get_pick_list_balances_map
    - Uses only the WIP Warehouse from the Work Order
    """
    if not pick_list:
        frappe.throw(_("Pick List is required"))

    pl = frappe.get_doc("Pick List", pick_list)
    pl.check_permission("read")
    if pl.docstatus != 1:
        frappe.throw(_("Pick List {0} must be submitted").format(pl.name))

    wo_name = pl.get("work_order")
    if not wo_name:
        frappe.throw(_("Pick List {0} is not linked to a Work Order").format(pl.name))

    wo = frappe.get_doc("Work Order", wo_name)
    if wo.docstatus != 1 or wo.get("status") in {
        "Stopped",
        "Closed",
        "Completed",
        "Cancelled",
    }:
        frappe.throw(
            _("Material cannot be transferred while Work Order status is {0}.").format(
                wo.get("status")
            )
        )

    if not wo.get("wip_warehouse"):
        frappe.throw(_("Work Order {0} has no WIP Warehouse set").format(wo.name))

    continuous_production = bool(flt(pl.get("custom_continuous_production")))
    if not frappe.has_permission("Stock Entry", "create"):
        frappe.throw(_("You do not have permission to create Stock Entries."))
    if continuous_production and not frappe.has_permission("Stock Entry", "submit"):
        frappe.throw(_("You do not have permission to submit Stock Entries."))

    # Parse items from dialog
    items = frappe.parse_json(items_json) or []
    if not items:
        frappe.throw(_("No items were selected to transfer"))

    balances = _get_pick_list_balances_map(pl)
    pl_rows_by_name = {row.name: row for row in (pl.get("locations") or [])}

    # Create Stock Entry header
    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = "Material Transfer for Manufacture"
    se.company = pl.company
    se.pick_list = pl.name

    # Link to Work Order if field exists
    if hasattr(se, "work_order"):
        se.work_order = wo.name

    # Build items
    for row in items:
        pl_item_name = row.get("pl_item_name")
        qty = flt(row.get("qty"))

        if not pl_item_name or qty <= 0:
            continue

        pl_row = pl_rows_by_name.get(pl_item_name)
        if not pl_row:
            frappe.throw(_("Invalid Pick List Item {0}").format(pl_item_name))

        info = balances.get(pl_item_name) or {
            "pl_qty": 0.0,
            "transferred": 0.0,
            "balance": 0.0,
        }
        balance = flt(info["balance"])

        if qty > balance + 1e-9:
            frappe.throw(
                _("Item {0}: Transfer Qty ({1}) cannot exceed PL Balance ({2})").format(
                    pl_row.item_code, qty, balance
                )
            )

        warehouse_allocations = _get_source_warehouse_allocations(
            item_code=pl_row.item_code,
            warehouse=pl_row.warehouse,
            company=pl.company,
            qty=qty,
        )
        for source_warehouse, allocated_qty in warehouse_allocations:
            item = se.append("items", {})
            item.item_code = pl_row.item_code
            item.item_name = pl_row.item_name
            item.uom = pl_row.uom
            item.qty = allocated_qty

            # A configured group warehouse is expanded to leaf warehouses.
            item.s_warehouse = source_warehouse
            item.t_warehouse = wo.wip_warehouse

            # Multiple leaf rows may reference the same Pick List item. Balance
            # calculations deliberately sum those submitted Stock Entry rows.
            try:
                item.custom_pick_list_item = pl_row.name
            except Exception:
                pass
            try:
                item.custom_work_order_item = pl_row.get("custom_work_order_item")
            except Exception:
                pass

    if not se.get("items"):
        frappe.throw(_("No valid items to transfer for Work Order {0}").format(wo.name))

    se.insert(ignore_permissions=True)
    if continuous_production:
        se.submit()
    frappe.db.commit()

    return se.name


def _get_source_warehouse_allocations(
    item_code: str,
    warehouse: str,
    company: str,
    qty: float,
) -> list[tuple[str, float]]:
    """Expand a configured group warehouse into stock-bearing leaf warehouses."""
    details = frappe.db.get_value(
        "Warehouse",
        warehouse,
        ["is_group", "lft", "rgt", "company"],
        as_dict=True,
    )
    if not details or not flt(details.get("is_group")):
        return [(warehouse, qty)]

    leaf_rows = frappe.db.sql(
        """
        SELECT w.name,
               GREATEST(COALESCE(bin.actual_qty, 0), 0) AS available_qty
        FROM `tabWarehouse` w
        LEFT JOIN `tabBin` bin
          ON bin.warehouse = w.name
         AND bin.item_code = %(item_code)s
        WHERE w.lft > %(lft)s
          AND w.rgt < %(rgt)s
          AND COALESCE(w.is_group, 0) = 0
          AND COALESCE(w.disabled, 0) = 0
          AND (%(company)s = '' OR w.company = %(company)s)
        ORDER BY available_qty DESC, w.lft ASC
        """,
        {
            "item_code": item_code,
            "lft": details.lft,
            "rgt": details.rgt,
            "company": company or details.get("company") or "",
        },
        as_dict=True,
    )
    available_total = sum(flt(row.available_qty) for row in leaf_rows)
    if not leaf_rows or available_total + 0.000001 < qty:
        frappe.throw(
            _(
                "Item {0}: group warehouse {1} has only {2} available across "
                "its leaf warehouses, but transfer quantity is {3}."
            ).format(
                frappe.bold(item_code),
                frappe.bold(warehouse),
                available_total,
                qty,
            )
        )

    allocations = []
    remaining = qty
    for row in leaf_rows:
        allocated_qty = min(remaining, flt(row.available_qty))
        if allocated_qty <= 0:
            continue
        allocations.append((row.name, allocated_qty))
        remaining -= allocated_qty
        if remaining <= 0.000001:
            break

    return allocations


@frappe.whitelist()
def create_job_cards_from_pick_list(pick_list: str) -> list[str]:
    """
    Create draft Job Cards for a submitted Pick List using the Pick List's
    finished-goods quantity as the Job Card quantity to manufacture.
    """
    if not pick_list:
        frappe.throw(_("Pick List is required"))

    pl = frappe.get_doc("Pick List", pick_list)
    if pl.docstatus != 1:
        frappe.throw(_("Pick List {0} must be submitted").format(pl.name))

    if not pl.get("work_order"):
        frappe.throw(_("Pick List {0} is not linked to a Work Order").format(pl.name))

    if _is_work_order_operation_disabled(pl.get("work_order")):
        frappe.msgprint(_("Operation is disabled for Work Order {0}.").format(pl.get("work_order")))
        update_pick_list_operation_cost(pl.name)
        frappe.db.commit()
        return []

    job_cards = _ensure_job_cards_for_pick_list(pl)
    if not job_cards:
        frappe.msgprint(_("No new Job Cards were created for Pick List {0}.").format(pl.name))

    update_pick_list_operation_cost(pl.name)
    frappe.db.commit()
    return job_cards


# ================================================================
# Stock Entry hooks (do NOT modify Stock Entry itself)
# ================================================================


def on_stock_entry_submit(doc, method=None):
    """
    Hook: Stock Entry.on_submit

    Do NOT modify or save the Stock Entry document here.
    Only update related Work Order and Pick List.
    """
    pl_name = doc.get("pick_list")
    if pl_name:
        try:
            _update_pick_list_status_from_db(pl_name)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(), "C4Factory: on_stock_entry_submit (Pick List)"
            )

    wo_name = doc.get("work_order")
    if wo_name:
        try:
            _recompute_wo_material_transfer_from_pls(wo_name)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(), "C4Factory: on_stock_entry_submit (WO)"
            )


from c4factory.c4_manufacturing.stock_entry_hooks import recompute_work_order_costing

# ================================================================
# Recompute Work Order.material_transferred_for_manufacturing
# ================================================================


def _recompute_wo_material_transfer_from_pls(wo_name: str):
    """
    Recompute Work Order.material_transferred_for_manufacturing from actual
    submitted material-transfer Stock Entry rows.
    """
    if not wo_name:
        return

    wo = frappe.get_doc("Work Order", wo_name)
    total_for_qty = _get_transferred_production_qty_from_stock_entries(wo_name)

    # Persist transferred qty without updating the document `modified` timestamp
    try:
        frappe.db.set_value(
            "Work Order",
            wo_name,
            "material_transferred_for_manufacturing",
            total_for_qty,
            update_modified=False,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "C4Factory: set material_transferred_for_manufacturing failed")

    # Update in-memory object and recompute balances/status.
    wo.material_transferred_for_manufacturing = total_for_qty
    _update_wo_item_balances(wo)

    # _update_wo_status will persist status (using update_modified=False in override)
    _update_wo_status(wo)

    # Do NOT call wo.save() here to avoid bumping the Work Order `modified`
    # timestamp which causes the client-side "Document has been modified" alert
    # when users have the Work Order open while Stock Entry is submitted.


@frappe.whitelist()
def sync_work_order_material_transfer(wo_name: str) -> float:
    """Manually refresh material_transferred_for_manufacturing for one WO."""
    _recompute_wo_material_transfer_from_pls(wo_name)
    return flt(
        frappe.db.get_value(
            "Work Order", wo_name, "material_transferred_for_manufacturing"
        )
    )


@frappe.whitelist()
def get_work_order_material_progress(wo_name: str) -> dict:
    """Return row-normalized progress for actual submitted material transfers."""
    wo = frappe.get_doc("Work Order", wo_name)
    wo.check_permission("read")
    required_rows = [
        row
        for row in _get_wo_items(wo)
        if row.get("item_code")
        and flt(row.get("required_qty") or row.get("qty")) > 0
    ]
    if not required_rows:
        return {"percent": 0, "transferred_items": 0, "total_items": 0}

    transferred_rows = frappe.db.sql(
        """
        SELECT sed.custom_work_order_item,
               sed.item_code,
               COALESCE(SUM(ABS(sed.qty)), 0) AS transferred_qty
        FROM `tabStock Entry Detail` sed
        INNER JOIN `tabStock Entry` se ON se.name = sed.parent
        WHERE se.work_order = %(work_order)s
          AND se.docstatus = 1
          AND COALESCE(se.custom_is_additional_material, 0) = 0
          AND (se.stock_entry_type = 'Material Transfer for Manufacture'
               OR se.purpose = 'Material Transfer for Manufacture')
          AND COALESCE(sed.is_finished_item, 0) = 0
          AND COALESCE(sed.is_scrap_item, 0) = 0
        GROUP BY sed.custom_work_order_item, sed.item_code
        """,
        {"work_order": wo.name},
        as_dict=True,
    )
    by_work_order_item = {}
    by_item_code = {}
    for row in transferred_rows:
        if row.custom_work_order_item:
            by_work_order_item[row.custom_work_order_item] = (
                flt(by_work_order_item.get(row.custom_work_order_item))
                + flt(row.transferred_qty)
            )
        else:
            by_item_code[row.item_code] = (
                flt(by_item_code.get(row.item_code)) + flt(row.transferred_qty)
            )

    ratios = []
    transferred_items = 0
    for row in required_rows:
        required_qty = flt(row.get("required_qty") or row.get("qty"))
        transferred_qty = flt(by_work_order_item.get(row.name))
        if transferred_qty <= 0:
            transferred_qty = min(flt(by_item_code.get(row.item_code)), required_qty)
            if transferred_qty > 0:
                by_item_code[row.item_code] -= transferred_qty
        if transferred_qty > 0:
            transferred_items += 1
        ratios.append(min(transferred_qty / required_qty, 1.0))

    return {
        "percent": sum(ratios) / len(ratios) * 100,
        "transferred_items": transferred_items,
        "total_items": len(required_rows),
    }


def _get_transferred_production_qty_from_stock_entries(wo_name: str) -> float:
    """
    Convert Pick List and continuous-material transfers to production quantity.

    Example: a Pick List is for 2 finished units, but only half of every row was
    moved by submitted Stock Entries. This returns 1, not the full PL for_qty.

    Manually finishing a Pick List does not waive its remaining material, so
    only submitted Stock Entries contribute coverage. The two Pick Lists from
    one Start request form one allocation and the lower covered channel is
    authoritative when both channels exist.
    """
    rows = frappe.db.sql(
        """
        SELECT
            se.pick_list,
            sed.custom_pick_list_item,
            sed.item_code,
            COALESCE(SUM(ABS(sed.qty)), 0) AS transferred_qty
        FROM `tabStock Entry` se
        INNER JOIN `tabStock Entry Detail` sed
            ON sed.parent = se.name
        WHERE
            se.docstatus = 1
            AND se.work_order = %(wo)s
            AND se.pick_list IS NOT NULL
            AND se.pick_list != ''
            AND COALESCE(se.custom_is_additional_material, 0) = 0
            AND (se.stock_entry_type = 'Material Transfer for Manufacture'
                 OR se.purpose = 'Material Transfer for Manufacture')
            AND COALESCE(sed.is_finished_item, 0) = 0
            AND COALESCE(sed.is_scrap_item, 0) = 0
        GROUP BY se.pick_list, sed.custom_pick_list_item, sed.item_code
        """,
        {"wo": wo_name},
        as_dict=True,
    )

    by_pick_list = {}
    for row in rows:
        by_pick_list.setdefault(row.pick_list, []).append(row)

    pl_meta = frappe.get_meta("Pick List")
    pl_fields = ["name", "for_qty", "docstatus"]
    has_request_id = pl_meta.has_field("custom_continuous_start_request_id")
    has_continuous_flag = pl_meta.has_field("custom_continuous_production")
    if has_request_id:
        pl_fields.append("custom_continuous_start_request_id")
    if has_continuous_flag:
        pl_fields.append("custom_continuous_production")

    all_pick_lists = frappe.get_all(
        "Pick List",
        filters={
            "work_order": wo_name,
            "docstatus": ("<", 2),
        },
        fields=pl_fields,
    )

    pick_list_coverage = {}
    for pick_list in all_pick_lists:
        request_id = pick_list.get("custom_continuous_start_request_id")
        if request_id:
            channel = (
                "continuous"
                if flt(pick_list.get("custom_continuous_production"))
                else "non_continuous"
            )
            pick_list_coverage.setdefault(f"start:{request_id}", {}).setdefault(
                channel, 0.0
            )
        else:
            pick_list_coverage.setdefault(f"pick-list:{pick_list.name}", {"single": 0.0})

        if pick_list.docstatus != 1:
            continue

        pl_name = pick_list.name
        try:
            pl = frappe.get_doc("Pick List", pl_name)
            pl_for_qty = _get_pick_list_finished_goods_qty(pl)
            transfer_rows = by_pick_list.get(pl_name) or []
            if not transfer_rows:
                continue
            row_ratios = _get_pick_list_transfer_ratios(pl, transfer_rows)
            if not row_ratios:
                continue

            covered_qty = min(row_ratios) * pl_for_qty
            if request_id:
                key = f"start:{request_id}"
                pick_list_coverage[key][channel] = max(
                    flt(pick_list_coverage[key].get(channel)), covered_qty
                )
            else:
                pick_list_coverage[f"pick-list:{pl_name}"]["single"] = covered_qty
            _update_pick_list_status_from_db(pl_name)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"C4Factory: transferred production qty failed ({pl_name})",
            )

    # Keep pre-migration Start requests correct. Those requests submitted the
    # continuous channel directly and created only the non-continuous Pick List.
    legacy_continuous_rows = frappe.db.sql(
        """
        SELECT custom_continuous_start_request_id AS request_id,
               COALESCE(SUM(custom_continuous_start_qty), 0) AS covered_qty
        FROM `tabStock Entry`
        WHERE work_order = %(wo)s
          AND docstatus = 1
          AND COALESCE(custom_continuous_manufacture_transfer, 0) = 1
          AND COALESCE(custom_continuous_start_request_id, '') != ''
          AND (stock_entry_type = 'Material Transfer for Manufacture'
               OR purpose = 'Material Transfer for Manufacture')
        GROUP BY custom_continuous_start_request_id
        """,
        {"wo": wo_name},
        as_dict=True,
    )
    for row in legacy_continuous_rows:
        key = f"start:{row.request_id}"
        pick_list_coverage.setdefault(key, {})["continuous"] = flt(row.covered_qty)

    total = 0.0
    for channels in pick_list_coverage.values():
        if "continuous" in channels and "non_continuous" in channels:
            total += min(channels["continuous"], channels["non_continuous"])
        else:
            total += max(channels.values(), default=0.0)

    wo = frappe.get_doc("Work Order", wo_name)
    wo_qty = flt(wo.qty)
    return min(total, wo_qty) if wo_qty > 0 else total


def _get_pick_list_transfer_ratios(pl, transfer_rows) -> list[float]:
    transfers_by_pl_item = {}
    transfers_by_item = {}
    for row in transfer_rows:
        transferred_qty = flt(row.get("transferred_qty"))
        if transferred_qty <= 0:
            continue

        pl_item = row.get("custom_pick_list_item")
        item_code = row.get("item_code")
        if pl_item:
            transfers_by_pl_item[pl_item] = (
                flt(transfers_by_pl_item.get(pl_item)) + transferred_qty
            )
        elif item_code:
            transfers_by_item[item_code] = (
                flt(transfers_by_item.get(item_code)) + transferred_qty
            )

    ratios = []
    for pl_row in pl.get("locations") or []:
        pl_qty = flt(pl_row.get("custom_pl_qty")) or flt(pl_row.get("qty"))
        if pl_qty <= 0:
            continue

        transferred_qty = flt(transfers_by_pl_item.get(pl_row.name))
        if transferred_qty <= 0:
            item_code = pl_row.get("item_code")
            transferred_qty = min(flt(transfers_by_item.get(item_code)), pl_qty)
            if item_code and transferred_qty > 0:
                transfers_by_item[item_code] -= transferred_qty

        ratios.append(min(transferred_qty / pl_qty, 1.0))

    return ratios

# ================================================================
# Stock Entry hooks: keep Pick List + Work Order in sync on cancel
# ================================================================


def on_stock_entry_cancel(doc, method=None):
    """
    Stock Entry.on_cancel hook.

    - Recompute Pick List balances/status and WO transferred qty.
    - Recompute WO costing.
    - Use background job to avoid transaction-timing issues.
    """
    # Recompute costing immediately (safe on cancel)
    if doc.work_order:
        try:
            recompute_work_order_costing(doc.work_order)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(), "C4Factory: on_stock_entry_cancel costing"
            )

    _recompute_links_for_stock_entry_doc(doc)


def on_stock_entry_trash(doc, method=None):
    """Keep Pick List and Work Order state correct when canceled SE is deleted."""
    _recompute_links_for_stock_entry_doc(doc)


def _recompute_links_for_stock_entry_doc(doc) -> None:
    pick_lists, work_orders = _get_stock_entry_related_links(doc)

    for pl in pick_lists:
        try:
            _update_pick_list_status_from_db(pl)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"C4Factory: recompute Stock Entry links (Pick List {pl})",
            )

    for wo in work_orders:
        try:
            _recompute_wo_material_transfer_from_pls(wo)
            recompute_work_order_costing(wo)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"C4Factory: recompute Stock Entry links (WO {wo})",
            )

    try:
        frappe.enqueue(
            "c4factory.api.work_order_flow.recompute_after_stock_entry_links",
            pick_lists=list(pick_lists),
            work_orders=list(work_orders),
            queue="short",
            enqueue_after_commit=True,
        )
    except Exception:
        frappe.log_error(
            frappe.get_traceback(), "C4Factory: Stock Entry link enqueue failed"
        )


def _get_stock_entry_related_links(doc) -> tuple[set[str], set[str]]:
    pick_lists = set()
    work_orders = set()

    if doc.get("pick_list"):
        pick_lists.add(doc.get("pick_list"))
    if doc.get("work_order"):
        work_orders.add(doc.get("work_order"))

    for row in doc.get("items") or []:
        pl_item_name = row.get("custom_pick_list_item")
        if not pl_item_name:
            continue

        pl_name = frappe.db.get_value("Pick List Item", pl_item_name, "parent")
        if not pl_name:
            continue

        pick_lists.add(pl_name)
        wo_name = frappe.db.get_value("Pick List", pl_name, "work_order")
        if wo_name:
            work_orders.add(wo_name)

    return pick_lists, work_orders


def recompute_after_stock_entry(se_name: str):
    """
    Background job.

    - Find all Pick Lists linked to this Stock Entry via the
      custom_pick_list_item link field on Stock Entry Detail.
    - For each Pick List, recompute its balances/status from DB.
    - For each related Work Order, recompute the transferred qty
      from all submitted Pick Lists.
    """
    try:
        se = frappe.get_doc("Stock Entry", se_name)
    except frappe.DoesNotExistError:
        return

    # Only care about Material Transfer for Manufacture
    se_type = (se.get("stock_entry_type") or se.get("purpose") or "").strip()
    if se_type != "Material Transfer for Manufacture":
        return

    pick_lists = set()
    work_orders = set()

    for row in se.get("items") or []:
        pl_item_name = row.get("custom_pick_list_item")
        if not pl_item_name:
            continue

        pl_name = frappe.db.get_value("Pick List Item", pl_item_name, "parent")
        if not pl_name:
            continue

        pick_lists.add(pl_name)

        wo_name = frappe.db.get_value("Pick List", pl_name, "work_order")
        if wo_name:
            work_orders.add(wo_name)

    # 1) Recompute Pick List balances/status from DB
    for pl in pick_lists:
        try:
            _update_pick_list_status_from_db(pl)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"C4Factory: recompute_after_stock_entry (Pick List {pl})",
            )

    # 2) Recompute WO material transferred based on all Pick Lists
    for wo in work_orders:
        try:
            _recompute_wo_material_transfer_from_pls(wo)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"C4Factory: recompute_after_stock_entry (WO {wo})",
            )


def recompute_after_stock_entry_links(pick_lists=None, work_orders=None):
    for pl in pick_lists or []:
        if not frappe.db.exists("Pick List", pl):
            continue
        try:
            _update_pick_list_status_from_db(pl)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"C4Factory: recompute_after_stock_entry_links (PL {pl})",
            )

    for wo in work_orders or []:
        if not frappe.db.exists("Work Order", wo):
            continue
        try:
            _recompute_wo_material_transfer_from_pls(wo)
            recompute_work_order_costing(wo)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"C4Factory: recompute_after_stock_entry_links (WO {wo})",
            )

# ---------------------------------------------------------
# Pick List doc events
# ---------------------------------------------------------

def on_pick_list_submit(doc, method: str | None = None):
    """
    Called from hooks on Pick List submit.

    Refresh Pick List status only. Material transfer is driven by Stock Entries,
    and Job Cards are not auto-created from Pick Lists.
    """
    try:
        _update_pick_list_status_from_db(doc.name)
    except Exception:
        frappe.log_error(
            title="C4Factory: on_pick_list_submit failed",
            message=frappe.get_traceback(),
        )


def _ensure_job_cards_for_pick_list(pl_doc) -> None:
    """
    Create one draft Job Card per Work Order operation for this Pick List.

    The Job Cards are tied to the Pick List through custom_pick_list when that
    custom field exists. They carry the Pick List's production quantity so
    operation cost can be allocated to the same material lot that was picked.
    """
    wo_name = pl_doc.get("work_order")
    if not wo_name:
        return []

    wo = frappe.get_doc("Work Order", wo_name)
    if wo.get("custom_disable_operation"):
        return []

    operations = wo.get("operations") or []
    if not operations:
        return []

    jc_meta = _ensure_job_card_pick_list_meta()
    has_custom_pick_list = jc_meta.has_field("custom_pick_list")
    pick_qty = _get_pick_list_finished_goods_qty(pl_doc)
    created = []

    for op in operations:
        operation = op.get("operation")
        workstation = op.get("workstation")

        filters = {"work_order": wo.name}
        if has_custom_pick_list:
            filters["custom_pick_list"] = pl_doc.name
        if operation and jc_meta.has_field("operation"):
            filters["operation"] = operation
        if workstation and jc_meta.has_field("workstation"):
            filters["workstation"] = workstation

        if frappe.db.exists("Job Card", filters):
            continue

        jc = frappe.new_doc("Job Card")
        _set_if_field(jc, jc_meta, "work_order", wo.name)
        _set_if_field(jc, jc_meta, "company", wo.get("company"))
        _set_if_field(jc, jc_meta, "bom_no", wo.get("bom_no"))
        _set_if_field(jc, jc_meta, "posting_date", nowdate())
        _set_if_field(jc, jc_meta, "project", wo.get("project"))
        _set_if_field(jc, jc_meta, "production_item", wo.get("production_item"))
        _set_if_field(jc, jc_meta, "item_name", wo.get("item_name"))
        _set_if_field(jc, jc_meta, "operation", operation)
        _set_if_field(jc, jc_meta, "workstation", workstation)
        _set_if_field(jc, jc_meta, "workstation_type", op.get("workstation_type"))
        _set_if_field(jc, jc_meta, "wip_warehouse", _get_job_card_wip_warehouse(wo, op))
        _set_if_field(jc, jc_meta, "serial_no", op.get("serial_no"))
        _set_if_field(jc, jc_meta, "for_quantity", pick_qty)
        _set_if_field(jc, jc_meta, "process_loss_qty", 0)
        _set_if_field(jc, jc_meta, "custom_pick_list", pl_doc.name)

        for op_field, jc_field in (
            ("name", "work_order_operation"),
            ("name", "operation_id"),
            ("idx", "sequence_id"),
            ("idx", "operation_row_id"),
            ("time_in_mins", "time_required"),
            ("time_in_mins", "for_time"),
            ("hour_rate", "hour_rate"),
            ("bom", "bom_no"),
        ):
            _set_if_field(jc, jc_meta, jc_field, op.get(op_field))
        _set_if_field(jc, jc_meta, "operation_row_number", op.name)

        if wo.get("transfer_material_against") == "Job Card" and not wo.get("skip_transfer"):
            try:
                jc.get_required_items()
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    f"C4Factory: Job Card required items failed ({pl_doc.name})",
                )

        jc.insert(ignore_permissions=True, ignore_mandatory=True)
        created.append(jc.name)

    return created


def _get_job_card_wip_warehouse(wo, op) -> str | None:
    if not wo.get("skip_transfer") or wo.get("from_wip_warehouse"):
        return wo.get("wip_warehouse") or op.get("wip_warehouse")

    return wo.get("source_warehouse") or op.get("source_warehouse")


def _get_pick_list_finished_goods_qty(pl_doc) -> float:
    return (
        flt(pl_doc.get("qty_of_finished_goods_item"))
        or flt(pl_doc.get("qty_of_finished_goods"))
        or flt(pl_doc.get("for_qty"))
        or flt(pl_doc.get("custom_for_qty"))
        or flt(pl_doc.get("qty"))
        or 1.0
    )


def _set_if_field(doc, meta, fieldname: str, value) -> None:
    if value is not None and meta.has_field(fieldname):
        doc.set(fieldname, value)


def _ensure_job_card_pick_list_meta():
    meta = frappe.get_meta("Job Card")
    if meta.has_field("custom_pick_list"):
        return meta

    try:
        from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

        create_custom_fields(
            {
                "Job Card": [
                    {
                        "fieldname": "custom_pick_list",
                        "label": "Pick List (C4)",
                        "fieldtype": "Link",
                        "options": "Pick List",
                        "insert_after": "work_order",
                        "read_only": 1,
                    }
                ]
            },
            update=True,
        )
        frappe.clear_cache(doctype="Job Card")
        meta = frappe.get_meta("Job Card")
    except Exception:
        frappe.log_error(
            title="C4Factory: ensure Job Card custom_pick_list failed",
            message=frappe.get_traceback(),
        )

    return meta


def update_pick_list_operation_cost(pick_list_name: str | None) -> None:
    if not pick_list_name:
        return

    pl_meta = frappe.get_meta("Pick List")
    if not pl_meta.has_field("custom_operation_cost"):
        return

    wo_name = frappe.db.get_value("Pick List", pick_list_name, "work_order")
    if not wo_name:
        return

    if _is_work_order_operation_disabled(wo_name):
        frappe.db.set_value(
            "Pick List",
            pick_list_name,
            "custom_operation_cost",
            0,
            update_modified=False,
        )
        return

    from c4factory.c4_manufacturing.stock_entry_hooks import (
        _get_work_order_operating_cost_from_job_cards,
    )

    operation_cost = _get_work_order_operating_cost_from_job_cards(
        wo_name, pick_lists={pick_list_name}
    )

    frappe.db.set_value(
        "Pick List",
        pick_list_name,
        "custom_operation_cost",
        operation_cost,
        update_modified=False,
    )


def _is_work_order_operation_disabled(work_order_name: str | None) -> bool:
    if not work_order_name:
        return False

    try:
        return bool(flt(frappe.db.get_value("Work Order", work_order_name, "custom_disable_operation")))
    except Exception:
        return False


def on_pick_list_cancel(doc, method: str | None = None):
    """
    Called from hooks on Pick List cancel.

    Same logic as submit: recompute the status based on linked
    Stock Entries (if any).
    """
    wo_name = doc.get("work_order")
    try:
        _update_pick_list_status_from_db(doc.name)
    except Exception:
        frappe.log_error(
            title="C4Factory: on_pick_list_cancel failed",
            message=frappe.get_traceback(),
        )

    if wo_name:
        try:
            _recompute_wo_material_transfer_from_pls(wo_name)
            recompute_work_order_costing(wo_name)
        except Exception:
            frappe.log_error(
                title="C4Factory: on_pick_list_cancel WO recompute failed",
                message=frappe.get_traceback(),
            )


def on_pick_list_trash(doc, method: str | None = None):
    """Refresh Work Order transfer/costing when a canceled Pick List is deleted."""
    wo_name = doc.get("work_order")
    if not wo_name:
        return

    try:
        _recompute_wo_material_transfer_from_pls(wo_name)
        recompute_work_order_costing(wo_name)
    except Exception:
        frappe.log_error(
            title="C4Factory: on_pick_list_trash WO recompute failed",
            message=frappe.get_traceback(),
        )
