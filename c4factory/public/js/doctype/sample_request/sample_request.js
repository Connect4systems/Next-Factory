frappe.ui.form.on("Sample Request", {
  setup(frm) {
    // Filter BOMs by chosen product
    frm.set_query("bom", function(doc) {
      return {
        filters: {
          item: doc.product || "",
          is_active: 1
        }
      };
    });
  },

  product(frm) {
    // reset BOM if product changes
    frm.set_value("bom", null);
  },

  refresh(frm) {
    if (!frm.is_new() && frm.doc.docstatus === 1) {
      frm.add_custom_button(__("Create Work Order"), async () => {
        try {
          const r = await frappe.call({
            method: "c4factory.api.sample_request.create_work_order_from_sample",
            args: { sample_request_name: frm.doc.name },
            freeze: true
          });
          const wo = r && r.message;
          if (wo) frappe.set_route("Form", "Work Order", wo);
        } catch (e) {
          console.error(e);
          frappe.msgprint({ title: __("Error"), message: e.message || e, indicator: "red" });
        }
      }, __("Create"));
    }
  }
});
