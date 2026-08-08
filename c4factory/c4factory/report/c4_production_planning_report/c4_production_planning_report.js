// Copyright (c) 2016, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.query_reports["C4 Production Planning Report"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			reqd: 1,
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "based_on",
			label: __("Based On"),
			fieldtype: "Select",
			options: ["Sales Order", "Material Request", "Work Order"],
			default: "Sales Order",
			reqd: 1,
			on_change: function () {
				let filters = frappe.query_report.filters;
				let based_on = frappe.query_report.get_filter_value("based_on");
				let options = {
					"Sales Order": ["Delivery Date", "Total Amount"],
					"Material Request": ["Required Date"],
					"Work Order": ["Planned Start Date"],
				};

				filters.forEach((d) => {
					if (d.fieldname == "order_by") {
						d.df.options = options[based_on];
						d.set_input(d.df.options);
					}
				});

				frappe.query_report.refresh();
			},
		},
		{
			fieldname: "docnames",
			label: __("Document Name"),
			fieldtype: "MultiSelectList",
			options: "based_on",
			get_data: function (txt) {
				if (!frappe.query_report.filters) return;

				let based_on = frappe.query_report.get_filter_value("based_on");
				if (!based_on) return;

				return frappe.db.get_link_options(based_on, txt);
			},
			get_query: function () {
				var company = frappe.query_report.get_filter_value("company");
				return {
					filters: {
						docstatus: 1,
						company: company,
					},
				};
			},
		},
		{
			fieldname: "raw_material_warehouse",
			label: __("Raw Material Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
			depends_on: "eval: doc.based_on != 'Work Order'",
			get_query: function () {
				var company = frappe.query_report.get_filter_value("company");
				return {
					filters: {
						company: company,
					},
				};
			},
		},
		{
			fieldname: "raw_material_item",
			label: __("Raw Material Item"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			fieldname: "order_by",
			label: __("Order By"),
			fieldtype: "Select",
			options: ["Delivery Date", "Total Amount"],
			default: "Delivery Date",
		},
		{
			fieldname: "include_subassembly_raw_materials",
			label: __("Include Sub-assembly Raw Materials"),
			fieldtype: "Check",
			depends_on: "eval: doc.based_on != 'Work Order'",
			default: 0,
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

		const get_planning_rows = () => {
			const selected_rows = get_selected_rows();

			return (selected_rows || [])
				.map((row) => {
					if (typeof row === "number" && report.data) {
						row = report.data[row];
					}

					if (!row || !row.production_item || !row.qty_to_manufacture) {
						return null;
					}

					return {
						name: row.name,
						item_code: row.production_item,
						item_name: row.production_item_name,
						qty: row.qty_to_manufacture,
						bom_no: row.bom_no,
					};
				})
				.filter(Boolean);
		};

		const open_total_report = (report_name) => {
			const rows = get_planning_rows();

			if (!rows.length) {
				frappe.msgprint(__("Please select at least one production row."));
				return;
			}

			frappe.set_route("query-report", report_name, {
				rows: JSON.stringify(rows),
			});
		};

		report.page.add_inner_button(__("Total Materials"), () => {
			open_total_report("Total Materials");
		});

		report.page.add_inner_button(__("Total Operations"), () => {
			open_total_report("Total Operations");
		});
	},
	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		if (
			column.fieldname == "production_item_name" &&
			data &&
			data.qty_to_manufacture > data.available_qty
		) {
			value = `<div style="color:red">${value}</div>`;
		}

		if (column.fieldname == "production_item" && !data.name) {
			value = "";
		}

		if (column.fieldname == "raw_material_name" && data && data.request_qty > 0) {
			value = `<div style="color:red">${value}</div>`;
		}

		return value;
	},
};
