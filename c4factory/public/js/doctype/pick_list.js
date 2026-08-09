// c4factory/public/js/doctype/pick_list.js

// C4Factory — Partial Stock Entry from Pick List
// ----------------------------------------------
// من Pick List (submitted) نقدر نعمل Stock Entry جزئي بناءً على
// balance الفعلي لكل صف، بدون ما نلمس منطق ERPNext الأصلي للـ Pick List.

frappe.ui.form.on("Pick List", {
  async refresh(frm) {
    configure_work_order_pick_list_grid(frm);
    // نشتغل فقط لما تكون الوثيقة Submitted
    if (frm.doc.docstatus !== 1) return;

    frm.add_custom_button(
      __(
        cint(frm.doc.custom_continuous_production)
          ? "Transfer Partially"
          : "Create Draft Stock Entry"
      ),
      () => open_partial_se_dialog(frm),
      __("Factory")
    );

    if (frm.doc.status !== "Completed") {
      frm.add_custom_button(
        __("Finish Pick List"),
        () => complete_pick_list(frm),
        __("Factory")
      );
    }

    if (frm.doc.work_order) {
      frm.add_custom_button(
        __("Additional Material"),
        () => open_additional_material_stock_entry(frm),
        __("Factory")
      );
    }

    if (frm.doc.work_order && !(await is_work_order_operation_disabled(frm.doc.work_order))) {
      frm.add_custom_button(
        __("Create Job Card"),
        () => create_job_cards_from_pick_list(frm),
        __("Factory")
      );
    }
  },
});

function configure_work_order_pick_list_grid(frm) {
  if (!frm.doc.work_order) return;

  const table_field = frm.fields_dict.locations;
  const grid = table_field && table_field.grid;
  if (!grid) return;

  const lock_grid = () => {
    table_field.df.cannot_add_rows = 1;
    table_field.df.cannot_delete_rows = 1;
    grid.df.cannot_add_rows = 1;
    grid.df.cannot_delete_rows = 1;
    grid.cannot_add_rows = true;
    grid.cannot_delete_rows = true;

    [
      "item_code",
      "item",
      "qty",
      "custom_pl_qty",
      "stock_qty",
      "qty_in_stock_uom",
      "warehouse",
      "uom",
      "stock_uom",
      "conversion_factor",
      "custom_work_order_item",
    ].forEach((fieldname) => {
      const df = frappe.meta.get_docfield(
        "Pick List Item",
        fieldname,
        frm.doc.name
      );
      if (df) {
        grid.update_docfield_property(fieldname, "read_only", 1);
      }
    });
    grid.refresh();
  };

  lock_grid();
  clearTimeout(frm.__c4_lock_pick_list_grid);
  frm.__c4_lock_pick_list_grid = setTimeout(lock_grid, 0);
}

async function is_work_order_operation_disabled(work_order) {
  if (!work_order) return false;

  const { message } = await frappe.db.get_value(
    "Work Order",
    work_order,
    "custom_disable_operation"
  );

  return !!(message && cint(message.custom_disable_operation));
}

function complete_pick_list(frm) {
  frappe.confirm(
    __(
      "Finish this Pick List operationally? Remaining balances will stay available for later material transfers while the Work Order is still open."
    ),
    async () => {
      try {
        await frappe.call({
          method: "c4factory.api.work_order_flow.complete_pick_list",
          args: {
            pick_list: frm.doc.name,
          },
          freeze: true,
          freeze_message: __("Completing Pick List..."),
        });

        frappe.show_alert({
          message: __("Pick List finished. Remaining balances are still transferable."),
          indicator: "green",
        });
        await frm.reload_doc();
      } catch (e) {
        console.error(e);
        frappe.msgprint(__("Failed to complete Pick List."));
      }
    }
  );
}

async function open_additional_material_stock_entry(frm) {
  try {
    const { message } = await frappe.call({
      method:
        "c4factory.c4factory.doctype.sub_pick_list.sub_pick_list.make_sub_pick_list",
      args: {
        main_pick_list: frm.doc.name,
      },
      freeze: true,
      freeze_message: __("Preparing Sub Pick List..."),
    });

    if (!message) {
      frappe.msgprint(__("Server did not return a Sub Pick List."));
      return;
    }

    const docs = frappe.model.sync(message);
    if (!docs.length) {
      frappe.msgprint(__("Could not open the Sub Pick List."));
      return;
    }

    frappe.set_route("Form", docs[0].doctype, docs[0].name);
  } catch (e) {
    console.error(e);
    frappe.msgprint(__("Failed to prepare Sub Pick List."));
  }
}

// ----------------------------------------------
// Dialog logic
// ----------------------------------------------

