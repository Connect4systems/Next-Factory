import frappe
from frappe import _
from frappe.utils import cint


def copy_scrap_from_bom(doc, method=None):
    """
    When a new Work Order is created from a BOM, copy BOM.scrap_items
    into Work Order.c4_scrap_items (child table type: BOM Scrap Item).

    - Runs only if:
      * doc.bom_no is set
      * c4_scrap_items is empty
    - After copying, item_name / stock_uom / rate are ensured from Item master.
    """
    if not getattr(doc, "bom_no", None):
        return

    # if there are already scrap rows, do nothing
    if getattr(doc, "c4_scrap_items", None):
        if len(doc.c4_scrap_items):
            return

    bom = frappe.get_doc("BOM", doc.bom_no)

    # copy BOM Scrap Items rows
    for row in bom.scrap_items:
        # BOM Scrap Item uses 'stock_qty' as quantity field
        new_row = doc.append("c4_scrap_items", {
            "item_code": row.item_code,
            "item_name": row.item_name,
            # stock_uom will be overridden by _ensure_scrap_item_fields_from_item
            "stock_uom": row.stock_uom,
            "stock_qty": row.stock_qty,
            "rate": row.rate,
            "amount": row.amount,
        })

        # ensure fields from Item master (including overriding stock_uom)
        _ensure_scrap_item_fields_from_item(new_row)


def _ensure_scrap_item_fields_from_item(row):
    """
    Helper: for a BOM Scrap Item row, make sure:
    - item_name
    - stock_uom (ALWAYS from Item default)
    - rate (from Item valuation if missing/zero)

    Rate is taken from Item.valuation_rate (fallback last_purchase_rate).
    """
    if not row.item_code:
        return

    fields = ["item_name", "stock_uom", "valuation_rate", "last_purchase_rate"]
    item = frappe.get_cached_value("Item", row.item_code, fields, as_dict=True)

    # Always override stock_uom by Item default
    row.stock_uom = item.stock_uom

    # Fill item_name if missing
    if not getattr(row, "item_name", None):
        row.item_name = item.item_name

    # If rate is zero/None, take a valuation from Item
    if not row.rate:
        rate = item.valuation_rate or item.last_purchase_rate or 0.0
        row.rate = float(rate)


def update_scrap_and_costing(doc, method=None):
    """
    On every save/validate of Work Order:

    - Ensure each scrap row has item_name / stock_uom / rate (from Item if needed)
    - Ensure each scrap row has amount = stock_qty * rate
    - Compute:
        c4_scrap_material_cost  = sum(scrap.amount)
        c4_raw_material_cost    = value of Material Transfers to WIP (actual SE)
        c4_operating_cost       = actual Job Card operating cost
        c4_total_cost           = raw + operating - scrap
    """

    # --- 1) Scrap rows + Scrap Material Cost ---
    total_scrap_amount = 0.0

    for row in (doc.get("c4_scrap_items") or []):
        # Ensure fields come from Item master (overrides stock_uom)
        _ensure_scrap_item_fields_from_item(row)

        # In BOM Scrap Item, quantity fieldname is 'stock_qty'
        qty = row.get("stock_qty") or 0.0
        rate = row.rate or 0.0

        row.amount = float(qty) * float(rate)
        total_scrap_amount += (row.amount or 0.0)

    doc.c4_scrap_material_cost = total_scrap_amount

    # --- 2) Raw Material Cost from Material Transfer to WIP ---
    raw_material_cost = _get_raw_material_cost_from_material_transfers(doc.name, doc.wip_warehouse)
    doc.c4_raw_material_cost = raw_material_cost

    # --- 3) Operating Cost from actual Job Cards ---
    from c4factory.c4_manufacturing.stock_entry_hooks import _get_work_order_operating_cost_from_job_cards

    operating_cost = _get_work_order_operating_cost_from_job_cards(doc.name)

    doc.c4_operating_cost = operating_cost

    # --- 4) Total Cost = Raw + Operating - Scrap ---
    raw = doc.c4_raw_material_cost or 0.0
    op = doc.c4_operating_cost or 0.0
    scrap = doc.c4_scrap_material_cost or 0.0

    doc.c4_total_cost = float(raw) + float(op) - float(scrap)


def _get_raw_material_cost_from_material_transfers(work_order_name, wip_warehouse):
    """
    Sum value of materials TRANSFERRED INTO WIP for this WO.

    We consider all Stock Entry Detail rows where:
    - parent is a submitted Stock Entry
    - stock_entry_type = 'Material Transfer for Manufacture'
    - work_order = work_order_name
    - t_warehouse = wip_warehouse   (i.e., moved into WIP)

    We look at basic_amount / amount.
    """

    if not work_order_name or not wip_warehouse:
        return 0.0

    se_names = frappe.get_all(
        "Stock Entry",
        filters={
            "work_order": work_order_name,
            "docstatus": 1,
            "stock_entry_type": "Material Transfer for Manufacture",
        },
        pluck="name",
    )

    if not se_names:
        return 0.0

    rows = frappe.get_all(
        "Stock Entry Detail",
        filters={
            "parent": ["in", se_names],
            "t_warehouse": wip_warehouse,
        },
        fields=["basic_amount", "amount"],
    )

    total = 0.0
    for r in rows:
        value = r.get("basic_amount") or r.get("amount") or 0.0
        total += float(value)

    return total


