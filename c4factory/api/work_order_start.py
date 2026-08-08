from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cstr, flt

from c4factory.api.work_order_pick_list import (
    create_pick_list,
    get_remaining_pick_list_qty,
    is_continuous_manufacture_item,
)


REALTIME_EVENT = "c4factory_continuous_start"


@frappe.whitelist()
def get_continuous_start_context(work_order: str) -> dict:
    """Return the eligible Start channels and their shared maximum quantity."""
    wo = frappe.get_doc("Work Order", work_order)
    wo.check_permission("read")

    continuous_rows, pick_list_rows = _get_start_rows(wo)
    return {
        "has_eligible_items": bool(continuous_rows or pick_list_rows),
        "has_continuous_items": bool(continuous_rows),
        "has_pick_list_items": bool(pick_list_rows),
        "remaining_qty": _get_remaining_start_qty(
            wo,
            has_continuous_items=bool(continuous_rows),
            has_pick_list_items=bool(pick_list_rows),
        ),
        "pending": bool(wo.get("custom_continuous_start_pending")),
    }


@frappe.whitelist()
def enqueue_continuous_start_transfer(work_order: str, qty: float) -> dict:
    """Reserve and enqueue one idempotent continuous-material Start request."""
    qty = flt(qty)
    if qty <= 0:
        frappe.throw(_("Start quantity must be greater than zero."))

    wo = frappe.get_doc("Work Order", work_order)
    wo.check_permission("read")
    _validate_work_order(wo)

    continuous_rows, pick_list_rows = _get_start_rows(wo)
    if not continuous_rows and not pick_list_rows:
        return {
            "status": "no_items",
            "message": _(
                "No eligible required items were found. No Stock Entry or Pick List was created."
            ),
        }
    if continuous_rows:
        if not frappe.has_permission("Stock Entry", "create"):
            frappe.throw(_("You do not have permission to create Stock Entries."))
        if not frappe.has_permission("Stock Entry", "submit"):
            frappe.throw(_("You do not have permission to submit Stock Entries."))
    if pick_list_rows and not frappe.has_permission("Pick List", "create"):
        frappe.throw(_("You do not have permission to create Pick Lists."))

    # Serialize Start clicks for this Work Order. Reading the flag in the same
    # SELECT ... FOR UPDATE prevents two requests from being queued concurrently.
    locked = frappe.db.sql(
        """
        SELECT custom_continuous_start_pending,
               custom_continuous_start_request_id
        FROM `tabWork Order`
        WHERE name = %s
        FOR UPDATE
        """,
        (wo.name,),
        as_dict=True,
    )
    if not locked:
        frappe.throw(_("Work Order {0} does not exist.").format(wo.name))
    if locked[0].custom_continuous_start_pending:
        return {
            "status": "already_queued",
            "message": _(
                "Start documents are already being created for this Work Order."
            ),
        }

    remaining_qty = _get_remaining_start_qty(
        wo,
        has_continuous_items=bool(continuous_rows),
        has_pick_list_items=bool(pick_list_rows),
    )
    if qty > remaining_qty + 0.000001:
        frappe.throw(
            _("Start quantity {0} exceeds the remaining quantity {1}.").format(
                qty,
                remaining_qty,
            )
        )

    request_id = frappe.generate_hash(length=20)
    frappe.db.set_value(
        "Work Order",
        wo.name,
        {
            "custom_continuous_start_pending": 1,
            "custom_continuous_start_request_id": request_id,
        },
        update_modified=False,
    )

    frappe.enqueue(
        "c4factory.api.work_order_start.create_continuous_start_stock_entry",
        queue="default",
        timeout=900,
        enqueue_after_commit=True,
        job_id=f"c4-continuous-start-{request_id}",
        deduplicate=True,
        work_order=wo.name,
        qty=qty,
        request_id=request_id,
        initiated_by=frappe.session.user,
    )
    return {
        "status": "queued",
        "request_id": request_id,
        "message": (
            _("Stock Entry and Pick List creation started in the background.")
            if continuous_rows and pick_list_rows
            else _("Stock Entry creation started in the background.")
            if continuous_rows
            else _("Pick List creation started in the background.")
        ),
    }


