from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from c4factory.c4_manufacturing.work_order_hooks import (
    get_default_source_warehouse,
)


class SubPickList(Document):
    def validate(self):
        self._set_parent_values()
        self._validate_items()
        self._refresh_balances()

    def before_submit(self):
        self.status = "Open"

    def on_submit(self):
        _apply_additional_materials(self)
        _update_sub_pick_list_status(self.name)

    def before_cancel(self):
        submitted_entries = frappe.get_all(
            "Stock Entry",
            filters={
                "docstatus": 1,
                "custom_sub_pick_list": self.name,
            },
            pluck="name",
        )
        if submitted_entries:
            frappe.throw(
                _(
                    "Cancel submitted Stock Entries {0} before cancelling this "
                    "Sub Pick List."
                ).format(", ".join(submitted_entries))
            )
        _reverse_additional_materials(self)

    def on_cancel(self):
        self.db_set("status", "Cancelled", update_modified=False)

    def _set_parent_values(self):
        if not self.main_pick_list:
            return
        main = frappe.get_doc("Pick List", self.main_pick_list)
        if main.docstatus != 1:
            frappe.throw(_("Main Pick List {0} must be submitted").format(main.name))
        if not main.get("work_order"):
            frappe.throw(_("Main Pick List is not linked to a Work Order"))

        wo = frappe.get_doc("Work Order", main.work_order)
        if wo.docstatus != 1 or wo.status in {"Stopped", "Closed", "Completed", "Cancelled"}:
            frappe.throw(
                _("Work Order {0} is not active").format(wo.name)
            )
        if not wo.wip_warehouse:
            frappe.throw(_("Work Order {0} has no WIP Warehouse").format(wo.name))

        self.company = wo.company
        self.work_order = wo.name
        self.wip_warehouse = wo.wip_warehouse

    def _validate_items(self):
        if not self.items:
            frappe.throw(_("Add at least one additional material"))
        seen = set()
        for row in self.items:
            if not row.item_code or flt(row.qty) <= 0:
                frappe.throw(_("Every row requires an Item and Qty greater than zero"))
            item = frappe.get_cached_doc("Item", row.item_code)
            if not item.is_stock_item or item.disabled:
                frappe.throw(_("Item {0} must be an active stock item").format(row.item_code))
            row.item_name = item.item_name
            row.stock_uom = item.stock_uom
            if not row.source_warehouse:
                row.source_warehouse = get_default_source_warehouse(
                    item_code=row.item_code,
                    item_group=item.item_group,
                    company=self.company,
                )
            if not row.source_warehouse:
                frappe.throw(
                    _("Source Warehouse is required for item {0}").format(row.item_code)
                )
            key = (row.item_code, row.source_warehouse)
            if key in seen:
                frappe.throw(
                    _("Combine duplicate item {0} for warehouse {1} into one row").format(
                        *key
                    )
                )
            seen.add(key)

    def _refresh_balances(self):
        if self.is_new():
            for row in self.items:
                row.transferred_qty = 0
                row.balance_qty = flt(row.qty)
            return
        balances = _get_balances(self)
        for row in self.items:
            info = balances.get(row.name, {})
            row.transferred_qty = flt(info.get("transferred"))
            row.balance_qty = flt(info.get("balance"))


@frappe.whitelist()
def make_sub_pick_list(
    main_pick_list: str | None = None,
    pick_list: str | None = None,
) -> dict:
    # Accept the old client argument during asset-cache transitions.
    main_pick_list = (main_pick_list or pick_list or "").strip()
    if not main_pick_list:
        frappe.throw(_("Main Pick List is required"))
    main = frappe.get_doc("Pick List", main_pick_list)
    main.check_permission("read")
    if main.docstatus != 1 or not main.get("work_order"):
        frappe.throw(_("A submitted Pick List linked to a Work Order is required"))

    doc = frappe.new_doc("Sub Pick List")
    doc.main_pick_list = main.name
    doc.company = main.company
    doc.work_order = main.work_order
    doc.wip_warehouse = frappe.db.get_value(
        "Work Order", main.work_order, "wip_warehouse"
    )
    return doc.as_dict()


