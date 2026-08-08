frappe.query_reports["Total Operations"] = {
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
	},
};
