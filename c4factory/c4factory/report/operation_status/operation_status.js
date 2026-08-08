frappe.query_reports["Operation Status"] = {
	filters: [
		{
			fieldname: "name",
			label: __("ID"),
			fieldtype: "Link",
			options: "Sales Order",
		},
		{
			fieldname: "customer",
			label: __("Customer"),
			fieldtype: "Link",
			options: "Customer",
		},
		{
			fieldname: "transaction_date",
			label: __("Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "po_no",
			label: __("Customer's Purch."),
			fieldtype: "Data",
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
		},
		{
			fieldname: "project",
			label: __("Project"),
			fieldtype: "Link",
			options: "Project",
		},
		{
			fieldname: "delivery_status",
			label: __("Delivery Status"),
			fieldtype: "Select",
			options: ["", "Not Delivered", "Partly Delivered", "Fully Delivered"],
		},
		{
			fieldname: "billing_status",
			label: __("Billing Status"),
			fieldtype: "Select",
			options: ["", "Not Billed", "Partly Billed", "Fully Billed"],
		},
		{
			fieldname: "has_default_bom",
			label: __("Has Default BOM"),
			fieldtype: "Check",
		},
	],
	get_datatable_options(options) {
		return Object.assign(options, {
			checkboxColumn: true,
		});
	},
	onload(report) {
		const get_selected_rows = () => {
			if (typeof report.get_checked_items === "function") {
				return report.get_checked_items();
			}
			if (typeof report.get_checked_rows === "function") {
				return report.get_checked_rows();
			}
			if (report.datatable && report.datatable.rowmanager) {
				return report.datatable.rowmanager.getCheckedRows();
			}
			return [];
		};

		report.page.add_inner_button(__("Create Plan BOM Request"), () => {
			const rows = get_selected_rows();

			if (!rows || !rows.length) {
				frappe.msgprint(__("Please select at least one row."));
				return;
			}

			frappe.call({
				method: "c4factory.api.planning_reports.create_plan_bom_request",
				args: {
					rows: rows,
				},
				freeze: true,
				freeze_message: __("Creating Plan BOM Request..."),
				callback: (r) => {
					if (r.message) {
						frappe.show_alert({
							message: __("Plan BOM Request created"),
							indicator: "green",
						});
						frappe.set_route("Form", "Plan BOM Request", r.message);
					}
				},
			});
		});

		report.page.add_inner_button(__("Create Manufacture Plan"), () => {
			const rows = get_selected_rows();

			if (!rows || !rows.length) {
				frappe.msgprint(__("Please select at least one row."));
				return;
			}

			frappe.call({
				method: "c4factory.api.planning_reports.create_production_plan_from_operation_status",
				args: {
					rows: rows,
				},
				freeze: true,
				freeze_message: __("Creating Production Plan..."),
				callback: (r) => {
					if (r.message) {
						frappe.show_alert({
							message: __("Production Plan created"),
							indicator: "green",
						});
						frappe.set_route("Form", "Production Plan", r.message);
					}
				},
			});
		});

		report.page.add_inner_button(__("Total Materials"), () => {
			const rows = get_selected_rows();

			if (!rows || !rows.length) {
				frappe.msgprint(__("Please select at least one row."));
				return;
			}

			frappe.set_route("query-report", "Total Materials", {
				rows: JSON.stringify(rows),
			});
		});

		report.page.add_inner_button(__("Total Operations"), () => {
			const rows = get_selected_rows();

			if (!rows || !rows.length) {
				frappe.msgprint(__("Please select at least one row."));
				return;
			}

			frappe.set_route("query-report", "Total Operations", {
				rows: JSON.stringify(rows),
			});
		});
	},
};
