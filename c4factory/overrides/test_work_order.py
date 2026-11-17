from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from c4factory.overrides.work_order import WorkOrder


class TestWorkOrderRequiredQuantities(FrappeTestCase):
  def test_missing_scrap_warehouse_uses_manufacturing_settings(self):
    wo = frappe._dict({"scrap_warehouse": None})
    with patch(
      "c4factory.overrides.work_order.frappe.db.get_single_value",
      return_value="Scrap - C4",
    ) as get_default:
      WorkOrder._set_default_scrap_warehouse(wo)
    self.assertEqual(wo.scrap_warehouse, "Scrap - C4")
    get_default.assert_called_once_with("Manufacturing Settings", "default_scrap_warehouse")

  def test_explicit_scrap_warehouse_is_preserved(self):
    wo = frappe._dict({"scrap_warehouse": "Custom Scrap - C4"})
    with patch("c4factory.overrides.work_order.frappe.db.get_single_value") as get_default:
      WorkOrder._set_default_scrap_warehouse(wo)
    get_default.assert_not_called()
    self.assertEqual(wo.scrap_warehouse, "Custom Scrap - C4")

  def test_submitted_partial_material_transfer_starts_work_order(self):
    work_order = frappe._dict(
      {
        "name": "WO-TEST",
        "docstatus": 1,
        "status": "Not Started",
        "qty": 20,
        "produced_qty": 0,
        "material_transferred_for_manufacturing": 0,
        "operations": [],
      }
    )

    with (
      patch("c4factory.overrides.work_order.frappe.db.exists", return_value=True),
      patch("c4factory.overrides.work_order.frappe.db.set_value") as set_value,
    ):
      WorkOrder.set_status(work_order)

    self.assertEqual(work_order.status, "In Process")
    set_value.assert_called_once_with(
      "Work Order", "WO-TEST", "status", "In Process", update_modified=False
    )

  def test_normalizes_exploded_bom_quantities_once(self):
    work_order = frappe._dict(
      {
        "bom_no": "BOM-TEST",
        "qty": 43,
        "use_multi_level_bom": 1,
        "required_items": [
          frappe._dict({"item_code": "RM-1", "required_qty": 2.8638, "rate": 10}),
          frappe._dict({"item_code": "RM-2", "required_qty": 129, "rate": 2}),
        ],
      }
    )
    bom_rows = [
      frappe._dict({"item_code": "RM-1", "stock_qty": 0.0333}),
      frappe._dict({"item_code": "RM-2", "stock_qty": 1.5}),
    ]

    with (
      patch("c4factory.overrides.work_order.frappe.get_all", return_value=bom_rows) as get_all,
      patch(
        "c4factory.overrides.work_order.frappe.get_cached_value",
        return_value=1,
      ),
    ):
      WorkOrder._normalize_required_item_quantities(work_order)

    self.assertAlmostEqual(work_order.required_items[0].required_qty, 1.4319)
    self.assertAlmostEqual(work_order.required_items[0].amount, 14.319)
    self.assertAlmostEqual(work_order.required_items[1].required_qty, 64.5)
    self.assertAlmostEqual(work_order.required_items[1].amount, 129)
    get_all.assert_called_once_with(
      "BOM Explosion Item",
      filters={"parent": "BOM-TEST", "docstatus": ("<", 2)},
      fields=["item_code", "stock_qty"],
    )

  def test_scales_direct_bom_rows_by_bom_output_quantity(self):
    work_order = frappe._dict(
      {
        "bom_no": "BOM-TEST",
        "qty": 10,
        "use_multi_level_bom": 0,
        "required_items": [
          frappe._dict({"item_code": "RM-1", "required_qty": 0, "rate": 4}),
        ],
      }
    )
    bom_rows = [
      frappe._dict({"item_code": "RM-1", "stock_qty": 2}),
      frappe._dict({"item_code": "RM-1", "stock_qty": 3}),
    ]

    with (
      patch("c4factory.overrides.work_order.frappe.get_all", return_value=bom_rows) as get_all,
      patch(
        "c4factory.overrides.work_order.frappe.get_cached_value",
        return_value=5,
      ),
    ):
      WorkOrder._normalize_required_item_quantities(work_order)

    self.assertAlmostEqual(work_order.required_items[0].required_qty, 10)
    self.assertAlmostEqual(work_order.required_items[0].amount, 40)
    get_all.assert_called_once_with(
      "BOM Item",
      filters={"parent": "BOM-TEST", "docstatus": ("<", 2)},
      fields=["item_code", "stock_qty"],
    )
