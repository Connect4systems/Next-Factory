from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt


# ============================================================
# Helper: get WO items table regardless of field name
# ============================================================

def _get_wo_items(wo_doc):
    """Return the Work Order items grid (v15 uses required_items)."""
    return wo_doc.get("required_items") or wo_doc.get("items") or []


def _is_material_transfer_for_manufacture(doc) -> bool:
    return "Material Transfer for Manufacture" in {
        (doc.get("stock_entry_type") or "").strip(),
        (doc.get("purpose") or "").strip(),
    }


def _is_manufacture_like_entry(doc) -> bool:
    return bool(
        {
            (doc.get("stock_entry_type") or "").strip(),
            (doc.get("purpose") or "").strip(),
        }
        & {"Manufacture", "Process Loss"}
    )


def get_reserved_draft_finish_qty(
    work_order: str, exclude_stock_entry: str | None = None
) -> float:
    """Return finished quantity reserved by open draft Manufacture entries."""
    return flt(
        frappe.db.sql(
            """
            SELECT COALESCE(SUM(fg_completed_qty), 0)
            FROM `tabStock Entry`
            WHERE work_order = %(work_order)s
              AND docstatus = 0
              AND (stock_entry_type = 'Manufacture' OR purpose = 'Manufacture')
              AND name != %(exclude_stock_entry)s
            """,
            {
                "work_order": work_order,
                "exclude_stock_entry": exclude_stock_entry or "",
            },
        )[0][0]
    )


def validate_transfer_against_draft_finish(doc, method: str | None = None) -> None:
    """Do not change WIP material while a draft Finish allocation is open."""
    if not _is_material_transfer_for_manufacture(doc) or not doc.get("work_order"):
        return

    work_order_status = frappe.db.get_value("Work Order", doc.work_order, "status")
    if work_order_status in {"Completed", "Closed", "Stopped", "Cancelled"}:
        frappe.throw(
            _("Material cannot be transferred while Work Order status is {0}.").format(
                work_order_status
            )
        )

    draft_finish = frappe.db.get_value(
        "Stock Entry",
        {
            "work_order": doc.work_order,
            "docstatus": 0,
            "stock_entry_type": "Manufacture",
        },
        "name",
    )
    if draft_finish:
        frappe.throw(
            _(
                "Material cannot be transferred while draft Finish Stock Entry {0} exists. "
                "Submit or delete that Finish entry first."
            ).format(frappe.bold(draft_finish))
        )


def prevent_allocated_transfer_cancel(doc, method: str | None = None) -> None:
    """Require dependent Finish entries to be cancelled before their transfer."""
    if not _is_material_transfer_for_manufacture(doc):
        return
    source_rows = [row.name for row in doc.get("items") or [] if row.name]
    if not source_rows:
        return
    dependent_finish = frappe.db.sql(
        """
        SELECT se.name
        FROM `tabStock Entry Detail` sed
        INNER JOIN `tabStock Entry` se ON se.name = sed.parent
        WHERE sed.custom_source_transfer_detail IN %(source_rows)s
          AND se.docstatus < 2
          AND (se.stock_entry_type IN ('Manufacture', 'Process Loss')
               OR se.purpose IN ('Manufacture', 'Process Loss'))
        LIMIT 1
        """,
        {"source_rows": tuple(source_rows)},
    )
    if dependent_finish:
        frappe.throw(
            _("Cancel Finish Stock Entry {0} before cancelling this material transfer.").format(
                frappe.bold(dependent_finish[0][0])
            )
        )


def set_additional_material_allocation_qty(doc, method: str | None = None) -> None:
    """Freeze the remaining production coverage for a submitted extra material."""
    if not flt(doc.get("custom_is_additional_material")) or not doc.get("work_order"):
        return
    if flt(doc.get("custom_material_allocation_qty")) > 0:
        return

    wo = frappe.get_doc("Work Order", doc.work_order)
    coverage_qty = max(
        flt(wo.qty)
        - flt(wo.produced_qty)
        - get_reserved_draft_finish_qty(wo.name),
        0.0,
    )
    if coverage_qty <= 0:
        frappe.throw(_("No unfinished quantity remains for this additional material."))
    doc.custom_material_allocation_qty = coverage_qty


