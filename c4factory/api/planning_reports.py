import frappe
from frappe import _


@frappe.whitelist()
def create_plan_bom_request(rows):
	rows = frappe.parse_json(rows) or []

	if not rows:
		frappe.throw(_("Please select at least one row."))

	doc = frappe.new_doc("Plan BOM Request")
	if doc.meta.has_field("date"):
		doc.date = frappe.utils.nowdate()

	for row in rows:
		sales_order = row.get("name")
		item_code = row.get("item_code")

		if not sales_order or not item_code:
			continue

		bom_no = row.get("bom_no") or frappe.db.get_value(
			"BOM",
			{"item": item_code, "is_default": 1, "is_active": 1},
			"name",
		)

		item_description = frappe.db.get_value("Item", item_code, "description")

		doc.append(
			"plan_bom_items",
			{
				"sales_order": sales_order,
				"item": item_code,
				"qty": row.get("qty") or 0,
				"bom": bom_no,
				"description": item_description or "",
			},
		)

	if not doc.get("plan_bom_items"):
		frappe.throw(_("No valid rows found to create Plan BOM Request."))

	doc.insert()
	return doc.name


@frappe.whitelist()
def create_production_plan_from_operation_status(rows):
	rows = frappe.parse_json(rows) or []

	if not rows:
		frappe.throw(_("Please select at least one row."))

	company = None
	for row in rows:
		sales_order = row.get("name")
		if sales_order:
			company = frappe.db.get_value("Sales Order", sales_order, "company")
			if company:
				break

	if not company:
		company = frappe.defaults.get_user_default("Company") or frappe.db.get_default("company")

	pp = frappe.new_doc("Production Plan")

	if company and pp.meta.has_field("company"):
		pp.company = company
	if pp.meta.has_field("posting_date"):
		pp.posting_date = frappe.utils.nowdate()
	if pp.meta.has_field("get_items_from"):
		pp.get_items_from = "Sales Order"

	items_table = _get_production_plan_items_table(pp)
	items_doctype = pp.meta.get_field(items_table).options
	items_meta = frappe.get_meta(items_doctype)

	for row in rows:
		sales_order = row.get("name")
		item_code = row.get("item_code")
		if not item_code:
			continue

		bom_no = row.get("bom_no") or frappe.db.get_value(
			"BOM",
			{"item": item_code, "is_default": 1, "is_active": 1},
			"name",
		)

		child = pp.append(items_table, {})
		_set_if_present(child, items_meta, "item_code" if items_meta.has_field("item_code") else "item", item_code)
		_set_if_present(child, items_meta, "bom_no" if items_meta.has_field("bom_no") else "bom", bom_no)
		_set_if_present(child, items_meta, "description", frappe.db.get_value("Item", item_code, "description") or "")
		_set_if_present(child, items_meta, "sales_order", sales_order)

		qty_field = _get_qty_field(items_meta)
		if qty_field:
			_set_if_present(child, items_meta, qty_field, row.get("qty") or 0)

	if not pp.get(items_table):
		frappe.throw(_("No valid rows found to create Production Plan."))

	_append_sales_orders(pp, rows)

	pp.insert()
	return pp.name


