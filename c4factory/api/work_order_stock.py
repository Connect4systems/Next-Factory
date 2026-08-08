import frappe
from frappe import _
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

    remaining_fg_qty = max(flt(wo.qty) - flt(wo.produced_qty), 0.0)
    if fg_qty <= 0 or fg_qty > remaining_fg_qty + 0.000001:
        frappe.throw(
            f"Finish quantity must be greater than zero and not more than {remaining_fg_qty}."
        )

    existing_draft = frappe.db.get_value(
        "Stock Entry",
        {
            "work_order": wo.name,
            "docstatus": 0,
            "stock_entry_type": "Manufacture",
        },
        "name",
    )
    if existing_draft:
        frappe.throw(
            _("Draft Finish Stock Entry {0} already reserves this Work Order's material.").format(
                frappe.bold(existing_draft)
            )
        )

    is_final_finish = abs(fg_qty - remaining_fg_qty) <= 0.000001
    transferred_items = _get_finish_material_allocations(wo, fg_qty, is_final_finish)

    if not transferred_items:
        frappe.throw(
            "No eligible submitted material-transfer balance was found "
            f"in WIP for Work Order {wo.name}. Draft Pick Lists and draft "
            "Stock Entries do not move stock."
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
    se.custom_uses_finish_allocation = 1
    se.custom_is_final_finish = 1 if is_final_finish else 0

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
            "s_warehouse": wo.wip_warehouse,
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
        if row.meta.has_field("custom_source_transfer_detail"):
            row.custom_source_transfer_detail = item["source_transfer_detail"]
        if row.meta.has_field("set_basic_rate_manually"):
            row.set_basic_rate_manually = 1
        row._c4_role = "raw"
        row._c4_expected_qty = item["qty"]
        row._c4_expected_rate = rate

    # 2) SCRAP ITEMS ROWS – created in Scrap Warehouse
    if wo.scrap_warehouse:
        for row in _get_scrap_allocations(wo, fg_qty, is_final_finish):
            qty = flt(row.get("qty"))
            if qty <= 0:
                continue

            scrap_row = se.append("items", {
                "item_code": row.item_code,
                "qty": qty,
                "t_warehouse": wo.scrap_warehouse,
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
        "t_warehouse": wo.fg_warehouse,
        "uom": wo.stock_uom,
        "stock_uom": wo.stock_uom,
        "conversion_factor": 1,
        "is_finished_item": 1,
        "is_scrap_item": 0,
    })
    fg_row._c4_role = "fg"
    fg_row._c4_expected_qty = fg_qty

    mold_cost_allocations = _get_allocated_mold_cost_rows(wo, fg_qty, is_final_finish)
    mold_cost = sum(mold_cost_allocations.values())
    se.custom_allocated_mold_cost = mold_cost
    for mold_account, allocated_cost in mold_cost_allocations.items():
        account_details = frappe.get_cached_value(
            "Account", mold_account, ["company", "is_group"], as_dict=True
        )
        if (
            not account_details
            or account_details.company != wo.company
            or flt(account_details.is_group)
        ):
            frappe.throw(
                _("Mold Cost Expense Account must be a ledger account for {0}.").format(
                    frappe.bold(wo.company)
                )
            )
        se.append(
            "additional_costs",
            {
                "expense_account": mold_account,
                "description": "Mold Cost",
                "amount": allocated_cost,
            },
        )

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
                if row.meta.has_field("set_basic_rate_manually"):
                    row.set_basic_rate_manually = 1

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


def _get_finish_material_allocations(
    wo, fg_qty: float, is_final_finish: bool, exclude_finish: str | None = None
):
    """Allocate exact submitted WIP transfer rows to one partial Finish."""
    sources = _get_available_transfer_sources(wo, exclude_finish=exclude_finish)
    if not sources:
        return []

    required_rows = {}
    required_item_by_code = {}
    for row in wo.get("required_items") or wo.get("items") or []:
        base_required_qty = max(
            flt(row.get("required_qty")) - flt(row.get("custom_additional_material_qty")),
            0.0,
        )
        if not row.get("item_code") or base_required_qty <= 0:
            continue
        required_rows[row.name] = frappe._dict(
            {
                "name": row.name,
                "item_code": row.item_code,
                "per_unit_qty": base_required_qty / (flt(wo.qty) or 1.0),
            }
        )
        required_item_by_code.setdefault(row.item_code, row.name)

    for source in sources:
        source.base_key = None
        if source.is_additional:
            continue
        if source.custom_work_order_item in required_rows:
            source.base_key = source.custom_work_order_item
        else:
            source.base_key = required_item_by_code.get(source.item_code)

    max_finishable = max(flt(wo.qty) - flt(wo.produced_qty), 0.0)
    for required in required_rows.values():
        available_qty = sum(
            flt(source.remaining_qty)
            for source in sources
            if source.base_key == required.name
        )
        max_finishable = min(
            max_finishable,
            available_qty / required.per_unit_qty if required.per_unit_qty > 0 else 0.0,
        )

    if required_rows and fg_qty > max_finishable + 0.000001:
        frappe.throw(
            _(
                "Only {0} finished quantity is covered by material already transferred to WIP."
            ).format(max_finishable)
        )

    allocation_by_source = {}
    if is_final_finish:
        for source in sources:
            if source.remaining_qty > 0:
                allocation_by_source[source.name] = source.remaining_qty
    else:
        for required in required_rows.values():
            desired_qty = required.per_unit_qty * fg_qty
            candidates = [
                source
                for source in sources
                if source.base_key == required.name and source.remaining_qty > 0
            ]
            _allocate_source_qty(candidates, desired_qty, allocation_by_source)

        for source in sources:
            if not source.is_additional or source.remaining_qty <= 0:
                continue
            coverage_qty = flt(source.allocation_coverage_qty) or max(
                flt(wo.qty) - flt(wo.produced_qty), 0.0
            )
            desired_qty = (
                source.total_qty * fg_qty / coverage_qty if coverage_qty > 0 else 0.0
            )
            _allocate_source_qty([source], desired_qty, allocation_by_source)

    result = []
    for source in sources:
        allocated_qty = flt(allocation_by_source.get(source.name))
        if allocated_qty <= 0:
            continue
        rate = source.total_amount / source.total_qty if source.total_qty > 0 else 0.0
        result.append(
            {
                "item_code": source.item_code,
                "stock_uom": source.stock_uom,
                "qty": allocated_qty,
                "valuation_rate": rate,
                "basic_rate": rate,
                "amount": allocated_qty * rate,
                "custom_pick_list_item": source.custom_pick_list_item,
                "custom_work_order_item": source.custom_work_order_item,
                "source_transfer_detail": source.name,
            }
        )
    return result


def _allocate_source_qty(sources, desired_qty: float, allocation_by_source: dict) -> None:
    remaining = max(flt(desired_qty), 0.0)
    for source in sources:
        if remaining <= 0:
            break
        allocated = min(flt(source.remaining_qty), remaining)
        if allocated <= 0:
            continue
        allocation_by_source[source.name] = (
            flt(allocation_by_source.get(source.name)) + allocated
        )
        source.remaining_qty -= allocated
        remaining -= allocated


def _get_available_transfer_sources(wo, exclude_finish: str | None = None):
    transfer_entries = frappe.get_all(
        "Stock Entry",
        filters={
            "work_order": wo.name,
            "docstatus": 1,
            "stock_entry_type": "Material Transfer for Manufacture",
        },
        fields=[
            "name",
            "custom_is_additional_material",
            "custom_material_allocation_qty",
            "custom_additional_material_pick_list",
            "posting_date",
            "posting_time",
            "creation",
        ],
        order_by="posting_date asc, posting_time asc, creation asc",
    )
    if not transfer_entries:
        return []

    entry_by_name = {entry.name: entry for entry in transfer_entries}
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
        "creation",
    ]
    if sed_meta.has_field("custom_work_order_item"):
        fields.append("custom_work_order_item")

    rows = frappe.get_all(
        "Stock Entry Detail",
        filters={
            "parent": ["in", list(entry_by_name)],
            "t_warehouse": wo.wip_warehouse,
            "is_finished_item": 0,
            "is_scrap_item": 0,
        },
        fields=fields,
        order_by="creation asc, idx asc",
    )

    linked_allocations = {
        row.source_transfer_detail: flt(row.qty)
        for row in frappe.db.sql(
            """
            SELECT sed.custom_source_transfer_detail AS source_transfer_detail,
                   COALESCE(SUM(ABS(sed.qty)), 0) AS qty
            FROM `tabStock Entry Detail` sed
            INNER JOIN `tabStock Entry` se ON se.name = sed.parent
            WHERE se.work_order = %(work_order)s
              AND se.docstatus < 2
              AND se.name != %(exclude_finish)s
              AND (se.stock_entry_type IN ('Manufacture', 'Process Loss')
                   OR se.purpose IN ('Manufacture', 'Process Loss'))
              AND COALESCE(sed.is_finished_item, 0) = 0
              AND COALESCE(sed.is_scrap_item, 0) = 0
              AND COALESCE(sed.custom_source_transfer_detail, '') != ''
            GROUP BY sed.custom_source_transfer_detail
            """,
            {"work_order": wo.name, "exclude_finish": exclude_finish or ""},
            as_dict=True,
        )
    }

    sources = []
    for row in rows:
        total_qty = abs(flt(row.get("transfer_qty")) or flt(row.get("qty")))
        if total_qty <= 0:
            continue
        total_amount = abs(flt(row.get("basic_amount")) or flt(row.get("amount")))
        if total_amount <= 0:
            rate = abs(flt(row.get("valuation_rate")) or flt(row.get("basic_rate")))
            total_amount = total_qty * rate
        entry = entry_by_name[row.parent]
        sources.append(
            frappe._dict(
                {
                    "name": row.name,
                    "item_code": row.item_code,
                    "stock_uom": row.stock_uom,
                    "total_qty": total_qty,
                    "total_amount": total_amount,
                    "remaining_qty": max(
                        total_qty - flt(linked_allocations.get(row.name)), 0.0
                    ),
                    "custom_pick_list_item": row.get("custom_pick_list_item"),
                    "custom_work_order_item": row.get("custom_work_order_item"),
                    "is_additional": bool(flt(entry.custom_is_additional_material)),
                    "allocation_coverage_qty": flt(entry.custom_material_allocation_qty),
                }
            )
        )

    _apply_legacy_finish_consumption(wo.name, sources, exclude_finish=exclude_finish)
    return [source for source in sources if source.remaining_qty > 0]


