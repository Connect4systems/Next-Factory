frappe.ui.form.on("Purchase Receipt", {
  company(frm) {
    set_purchase_receipt_warehouses(frm);
  },
});

frappe.ui.form.on("Purchase Receipt Item", {
  item_code(frm, cdt, cdn) {
    return set_purchase_receipt_warehouse(frm, cdt, cdn);
  },
});

async function set_purchase_receipt_warehouses(frm) {
  for (const row of frm.doc.items || []) {
    await set_purchase_receipt_warehouse(frm, row.doctype, row.name);
  }
}

async function set_purchase_receipt_warehouse(frm, cdt, cdn) {
  const row = locals[cdt] && locals[cdt][cdn];
  const itemCode = row && row.item_code;
  if (!row || !row.item_code || row.warehouse) {
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
    !currentRow.warehouse
  ) {
    await frappe.model.set_value(cdt, cdn, "warehouse", warehouse);
  }
}