def _get_balances(doc_or_name) -> dict:
    doc = (
        frappe.get_doc("Sub Pick List", doc_or_name)
        if isinstance(doc_or_name, str)
        else doc_or_name
    )
    result = {
        row.name: {
            "qty": flt(row.qty),
            "transferred": 0.0,
            "balance": flt(row.qty),
            "item_code": row.item_code,
            "item_name": row.item_name,
        }
        for row in doc.items
    }
    if not result:
        return result

    rows = frappe.db.sql(
        """
        SELECT sed.custom_sub_pick_list_item,
               COALESCE(SUM(ABS(CASE
                   WHEN COALESCE(sed.transfer_qty, 0) != 0
                   THEN sed.transfer_qty ELSE sed.qty
               END)), 0) AS qty
        FROM `tabStock Entry Detail` sed
        INNER JOIN `tabStock Entry` se ON se.name = sed.parent
        WHERE se.docstatus = 1
          AND se.custom_sub_pick_list = %(sub_pick_list)s
          AND COALESCE(sed.custom_sub_pick_list_item, '') != ''
        GROUP BY sed.custom_sub_pick_list_item
        """,
        {"sub_pick_list": doc.name},
        as_dict=True,
    )
    for row in rows:
        if row.custom_sub_pick_list_item in result:
            result[row.custom_sub_pick_list_item]["transferred"] = flt(row.qty)
    for info in result.values():
        info["balance"] = (
            0.0
            if doc.manually_completed
            else max(info["qty"] - info["transferred"], 0.0)
        )
    return result


@frappe.whitelist()
def get_balance_rows(sub_pick_list: str) -> list[dict]:
    doc = frappe.get_doc("Sub Pick List", sub_pick_list)
    doc.check_permission("read")
    if doc.docstatus != 1 or doc.status == "Completed":
        return []
    return [
        {
            "sub_pick_list_item": name,
            "item_code": info["item_code"],
            "item_name": info["item_name"],
            "balance_qty": info["balance"],
        }
        for name, info in _get_balances(doc).items()
        if info["balance"] > 0.000001
    ]


@frappe.whitelist()
def make_partial_stock_entry(sub_pick_list: str, items_json: str) -> str:
    doc = frappe.get_doc("Sub Pick List", sub_pick_list)
    doc.check_permission("read")
    if doc.docstatus != 1 or doc.status == "Completed":
        frappe.throw(_("Sub Pick List must be submitted and open"))
    if not frappe.has_permission("Stock Entry", "create"):
        frappe.throw(_("Not permitted to create Stock Entry"), frappe.PermissionError)
    if (
        not frappe.get_meta("Stock Entry").has_field("custom_sub_pick_list")
        or not frappe.get_meta("Stock Entry Detail").has_field(
            "custom_sub_pick_list_item"
        )
    ):
        frappe.throw(_("Please run bench migrate before using Sub Pick Lists"))

    requested = frappe.parse_json(items_json) or []
    balances = _get_balances(doc)
    rows_by_name = {row.name: row for row in doc.items}

    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = "Material Transfer for Manufacture"
    se.purpose = "Material Transfer for Manufacture"
    se.company = doc.company
    se.work_order = doc.work_order
    se.pick_list = doc.main_pick_list
    se.custom_is_additional_material = 1
    se.custom_additional_material_pick_list = doc.main_pick_list
    se.custom_sub_pick_list = doc.name
    if se.meta.has_field("custom_work_order"):
        se.custom_work_order = doc.work_order
    if se.meta.has_field("custom_pick_list"):
        se.custom_pick_list = doc.main_pick_list
    if se.meta.has_field("to_warehouse"):
        se.to_warehouse = doc.wip_warehouse

    for requested_row in requested:
        row_name = requested_row.get("sub_pick_list_item")
        qty = flt(requested_row.get("qty"))
        source = rows_by_name.get(row_name)
        balance = flt((balances.get(row_name) or {}).get("balance"))
        if not source or qty <= 0:
            continue
        if qty > balance + 0.000001:
            frappe.throw(
                _("Item {0}: transfer quantity exceeds balance {1}").format(
                    source.item_code, balance
                )
            )
        target = se.append("items", {})
        target.item_code = source.item_code
        target.item_name = source.item_name
        target.qty = qty
        target.uom = source.stock_uom
        target.stock_uom = source.stock_uom
        target.conversion_factor = 1
        target.s_warehouse = source.source_warehouse
        target.t_warehouse = doc.wip_warehouse
        target.custom_sub_pick_list_item = source.name
        target.custom_work_order_item = source.work_order_item

    if not se.items:
        frappe.throw(_("Select at least one material with quantity greater than zero"))
    se.insert()
    return se.name