async function open_partial_se_dialog(frm) {
  try {
    const { message } = await frappe.call({
      method: "c4factory.api.work_order_flow.get_pick_list_balance_rows",
      args: {
        pick_list: frm.doc.name,
      },
      freeze: true,
      freeze_message: __("Loading balance rows..."),
    });

    const rows = message || [];
    if (!rows.length) {
      frappe.msgprint(__("No remaining balance to transfer for this Pick List."));
      return;
    }

    const d = new frappe.ui.Dialog({
      title: __(
        cint(frm.doc.custom_continuous_production)
          ? "Submit Partial Material Transfer"
          : "Create Draft Material Transfer"
      ),
      size: "large",
      primary_action_label: __(
        cint(frm.doc.custom_continuous_production)
          ? "Transfer and Submit"
          : "Create Draft Stock Entry"
      ),
      primary_action: () => submit_partial_se(frm, d, rows),
    });

    // نبني جدول HTML بسيط
    const $wrapper = d.body;
    $wrapper.innerHTML = `
      <div class="partial-se-wrapper">
        <table class="table table-bordered table-sm" style="margin-top: 10px;">
          <thead>
            <tr>
              <th style="width:40px; text-align:center;">
                <input type="checkbox" data-role="select-all">
              </th>
              <th>${__("Item Code")}</th>
              <th>${__("Item Name")}</th>
              <th style="width:120px; text-align:right;">${__("Balance Qty")}</th>
              <th style="width:140px; text-align:right;">${__("Transfer Qty")}</th>
            </tr>
          </thead>
          <tbody>
            ${rows
              .map(
                (r, idx) => `
              <tr data-idx="${idx}">
                <td style="text-align:center;">
                  <input type="checkbox" data-role="row-select" data-idx="${idx}">
                </td>
                <td>${frappe.utils.escape_html(r.item_code || "")}</td>
                <td>${frappe.utils.escape_html(r.item_name || "")}</td>
                <td style="text-align:right;">
                  ${frappe.format(r.balance_qty, { fieldtype: "Float", precision: 3 })}
                </td>
                <td style="text-align:right;">
                  <input type="number"
                         min="0"
                         step="0.001"
                         class="form-control input-sm"
                         data-role="transfer-qty"
                         data-idx="${idx}"
                         value="${r.balance_qty}">
                </td>
              </tr>
            `
              )
              .join("")}
          </tbody>
        </table>
      </div>
    `;

    // select-all checkbox
    const selectAll = $wrapper.querySelector('input[data-role="select-all"]');
    if (selectAll) {
      selectAll.addEventListener("change", (e) => {
        const checked = e.target.checked;
        $wrapper
          .querySelectorAll('input[data-role="row-select"]')
          .forEach((cb) => (cb.checked = checked));
      });
    }

    d.show();
  } catch (e) {
    console.error(e);
    frappe.msgprint(__("Failed to load Pick List balances. See console for details."));
  }
}

// ----------------------------------------------
// Submit partial Stock Entry
// ----------------------------------------------

async function submit_partial_se(frm, dialog, rows) {
  const $wrapper = dialog.body;
  const items = [];

  rows.forEach((r, idx) => {
    const cb = $wrapper.querySelector(
      `input[data-role="row-select"][data-idx="${idx}"]`
    );
    if (!cb || !cb.checked) return;

    const qtyInput = $wrapper.querySelector(
      `input[data-role="transfer-qty"][data-idx="${idx}"]`
    );
    const qty = qtyInput ? parseFloat(qtyInput.value || "0") : 0;

    if (qty > 0) {
      items.push({
        pl_item_name: r.pl_item_name,
        qty,
      });
    }
  });

  if (!items.length) {
    frappe.msgprint(__("Please select at least one row with Transfer Qty > 0."));
    return;
  }

  try {
    const { message } = await frappe.call({
      method: "c4factory.api.work_order_flow.make_partial_stock_entry_from_pick_list",
      args: {
        pick_list: frm.doc.name,
        items_json: JSON.stringify(items),
      },
      freeze: true,
      freeze_message: __("Creating Stock Entry..."),
    });

    if (!message) {
      frappe.msgprint(__("Server did not return a Stock Entry name."));
      return;
    }

    dialog.hide();
    if (cint(frm.doc.custom_continuous_production)) {
      frappe.show_alert({
        message: __("Stock Entry {0} was submitted successfully.", [message]),
        indicator: "green",
      });
    }
    frappe.set_route("Form", "Stock Entry", message);
  } catch (e) {
    console.error(e);
    // Frappe عادةً يعرض رسالة الخطأ تلقائياً، بس نضيف رسالة عامة
    frappe.msgprint(__("Failed to create Stock Entry. Check server error log."));
  }
}

async function create_job_cards_from_pick_list(frm) {
  try {
    const { message } = await frappe.call({
      method: "c4factory.api.work_order_flow.create_job_cards_from_pick_list",
      args: {
        pick_list: frm.doc.name,
      },
      freeze: true,
      freeze_message: __("Creating Job Cards..."),
    });

    const job_cards = message || [];
    if (!job_cards.length) {
      return;
    }

    frappe.show_alert({
      message: __("Created {0} Job Card(s)", [job_cards.length]),
      indicator: "green",
    });

    if (job_cards.length === 1) {
      frappe.set_route("Form", "Job Card", job_cards[0]);
    } else {
      frappe.set_route("List", "Job Card", {
        custom_pick_list: frm.doc.name,
      });
    }
  } catch (e) {
    console.error(e);
    frappe.msgprint(__("Failed to create Job Cards. Check server error log."));
  }
}
