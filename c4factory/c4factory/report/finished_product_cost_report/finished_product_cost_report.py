import frappe
from frappe import _
from frappe.utils import getdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	validate_filters(filters)
	return get_columns(), get_data(filters)


def validate_filters(filters):
	if filters.get("from_date") and filters.get("to_date"):
		if getdate(filters.from_date) > getdate(filters.to_date):
			frappe.throw(_("From Date cannot be after To Date."))


def get_columns():
	return [
		{
			"label": _("Work Order ID"),
			"fieldname": "work_order",
			"fieldtype": "Link",
			"options": "Work Order",
			"width": 180,
		},
		{
			"label": _("Sales Order ID"),
			"fieldname": "sales_order",
			"fieldtype": "Link",
			"options": "Sales Order",
			"width": 170,
		},
		{
			"label": _("Customer"),
			"fieldname": "customer",
			"fieldtype": "Link",
			"options": "Customer",
			"width": 180,
		},
		{
			"label": _("Project"),
			"fieldname": "project",
			"fieldtype": "Link",
			"options": "Project",
			"width": 160,
		},
		{
			"label": _("Finished Item"),
			"fieldname": "finished_item",
			"fieldtype": "Link",
			"options": "Item",
			"width": 180,
		},
		{
			"label": _("Total Qty"),
			"fieldname": "total_qty",
			"fieldtype": "Float",
			"width": 110,
		},
		{
			"label": _("Finished Item Qty"),
			"fieldname": "finished_item_qty",
			"fieldtype": "Float",
			"width": 140,
		},
		{
			"label": _("Total Cost"),
			"fieldname": "total_cost",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
		{
			"label": _("Valuation Rate"),
			"fieldname": "valuation_rate",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
		{
			"label": _("BOM Cost"),
			"fieldname": "bom_cost",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
		{
			"label": _("Sales Price"),
			"fieldname": "sales_price",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
		{
			"label": _("Currency"),
			"fieldname": "currency",
			"fieldtype": "Link",
			"options": "Currency",
			"hidden": 1,
		},
	]


def get_data(filters):
	conditions, values = get_conditions(filters)

	return frappe.db.sql(
		f"""
		SELECT
			wo.name AS work_order,
			COALESCE(NULLIF(wo.sales_order, ''), linked_soi.parent) AS sales_order,
			so.customer,
			COALESCE(NULLIF(wo.project, ''), so.project) AS project,
			wo.production_item AS finished_item,
			COALESCE(wo.qty, 0) AS total_qty,
			manufacture.finished_item_qty,
			COALESCE(wo.c4_total_cost, 0) AS total_cost,
			CASE
				WHEN manufacture.finished_item_qty = 0 THEN 0
				ELSE manufacture.manufactured_value / manufacture.finished_item_qty
			END AS valuation_rate,
			CASE
				WHEN COALESCE(bom.quantity, 0) = 0 THEN 0
				ELSE COALESCE(bom.total_cost, 0) * COALESCE(wo.qty, 0) / bom.quantity
			END AS bom_cost,
			COALESCE(
				linked_soi.base_rate,
				(
					SELECT fallback_soi.base_rate
					FROM `tabSales Order Item` fallback_soi
					WHERE fallback_soi.parent = so.name
						AND fallback_soi.item_code = wo.production_item
					ORDER BY fallback_soi.idx
					LIMIT 1
				),
				0
			) AS sales_price,
			company.default_currency AS currency
		FROM `tabWork Order` wo
		INNER JOIN (
			SELECT
				se.work_order,
				SUM(COALESCE(finished.qty, se.fg_completed_qty, 0)) AS finished_item_qty,
				SUM(
					CASE
						WHEN finished.qty IS NULL THEN
							COALESCE(se.total_outgoing_value, 0)
							+ COALESCE(se.total_additional_costs, 0)
						ELSE finished.value
					END
				) AS manufactured_value
			FROM `tabStock Entry` se
			INNER JOIN `tabWork Order` manufacture_wo ON manufacture_wo.name = se.work_order
			LEFT JOIN (
				SELECT
					sed.parent,
					sed.item_code,
					SUM(ABS(COALESCE(sed.qty, 0))) AS qty,
					SUM(ABS(COALESCE(sed.amount, 0))) AS value
				FROM `tabStock Entry Detail` sed
				WHERE COALESCE(sed.is_finished_item, 0) = 1
					AND COALESCE(sed.is_scrap_item, 0) = 0
				GROUP BY sed.parent, sed.item_code
			) finished
				ON finished.parent = se.name
				AND finished.item_code = manufacture_wo.production_item
			WHERE se.docstatus = 1
				AND se.stock_entry_type = 'Manufacture'
				AND COALESCE(se.work_order, '') != ''
				{get_stock_entry_date_conditions(filters)}
			GROUP BY se.work_order
		) manufacture ON manufacture.work_order = wo.name
		LEFT JOIN `tabSales Order Item` linked_soi ON linked_soi.name = wo.sales_order_item
		LEFT JOIN `tabSales Order` so
			ON so.name = COALESCE(NULLIF(wo.sales_order, ''), linked_soi.parent)
		LEFT JOIN `tabBOM` bom ON bom.name = wo.bom_no
		LEFT JOIN `tabCompany` company ON company.name = wo.company
		WHERE wo.docstatus < 2
			{conditions}
		ORDER BY wo.name DESC
		""",
		values,
		as_dict=True,
	)


def get_conditions(filters):
	conditions = []
	values = {}
	if filters.get("from_date"):
		values["from_date"] = filters.from_date

	if filters.get("to_date"):
		values["to_date"] = filters.to_date

	if filters.get("work_order"):
		conditions.append("wo.name = %(work_order)s")
		values["work_order"] = filters.work_order

	if filters.get("sales_order"):
		conditions.append("COALESCE(NULLIF(wo.sales_order, ''), linked_soi.parent) = %(sales_order)s")
		values["sales_order"] = filters.sales_order

	if filters.get("customer"):
		conditions.append("so.customer = %(customer)s")
		values["customer"] = filters.customer

	if filters.get("project"):
		conditions.append("COALESCE(NULLIF(wo.project, ''), so.project) = %(project)s")
		values["project"] = filters.project

	return (" AND " + " AND ".join(conditions) if conditions else ""), values


def get_stock_entry_date_conditions(filters):
	conditions = []

	if filters.get("from_date"):
		conditions.append("se.posting_date >= %(from_date)s")

	if filters.get("to_date"):
		conditions.append("se.posting_date <= %(to_date)s")

	return (" AND " + " AND ".join(conditions)) if conditions else ""