def _apply_legacy_finish_consumption(
    work_order: str, sources, exclude_finish: str | None = None
) -> None:
    """Deduct old Manufacture rows created before exact source links existed."""
    legacy_rows = frappe.db.sql(
        """
        SELECT sed.custom_pick_list_item, sed.item_code,
               COALESCE(SUM(ABS(sed.qty)), 0) AS qty
        FROM `tabStock Entry Detail` sed
        INNER JOIN `tabStock Entry` se ON se.name = sed.parent
        WHERE se.work_order = %(work_order)s
          AND se.docstatus = 1
          AND se.name != %(exclude_finish)s
          AND (se.stock_entry_type IN ('Manufacture', 'Process Loss')
               OR se.purpose IN ('Manufacture', 'Process Loss'))
          AND COALESCE(sed.is_finished_item, 0) = 0
          AND COALESCE(sed.is_scrap_item, 0) = 0
          AND COALESCE(sed.custom_source_transfer_detail, '') = ''
        GROUP BY sed.custom_pick_list_item, sed.item_code
        """,
        {"work_order": work_order, "exclude_finish": exclude_finish or ""},
        as_dict=True,
    )
    for legacy in legacy_rows:
        remaining = flt(legacy.qty)
        candidates = []
        if legacy.custom_pick_list_item:
            candidates = [
                source
                for source in sources
                if source.custom_pick_list_item == legacy.custom_pick_list_item
                and source.item_code == legacy.item_code
            ]
        if not candidates:
            candidates = sorted(
                [source for source in sources if source.item_code == legacy.item_code],
                key=lambda source: 0 if source.is_additional else 1,
            )
        for source in candidates:
            consumed = min(source.remaining_qty, remaining)
            source.remaining_qty -= consumed
            remaining -= consumed
            if remaining <= 0:
                break


