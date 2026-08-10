frappe.ui.form.on("Material Request", {
  company(frm) {
    set_material_request_source_warehouses(frm);
  },

  material_request_type(frm) {
    set_material_request_source_warehouses(frm);
  },
});

frappe.ui.form.on("Material Request Item", {
  item_code(frm, cdt, cdn) {
    return set_material_request_source_warehouse(frm, cdt, cdn);
  },
});

async function set_material_request_source_warehouses(frm) {
  for (const row of frm.doc.items || []) {
    await set_material_request_source_warehouse(frm, row.doctype, row.name);
  }
}

async function set_material_request_source_warehouse(frm, cdt, cdn) {
  const row = locals[cdt] && locals[cdt][cdn];
  const itemCode = row && row.item_code;
  if (
    frm.doc.material_request_type !== "Material Transfer" ||
    !row ||
    !row.item_code ||
    row.from_warehouse
  ) {
    return;
  }

  const { message: warehouse } = await frappe.call({
    method: "c4factory.c4_manufacturing.work_order_hooks.get_default_source_warehouse",
    args: {
      item_code: row.item_code,
      item_group: row.item_group,
      company: frm.doc.company,
    },
  });

  const currentRow = locals[cdt] && locals[cdt][cdn];
  if (
    warehouse &&
    currentRow &&
    currentRow.item_code === itemCode &&
    !currentRow.from_warehouse
  ) {
    await frappe.model.set_value(cdt, cdn, "from_warehouse", warehouse);
  }
}