def create_continuous_start_stock_entry(
    work_order: str,
    qty: float,
    request_id: str,
    initiated_by: str,
) -> None:
    """Background job: submit the continuous transfer and save the standard Pick List."""
    try:
        existing_stock_entry = frappe.db.get_value(
            "Stock Entry",
            {
                "custom_continuous_start_request_id": request_id,
                "docstatus": 1,
            },
            "name",
        )
        existing_pick_list = frappe.db.get_value(
            "Pick List",
            {
                "custom_continuous_start_request_id": request_id,
                "docstatus": ("<", 2),
            },
            "name",
        )
        if existing_stock_entry or existing_pick_list:
            _clear_pending_request(work_order, request_id)
            frappe.db.commit()
            _publish_result(
                initiated_by,
                work_order,
                "success",
                _get_success_message(
                    existing_stock_entry,
                    existing_pick_list,
                ),
                stock_entry=existing_stock_entry,
                pick_list=existing_pick_list,
            )
            return

        wo = frappe.get_doc("Work Order", work_order)
        _validate_work_order(wo)
        continuous_rows, pick_list_rows = _get_start_rows(wo)
        if not continuous_rows and not pick_list_rows:
            _clear_pending_request(wo.name, request_id)
            frappe.db.commit()
            _publish_result(
                initiated_by,
                wo.name,
                "no_items",
                _(
                    "No eligible required items were found. "
                    "No Stock Entry or Pick List was created."
                ),
            )
            return

        qty = flt(qty)
        remaining_qty = _get_remaining_start_qty(
            wo,
            has_continuous_items=bool(continuous_rows),
            has_pick_list_items=bool(pick_list_rows),
        )
        if qty <= 0 or qty > remaining_qty + 0.000001:
            frappe.throw(
                _("Start quantity {0} exceeds the remaining quantity {1}.").format(
                    qty,
                    remaining_qty,
                )
            )

        pick_list = None
        if pick_list_rows:
            pick_list_data = create_pick_list(
                work_order=wo.name,
                for_qty=qty,
            )
            pick_list = frappe.get_doc(pick_list_data)
            pick_list.custom_continuous_start_request_id = request_id
            pick_list.insert()

        stock_entry = None
        if continuous_rows:
            stock_entry = _build_stock_entry(
                wo,
                continuous_rows,
                qty,
                request_id,
            )
            stock_entry.insert()
            stock_entry.submit()
            started_work_order = frappe.get_doc("Work Order", wo.name)
            started_work_order.set_status()

        _clear_pending_request(wo.name, request_id)
        frappe.db.commit()
        stock_entry_name = stock_entry.name if stock_entry else None
        pick_list_name = pick_list.name if pick_list else None
        _publish_result(
            initiated_by,
            wo.name,
            "success",
            _get_success_message(
                stock_entry_name,
                pick_list_name,
            ),
            stock_entry=stock_entry_name,
            pick_list=pick_list_name,
        )
    except Exception as exc:
        error_message = cstr(exc) or _(
            "Unable to create the Stock Entry and Pick List."
        )
        traceback = frappe.get_traceback()
        frappe.db.rollback()
        _clear_pending_request(work_order, request_id)
        frappe.db.commit()
        frappe.log_error(
            traceback,
            f"C4Factory continuous Start failed ({work_order})",
        )
        _publish_result(
            initiated_by,
            work_order,
            "error",
            error_message,
        )


