frappe.query_reports["Total Materials"] = {
	filters: [
		{
			fieldname: "rows",
			label: __("Rows"),
			fieldtype: "Long Text",
			hidden: 1,
		},
	],
	onload(report) {
		if (frappe.route_options && frappe.route_options.rows) {
			report.set_filter_value("rows", frappe.route_options.rows);
			frappe.route_options = null;
		}

		report.page.add_inner_button(__("Create Material Request"), () => {
			const rows = (report.data || []).filter((row) => row.to_request > 0);

			if (!rows.length) {
				frappe.msgprint(__("No To Request quantities found."));
				return;
			}

			frappe.call({
				method: "c4factory.api.planning_reports.create_material_request_from_total_materials",
				args: {
					rows: rows,
					company: frappe.defaults.get_user_default("Company"),
				},
				freeze: true,
				freeze_message: __("Creating Material Request..."),
				callback: (r) => {
					if (r.message) {
						frappe.show_alert({
							message: __("Draft Material Request created"),
							indicator: "green",
						});
						frappe.set_route("Form", "Material Request", r.message);
					}
				},
			});
		});
	},
};
