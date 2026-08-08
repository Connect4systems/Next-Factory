frappe.ui.form.on("Timesheet", {
  refresh(frm) {
    if (!frm.is_new() || !frm.doc.custom_work_order) return;
    set_work_order_project(frm);
    setTimeout(() => set_work_order_project(frm), 300);
  },
  custom_work_order(frm) {
    set_work_order_project(frm);
  },
});

frappe.ui.form.on("Timesheet Detail", {
  time_logs_add(frm, cdt, cdn) {
    set_work_order_project(frm, cdt, cdn);
  },
});

async function set_work_order_project(frm, cdt, cdn) {
  if (!frm.doc.custom_work_order) return;

  const { message: work_order } = await frappe.db.get_value(
    "Work Order",
    frm.doc.custom_work_order,
    ["project", "company"]
  );
  if (!work_order) return;

  if (work_order.company && frm.doc.company !== work_order.company) {
    await frm.set_value("company", work_order.company);
  }

  if (!work_order.project) return;

  if (
    frm.fields_dict.parent_project &&
    frm.doc.parent_project !== work_order.project
  ) {
    await frm.set_value("parent_project", work_order.project);
  }

  if (cdt && cdn) {
    const row = locals[cdt]?.[cdn];
    if (row && row.project !== work_order.project) {
      await frappe.model.set_value(cdt, cdn, "project", work_order.project);
    }
    return;
  }

  for (const row of frm.doc.time_logs || []) {
    if (row.project !== work_order.project) {
      await frappe.model.set_value(row.doctype, row.name, "project", work_order.project);
    }
  }
}