def _build_stock_entry(wo, eligible_rows, qty: float, request_id: str):
    if not wo.get("wip_warehouse"):
        frappe.throw(
            _("Work-in-Progress Warehouse is required for Work Order {0}.").format(
                wo.name
            )
        )

    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = "Material Transfer for Manufacture"
    se.purpose = "Material Transfer for Manufacture"
    se.company = wo.company
    se.work_order = wo.name
    se.from_bom = 0
    se.fg_completed_qty = qty
    se.custom_continuous_manufacture_transfer = 1
    se.custom_continuous_start_request_id = request_id
    se.custom_continuous_start_qty = qty
    if se.meta.has_field("custom_work_order"):
        se.custom_work_order = wo.name
    if se.meta.has_field("to_warehouse"):
        se.to_warehouse = wo.wip_warehouse

    quantity_scale = qty / (flt(wo.qty) or 1.0)
    for wo_row, item_group in eligible_rows:
        group = frappe.get_cached_doc("Item Group", item_group)
        source_warehouse = group.get("custom_warehouse")
        if not source_warehouse:
            frappe.throw(
                _("Manufacture Warehouse is required for Item Group {0}.").format(
                    frappe.bold(item_group)
                )
            )

        row_qty = flt(wo_row.get("required_qty") or wo_row.get("qty")) * quantity_scale
        if row_qty <= 0:
            continue

        stock_uom = (
            wo_row.get("stock_uom")
            or wo_row.get("uom")
            or frappe.db.get_value("Item", wo_row.item_code, "stock_uom")
        )
        valuation_rate = _get_latest_valuation_rate(
            wo_row.item_code,
            source_warehouse,
            wo.company,
        )
        amount = row_qty * valuation_rate
        se_row = se.append(
            "items",
            {
                "item_code": wo_row.item_code,
                "qty": row_qty,
                "uom": stock_uom,
                "stock_uom": stock_uom,
                "conversion_factor": 1,
                "s_warehouse": source_warehouse,
                "t_warehouse": wo.wip_warehouse,
                "is_finished_item": 0,
                "is_scrap_item": 0,
                "valuation_rate": valuation_rate,
                "basic_rate": valuation_rate,
                "amount": amount,
                "basic_amount": amount,
            },
        )
        if se_row.meta.has_field("custom_work_order_item"):
            se_row.custom_work_order_item = wo_row.name
        if se_row.meta.has_field("set_basic_rate_manually"):
            se_row.set_basic_rate_manually = 1
        se_row._c4_source_warehouse = source_warehouse
        se_row._c4_qty = row_qty
        se_row._c4_valuation_rate = valuation_rate

    if not se.get("items"):
        frappe.throw(
            _("No continuous-manufacture items were found. No Stock Entry was created.")
        )

    se.set_missing_values()
    for row in se.items:
        row.s_warehouse = row._c4_source_warehouse
        row.t_warehouse = wo.wip_warehouse
        row.qty = row._c4_qty
        row.valuation_rate = row._c4_valuation_rate
        row.basic_rate = row._c4_valuation_rate
        row.amount = row.qty * row._c4_valuation_rate
        row.basic_amount = row.amount
        if row.meta.has_field("set_basic_rate_manually"):
            row.set_basic_rate_manually = 1
        delattr(row, "_c4_source_warehouse")
        delattr(row, "_c4_qty")
        delattr(row, "_c4_valuation_rate")

    return se


def _get_start_rows(wo) -> tuple[list[tuple[object, str]], list[object]]:
    item_group_cache = {}
    continuous_group_cache = {}
    continuous_rows = []
    pick_list_rows = []

    for row in wo.get("required_items") or wo.get("items") or []:
        item_code = row.get("item_code")
        if not item_code or flt(row.get("required_qty") or row.get("qty")) <= 0:
            continue

        is_continuous = is_continuous_manufacture_item(
            row,
            item_group_cache=item_group_cache,
            continuous_group_cache=continuous_group_cache,
        )
        if not is_continuous:
            pick_list_rows.append(row)
            continue

        item_group = row.get("item_group") or item_group_cache.get(item_code)
        if item_group:
            continuous_rows.append((row, item_group))

    return continuous_rows, pick_list_rows


def _get_remaining_start_qty(
    wo,
    has_continuous_items: bool,
    has_pick_list_items: bool,
) -> float:
    remaining = []
    if has_continuous_items:
        remaining.append(
            max(flt(wo.qty) - _get_continuous_transferred_qty(wo.name), 0.0)
        )
    if has_pick_list_items:
        pick_list_remaining = get_remaining_pick_list_qty(wo)
        pick_list_remaining -= _get_draft_start_pick_list_qty(wo.name)
        remaining.append(max(pick_list_remaining, 0.0))

    return min(remaining) if remaining else 0.0


def _get_draft_start_pick_list_qty(work_order: str) -> float:
    return flt(
        frappe.db.sql(
            """
            SELECT COALESCE(SUM(for_qty), 0)
            FROM `tabPick List`
            WHERE work_order = %s
              AND docstatus = 0
              AND COALESCE(custom_continuous_start_request_id, '') != ''
            """,
            (work_order,),
        )[0][0]
    )