def validate_finish_material_allocation(doc, method: str | None = None) -> None:
    """Keep generated Finish rows tied to their exact WIP transfer balances."""
    if not flt(doc.get("custom_uses_finish_allocation")):
        return
    if not _is_manufacture_like_entry(doc) or not doc.get("work_order"):
        frappe.throw(_("Allocated Finish Stock Entry requires a Work Order."))

    wo = frappe.get_doc("Work Order", doc.work_order)
    row_finished_qty = sum(
        abs(flt(row.get("transfer_qty")) or flt(row.get("qty")))
        for row in doc.get("items") or []
        if flt(row.get("is_finished_item"))
        and not flt(row.get("is_scrap_item"))
    )
    finished_qty = row_finished_qty or flt(doc.get("fg_completed_qty"))
    if finished_qty <= 0:
        frappe.throw(_("Finish quantity must be greater than zero."))
    doc.fg_completed_qty = finished_qty
    if hasattr(doc, "manufactured_qty"):
        doc.manufactured_qty = finished_qty
    if hasattr(doc, "for_quantity"):
        doc.for_quantity = finished_qty
    remaining_fg_qty = max(flt(wo.qty) - flt(wo.produced_qty), 0.0)
    is_final_finish = abs(finished_qty - remaining_fg_qty) <= 0.000001
    if bool(flt(doc.get("custom_is_final_finish"))) != is_final_finish:
        frappe.throw(_("Final Finish reconciliation flag does not match the remaining quantity."))

    from c4factory.api.work_order_stock import (
        _get_allocated_mold_cost_rows,
        _get_finish_material_allocations,
        _get_scrap_allocations,
    )

    expected_rows = _get_finish_material_allocations(
        wo,
        finished_qty,
        is_final_finish,
        exclude_finish=doc.name,
    )
    expected = {
        row["source_transfer_detail"]: row
        for row in expected_rows
    }
    actual = {}
    for row in doc.get("items") or []:
        if flt(row.get("is_finished_item")) or flt(row.get("is_scrap_item")):
            continue
        source_detail = row.get("custom_source_transfer_detail")
        if not source_detail or source_detail not in expected:
            frappe.throw(_("Every Finish raw-material row must reference its source transfer row."))
        if row.item_code != expected[source_detail]["item_code"]:
            frappe.throw(_("Finish item must match source transfer item {0}.").format(source_detail))
        row_qty = abs(
            flt(row.get("transfer_qty")) or flt(row.get("qty"))
        )
        expected_rate = flt(
            expected[source_detail].get("valuation_rate")
            or expected[source_detail].get("basic_rate")
        )
        row.valuation_rate = expected_rate
        row.basic_rate = expected_rate
        row.amount = row_qty * expected_rate
        row.basic_amount = row.amount
        if row.meta.has_field("set_basic_rate_manually"):
            row.set_basic_rate_manually = 1
        actual[source_detail] = flt(actual.get(source_detail)) + row_qty

    for source_detail, expected_row in expected.items():
        if abs(flt(actual.get(source_detail)) - flt(expected_row["qty"])) > 0.000001:
            frappe.throw(
                _("Source transfer row {0} must consume quantity {1}.").format(
                    frappe.bold(source_detail), expected_row["qty"]
                )
            )

    expected_scrap = {
        row.item_code: flt(row.qty)
        for row in _get_scrap_allocations(
            wo,
            finished_qty,
            is_final_finish,
            exclude_finish=doc.name,
        )
    }
    actual_scrap = {}
    for row in doc.get("items") or []:
        if flt(row.get("is_scrap_item")):
            actual_scrap[row.item_code] = flt(actual_scrap.get(row.item_code)) + abs(
                flt(row.get("transfer_qty")) or flt(row.get("qty"))
            )
    if set(actual_scrap) != set(expected_scrap) or any(
        abs(flt(actual_scrap.get(item)) - qty) > 0.000001
        for item, qty in expected_scrap.items()
    ):
        frappe.throw(_("Scrap rows must match the Work Order Finish allocation."))

    expected_mold_cost_rows = _get_allocated_mold_cost_rows(
        wo, finished_qty, is_final_finish
    )
    expected_mold_cost = sum(expected_mold_cost_rows.values())
    doc.custom_allocated_mold_cost = expected_mold_cost
    mold_cost_rows = [
        row
        for row in doc.get("additional_costs") or []
        if (row.get("description") or "").strip() == "Mold Cost"
    ]
    actual_mold_cost_rows = {}
    for mold_row in mold_cost_rows:
        actual_mold_cost_rows[mold_row.expense_account] = (
            flt(actual_mold_cost_rows.get(mold_row.expense_account))
            + flt(mold_row.amount)
        )
    mold_rows_match = set(actual_mold_cost_rows) == set(
        expected_mold_cost_rows
    ) and all(
        abs(flt(actual_mold_cost_rows.get(account)) - amount) <= 0.000001
        for account, amount in expected_mold_cost_rows.items()
    )
    if not mold_rows_match:
        # ERPNext clears Additional Costs when rate calculation runs before an
        # incoming warehouse is present. Rebuild these controlled rows so old
        # drafts and client-side omissions repair themselves on save.
        for mold_row in list(mold_cost_rows):
            doc.remove(mold_row)
        for account, amount in expected_mold_cost_rows.items():
            doc.append(
                "additional_costs",
                {
                    "expense_account": account,
                    "description": "Mold Cost",
                    "amount": amount,
                },
            )
        doc.calculate_rate_and_amount(
            reset_outgoing_rate=False,
            raise_error_if_no_rate=False,
        )


def validate_additional_material_transfer(doc, method: str | None = None) -> None:
    """Keep an Additional Material transfer tied to its originating Pick List."""
    if not flt(doc.get("custom_is_additional_material")):
        return

    origin_pick_list = doc.get("custom_additional_material_pick_list")
    if not origin_pick_list:
        frappe.throw(
            _("Additional Material Stock Entry requires an originating Pick List")
        )

    pl = frappe.get_doc("Pick List", origin_pick_list)
    if pl.docstatus != 1:
        frappe.throw(_("Pick List {0} must be submitted").format(pl.name))

    if not pl.get("work_order"):
        frappe.throw(_("Pick List {0} is not linked to a Work Order").format(pl.name))

    from c4factory.api.work_order_flow import (
        _validate_work_order_for_additional_material,
    )

    wo = frappe.get_doc("Work Order", pl.work_order)
    _validate_work_order_for_additional_material(wo)

    sub_pick_list = doc.get("custom_sub_pick_list")
    sub_rows = {}
    sub_balances = {}
    if sub_pick_list:
        sub = frappe.get_doc("Sub Pick List", sub_pick_list)
        if (
            sub.docstatus != 1
            or sub.main_pick_list != pl.name
            or sub.work_order != wo.name
        ):
            frappe.throw(_("Invalid Sub Pick List relationship"))
        sub_rows = {row.name: row for row in sub.items}
        from c4factory.c4factory.doctype.sub_pick_list.sub_pick_list import (
            _get_balances,
        )

        sub_balances = _get_balances(sub)

    doc.stock_entry_type = "Material Transfer for Manufacture"
    doc.purpose = "Material Transfer for Manufacture"
    doc.company = wo.company
    doc.work_order = wo.name
    doc.pick_list = pl.name

    if doc.meta.has_field("custom_work_order"):
        doc.custom_work_order = wo.name
    if doc.meta.has_field("custom_pick_list"):
        doc.custom_pick_list = pl.name
    if doc.meta.has_field("to_warehouse"):
        doc.to_warehouse = wo.wip_warehouse

    requested_by_sub_item = {}
    for row in doc.get("items") or []:
        row.t_warehouse = wo.wip_warehouse
        row.custom_pick_list_item = None
        if sub_pick_list:
            source = sub_rows.get(row.get("custom_sub_pick_list_item"))
            if not source:
                frappe.throw(
                    _("Every Stock Entry row must reference a Sub Pick List Item")
                )
            if (
                row.item_code != source.item_code
                or (row.get("s_warehouse") or "")
                != (source.source_warehouse or "")
            ):
                frappe.throw(
                    _("Stock Entry row must match Sub Pick List item {0}").format(
                        source.item_code
                    )
                )
            row.custom_work_order_item = source.work_order_item
            row_qty = abs(flt(row.get("transfer_qty")) or flt(row.get("qty")))
            requested_by_sub_item[source.name] = (
                flt(requested_by_sub_item.get(source.name)) + row_qty
            )
        if doc.docstatus == 0:
            if (
                row.meta.has_field("custom_work_order_item")
                and not doc.get("custom_sub_pick_list")
            ):
                row.custom_work_order_item = None
            if row.meta.has_field("custom_additional_required_qty"):
                row.custom_additional_required_qty = 0
            if row.meta.has_field("custom_additional_transferred_qty_applied"):
                row.custom_additional_transferred_qty_applied = 0

    for source_name, requested_qty in requested_by_sub_item.items():
        source = sub_rows[source_name]
        balance = flt((sub_balances.get(source_name) or {}).get("balance"))
        if requested_qty > balance + 0.000001:
            frappe.throw(
                _("Item {0}: transfer quantity exceeds balance {1}").format(
                    source.item_code, balance
                )
            )