@frappe.whitelist()
def complete_sub_pick_list(sub_pick_list: str) -> dict:
    doc = frappe.get_doc("Sub Pick List", sub_pick_list)
    doc.check_permission("write")
    if doc.docstatus != 1:
        frappe.throw(_("Sub Pick List must be submitted"))

    balances = _get_balances(doc)
    wo = frappe.get_doc("Work Order", doc.work_order)
    wo_rows = {row.name: row for row in _get_additional_material_rows(wo)}
    changed = False
    for row in doc.items:
        waived = flt((balances.get(row.name) or {}).get("balance"))
        if waived <= 0 or not row.work_order_item:
            continue
        wo_row = wo_rows.get(row.work_order_item)
        if not wo_row:
            continue
        wo_row.required_qty = max(flt(wo_row.required_qty) - waived, 0.0)
        wo_row.custom_additional_material_qty = max(
            flt(wo_row.custom_additional_material_qty) - waived, 0.0
        )
        row.db_set(
            "required_contribution_qty",
            max(flt(row.required_contribution_qty) - waived, 0.0),
            update_modified=False,
        )
        changed = True
        if (
            wo_row.required_qty <= 0.000001
            and wo_row.custom_additional_material_qty <= 0.000001
        ):
            _clear_work_order_item_links(row, wo_row.name)
            wo.remove(wo_row)
    if changed:
        _save_work_order(wo)

    frappe.db.set_value(
        "Sub Pick List",
        doc.name,
        {"manually_completed": 1, "status": "Completed"},
    )
    return {"status": "Completed", "work_order": doc.work_order}


def update_from_stock_entry(doc, method=None):
    if not doc.get("custom_sub_pick_list"):
        return
    _update_sub_pick_list_status(doc.custom_sub_pick_list)
    _sync_work_order_transferred_quantities(doc.custom_sub_pick_list)


def prevent_main_pick_list_cancel(doc, method=None):
    active = frappe.get_all(
        "Sub Pick List",
        filters={"main_pick_list": doc.name, "docstatus": 1},
        pluck="name",
    )
    if active:
        frappe.throw(
            _("Cancel Sub Pick Lists {0} first").format(", ".join(active))
        )


def _update_sub_pick_list_status(name: str):
    if not name or not frappe.db.exists("Sub Pick List", name):
        return
    doc = frappe.get_doc("Sub Pick List", name)
    if doc.docstatus == 2:
        status = "Cancelled"
    elif doc.manually_completed:
        status = "Completed"
    else:
        status = (
            "Completed"
            if all(info["balance"] <= 0.000001 for info in _get_balances(doc).values())
            else "Open"
        )
    frappe.db.set_value("Sub Pick List", name, "status", status, update_modified=False)
    balances = _get_balances(doc)
    for row in doc.items:
        info = balances.get(row.name, {})
        frappe.db.set_value(
            "Sub Pick List Item",
            row.name,
            {
                "transferred_qty": flt(info.get("transferred")),
                "balance_qty": flt(info.get("balance")),
            },
            update_modified=False,
        )


def _sync_work_order_transferred_quantities(sub_pick_list: str):
    sub = frappe.get_doc("Sub Pick List", sub_pick_list)
    from c4factory.c4_manufacturing.stock_entry_hooks import (
        _set_work_order_item_balances,
    )

    for row in sub.items:
        if not row.work_order_item:
            continue
        transferred = _get_additional_material_transferred_qty(row.work_order_item)
        required, consumed = frappe.db.get_value(
            "Work Order Item",
            row.work_order_item,
            ["required_qty", "consumed_qty"],
        ) or (0, 0)
        frappe.db.set_value(
            "Work Order Item",
            row.work_order_item,
            "transferred_qty",
            transferred,
            update_modified=False,
        )
        _set_work_order_item_balances(
            row.work_order_item,
            flt(required),
            transferred,
            flt(consumed),
        )


