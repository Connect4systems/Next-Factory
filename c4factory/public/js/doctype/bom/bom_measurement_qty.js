// c4factory/public/js/doctype/bom/bom_measurement_qty.js
// Final logic: Area / Perimeter / Value / NOS
// Keep Stock UOM = Item.stock_uom (do not change it logically)

frappe.ui.form.on("BOM Item", {
  item_code: function (frm, cdt, cdn) {
    const row = frappe.get_doc(cdt, cdn);
    if (!row.item_code) return;

    const toFloat = (v) => {
      const x = parseFloat(v);
      return isNaN(x) ? 0 : x;
    };

    const normalizeType = (v) =>
      (v || "").toString().trim().toLowerCase();

    // Read BOM header dimensions and FG qty from custom fields
    const W = toFloat(frm.doc.custom_width);
    const H = toFloat(frm.doc.custom_height);
    const D = toFloat(frm.doc.custom_depth);
    const FG_QTY = toFloat(frm.doc.quantity || 1);

    // Fetch Item fields
    frappe.call({
      method: "frappe.client.get_value",
      args: {
        doctype: "Item",
        filters: { name: row.item_code },
        fieldname: [
          "custom_measurement_type",
          "custom_techniacl_uom",
          "stock_uom",          // <-- IMPORTANT: original stock UOM
        ],
      },
      callback: function (res) {
        if (!res || !res.message) return;

        const data = res.message;
        const measurement_type_raw = data.custom_measurement_type || "";
        const mt = normalizeType(measurement_type_raw); // area / perimeter / value / nos
        const tech_uom = data.custom_techniacl_uom || "";
        const item_stock_uom = data.stock_uom || row.stock_uom;

        // 1️⃣ Set BOM Item UOM (display UOM) - DO NOT touch stock qty here
        if (tech_uom) {
          frappe.model.set_value(cdt, cdn, "uom", tech_uom);
        }

        // 2️⃣ NOS or empty → qty is always manual
        if (!mt || mt === "nos") {
          // لكن لازم نضمن أن Stock UOM ثابت من الـ Item
          if (item_stock_uom) {
            frappe.model.set_value(cdt, cdn, "stock_uom", item_stock_uom);
          }
          return;
        }

        // 3️⃣ Don't overwrite qty if user already filled it
        const current_qty = toFloat(row.qty);
        if (current_qty > 0) {
          if (item_stock_uom) {
            frappe.model.set_value(cdt, cdn, "stock_uom", item_stock_uom);
          }
          return;
        }

        const has_dim = (val) => toFloat(val) > 0;

        // 4️⃣ Validate required dimensions
        if (mt === "area" || mt === "perimeter") {
          if (!has_dim(W) || !has_dim(H)) {
            frappe.msgprint(
              __(
                "Missing Width/Height on BOM. Cannot auto-calculate qty for item {0}.",
                [row.item_code]
              )
            );
            // Ensure stock_uom reset anyway
            if (item_stock_uom) {
              frappe.model.set_value(cdt, cdn, "stock_uom", item_stock_uom);
            }
            return;
          }
        } else if (mt === "value") {
          if (!has_dim(W) || !has_dim(H) || !has_dim(D)) {
            frappe.msgprint(
              __(
                "Missing Width/Height/Depth on BOM. Cannot auto-calculate qty for item {0}.",
                [row.item_code]
              )
            );
            if (item_stock_uom) {
              frappe.model.set_value(cdt, cdn, "stock_uom", item_stock_uom);
            }
            return;
          }
        }

        // 5️⃣ Apply formulas EXACTLY as specified
        let new_qty = 0;

        if (mt === "area") {
          // Area → qty = (Width × Height) × BOM_qty
          new_qty = W * H * FG_QTY;
        } else if (mt === "perimeter") {
          // Perimeter → qty = (2 × (Width + Height)) × BOM_qty
          new_qty = 2 * (W + H) * FG_QTY;
        } else if (mt === "value") {
          // Value → qty = (Width × Height × Depth) × BOM_qty
          new_qty = W * H * D * FG_QTY;
        } else {
          if (item_stock_uom) {
            frappe.model.set_value(cdt, cdn, "stock_uom", item_stock_uom);
          }
          return;
        }

        // 6️⃣ Set calculated qty (rounded) + reset Stock UOM to Item's default
        if (new_qty > 0) {
          const rounded = Number(new_qty.toFixed(6));
          frappe.model.set_value(cdt, cdn, "qty", rounded);
        }

        // Always force stock_uom back to the Item's default UOM
        if (item_stock_uom) {
          frappe.model.set_value(cdt, cdn, "stock_uom", item_stock_uom);
        }
      },
    });
  },
});