def set_pick_list_transferred_production_qty(doc, method: str | None = None) -> None:
    """
    Set the production-equivalent quantity for a main Pick List transfer.

    ERPNext updates Work Order.material_transferred_for_manufacturing by summing
    Stock Entry.fg_completed_qty. A partial transfer contributes only the new
    fully covered production quantity across all required PL rows.
    """
    if (
        not _is_material_transfer_for_manufacture(doc)
        or not doc.get("work_order")
        or not doc.get("pick_list")
        or flt(doc.get("custom_is_additional_material"))
    ):
        return

    from c4factory.api.work_order_flow import (
        _get_pick_list_finished_goods_qty,
        _get_pick_list_transfer_ratios,
    )

    pl = frappe.get_doc("Pick List", doc.pick_list)
    submitted_rows = frappe.db.sql(
        """
        SELECT sed.custom_pick_list_item, sed.item_code,
               COALESCE(SUM(ABS(CASE
                   WHEN COALESCE(sed.transfer_qty, 0) != 0
                   THEN sed.transfer_qty ELSE sed.qty
               END)), 0) AS transferred_qty
        FROM `tabStock Entry Detail` sed
        INNER JOIN `tabStock Entry` se ON se.name = sed.parent
        WHERE se.docstatus = 1
          AND se.pick_list = %(pick_list)s
          AND COALESCE(se.custom_is_additional_material, 0) = 0
          AND (se.stock_entry_type = 'Material Transfer for Manufacture'
               OR se.purpose = 'Material Transfer for Manufacture')
        GROUP BY sed.custom_pick_list_item, sed.item_code
        """,
        {"pick_list": pl.name},
        as_dict=True,
    )
    before_ratios = _get_pick_list_transfer_ratios(pl, submitted_rows)

    combined = {}
    for row in submitted_rows:
        key = (row.get("custom_pick_list_item") or "", row.item_code)
        combined[key] = flt(row.transferred_qty)
    for row in doc.get("items") or []:
        key = (row.get("custom_pick_list_item") or "", row.item_code)
        combined[key] = flt(combined.get(key)) + abs(
            flt(row.get("transfer_qty")) or flt(row.get("qty"))
        )

    after_rows = [
        frappe._dict(
            {
                "custom_pick_list_item": key[0],
                "item_code": key[1],
                "transferred_qty": qty,
            }
        )
        for key, qty in combined.items()
    ]
    after_ratios = _get_pick_list_transfer_ratios(pl, after_rows)
    production_qty = _get_pick_list_finished_goods_qty(pl)
    before_qty = min(before_ratios) * production_qty if before_ratios else 0.0
    after_qty = min(after_ratios) * production_qty if after_ratios else 0.0
    doc.fg_completed_qty = max(after_qty - before_qty, 0.0)


def apply_additional_material_to_work_order(doc, method: str | None = None) -> None:
    """Add submitted Additional Material quantities to Work Order requirements."""
    if (
        not flt(doc.get("custom_is_additional_material"))
        or doc.get("custom_sub_pick_list")
    ):
        return

    wo = frappe.get_doc("Work Order", doc.work_order)
    table_field = "required_items" if wo.meta.has_field("required_items") else "items"
    mappings = []

    for stock_row in doc.get("items") or []:
        if flt(stock_row.get("custom_additional_required_qty")) > 0:
            continue
        contribution = _get_stock_row_qty_in_stock_uom(stock_row)
        if contribution <= 0 or not stock_row.get("item_code"):
            continue

        required_row = _find_work_order_required_row(
            wo,
            stock_row.item_code,
            stock_row.get("s_warehouse"),
        )
        if not required_row:
            required_row = _append_work_order_required_row(
                wo,
                table_field,
                stock_row,
            )

        required_row.required_qty = flt(required_row.get("required_qty")) + contribution
        required_row.custom_additional_material_qty = (
            flt(required_row.get("custom_additional_material_qty")) + contribution
        )
        mappings.append((stock_row, required_row, contribution))

    if not mappings:
        return

    _save_submitted_work_order(wo)

    # Persist an exact audit link from each Stock Entry row to the affected
    # Work Order Item. This makes repeated hooks idempotent and cancellation
    # capable of reversing only this Stock Entry's contribution.
    for stock_row, required_row, contribution in mappings:
        frappe.db.set_value(
            "Stock Entry Detail",
            stock_row.name,
            {
                "custom_work_order_item": required_row.name,
                "custom_additional_required_qty": contribution,
            },
            update_modified=False,
        )
        stock_row.custom_work_order_item = required_row.name
        stock_row.custom_additional_required_qty = contribution

    affected_rows = {}
    for stock_row, required_row, _contribution in mappings:
        affected_rows[required_row.name] = (required_row, stock_row)

    for required_row, stock_row in affected_rows.values():
        actual_transferred = _get_actual_transferred_qty(
            wo.name,
            stock_row.item_code,
            stock_row.get("s_warehouse"),
            wo.wip_warehouse,
        )
        current_transferred = flt(required_row.get("transferred_qty"))
        applied_qty = max(actual_transferred - current_transferred, 0.0)
        if applied_qty > 0:
            frappe.db.set_value(
                "Work Order Item",
                required_row.name,
                "transferred_qty",
                current_transferred + applied_qty,
                update_modified=False,
            )

        # Store the adjustment on one source row for exact cancellation.
        source_row = next(
            row
            for row, target, _qty in mappings
            if target.name == required_row.name
        )
        frappe.db.set_value(
            "Stock Entry Detail",
            source_row.name,
            "custom_additional_transferred_qty_applied",
            applied_qty,
            update_modified=False,
        )
        source_row.custom_additional_transferred_qty_applied = applied_qty
        _set_work_order_item_balances(
            required_row.name,
            flt(required_row.get("required_qty")),
            current_transferred + applied_qty,
            flt(required_row.get("consumed_qty")),
        )


