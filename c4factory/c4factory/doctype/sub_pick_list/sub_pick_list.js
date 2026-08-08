frappe.ui.form.on("Sub Pick List", {
  setup(frm) {
    frm.set_query("item_code", "items", () => ({
      filters: { is_stock_item: 1, disabled: 0 },
    }));
  },

  refresh(frm) {
    if (frm.doc.docstatus !== 1 || frm.doc.status === "Completed") return;

    frm.add_custom_button(
      __("Create Partial Stock Entry"),
      () => open_partial_transfer_dialog(frm),
      __("Factory")
    );
    frm.add_custom_button(
      __("Completed"),
      () => complete_sub_pick_list(frm),
      __("Factory")
    );
  },
});

async function open_partial_transfer_dialog(frm) {
  const { message } = await frappe.call({
    method:
      "c4factory.c4factory.doctype.sub_pick_list.sub_pick_list.get_balance_rows",
    args: { sub_pick_list: frm.doc.name },
    freeze: true,
    freeze_message: __("Loading balances..."),
  });
  const rows = message || [];
  if (!rows.length) {
    frappe.msgprint(__("No remaining balance."));
    return;
  }

  const dialog = new frappe.ui.Dialog({
    title: __("Create Partial Stock Entry"),
    size: "large",
    fields: [{ fieldname: "materials", fieldtype: "HTML" }],
    primary_action_label: __("Create Stock Entry"),
    primary_action: async () => {
      const items = [];
      rows.forEach((row, index) => {
        const selected = dialog.$wrapper.find(
          `[data-select="${index}"]`
        )[0];
        const qtyInput = dialog.$wrapper.find(`[data-qty="${index}"]`)[0];
        const qty = qtyInput ? flt(qtyInput.value) : 0;
        if (selected && selected.checked && qty > 0) {
          items.push({
            sub_pick_list_item: row.sub_pick_list_item,
            qty,
          });
        }
      });
      if (!items.length) {
        frappe.msgprint(__("Select at least one material."));
        return;
      }
      const { message: stockEntry } = await frappe.call({
        method:
          "c4factory.c4factory.doctype.sub_pick_list.sub_pick_list.make_partial_stock_entry",
        args: {
          sub_pick_list: frm.doc.name,
          items_json: JSON.stringify(items),
        },
        freeze: true,
        freeze_message: __("Creating Stock Entry..."),
      });
      dialog.hide();
      frappe.set_route("Form", "Stock Entry", stockEntry);
    },
  });

  const html = `
    <table class="table table-bordered">
      <thead><tr>
        <th style="width:40px"></th>
        <th>${__("Item")}</th>
        <th>${__("Item Name")}</th>
        <th>${__("Balance Qty")}</th>
        <th>${__("Transfer Qty")}</th>
      </tr></thead>
      <tbody>
        ${rows
          .map(
            (row, index) => `<tr>
              <td><input type="checkbox" data-select="${index}"></td>
              <td>${frappe.utils.escape_html(row.item_code || "")}</td>
              <td>${frappe.utils.escape_html(row.item_name || "")}</td>
              <td>${frappe.format(row.balance_qty, { fieldtype: "Float" })}</td>
              <td><input class="form-control" type="number" min="0"
                max="${row.balance_qty}" step="0.001" data-qty="${index}"
                value="${row.balance_qty}"></td>
            </tr>`
          )
          .join("")}
      </tbody>
    </table>`;
  dialog.fields_dict.materials.$wrapper.html(html);
  dialog.show();
}

function complete_sub_pick_list(frm) {
  frappe.confirm(
    __("Complete this Sub Pick List and waive its remaining balances?"),
    async () => {
      await frappe.call({
        method:
          "c4factory.c4factory.doctype.sub_pick_list.sub_pick_list.complete_sub_pick_list",
        args: { sub_pick_list: frm.doc.name },
        freeze: true,
        freeze_message: __("Completing Sub Pick List..."),
      });
      await frm.reload_doc();
    }
  );
}