def _get_scrap_allocations(
    wo, fg_qty: float, is_final_finish: bool, exclude_finish: str | None = None
):
    created = {
        row.item_code: flt(row.qty)
        for row in frappe.db.sql(
            """
            SELECT sed.item_code, COALESCE(SUM(ABS(sed.qty)), 0) AS qty
            FROM `tabStock Entry Detail` sed
            INNER JOIN `tabStock Entry` se ON se.name = sed.parent
            WHERE se.work_order = %(work_order)s
              AND se.docstatus < 2
              AND se.name != %(exclude_finish)s
              AND (se.stock_entry_type = 'Manufacture' OR se.purpose = 'Manufacture')
              AND COALESCE(sed.is_scrap_item, 0) = 1
            GROUP BY sed.item_code
            """,
            {"work_order": wo.name, "exclude_finish": exclude_finish or ""},
            as_dict=True,
        )
    }
    result = []
    for row in wo.get("c4_scrap_items") or []:
        planned_qty = flt(row.get("stock_qty"))
        balance_qty = max(planned_qty - flt(created.get(row.item_code)), 0.0)
        qty = (
            balance_qty
            if is_final_finish
            else min(planned_qty * fg_qty / (flt(wo.qty) or 1.0), balance_qty)
        )
        if qty > 0:
            result.append(
                frappe._dict(
                    {
                        "item_code": row.item_code,
                        "stock_uom": row.stock_uom,
                        "qty": qty,
                    }
                )
            )
    return result