def reverse_additional_material_from_work_order(
    doc, method: str | None = None
) -> None:
    """Reverse only the Work Order requirement contributed by this Stock Entry."""
    if (
        not flt(doc.get("custom_is_additional_material"))
        or doc.get("custom_sub_pick_list")
        or not doc.get("work_order")
    ):
        return

    contributions = {}
    source_rows = {}
    applied_transfers = {}
    for stock_row in doc.get("items") or []:
        work_order_item = stock_row.get("custom_work_order_item")
        contribution = flt(stock_row.get("custom_additional_required_qty"))
        if not work_order_item or contribution <= 0:
            continue

        contributions[work_order_item] = (
            flt(contributions.get(work_order_item)) + contribution
        )
        applied_transfers[work_order_item] = (
            flt(applied_transfers.get(work_order_item))
            + flt(stock_row.get("custom_additional_transferred_qty_applied"))
        )
        source_rows.setdefault(work_order_item, stock_row)

    if not contributions:
        return

    wo = frappe.get_doc("Work Order", doc.work_order)
    rows_by_name = {row.name: row for row in _get_wo_items(wo)}

    for row_name, contribution in contributions.items():
        required_row = rows_by_name.get(row_name)
        if not required_row:
            continue

        required_qty = max(flt(required_row.get("required_qty")) - contribution, 0.0)
        additional_qty = max(
            flt(required_row.get("custom_additional_material_qty")) - contribution,
            0.0,
        )
        required_row.required_qty = required_qty
        required_row.custom_additional_material_qty = additional_qty

        stock_row = source_rows[row_name]
        actual_transferred = _get_actual_transferred_qty(
            wo.name,
            stock_row.item_code,
            stock_row.get("s_warehouse"),
            wo.wip_warehouse,
        )
        required_row.transferred_qty = max(
            flt(required_row.get("transferred_qty"))
            - flt(applied_transfers.get(row_name)),
            actual_transferred,
        )

        if required_qty <= 0.000001 and additional_qty <= 0.000001:
            wo.remove(required_row)
        else:
            if hasattr(required_row, "custom_balance_to_transfer"):
                required_row.custom_balance_to_transfer = max(
                    required_qty - flt(required_row.get("transferred_qty")), 0.0
                )
            if hasattr(required_row, "custom_balance_to_consume"):
                required_row.custom_balance_to_consume = max(
                    required_qty - flt(required_row.get("consumed_qty")), 0.0
                )

    _save_submitted_work_order(wo)


def _get_stock_row_qty_in_stock_uom(row) -> float:
    transfer_qty = abs(flt(row.get("transfer_qty")))
    if transfer_qty > 0:
        return transfer_qty
    return abs(flt(row.get("qty")) * (flt(row.get("conversion_factor")) or 1.0))


def _find_work_order_required_row(wo, item_code: str, source_warehouse: str | None):
    for row in _get_wo_items(wo):
        if (
            row.get("item_code") == item_code
            and (row.get("source_warehouse") or "") == (source_warehouse or "")
        ):
            return row
    return None


def _append_work_order_required_row(wo, table_field: str, stock_row):
    item = frappe.get_cached_doc("Item", stock_row.item_code)
    return wo.append(
        table_field,
        {
            "item_code": stock_row.item_code,
            "item_name": stock_row.get("item_name") or item.item_name,
            "description": stock_row.get("description") or item.description,
            "stock_uom": stock_row.get("stock_uom") or item.stock_uom,
            "source_warehouse": stock_row.get("s_warehouse"),
            "required_qty": 0,
            "transferred_qty": 0,
        },
    )


def _save_submitted_work_order(wo) -> None:
    wo.flags.ignore_validate_update_after_submit = True
    wo.flags.ignore_permissions = True
    wo.save(ignore_permissions=True)


def _get_actual_transferred_qty(
    work_order: str,
    item_code: str,
    source_warehouse: str | None,
    wip_warehouse: str | None,
) -> float:
    return flt(
        frappe.db.sql(
            """
            SELECT COALESCE(SUM(
                ABS(CASE
                    WHEN COALESCE(sed.transfer_qty, 0) != 0
                    THEN sed.transfer_qty
                    ELSE sed.qty * COALESCE(NULLIF(sed.conversion_factor, 0), 1)
                END)
            ), 0)
            FROM `tabStock Entry Detail` sed
            INNER JOIN `tabStock Entry` se ON se.name = sed.parent
            WHERE se.docstatus = 1
              AND se.work_order = %(work_order)s
              AND se.stock_entry_type = 'Material Transfer for Manufacture'
              AND sed.item_code = %(item_code)s
              AND COALESCE(sed.s_warehouse, '') = %(source_warehouse)s
              AND COALESCE(sed.t_warehouse, '') = %(wip_warehouse)s
            """,
            {
                "work_order": work_order,
                "item_code": item_code,
                "source_warehouse": source_warehouse or "",
                "wip_warehouse": wip_warehouse or "",
            },
        )[0][0]
    )


