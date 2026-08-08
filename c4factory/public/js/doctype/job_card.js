frappe.ui.form.on("Job Card", {
  refresh(frm) {
    set_operation_row_reference(frm);
  },
  onload_post_render(frm) {
    set_operation_row_reference(frm);
  },
  work_order(frm) {
    set_operation_row_reference(frm);
  },
  operation(frm) {
    set_operation_row_reference(frm);
  },
  workstation(frm) {
    set_operation_row_reference(frm);
  },
});

async function set_operation_row_reference(frm) {
  if (!frm.doc.work_order) return;

  if (frm.doc.operation_id && frm.doc.operation_row_number) return;

  const { message } = await frappe.call({
    method: "c4factory.c4_manufacturing.job_card_hooks.get_operation_row_reference",
    args: {
      work_order: frm.doc.work_order,
      operation: frm.doc.operation,
      workstation: frm.doc.workstation,
      operation_id: frm.doc.operation_id,
    },
  });

  if (!message) return;

  for (const [fieldname, value] of Object.entries(message)) {
    if (frm.fields_dict[fieldname] && frm.doc[fieldname] !== value) {
      await frm.set_value(fieldname, value);
    }
  }
}
