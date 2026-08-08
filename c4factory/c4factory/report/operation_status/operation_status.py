import frappe


def execute(filters=None):
    columns = get_columns()
    data = get_data(filters or {})
    return columns, data


def get_columns():
    return [
        {"label": "ID", "fieldname": "name", "fieldtype": "Link", "options": "Sales Order", "width": 120},
        {"label": "Priority", "fieldname": "priority", "fieldtype": "Data", "width": 100},
        {"label": "Customer Name", "fieldname": "customer_name", "fieldtype": "Data", "width": 160},
        {"label": "Date", "fieldname": "transaction_date", "fieldtype": "Date", "width": 120},
        {"label": "Delivery Date", "fieldname": "delivery_date", "fieldtype": "Date", "width": 120},
        {"label": "Customer's Purchase", "fieldname": "po_no", "fieldtype": "Data", "width": 120},
        {"label": "Project", "fieldname": "project", "fieldtype": "Link", "options": "Project", "width": 120},
        {"label": "Item Code", "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 150},
        {"label": "Item Name", "fieldname": "item_name", "fieldtype": "Data", "width": 180},
        {"label": "Description", "fieldname": "description", "fieldtype": "Data", "width": 220},
        {"label": "Qty", "fieldname": "qty", "fieldtype": "Float", "width": 100},
        {"label": "Actual Qty", "fieldname": "actual_qty", "fieldtype": "Float", "width": 90},
        {"label": "Status", "fieldname": "status", "fieldtype": "Data", "width": 120},
        {"label": "Work Order Qty", "fieldname": "work_order_qty", "fieldtype": "Float", "width": 120},
        {"label": "Work Order", "fieldname": "work_order", "fieldtype": "Link", "options": "Work Order", "width": 140},
        {"label": "Qty To Manufacture", "fieldname": "wo_qty", "fieldtype": "Float", "width": 140},
        {"label": "Manufactured Qty", "fieldname": "manufactured_qty", "fieldtype": "Float", "width": 140},
        {"label": "Delivery Status", "fieldname": "delivery_status", "fieldtype": "Data", "width": 120},
        {"label": "% Delivered", "fieldname": "per_delivered", "fieldtype": "Percent", "width": 100},
        {"label": "% Billed", "fieldname": "per_billed", "fieldtype": "Percent", "width": 100},
        {"label": "Billing Status", "fieldname": "billing_status", "fieldtype": "Data", "width": 120},
        {"label": "Grand Total", "fieldname": "base_grand_total", "fieldtype": "Currency", "width": 140},
        {"label": "BOM", "fieldname": "bom_no", "fieldtype": "Link", "options": "BOM", "width": 120},
    ]


def get_data(filters):
    values = {}

    conditions = [
        "so.docstatus = 1",
    ]

    if filters.get("name"):
        conditions.append("so.name = %(name)s")
        values["name"] = filters["name"]

    if filters.get("customer"):
        conditions.append("so.customer = %(customer)s")
        values["customer"] = filters["customer"]

    if filters.get("transaction_date"):
        conditions.append("so.transaction_date = %(transaction_date)s")
        values["transaction_date"] = filters["transaction_date"]

    if filters.get("po_no"):
        conditions.append("so.po_no LIKE %(po_no)s")
        values["po_no"] = f"%{filters['po_no']}%"

    if filters.get("company"):
        conditions.append("so.company = %(company)s")
        values["company"] = filters["company"]

    if filters.get("project"):
        conditions.append("so.project = %(project)s")
        values["project"] = filters["project"]

    if filters.get("delivery_status"):
        conditions.append("so.delivery_status = %(delivery_status)s")
        values["delivery_status"] = filters["delivery_status"]

    if filters.get("billing_status"):
        conditions.append("so.billing_status = %(billing_status)s")
        values["billing_status"] = filters["billing_status"]

    if filters.get("has_default_bom"):
        conditions.append("b.name IS NOT NULL")

    where_clause = " AND ".join(conditions)

    return frappe.db.sql(
        f"""
        SELECT
            so.name AS name,
            so.custom_priority_ AS priority,
            so.customer_name AS customer_name,
            so.transaction_date AS transaction_date,
            so.delivery_date AS delivery_date,
            so.po_no AS po_no,
            so.project AS project,
            soi.item_code AS item_code,
            item.item_name AS item_name,
            item.description AS description,
            soi.qty AS qty,
            soi.actual_qty AS actual_qty,
            so.status AS status,
            soi.work_order_qty AS work_order_qty,
            wo.work_order AS work_order,
            wo.wo_qty AS wo_qty,
            wo.manufactured_qty AS manufactured_qty,
            so.delivery_status AS delivery_status,
            so.per_delivered AS per_delivered,
            so.per_billed AS per_billed,
            so.billing_status AS billing_status,
            so.base_grand_total AS base_grand_total,
            COALESCE(soi.bom_no, b.name) AS bom_no
        FROM `tabSales Order` so
        LEFT JOIN `tabSales Order Item` soi ON soi.parent = so.name
        LEFT JOIN `tabItem` item ON item.name = soi.item_code
        LEFT JOIN (
            SELECT
                sales_order,
                production_item,
                MAX(name) AS work_order,
                SUM(qty) AS wo_qty,
                SUM(produced_qty) AS manufactured_qty
            FROM `tabWork Order`
            WHERE docstatus < 2
            GROUP BY sales_order, production_item
        ) wo ON wo.sales_order = so.name AND wo.production_item = soi.item_code
        LEFT JOIN `tabBOM` b ON b.item = soi.item_code AND b.is_default = 1 AND b.is_active = 1
        WHERE {where_clause}
        ORDER BY so.modified DESC
        """,
        values,
        as_dict=True,
    )