def _set_work_order_item_balances(
    row_name: str,
    required_qty: float,
    transferred_qty: float,
    consumed_qty: float,
) -> None:
    values = {}
    meta = frappe.get_meta("Work Order Item")
    if meta.has_field("custom_balance_to_transfer"):
        values["custom_balance_to_transfer"] = max(
            required_qty - transferred_qty, 0.0
        )
    if meta.has_field("custom_balance_to_consume"):
        values["custom_balance_to_consume"] = max(required_qty - consumed_qty, 0.0)
    if values:
        frappe.db.set_value(
            "Work Order Item",
            row_name,
            values,
            update_modified=False,
        )


# ============================================================
# 1) Stock Entry.validate – default WIP target warehouse
# ============================================================

def set_wip_target_warehouse(doc, method: str | None = None) -> None:
    """
        Keep warehouse mapping consistent for manufacturing entries.

        - Material Transfer for Manufacture:
            default missing target warehouse from Work Order.wip_warehouse.
        - Manufacture:
            raw rows must be source-only; finished/scrap rows must be target-only.
    """
    if not (
        _is_material_transfer_for_manufacture(doc)
        or _is_manufacture_like_entry(doc)
    ):
        return

    if not doc.work_order:
        return

    wo = frappe.get_doc("Work Order", doc.work_order)

    if _is_material_transfer_for_manufacture(doc):
        if not wo.wip_warehouse:
            return

        for row in doc.items:
            if not row.t_warehouse:
                row.t_warehouse = wo.wip_warehouse

        return

    # Manufacture only: determine finished quantity robustly.
    # In some flows (e.g. Job Card), header fields may carry the actual qty
    # while row qty is still empty during early validate.
    row_finished_qty = 0.0
    finished_rows = []
    for row in doc.items:
        if flt(row.get("is_finished_item")) == 1 and flt(row.get("is_scrap_item")) != 1:
            finished_rows.append(row)
            row_qty = flt(row.get("qty")) or flt(row.get("transfer_qty"))
            if row_qty < 0:
                row_qty = abs(row_qty)
                row.qty = row_qty
            row_finished_qty += row_qty

    header_finished_qty = (
        flt(getattr(doc, "fg_completed_qty", 0))
        or flt(getattr(doc, "manufactured_qty", 0))
        or flt(getattr(doc, "for_quantity", 0))
    )

    finished_qty = row_finished_qty or header_finished_qty

    # If we have a finished qty at header but row qty is empty, sync first row.
    if row_finished_qty <= 0 and header_finished_qty > 0 and finished_rows:
        finished_rows[0].qty = header_finished_qty
        row_finished_qty = header_finished_qty
        finished_qty = header_finished_qty

    # Hard-fail only before submit when quantity is still truly missing.
    if finished_qty <= 0:
        if method == "before_submit":
            frappe.throw(
                _(
                    "Manufacture Stock Entry only: Finished item quantity is missing. "
                    "Please add a finished item row with Qty greater than 0."
                )
            )
        return

    doc.fg_completed_qty = finished_qty
    if hasattr(doc, "for_quantity"):
        doc.for_quantity = finished_qty
    if hasattr(doc, "manufactured_qty"):
        doc.manufactured_qty = finished_qty

    # Manufacture only: normalize warehouses and provide clear message
    # before ERPNext raises the generic same source/target alert.
    for row in doc.items:
        is_finished = flt(row.get("is_finished_item")) == 1
        is_scrap = flt(row.get("is_scrap_item")) == 1

        if is_finished or is_scrap:
            row.s_warehouse = None
        else:
            row.t_warehouse = None

        if row.s_warehouse and row.t_warehouse and row.s_warehouse == row.t_warehouse:
            frappe.throw(
                _(
                    "Manufacture Stock Entry only: Row {0} has same Source and Target Warehouse. "
                    "Raw material rows must have Source only, while Finished/Scrap rows must have Target only."
                ).format(row.idx)
            )

    # Manufacture only: set finished-item valuation from actual consumed
    # material + allocated Job Card operating cost.
    _set_manufacture_finished_item_valuation(doc, wo)


