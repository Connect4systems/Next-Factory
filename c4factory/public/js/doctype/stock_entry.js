frappe.ui.form.on("Stock Entry", {
  refresh(frm) {
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