@frappe.whitelist()
def create_production_plan_from_plan_bom_request(plan_bom_request):
	plan_doc = frappe.get_doc("Plan BOM Request", plan_bom_request)

	if plan_doc.docstatus != 1:
		frappe.throw(_("Plan BOM Request must be submitted first."))

	if not plan_doc.get("plan_bom_items"):
		frappe.throw(_("No items found in Plan BOM Request."))

	company = None
	for row in plan_doc.plan_bom_items:
		if row.sales_order:
			company = frappe.db.get_value("Sales Order", row.sales_order, "company")
			if company:
				break

	if not company:
		company = frappe.defaults.get_user_default("Company") or frappe.db.get_default("company")

	pp = frappe.new_doc("Production Plan")

	if company and pp.meta.has_field("company"):
		pp.company = company
	if pp.meta.has_field("posting_date"):
		pp.posting_date = frappe.utils.nowdate()
	if pp.meta.has_field("get_items_from"):
		pp.get_items_from = "Sales Order"

	items_table = _get_production_plan_items_table(pp)
	items_doctype = pp.meta.get_field(items_table).options
	items_meta = frappe.get_meta(items_doctype)

	for row in plan_doc.plan_bom_items:
		child = pp.append(items_table, {})

		_set_if_present(child, items_meta, "item_code" if items_meta.has_field("item_code") else "item", row.item)
		_set_if_present(child, items_meta, "bom_no" if items_meta.has_field("bom_no") else "bom", row.bom)
		_set_if_present(child, items_meta, "description", row.description)
		_set_if_present(child, items_meta, "sales_order", row.sales_order)

		qty_field = _get_qty_field(items_meta)
		if qty_field:
			_set_if_present(child, items_meta, qty_field, row.qty)

	if not pp.get(items_table):
		frappe.throw(_("No valid rows found to create Production Plan."))

	_append_sales_orders(pp, plan_doc.plan_bom_items)

	pp.insert()
	return pp.name


@frappe.whitelist()
def create_material_request_from_total_materials(rows, company=None, material_request_type="Purchase"):
	rows = frappe.parse_json(rows) or []

	if not rows:
		frappe.throw(_("No rows found to create Material Request."))

	items = []
	for row in rows:
		item_code = row.get("item_code")
		qty = row.get("to_request") or row.get("request_qty") or 0

		if not item_code or qty <= 0:
			continue

		items.append(
			{
				"item_code": item_code,
				"qty": qty,
				"warehouse": row.get("warehouse"),
				"schedule_date": frappe.utils.nowdate(),
				"description": frappe.db.get_value("Item", item_code, "description") or row.get("item_name"),
				"uom": row.get("uom"),
			}
		)

	if not items:
		frappe.throw(_("No To Request quantities found."))

	doc = frappe.new_doc("Material Request")
	if doc.meta.has_field("company"):
		doc.company = company or frappe.defaults.get_user_default("Company") or frappe.db.get_default("company")
	if doc.meta.has_field("transaction_date"):
		doc.transaction_date = frappe.utils.nowdate()
	if doc.meta.has_field("schedule_date"):
		doc.schedule_date = frappe.utils.nowdate()
	if doc.meta.has_field("material_request_type"):
		doc.material_request_type = material_request_type or "Purchase"

	item_meta = frappe.get_meta("Material Request Item")
	for item in items:
		row = doc.append("items", {})
		_set_if_present(row, item_meta, "item_code", item["item_code"])
		_set_if_present(row, item_meta, "qty", item["qty"])
		_set_if_present(row, item_meta, "warehouse", item["warehouse"])
		_set_if_present(row, item_meta, "schedule_date", item["schedule_date"])
		_set_if_present(row, item_meta, "description", item["description"])
		_set_if_present(row, item_meta, "uom", item["uom"])

	doc.insert()
	return doc.name


def _get_production_plan_items_table(pp):
	if pp.meta.has_field("assembly_items"):
		return "assembly_items"
	if pp.meta.has_field("po_items"):
		return "po_items"

	frappe.throw(_("Production Plan items table not found."))


def _get_qty_field(meta):
	if meta.has_field("planned_qty"):
		return "planned_qty"
	if meta.has_field("qty"):
		return "qty"
	if meta.has_field("quantity"):
		return "quantity"

	return None


def _set_if_present(doc, meta, fieldname, value):
	if value is not None and meta.has_field(fieldname):
		doc.set(fieldname, value)


def _append_sales_orders(pp, rows):
	if not pp.meta.has_field("sales_orders"):
		return

	sales_table = "sales_orders"
	sales_doctype = pp.meta.get_field(sales_table).options
	sales_meta = frappe.get_meta(sales_doctype)
	added = set()

	for row in rows:
		sales_order = row.get("name")
		if not sales_order or sales_order in added:
			continue

		sales_child = pp.append(sales_table, {})
		_set_if_present(sales_child, sales_meta, "sales_order", sales_order)
		_set_if_present(sales_child, sales_meta, "customer", frappe.db.get_value("Sales Order", sales_order, "customer"))
		added.add(sales_order)