def _set_manufacture_finished_item_valuation(doc, wo_doc) -> None:
    """
    For Manufacture entries, compute finished-item basic_rate as:
      (raw_consumed_material_cost + allocated_operation_cost) / finished_qty

    - raw cost is taken from raw rows in this Stock Entry.
    - operation cost is taken from actual Job Card totals for the Work Order,
      allocated to this SE by produced quantity share.
    """
    if not _is_manufacture_like_entry(doc):
        return

    finished_rows = []
    raw_material_cost = 0.0
    scrap_material_credit = 0.0
    transferred_rate_map = None
    pick_lists = set()

    for row in doc.items or []:
        is_finished = flt(row.get("is_finished_item")) == 1
        is_scrap = flt(row.get("is_scrap_item")) == 1

        qty = abs(flt(row.get("transfer_qty")) or flt(row.get("qty")))

        if is_finished and not is_scrap:
            finished_rows.append(row)
            continue

        if is_scrap:
            scrap_amount = abs(flt(row.get("basic_amount") or row.get("amount")))
            if scrap_amount <= 0 and qty > 0:
                scrap_rate = _get_stock_entry_row_rate(row)
                if scrap_rate <= 0 and row.get("item_code"):
                    scrap_rate = flt(
                        frappe.get_cached_value("Item", row.item_code, "valuation_rate")
                    )
                scrap_amount = qty * scrap_rate
            scrap_material_credit += scrap_amount
            continue

        pick_list = _get_pick_list_from_stock_entry_row(row)
        if pick_list:
            pick_lists.add(pick_list)

        # Prefer explicit row amount, then the row rate, then the weighted
        # valuation from submitted transfers into WIP for this Work Order.
        amount = abs(flt(row.get("basic_amount") or row.get("amount")))
        if amount <= 0 and qty > 0:
            rate = _get_stock_entry_row_rate(row)
            if rate <= 0 and wo_doc.get("wip_warehouse"):
                if transferred_rate_map is None:
                    transferred_rate_map = _get_transferred_wip_rate_map(
                        wo_doc.name, wo_doc.wip_warehouse
                    )
                rate = transferred_rate_map.get(row.get("item_code"), 0.0)

            if rate > 0:
                amount = qty * rate
                row.basic_rate = rate
                if hasattr(row, "valuation_rate"):
                    row.valuation_rate = rate

        raw_material_cost += amount

    finished_qty = sum(
        abs(flt(r.get("transfer_qty")) or flt(r.get("qty"))) for r in finished_rows
    )
    if finished_qty <= 0:
        return

    wo_qty = max(flt(getattr(wo_doc, "qty", 0)), 0.0)
    wo_produced_before = max(flt(getattr(wo_doc, "produced_qty", 0)), 0.0)
    wo_produced_after = max(wo_produced_before + finished_qty, finished_qty)

    total_op_cost = _get_work_order_operating_cost_from_job_cards(wo_doc.name)
    prior_allocated_op_cost = 0.0
    if doc.meta.has_field("custom_allocated_operation_cost"):
        prior_allocated_op_cost = flt(
            frappe.db.sql(
                """
                SELECT COALESCE(SUM(custom_allocated_operation_cost), 0)
                FROM `tabStock Entry`
                WHERE work_order = %(work_order)s
                  AND docstatus = 1
                  AND name != %(current)s
                  AND (stock_entry_type = 'Manufacture' OR purpose = 'Manufacture')
                """,
                {"work_order": wo_doc.name, "current": doc.name or ""},
            )[0][0]
        )
        legacy_values = frappe.db.sql(
            """
            SELECT
                COALESCE(SUM(CASE
                    WHEN COALESCE(sed.is_finished_item, 0) = 1
                     AND COALESCE(sed.is_scrap_item, 0) = 0
                    THEN ABS(sed.basic_amount) ELSE 0 END), 0) AS fg_value,
                COALESCE(SUM(CASE
                    WHEN COALESCE(sed.is_finished_item, 0) = 0
                     AND COALESCE(sed.is_scrap_item, 0) = 0
                    THEN ABS(sed.basic_amount) ELSE 0 END), 0) AS raw_value
            FROM `tabStock Entry Detail` sed
            INNER JOIN `tabStock Entry` se ON se.name = sed.parent
            WHERE se.work_order = %(work_order)s
              AND se.docstatus = 1
              AND COALESCE(se.custom_uses_finish_allocation, 0) = 0
              AND (se.stock_entry_type = 'Manufacture' OR se.purpose = 'Manufacture')
            """,
            {"work_order": wo_doc.name},
            as_dict=True,
        )[0]
        prior_allocated_op_cost += max(
            flt(legacy_values.fg_value) - flt(legacy_values.raw_value), 0.0
        )

    remaining_op_cost = max(total_op_cost - prior_allocated_op_cost, 0.0)
    is_final_finish = bool(flt(doc.get("custom_is_final_finish")))
    if is_final_finish:
        allocated_op_cost = remaining_op_cost
    else:
        cumulative_op_target = total_op_cost * wo_produced_after / (wo_qty or 1.0)
        allocated_op_cost = min(
            max(cumulative_op_target - prior_allocated_op_cost, 0.0),
            remaining_op_cost,
        )
    if doc.meta.has_field("custom_allocated_operation_cost"):
        doc.custom_allocated_operation_cost = allocated_op_cost

    total_fg_basic_amount = max(
        raw_material_cost
        + allocated_op_cost
        - scrap_material_credit,
        0.0,
    )
    fg_basic_rate = (
        total_fg_basic_amount / finished_qty if finished_qty > 0 else 0.0
    )

    for row in finished_rows:
        row_qty = abs(flt(row.get("transfer_qty")) or flt(row.get("qty")))
        if row_qty <= 0:
            continue
        row.qty = row_qty
        row.basic_rate = fg_basic_rate
        row.basic_amount = row_qty * fg_basic_rate
        row.amount = row.basic_amount + flt(row.get("additional_cost"))
        if hasattr(row, "valuation_rate"):
            row.valuation_rate = row.amount / row_qty


def _get_stock_entry_row_rate(row) -> float:
    """Return the best material valuation rate already available on a row."""
    return abs(
        flt(row.get("valuation_rate"))
        or flt(row.get("basic_rate"))
        or flt(row.get("incoming_rate"))
    )


def _get_pick_list_from_stock_entry_row(row) -> str | None:
    pl_item = row.get("custom_pick_list_item")
    if not pl_item:
        return None

    return frappe.db.get_value("Pick List Item", pl_item, "parent")


def _get_transferred_wip_rate_map(
    work_order_name: str, wip_warehouse: str
) -> dict[str, float]:
    """
    Return weighted valuation rates for materials transferred into WIP.

    Manufacture Stock Entries in this app consume the actual transferred WIP
    materials. If the new consumption rows have not been valued yet, this lets
    the finished item cost still use the submitted transfer values.
    """
    if not work_order_name or not wip_warehouse:
        return {}

    rows = frappe.db.sql(
        """
        SELECT
            sed.item_code,
            sed.qty,
            sed.transfer_qty,
            sed.basic_rate,
            sed.valuation_rate,
            sed.basic_amount,
            sed.amount
        FROM `tabStock Entry Detail` sed
        INNER JOIN `tabStock Entry` se
            ON se.name = sed.parent
        WHERE
            se.docstatus = 1
            AND se.work_order = %s
            AND se.stock_entry_type = 'Material Transfer for Manufacture'
            AND sed.t_warehouse = %s
        """,
        (work_order_name, wip_warehouse),
        as_dict=True,
    )

    totals = {}
    for row in rows:
        item_code = row.get("item_code")
        if not item_code:
            continue

        qty = abs(flt(row.get("transfer_qty")) or flt(row.get("qty")))
        if qty <= 0:
            continue

        rate = abs(flt(row.get("valuation_rate")) or flt(row.get("basic_rate")))
        amount = abs(flt(row.get("basic_amount")) or flt(row.get("amount")))
        if amount <= 0 and rate > 0:
            amount = qty * rate

        if amount <= 0:
            continue

        totals.setdefault(item_code, {"qty": 0.0, "amount": 0.0})
        totals[item_code]["qty"] += qty
        totals[item_code]["amount"] += amount

    return {
        item_code: values["amount"] / values["qty"]
        for item_code, values in totals.items()
        if values["qty"] > 0 and values["amount"] > 0
    }


