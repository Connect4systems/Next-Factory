frappe.ui.form.on("Stock Entry", {
  after_save(frm) {
    if (frm.doc.docstatus !== 0 || frm.is_new()) return;

    window.clearTimeout(frm.__c4_post_save_stabilizer);
    frm.__c4_user_edited_after_save = false;

    $(frm.wrapper)
      .off("input.c4_post_save change.c4_post_save")
      .one("input.c4_post_save change.c4_post_save", () => {
        frm.__c4_user_edited_after_save = true;
      });

    const savedName = frm.doc.name;
    frm.__c4_post_save_stabilizer = window.setTimeout(async () => {
      delete frm.__c4_post_save_stabilizer;
      $(frm.wrapper).off("input.c4_post_save change.c4_post_save");

      if (
        frm.doc.name !== savedName ||
        frm.doc.docstatus !== 0 ||
        frm.is_new() ||
        !frm.is_dirty() ||
        frm.__c4_user_edited_after_save
      ) {
        return;
      }

      // Some Stock Entry callbacks (including ERPNext warehouse/rate calls and
      // database Client Scripts) can finish after save and mark the form dirty
      // again. Reload the document saved by the server so Submit stays visible.
      await frm.reload_doc();
    }, 1500);
  },

  refresh(frm) {
    if (cint(frm.doc.custom_uses_finish_allocation)) {
      const items_grid = frm.fields_dict.items && frm.fields_dict.items.grid;
      if (items_grid) {
        items_grid.cannot_add_rows = true;
        items_grid.cannot_delete_rows = true;
        items_grid.df.cannot_add_rows = 1;
        items_grid.df.cannot_delete_rows = 1;
        ["item_code", "qty", "s_warehouse", "t_warehouse", "is_finished_item", "is_scrap_item"]
          .forEach((fieldname) => items_grid.update_docfield_property(fieldname, "read_only", 1));
        items_grid.refresh();
      }
      frm.set_intro(
        cint(frm.doc.custom_is_final_finish)
          ? __("Final Finish: all remaining transferred material and cost balances are reconciled here.")
          : __("Material quantities are allocated from exact submitted WIP transfer balances."),
        "blue"
      );
    }
    if (cint(frm.doc.custom_is_mold_material_issue)) {
      ["stock_entry_type", "purpose", "company", "work_order"].forEach((fieldname) => {
        if (frm.fields_dict[fieldname]) {
          frm.set_df_property(fieldname, "read_only", 1);
        }
      });
      frm.set_intro(
        frm.doc.custom_mold_issue_channel === "Continuous"
          ? __("Continuous-production Mold Material Issue. This entry is submitted automatically.")
          : __("Review the Mold Material Issue and submit it when the materials are taken."),
        "blue"
      );
      return;
    }
    if (!cint(frm.doc.custom_is_additional_material)) return;

    if (!frm.doc.custom_sub_pick_list) {
      frm.set_query("item_code", "items", () => ({
        filters: {
          is_stock_item: 1,
          disabled: 0,
        },
      }));
    }

    [
      "stock_entry_type",
      "purpose",
      "company",
      "work_order",
      "pick_list",
      "to_warehouse",
    ].forEach((fieldname) => {
      if (frm.fields_dict[fieldname]) {
        frm.set_df_property(fieldname, "read_only", 1);
      }
    });

    const items_grid = frm.fields_dict.items && frm.fields_dict.items.grid;
    if (items_grid) {
      items_grid.update_docfield_property("t_warehouse", "read_only", 1);
    }

    frm.set_intro(
      __(
        "Add the extra materials and source warehouses. All rows will be transferred to the Work Order WIP Warehouse."
      ),
      "blue"
    );
  },
});