def _get_allocated_mold_cost(wo, fg_qty: float, is_final_finish: bool) -> float:
    return sum(_get_allocated_mold_cost_rows(wo, fg_qty, is_final_finish).values())


def _get_allocated_mold_cost_rows(wo, fg_qty: float, is_final_finish: bool) -> dict:
    totals = {
        row.expense_account: flt(row.amount)
        for row in frappe.db.sql(
            """
            SELECT sed.expense_account,
                   COALESCE(SUM(COALESCE(NULLIF(ABS(sed.basic_amount), 0), ABS(sed.amount))), 0) AS amount
            FROM `tabStock Entry Detail` sed
            INNER JOIN `tabStock Entry` se ON se.name = sed.parent
            WHERE se.work_order = %s
              AND se.docstatus = 1
              AND COALESCE(se.custom_is_mold_material_issue, 0) = 1
              AND COALESCE(sed.is_finished_item, 0) = 0
              AND COALESCE(sed.is_scrap_item, 0) = 0
              AND COALESCE(sed.expense_account, '') != ''
            GROUP BY sed.expense_account
            """,
            (wo.name,),
            as_dict=True,
        )
    }
    prior = {
        row.expense_account: flt(row.amount)
        for row in frappe.db.sql(
            """
            SELECT cost.expense_account, COALESCE(SUM(cost.amount), 0) AS amount
            FROM `tabLanded Cost Taxes and Charges` cost
            INNER JOIN `tabStock Entry` se ON se.name = cost.parent
            WHERE se.work_order = %s
              AND se.docstatus = 1
              AND (se.stock_entry_type = 'Manufacture' OR se.purpose = 'Manufacture')
              AND cost.description = 'Mold Cost'
            GROUP BY cost.expense_account
            """,
            (wo.name,),
            as_dict=True,
        )
    }

    allocations = {}
    cumulative_ratio = (flt(wo.produced_qty) + fg_qty) / (flt(wo.qty) or 1.0)
    for account, total_cost in totals.items():
        remaining_cost = max(total_cost - flt(prior.get(account)), 0.0)
        allocated_cost = (
            remaining_cost
            if is_final_finish
            else min(
                max(total_cost * cumulative_ratio - flt(prior.get(account)), 0.0),
                remaining_cost,
            )
        )
        if allocated_cost > 0:
            allocations[account] = allocated_cost
    return allocations


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