def _get_work_order_operating_cost_from_job_cards(
    work_order_name: str, pick_lists: set[str] | None = None
) -> float:
    """Return actual operating cost from Job Cards linked to the Work Order."""
    if not work_order_name:
        return 0.0

    if flt(frappe.db.get_value("Work Order", work_order_name, "custom_disable_operation")):
        return 0.0

    try:
        jc_meta = frappe.get_meta("Job Card")
    except Exception:
        return 0.0

    has_total_operating_cost = jc_meta.has_field("total_operating_cost")
    has_total_time_in_mins = jc_meta.has_field("total_time_in_mins")
    has_hour_rate = jc_meta.has_field("hour_rate")
    has_workstation = jc_meta.has_field("workstation")
    has_operation = jc_meta.has_field("operation")

    fields = ["name", "status"]
    if has_total_operating_cost:
        fields.append("total_operating_cost")
    if has_total_time_in_mins:
        fields.append("total_time_in_mins")
    if has_hour_rate:
        fields.append("hour_rate")
    if has_workstation:
        fields.append("workstation")
    if has_operation:
        fields.append("operation")
    for fieldname in ("total_completed_qty", "completed_qty", "for_quantity"):
        if jc_meta.has_field(fieldname):
            fields.append(fieldname)

    filters = {
        "work_order": work_order_name,
        "docstatus": ["<", 2],
    }
    if pick_lists:
        if not jc_meta.has_field("custom_pick_list"):
            return 0.0
        filters["custom_pick_list"] = ["in", list(pick_lists)]
        fields.append("custom_pick_list")

    jc_rows = frappe.get_all("Job Card", filters=filters, fields=fields)

    total = 0.0
    for jc in jc_rows:
        status = (jc.get("status") or "").strip()
        if status == "Cancelled":
            continue

        cost = flt(jc.get("total_operating_cost"))
        if cost > 0:
            total += cost
            continue

        cost = _get_job_card_cost_from_time_logs(jc.get("name"))
        if cost > 0:
            total += cost
            continue

        cost = _get_job_card_cost_from_work_order_operation(work_order_name, jc)
        if cost > 0:
            total += cost
            continue

        mins = flt(jc.get("total_time_in_mins"))
        rate = _get_job_card_hour_rate(jc)
        if mins > 0 and rate > 0:
            total += (mins / 60.0) * rate

    return total


def _get_job_card_cost_from_work_order_operation(work_order_name: str, jc_row) -> float:
    """
    Price a completed Job Card from the matching Work Order operation.

    Some C4 flows complete Job Cards by quantity without recording a time log.
    In that case, the Job Card is still the real operation signal, and the
    Work Order operation row provides the rate/time basis.
    """
    if not work_order_name or not jc_row:
        return 0.0

    completed_qty = _get_job_card_completed_qty(jc_row)
    if completed_qty <= 0:
        return 0.0

    operation = jc_row.get("operation")
    workstation = jc_row.get("workstation")

    wo_op_meta = frappe.get_meta("Work Order Operation")
    wo_op_fields = _get_existing_fields(
        wo_op_meta,
        [
            "name",
            "operation",
            "workstation",
            "time_in_mins",
            "hour_rate",
            "planned_operating_cost",
            "actual_operating_cost",
            "completed_qty",
        ],
    )

    filters = {"parent": work_order_name, "parenttype": "Work Order"}
    if operation and wo_op_meta.has_field("operation"):
        filters["operation"] = operation
    if workstation and wo_op_meta.has_field("workstation"):
        filters["workstation"] = workstation

    rows = frappe.get_all(
        "Work Order Operation",
        filters=filters,
        fields=wo_op_fields,
        order_by="idx asc",
    )

    if not rows and operation and wo_op_meta.has_field("operation"):
        rows = frappe.get_all(
            "Work Order Operation",
            filters={
                "parent": work_order_name,
                "parenttype": "Work Order",
                "operation": operation,
            },
            fields=wo_op_fields,
            order_by="idx asc",
        )

    if not rows:
        return 0.0

    op = rows[0]
    cost = flt(op.get("actual_operating_cost")) or flt(op.get("planned_operating_cost"))
    op_completed_qty = flt(op.get("completed_qty"))
    wo_qty = flt(frappe.db.get_value("Work Order", work_order_name, "qty"))

    if cost > 0:
        qty_basis = op_completed_qty or wo_qty or completed_qty
        qty_share = (completed_qty / qty_basis) if qty_basis > 0 else 1.0
        return cost * min(qty_share, 1.0)

    mins = flt(op.get("time_in_mins"))
    rate = flt(op.get("hour_rate")) or _get_job_card_hour_rate(jc_row)
    if mins <= 0 or rate <= 0:
        return 0.0

    qty_basis = op_completed_qty or wo_qty or completed_qty
    qty_share = (completed_qty / qty_basis) if qty_basis > 0 else 1.0

    return (mins / 60.0) * rate * qty_share


def _get_existing_fields(meta, fieldnames: list[str]) -> list[str]:
    """Return only field names available on a DocType, keeping `name`."""
    fields = []
    for fieldname in fieldnames:
        if fieldname == "name" or meta.has_field(fieldname):
            fields.append(fieldname)

    return fields


def _get_job_card_completed_qty(job_card) -> float:
    """Return the quantity that this Job Card actually completed."""
    for fieldname in ("total_completed_qty", "completed_qty"):
        qty = flt(job_card.get(fieldname))
        if qty > 0:
            return qty

    if (job_card.get("status") or "").strip() == "Completed":
        return flt(job_card.get("for_quantity"))

    return 0.0


def _get_job_card_cost_from_time_logs(job_card_name: str) -> float:
    """Calculate actual Job Card cost from its recorded time logs."""
    if not job_card_name:
        return 0.0

    try:
        job_card = frappe.get_doc("Job Card", job_card_name)
    except Exception:
        return 0.0

    parent_rate = _get_job_card_hour_rate(job_card)
    total = 0.0

    for row in job_card.get("time_logs") or []:
        direct_cost = (
            flt(row.get("operating_cost"))
            or flt(row.get("operation_cost"))
            or flt(row.get("cost"))
            or flt(row.get("amount"))
        )
        if direct_cost > 0:
            total += direct_cost
            continue

        mins = flt(row.get("time_in_mins")) or flt(row.get("total_time_in_mins"))
        rate = flt(row.get("hour_rate")) or flt(row.get("hourly_rate")) or parent_rate

        if mins > 0 and rate > 0:
            total += (mins / 60.0) * rate

    return total