def _apply_additional_materials(doc):
    wo = frappe.get_doc("Work Order", doc.work_order)
    additional_materials = _get_additional_material_rows(wo)
    mappings = []
    for row in doc.items:
        wo_row = next(
            (
                candidate
                for candidate in additional_materials
                if candidate.item_code == row.item_code
                and (candidate.source_warehouse or "") == (row.source_warehouse or "")
            ),
            None,
        )
        if not wo_row:
            item = frappe.get_cached_doc("Item", row.item_code)
            wo_row = wo.append(
                "custom_additional_material",
                {
                    "item_code": row.item_code,
                    "item_name": item.item_name,
                    "description": item.description,
                    "stock_uom": item.stock_uom,
                    "source_warehouse": row.source_warehouse,
                    "required_qty": 0,
                },
            )
            additional_materials = _get_additional_material_rows(wo)
        wo_row.required_qty = flt(wo_row.required_qty) + flt(row.qty)
        wo_row.custom_additional_material_qty = (
            flt(wo_row.custom_additional_material_qty) + flt(row.qty)
        )
        mappings.append((row, wo_row))
    _save_work_order(wo)
    for row, wo_row in mappings:
        row.work_order_item = wo_row.name
        row.required_contribution_qty = flt(row.qty)
        frappe.db.set_value(
            "Sub Pick List Item",
            row.name,
            {
                "work_order_item": row.work_order_item,
                "required_contribution_qty": row.required_contribution_qty,
            },
            update_modified=False,
        )


def _reverse_additional_materials(doc):
    wo = frappe.get_doc("Work Order", doc.work_order)
    by_name = {row.name: row for row in _get_additional_material_rows(wo)}
    for row in doc.items:
        contribution = flt(row.required_contribution_qty)
        wo_row = by_name.get(row.work_order_item)
        if not wo_row or contribution <= 0:
            continue
        wo_row.required_qty = max(flt(wo_row.required_qty) - contribution, 0.0)
        wo_row.custom_additional_material_qty = max(
            flt(wo_row.custom_additional_material_qty) - contribution, 0.0
        )
        if wo_row.required_qty <= 0.000001 and wo_row.custom_additional_material_qty <= 0.000001:
            _clear_work_order_item_links(row, wo_row.name)
            wo.remove(wo_row)
    _save_work_order(wo)


def _get_additional_material_rows(wo):
    if not wo.meta.has_field("custom_additional_material"):
        frappe.throw(
            _(
                "Work Order Additional Material table is not installed. "
                "Please run bench migrate and try again."
            )
        )
    return wo.get("custom_additional_material") or []


def _get_additional_material_transferred_qty(work_order_item: str) -> float:
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
              AND COALESCE(se.custom_sub_pick_list, '') != ''
              AND sed.custom_work_order_item = %(work_order_item)s
              AND COALESCE(sed.custom_sub_pick_list_item, '') != ''
            """,
            {"work_order_item": work_order_item},
        )[0][0]
    )


def _save_work_order(wo):
    wo.flags.ignore_validate_update_after_submit = True
    wo.flags.ignore_permissions = True
    wo.save(ignore_permissions=True)


def _clear_work_order_item_links(sub_row, work_order_item: str):
    sub_row.work_order_item = None
    frappe.db.set_value(
        "Sub Pick List Item",
        sub_row.name,
        "work_order_item",
        None,
        update_modified=False,
    )
    stock_rows = frappe.get_all(
        "Stock Entry Detail",
        filters={"custom_sub_pick_list_item": sub_row.name},
        pluck="name",
    )
    for stock_row in stock_rows:
        frappe.db.set_value(
            "Stock Entry Detail",
            stock_row,
            "custom_work_order_item",
            None,
            update_modified=False,
        )
