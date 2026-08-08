import frappe


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{
			"label": "Production Plan",
			"fieldname": "production_plan",
			"fieldtype": "Link",
			"options": "Production Plan",
			"width": 180,
		},
		{
			"label": "Work Order",
			"fieldname": "work_order",
			"fieldtype": "Link",
			"options": "Work Order",
			"width": 180,
		},
		{"label": "Planned Start Date", "fieldname": "planned_start_date", "fieldtype": "Date", "width": 130},
		{"label": "Company", "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 140},
		{"label": "Sales Order", "fieldname": "sales_order", "fieldtype": "Link", "options": "Sales Order", "width": 150},
		{"label": "Item", "fieldname": "item", "fieldtype": "Link", "options": "Item", "width": 160},
		{"label": "Item Name", "fieldname": "item_name", "fieldtype": "Data", "width": 180},
		{"label": "Status", "fieldname": "status", "fieldtype": "Data", "width": 120},
		{"label": "Qty", "fieldname": "qty", "fieldtype": "Float", "width": 100},
		{"label": "Produced Qty", "fieldname": "produced_qty", "fieldtype": "Float", "width": 120},
		{"label": "Pending Qty", "fieldname": "pending_qty", "fieldtype": "Float", "width": 120},
		{"label": "% Produced", "fieldname": "percent_produced", "fieldtype": "Percent", "width": 110},
	]


def get_data(filters):
	conditions = ["wo.docstatus < 2"]
	values = {}

	if filters.get("production_plan"):
		conditions.append("wo.production_plan = %(production_plan)s")
		values["production_plan"] = filters.production_plan

	if filters.get("company"):
		conditions.append("wo.company = %(company)s")
		values["company"] = filters.company

	if filters.get("status"):
		conditions.append("wo.status = %(status)s")
		values["status"] = filters.status

	if filters.get("item"):
		conditions.append("wo.production_item = %(item)s")
		values["item"] = filters.item

	if filters.get("sales_order"):
		conditions.append("wo.sales_order = %(sales_order)s")
		values["sales_order"] = filters.sales_order

	if filters.get("from_date"):
		conditions.append("wo.planned_start_date >= %(from_date)s")
		values["from_date"] = filters.from_date

	if filters.get("to_date"):
		conditions.append("wo.planned_start_date <= %(to_date)s")
		values["to_date"] = filters.to_date

	where_clause = " AND ".join(conditions)

	return frappe.db.sql(
		f"""
		SELECT
			wo.production_plan AS production_plan,
			wo.name AS work_order,
			wo.planned_start_date,
			wo.company,
			wo.sales_order,
			wo.production_item AS item,
			i.item_name AS item_name,
			wo.status,
			wo.qty,
			wo.produced_qty,
			GREATEST(IFNULL(wo.qty, 0) - IFNULL(wo.produced_qty, 0), 0) AS pending_qty,
			CASE
				WHEN IFNULL(wo.qty, 0) = 0 THEN 0
				ELSE (IFNULL(wo.produced_qty, 0) / wo.qty) * 100
			END AS percent_produced
		FROM `tabWork Order` wo
		LEFT JOIN `tabItem` i ON i.name = wo.production_item
		WHERE {where_clause}
		ORDER BY wo.production_plan DESC, wo.planned_start_date DESC, wo.name
		""",
		values,
		as_dict=True,
	)