def _get_job_card_hour_rate(job_card) -> float:
    """Resolve an hourly operation rate from Job Card, Workstation, or Operation."""
    if not job_card:
        return 0.0

    rate = flt(job_card.get("hour_rate")) or flt(job_card.get("hourly_rate"))
    if rate > 0:
        return rate

    workstation = job_card.get("workstation")
    if workstation:
        rate = _get_hour_rate_from_doctype("Workstation", workstation)
        if rate > 0:
            return rate

    operation = job_card.get("operation")
    if operation:
        rate = _get_hour_rate_from_doctype("Operation", operation)
        if rate > 0:
            return rate

    return 0.0


def _get_hour_rate_from_doctype(doctype: str, name: str) -> float:
    """Read a rate field only when the target DocType has it."""
    if not doctype or not name:
        return 0.0

    try:
        meta = frappe.get_meta(doctype)
    except Exception:
        return 0.0

    for fieldname in ("hour_rate", "hourly_rate"):
        if not meta.has_field(fieldname):
            continue

        rate = flt(frappe.db.get_value(doctype, name, fieldname))
        if rate > 0:
            return rate

    return 0.0


# ============================================================
# 2) Work Order costing from submitted Stock Entries
# ============================================================

def on_submit_update_work_order_costing(doc, method: str | None = None) -> None:
    """
    Called on Stock Entry submit.
    Recalculate the Work Order costing (raw / scrap / total) from all
    submitted Stock Entries linked to this Work Order.
    """
    if not doc.work_order:
        return

    _recalculate_work_order_costs(doc.work_order)


def verify_final_finish_cost_reconciliation(doc, method: str | None = None) -> None:
    """Ensure final finished-goods value equals the final Work Order cost."""
    if not flt(doc.get("custom_uses_finish_allocation")) or not flt(
        doc.get("custom_is_final_finish")
    ):
        return

    legacy_finish_exists = frappe.db.exists(
        "Stock Entry",
        {
            "work_order": doc.work_order,
            "docstatus": 1,
            "stock_entry_type": "Manufacture",
            "custom_uses_finish_allocation": 0,
        },
    )
    if legacy_finish_exists:
        return

    work_order_cost = flt(
        frappe.db.get_value("Work Order", doc.work_order, "c4_total_cost")
    )
    finished_goods_value = flt(
        frappe.db.sql(
            """
            SELECT COALESCE(SUM(ABS(sed.amount)), 0)
            FROM `tabStock Entry Detail` sed
            INNER JOIN `tabStock Entry` se ON se.name = sed.parent
            WHERE se.work_order = %s
              AND se.docstatus = 1
              AND (se.stock_entry_type = 'Manufacture' OR se.purpose = 'Manufacture')
              AND COALESCE(sed.is_finished_item, 0) = 1
              AND COALESCE(sed.is_scrap_item, 0) = 0
            """,
            (doc.work_order,),
        )[0][0]
    )
    variance = work_order_cost - finished_goods_value
    if abs(variance) > 0.05:
        frappe.throw(
            _(
                "Final Finish cost reconciliation failed. Work Order cost is {0}, "
                "finished-product value is {1}, and variance is {2}."
            ).format(work_order_cost, finished_goods_value, variance)
        )


@frappe.whitelist()
def recompute_work_order_costing(work_order_name: str) -> None:
    """
    Public helper for other modules (e.g. on Stock Entry cancel)
    to recompute costing.
    """
    if not work_order_name:
        return

    _recalculate_work_order_costs(work_order_name)


def _recalculate_work_order_costs(work_order_name: str) -> None:
    """
    Aggregate actual material/scrap cost for the given Work Order from all
    submitted Stock Entries and combine with actual Job Card operating cost.

    Logic:
      - Look at all submitted Stock Entries with se.work_order = WO
      - For each Stock Entry Detail row:
          * ignore finished items (is_finished_item = 1)
          * if is_scrap_item = 1 → goes to Scrap Material Cost
          * normal raw cost is counted on Material Transfer for Manufacture
          * mold raw cost is counted on its flagged Material Issue
            so neither is counted a second time during manufacture
      - Use transfer_qty * basic_rate as the amount
    - Operating Cost = sum of actual Job Card operating cost
    - Total Cost = Raw + Operating - Scrap
    """
    wo = frappe.get_doc("Work Order", work_order_name)

    # Fetch all Stock Entry rows for this Work Order
    rows = frappe.db.sql(
        """
        SELECT
            sed.is_finished_item,
            sed.is_scrap_item,
            sed.transfer_qty,
            sed.basic_rate,
            se.stock_entry_type,
            se.custom_is_mold_material_issue
        FROM `tabStock Entry Detail` sed
        INNER JOIN `tabStock Entry` se
            ON se.name = sed.parent
        WHERE
            se.docstatus = 1
            AND se.work_order = %s
        """,
        (work_order_name,),
        as_dict=True,
    )

    raw_material_cost = 0.0
    scrap_material_cost = 0.0

    for r in rows:
        qty = abs(flt(r.transfer_qty))
        rate = abs(flt(r.basic_rate))
        amount = qty * rate

        # finished item cost is not counted here (it is the result)
        if r.is_scrap_item:
            scrap_material_cost += amount
        elif (
            not r.is_finished_item
            and (
                r.stock_entry_type == "Material Transfer for Manufacture"
                or flt(r.custom_is_mold_material_issue)
            )
        ):
            raw_material_cost += amount

    # Operating cost from actual Job Cards linked to the Work Order
    operating_cost = _get_work_order_operating_cost_from_job_cards(work_order_name)

    # Write back to Work Order custom fields
    wo.db_set("c4_raw_material_cost", raw_material_cost)
    wo.db_set("c4_scrap_material_cost", scrap_material_cost)
    wo.db_set("c4_operating_cost", operating_cost)
    wo.db_set(
        "c4_total_cost",
        raw_material_cost + operating_cost - scrap_material_cost,
    )