def _get_latest_valuation_rate(
    item_code: str,
    warehouse: str,
    company: str,
) -> float:
    rows = frappe.db.sql(
        """
        SELECT valuation_rate, warehouse
        FROM `tabStock Ledger Entry`
        WHERE item_code = %(item_code)s
          AND warehouse = %(warehouse)s
          AND company = %(company)s
          AND COALESCE(is_cancelled, 0) = 0
          AND COALESCE(valuation_rate, 0) > 0
        ORDER BY posting_date DESC, posting_time DESC, creation DESC
        LIMIT 1
        """,
        {
            "item_code": item_code,
            "warehouse": warehouse,
            "company": company,
        },
        as_dict=True,
    )
    if not rows:
        # Some manufacture warehouses are newly created or have not received
        # this item yet. Use the item's latest positive company valuation as the
        # requested Stock Ledger average-rate fallback.
        rows = frappe.db.sql(
            """
            SELECT valuation_rate, warehouse
            FROM `tabStock Ledger Entry`
            WHERE item_code = %(item_code)s
              AND company = %(company)s
              AND COALESCE(is_cancelled, 0) = 0
              AND COALESCE(valuation_rate, 0) > 0
            ORDER BY posting_date DESC, posting_time DESC, creation DESC
            LIMIT 1
            """,
            {
                "item_code": item_code,
                "company": company,
            },
            as_dict=True,
        )
    if not rows:
        frappe.throw(
            _(
                "No positive Stock Ledger valuation rate was found for item {0} "
                "in company {1}."
            ).format(
                frappe.bold(item_code),
                frappe.bold(company),
            )
        )

    return flt(rows[0].valuation_rate)


def _get_success_message(
    stock_entry: str | None,
    pick_list: str | None,
) -> str:
    if stock_entry and pick_list:
        return _(
            "Stock Entry {0} was submitted and Pick List {1} was created successfully."
        ).format(stock_entry, pick_list)
    if stock_entry:
        return _("Stock Entry {0} was created and submitted successfully.").format(
            stock_entry
        )
    return _("Pick List {0} was created successfully.").format(pick_list)


def _get_continuous_transferred_qty(work_order: str) -> float:
    return flt(
        frappe.db.sql(
            """
            SELECT COALESCE(SUM(custom_continuous_start_qty), 0)
            FROM `tabStock Entry`
            WHERE work_order = %s
              AND docstatus = 1
              AND COALESCE(custom_continuous_manufacture_transfer, 0) = 1
              AND (stock_entry_type = 'Material Transfer for Manufacture'
                   OR purpose = 'Material Transfer for Manufacture')
            """,
            (work_order,),
        )[0][0]
    )


def _validate_work_order(wo) -> None:
    if wo.docstatus != 1:
        frappe.throw(_("Work Order must be submitted before it can be started."))
    if wo.get("status") in {"Stopped", "Closed", "Completed", "Cancelled"}:
        frappe.throw(
            _("Work Order {0} cannot be started while its status is {1}.").format(
                wo.name,
                wo.status,
            )
        )
    if not wo.get("wip_warehouse"):
        frappe.throw(
            _("Work-in-Progress Warehouse is required for Work Order {0}.").format(
                wo.name
            )
        )


def _clear_pending_request(work_order: str, request_id: str) -> None:
    current_request = frappe.db.get_value(
        "Work Order",
        work_order,
        "custom_continuous_start_request_id",
    )
    if current_request != request_id:
        return

    frappe.db.set_value(
        "Work Order",
        work_order,
        {
            "custom_continuous_start_pending": 0,
            "custom_continuous_start_request_id": None,
        },
        update_modified=False,
    )


def _publish_result(
    user: str,
    work_order: str,
    status: str,
    message: str,
    stock_entry: str | None = None,
    pick_list: str | None = None,
) -> None:
    try:
        frappe.publish_realtime(
            REALTIME_EVENT,
            {
                "work_order": work_order,
                "status": status,
                "message": message,
                "stock_entry": stock_entry,
                "pick_list": pick_list,
            },
            user=user,
        )
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"C4Factory continuous Start notification failed ({work_order})",
        )
