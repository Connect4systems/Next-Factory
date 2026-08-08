// c4factory • Work Order — allow editing required_qty in items

frappe.ui.form.on("Work Order", {
  refresh(frm) {
    configure_required_items_grid(frm);
    set_source_warehouses(frm);
    configure_continuous_start_button(frm);
    register_continuous_start_listener();
    hide_create_job_card_button(frm);
    refresh_material_transferred_qty(frm);
  },
  onload_post_render(frm) {
    configure_required_items_grid(frm);
    hide_create_job_card_button(frm);
  },
  bom_no(frm) {
    setTimeout(() => set_source_warehouses(frm), 800);
  },
  company(frm) {
    set_source_warehouses(frm);
  },
  custom_disable_operation(frm) {
    hide_create_job_card_button(frm);
  }
});

frappe.ui.form.on("Work Order Item", {
  form_render(frm) {
    configure_required_items_grid(frm);
  },
  item_code(frm, cdt, cdn) {
    set_source_warehouse_from_item_group(frm, cdt, cdn);
  }
});

// Keep draft Work Order materials fully editable. ERPNext marks this grid as
// non-addable/non-deletable after populating it from the BOM.
function configure_required_items_grid(frm) {
  if (frm.doc.docstatus !== 0) return;

  // ERPNext v15 uses required_items; keep items as a compatibility fallback.
  const table_field =
    frm.fields_dict.required_items || frm.fields_dict.items;
  apply_required_items_grid_permissions(table_field);

  // Also fix the child DocType meta so newly added/rendered rows can select an
  // item and set its quantity.
  for (const fieldname of ["item_code", "required_qty", "source_warehouse"]) {
    const df = frappe.meta.get_docfield(
      "Work Order Item",
      fieldname,
      frm.doc.name
    );
    if (df) {
      df.read_only = 0;
    }
  }

  // Core Work Order refresh handlers can run after custom handlers and restore
  // the grid restrictions, so re-apply once the refresh cycle has settled.
  clearTimeout(frm.__c4_required_items_grid_timer);
  frm.__c4_required_items_grid_timer = setTimeout(() => {
    const current_field =
      frm.fields_dict.required_items || frm.fields_dict.items;
    if (frm.doc.docstatus !== 0) return;
    apply_required_items_grid_permissions(current_field);
  }, 0);
}

async function refresh_material_transferred_qty(frm) {
  if (frm.doc.docstatus !== 1 || frm.__c4_syncing_transferred_qty) return;

  frm.__c4_syncing_transferred_qty = true;
  try {
    const { message } = await frappe.call({
      method: "c4factory.api.work_order_flow.sync_work_order_material_transfer",
      args: { wo_name: frm.doc.name },
    });
    const transferred = flt(message);
    if (
      Math.abs(
        transferred - flt(frm.doc.material_transferred_for_manufacturing)
      ) > 0.000001
    ) {
      frm.doc.material_transferred_for_manufacturing = transferred;
      frm.refresh_field("material_transferred_for_manufacturing");
      await frm.reload_doc();
    }
  } finally {
    frm.__c4_syncing_transferred_qty = false;
  }
}

function apply_required_items_grid_permissions(table_field) {
  const grid = table_field && table_field.grid;
  if (!grid) return;

  table_field.df.read_only = 0;
  table_field.df.cannot_add_rows = 0;
  table_field.df.cannot_delete_rows = 0;

  grid.df.read_only = 0;
  grid.df.cannot_add_rows = 0;
  grid.df.cannot_delete_rows = 0;
  grid.cannot_add_rows = false;
  grid.cannot_delete_rows = false;

  for (const fieldname of ["item_code", "required_qty", "source_warehouse"]) {
    grid.update_docfield_property(fieldname, "read_only", 0);
    grid.toggle_enable(fieldname, true);
  }
  grid.refresh();
}

async function set_source_warehouses(frm) {
  if (frm.doc.docstatus !== 0) return;

  const rows = frm.doc.required_items || frm.doc.items || [];
  for (const row of rows) {
    if (row.item_code) {
      await set_source_warehouse_from_item_group(frm, row.doctype, row.name);
    }
  }
}

async function set_source_warehouse_from_item_group(frm, cdt, cdn) {
  const row = locals[cdt] && locals[cdt][cdn];
  if (!row || !row.item_code) return;

  const { message: details } = await frappe.call({
    method: "c4factory.c4_manufacturing.work_order_hooks.get_source_warehouse_details",
    args: {
      item_code: row.item_code,
      item_group: row.item_group,
      company: frm.doc.company
    }
  });

  const current_row = locals[cdt] && locals[cdt][cdn];
  if (
    details?.warehouse &&
    current_row &&
    (details.override_existing || !current_row.source_warehouse) &&
    current_row.source_warehouse !== details.warehouse
  ) {
    await frappe.model.set_value(
      cdt,
      cdn,
      "source_warehouse",
      details.warehouse
    );
  }
}