def recalculate_costing_for_work_order(work_order_name):
    """
    Utility: load a Work Order, recompute costing, and save it.

    Used from Stock Entry hooks when a Material Transfer to WIP is submitted.
    """
    if not work_order_name:
        return

    wo = frappe.get_doc("Work Order", work_order_name)
    # Run the same logic used on validate
    update_scrap_and_costing(wo)

    # We might be saving a submitted WO; ignore update-after-submit warnings
    wo.flags.ignore_validate_update_after_submit = True
    wo.flags.ignore_permissions = True
    wo.save()


def set_source_warehouse_from_item_group(doc, method=None):
    """
    Set each Work Order required item's source warehouse from its Item Group.

    Rules:
    - Continuous-manufacture groups always use their custom manufacture warehouse.
    - Other groups retain an existing source warehouse; when empty, resolve the
      company/default warehouse with parent-group fallback.
    """
    rows = doc.get("required_items") or doc.get("items") or []
    if not rows:
        return

    item_group_cache = {}
    company = doc.get("company")
    warehouse_cache = {}

    for row in rows:
        if not row.get("item_code"):
            continue

        item_code = row.get("item_code")
        item_group = row.get("item_group") or item_group_cache.get(item_code)
        if item_group is None:
            item_group = frappe.db.get_value("Item", item_code, "item_group")
            item_group_cache[item_code] = item_group

        if not item_group:
            continue

        cache_key = (item_group, company)
        warehouse_details = warehouse_cache.get(cache_key)
        if warehouse_details is None:
            warehouse_details = _resolve_source_warehouse(item_group, company)
            warehouse_cache[cache_key] = warehouse_details

        warehouse, override_existing = warehouse_details
        if warehouse and (override_existing or not row.get("source_warehouse")):
            row.source_warehouse = warehouse


@frappe.whitelist()
def get_default_source_warehouse(
    item_code: str | None = None,
    company: str | None = None,
    item_group: str | None = None,
) -> str | None:
    """Return the applicable source warehouse for an item's Item Group."""
    if not item_group and item_code:
        item_group = frappe.db.get_value("Item", item_code, "item_group")
    if not item_group:
        return None

    warehouse, _override_existing = _resolve_source_warehouse(item_group, company)
    return warehouse


@frappe.whitelist()
def get_source_warehouse_details(
    item_code: str | None = None,
    company: str | None = None,
    item_group: str | None = None,
) -> dict:
    """Return the warehouse and whether it must replace the current row value."""
    if not item_group and item_code:
        item_group = frappe.db.get_value("Item", item_code, "item_group")
    if not item_group:
        return {"warehouse": None, "override_existing": False}

    warehouse, override_existing = _resolve_source_warehouse(item_group, company)
    return {
        "warehouse": warehouse,
        "override_existing": override_existing,
    }


def _resolve_source_warehouse(
    item_group: str,
    company: str | None = None,
) -> tuple[str | None, bool]:
    """
    Return (warehouse, override_existing).

    A continuous-manufacture Item Group uses its own custom warehouse and takes
    priority over ERPNext's populated value. All other groups keep the existing
    company/default warehouse lookup.
    """
    group = frappe.get_cached_doc("Item Group", item_group)
    if cint(group.get("custom_continues_manufacture")):
        warehouse = group.get("custom_warehouse")
        if not warehouse:
            frappe.throw(
                _("Manufacture Warehouse is required for Item Group {0}.").format(
                    frappe.bold(item_group)
                )
            )
        return warehouse, True

    return _get_default_warehouse_from_item_group(item_group, company), False


def _get_default_warehouse_from_item_group(item_group: str, company: str | None = None) -> str | None:
    """
    Return Item Group Defaults -> default_warehouse, traversing parent_item_group
    upward until a default warehouse is found.

    ERPNext stores Item/Item Group defaults in the child DocType "Item Default".
    Prefer a row for the Work Order company, then a company-less/global row.
    """
    seen = set()
    current = item_group

    while current and current not in seen:
        seen.add(current)
        group = frappe.get_cached_doc("Item Group", current)
        if not group:
            return None

        default_wh = _get_default_warehouse_from_item_group_defaults(group, company)
        if default_wh:
            return default_wh

        parent = group.get("parent_item_group")
        if not parent or parent == current:
            return None

        current = parent

    return None


def _get_default_warehouse_from_item_group_defaults(group, company: str | None = None) -> str | None:
    defaults = []
    for fieldname in ("item_group_defaults", "item_defaults", "defaults"):
        defaults.extend(group.get(fieldname) or [])

    direct_warehouse = group.get("default_warehouse") or group.get("warehouse")

    if defaults:
        if company:
            for row in defaults:
                warehouse = row.get("default_warehouse") or row.get("warehouse")
                if row.get("company") == company and warehouse:
                    return warehouse

        for row in defaults:
            warehouse = row.get("default_warehouse") or row.get("warehouse")
            if not row.get("company") and warehouse:
                return warehouse

        for row in defaults:
            warehouse = row.get("default_warehouse") or row.get("warehouse")
            if warehouse:
                return warehouse

    return direct_warehouse
