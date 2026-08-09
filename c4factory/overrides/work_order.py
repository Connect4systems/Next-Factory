from erpnext.manufacturing.doctype.work_order.work_order import WorkOrder as ERPNextWorkOrder
import frappe
from frappe import _
from frappe.utils import flt
from c4factory.c4_manufacturing.work_order_hooks import set_source_warehouse_from_item_group


class WorkOrder(ERPNextWorkOrder):
  """
  Custom Work Order for c4factory.

  Goal:
  - First time: when there are NO required_items, use ERPNext logic to
    populate from BOM.
  - After that: if required_items already has rows, do not rebuild or
    reset them automatically, so the user can freely edit required_qty,
    add or remove rows.
  - Provide a stronger `set_status` implementation that follows the
    standard status list while preserving other logic.
  """

  def set_required_items(self, reset_only_qty: bool = False):  # signature must match core
    """
    ERPNext sometimes calls set_required_items(reset_only_qty=True).
    We accept the same argument, but if rows already exist we do nothing.
    """

    # If there are no rows yet, use standard behaviour (populate from BOM)
    if not self.get("required_items"):
      result = super().set_required_items(reset_only_qty=reset_only_qty)
      self._normalize_required_item_quantities()
      set_source_warehouse_from_item_group(self)
      return result

    # If rows already exist:
    # - when reset_only_qty is True, core would normally recompute required_qty.
    # - we skip this to keep the user edits.
    set_source_warehouse_from_item_group(self)
    return

  def validate(self):
    super().validate()
    from c4factory.api.work_order_mold import validate_and_set_mold_materials

    validate_and_set_mold_materials(self)

  def _normalize_required_item_quantities(self):
    """
    Recalculate quantities from the BOM child table without joining Item Default.

    ERPNext's BOM helper joins Item Default before grouping the BOM rows. If an
    item has duplicate defaults for the Work Order company, that join multiplies
    its required quantity. Keep the standard fetch for item metadata, then make
    the quantity authoritative from the BOM itself:

      required_qty = sum(stock_qty) / BOM quantity * Work Order quantity
    """
    if not self.bom_no or not self.qty or not self.get("required_items"):
      return

    table = "BOM Explosion Item" if self.get("use_multi_level_bom") else "BOM Item"
    bom_rows = frappe.get_all(
      table,
      filters={
        "parent": self.bom_no,
        "docstatus": ("<", 2),
      },
      fields=["item_code", "stock_qty"],
    )

    bom_qty = flt(frappe.get_cached_value("BOM", self.bom_no, "quantity")) or 1
    quantity_scale = flt(self.qty) / bom_qty
    required_by_item = {}

    for bom_row in bom_rows:
      item_code = bom_row.get("item_code")
      if not item_code:
        continue

      required_by_item[item_code] = (
        flt(required_by_item.get(item_code))
        + flt(bom_row.get("stock_qty")) * quantity_scale
      )

    for required_item in self.get("required_items"):
      item_code = required_item.get("item_code")
      if item_code not in required_by_item:
        continue

      required_item.required_qty = required_by_item[item_code]
      required_item.amount = flt(required_item.get("rate")) * required_item.required_qty

  def on_submit(self):
    """
    Keep ERPNext's standard Work Order submit bookkeeping, but do not create
    Job Cards automatically. Users can still create Job Cards manually from
    the Work Order or from a Pick List.
    """
    if not self.wip_warehouse and not self.skip_transfer:
      frappe.throw(_("Work-in-Progress Warehouse is required before Submit"))

    if not self.fg_warehouse:
      frappe.throw(_("For Warehouse is required before Submit"))

    if self.production_plan and frappe.db.exists(
      "Production Plan Item Reference", {"parent": self.production_plan}
    ):
      self.update_work_order_qty_in_combined_so()
    else:
      self.update_work_order_qty_in_so()

    self.update_ordered_qty()
    self.update_reserved_qty_for_production()
    self.update_completed_qty_in_material_request()
    self.update_planned_qty()

  def set_status(self):
    """
    Compute Work Order `status` to follow the standard sequence:
    Draft, Submitted, Not Started, In Process, Completed, Stopped, Closed, Cancelled.

    - Keep `Stopped` and `Closed` if already set.
    - Use `Draft` when docstatus == 0, `Cancelled` when docstatus == 2.
    - For submitted docs, decide between Not Started / In Process / Completed
      based on produced qty, transferred material, and operations.
    """
    try:
      super().set_status()
    except Exception:
      # If core set_status is unavailable or errors, continue with our logic
      pass

    # Determine base status from docstatus first
    docstatus = getattr(self, "docstatus", 0)
    if docstatus == 0:
      new_status = "Draft"
    elif docstatus == 2:
      new_status = "Cancelled"
    else:
      # Preserve explicit stopped/closed states
      current = (getattr(self, "status", None) or "")
      if current in ("Stopped", "Closed"):
        new_status = current
      else:
        qty = flt(getattr(self, "qty", 0))
        produced = flt(getattr(self, "produced_qty", 0))
        transferred = flt(getattr(self, "material_transferred_for_manufacturing", 0))
        material_transfer_started = bool(
          frappe.db.exists(
            "Stock Entry",
            {
              "work_order": self.name,
              "docstatus": 1,
              "stock_entry_type": "Material Transfer for Manufacture",
            },
          )
          or frappe.db.exists(
            "Stock Entry",
            {
              "work_order": self.name,
              "docstatus": 1,
              "purpose": "Material Transfer for Manufacture",
            },
          )
        )

        # Check operations for any progress/completed work
        in_process = False
        for op in (getattr(self, "operations") or []):
          if flt(op.get("completed_qty") or 0) > 0 or flt(op.get("progress") or 0) > 0:
            in_process = True
            break

        if qty and produced >= qty:
          new_status = "Completed"
        elif transferred > 0 or produced > 0 or in_process or material_transfer_started:
          new_status = "In Process"
        else:
          # When submitted but nothing started
          new_status = "Not Started"

    # Apply status if changed
    if getattr(self, "status", None) != new_status:
      self.status = new_status
      # Persist immediately when the document exists in DB
      if getattr(self, "name", None):
        try:
          frappe.db.set_value(
            "Work Order",
            self.name,
            "status",
            new_status,
            update_modified=False,
          )
        except Exception:
          frappe.log_error(frappe.get_traceback(), "C4Factory: WorkOrder.set_status db_set failed")