function hide_create_job_card_button(frm) {
  if (!frm.doc.custom_disable_operation) return;

  const remove_buttons = () => {
    frm.remove_custom_button(__("Job Card"), __("Create"));
    frm.remove_custom_button(__("Create Job Card"), __("Create"));
    frm.remove_custom_button(__("Create Job Card"));
  };

  remove_buttons();
  setTimeout(remove_buttons, 300);
  setTimeout(remove_buttons, 1000);
}

function configure_continuous_start_button(frm) {
  const replace_start_button = () => {
    frm.remove_custom_button(__("Start"));

    if (
      frm.doc.docstatus !== 1 ||
      ["Stopped", "Closed", "Completed", "Cancelled"].includes(frm.doc.status) ||
      frm.doc.skip_transfer ||
      frm.doc.transfer_material_against === "Job Card"
    ) {
      return;
    }

    const start_button = frm.add_custom_button(__("Start"), () => {
      start_continuous_material_transfer(frm);
    });
    start_button.addClass("btn-primary");
  };

  replace_start_button();
  setTimeout(replace_start_button, 0);
  setTimeout(replace_start_button, 300);
  setTimeout(replace_start_button, 1000);
}

async function start_continuous_material_transfer(frm) {
  try {
    const context = await frappe.xcall(
      "c4factory.api.work_order_start.get_continuous_start_context",
      { work_order: frm.doc.name }
    );

    if (!context?.has_eligible_items) {
      frappe.show_alert(
        {
          message: __(
            "No eligible required items were found. No Stock Entry or Pick List was created."
          ),
          indicator: "orange"
        },
        10
      );
      return;
    }

    if (context.pending) {
      frappe.show_alert(
        {
          message: __(
            "Start documents are already being created for this Work Order."
          ),
          indicator: "orange"
        },
        10
      );
      return;
    }

    const max_qty = flt(context.remaining_qty);
    if (max_qty <= 0) {
      frappe.show_alert(
        {
          message: __("No continuous-material quantity remains to transfer."),
          indicator: "orange"
        },
        10
      );
      return;
    }

    frappe.prompt(
      {
        fieldname: "qty",
        fieldtype: "Float",
        label: __("Qty for Material Transfer for Manufacture"),
        description: __("Max: {0}", [max_qty]),
        default: max_qty,
        reqd: 1
      },
      async ({ qty }) => {
        try {
          if (flt(qty) <= 0 || flt(qty) > max_qty) {
            frappe.show_alert(
              {
                message: __(
                  "Quantity must be greater than zero and not more than {0}.",
                  [max_qty]
                ),
                indicator: "red"
              },
              10
            );
            return;
          }

          const result = await frappe.xcall(
            "c4factory.api.work_order_start.enqueue_continuous_start_transfer",
            {
              work_order: frm.doc.name,
              qty: flt(qty)
            }
          );
          frappe.show_alert(
            {
              message: result.message,
              indicator: result.status === "queued" ? "blue" : "orange"
            },
            10
          );
        } catch (error) {
          frappe.show_alert(
            {
              message: __("Unable to queue the background Stock Entry."),
              indicator: "red"
            },
            10
          );
        }
      },
      __("Select Quantity"),
      __("Start")
    );
  } catch (error) {
    frappe.show_alert(
      {
        message: __("Unable to start the background Stock Entry."),
        indicator: "red"
      },
      10
    );
  }
}

function register_continuous_start_listener() {
  if (frappe.__c4_continuous_start_listener_registered) return;

  frappe.__c4_continuous_start_listener_registered = true;
  frappe.realtime.on("c4factory_continuous_start", (result) => {
    const active_form = window.cur_frm;
    const active_work_order =
      active_form &&
      active_form.doctype === "Work Order" &&
      active_form.doc.name === result.work_order;

    let message = result.message;
    if (result.status === "success") {
      let stock_entry_link = "";
      let pick_list_link = "";
      if (result.stock_entry) {
        const route = `/app/stock-entry/${encodeURIComponent(
          result.stock_entry
        )}`;
        stock_entry_link = `${__("Stock Entry")} <a href="${route}"><b>${
          result.stock_entry
        }</b></a>`;
      }
      if (result.pick_list) {
        const route = `/app/pick-list/${encodeURIComponent(result.pick_list)}`;
        pick_list_link = `${__("Pick List")} <a href="${route}"><b>${
          result.pick_list
        }</b></a>`;
      }
      if (stock_entry_link && pick_list_link) {
        message = `${stock_entry_link} ${__("was submitted and")} ${pick_list_link} ${__(
          "was created successfully."
        )}`;
      } else if (stock_entry_link) {
        message = `${stock_entry_link} ${__(
          "was created and submitted successfully."
        )}`;
      } else if (pick_list_link) {
        message = `${pick_list_link} ${__("was created successfully.")}`;
      }
    }

    frappe.show_alert(
      {
        message,
        indicator:
          result.status === "success"
            ? "green"
            : result.status === "error"
              ? "red"
              : "orange"
      },
      15
    );

    if (active_work_order) {
      active_form.reload_doc();
    }
  });
}
