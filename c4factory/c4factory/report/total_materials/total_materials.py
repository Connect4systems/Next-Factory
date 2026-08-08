import frappe

from c4factory.c4_manufacturing.work_order_hooks import get_default_source_warehouse


def execute(filters=None):
	filters = filters or {}
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": "Item Code", "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 160},
		{"label": "Item Name", "fieldname": "item_name", "fieldtype": "Data", "width": 220},
		{"label": "Required Qty", "fieldname": "required_qty", "fieldtype": "Float", "width": 120},
		{"label": "UOM", "fieldname": "uom", "fieldtype": "Data", "width": 80},
		{"label": "Warehouse", "fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 180},
		{"label": "Available Qty", "fieldname": "available_qty", "fieldtype": "Float", "width": 120},
		{"label": "Projected Qty", "fieldname": "projected_qty", "fieldtype": "Float", "width": 120},
		{"label": "To Request", "fieldname": "to_request", "fieldtype": "Float", "width": 110},
	]


def get_data(filters):
	rows = frappe.parse_json(filters.get("rows") or "[]")
	if not rows:
		return []

	totals = {}
	bom_cache = {}
	item_cache = {}
	warehouse_cache = {}
	bin_cache = {}

	def get_item_info(item_code):
		if item_code not in item_cache:
			item_cache[item_code] = (
				frappe.db.get_value(
					"Item",
					item_code,
					["item_name", "stock_uom", "item_group"],
					as_dict=True,
				)
				or {}
			)
		return item_cache[item_code]

	def get_default_warehouse(item_code):
		if item_code in warehouse_cache:
			return warehouse_cache[item_code]

		item_info = get_item_info(item_code)
		warehouse = get_default_source_warehouse(
			item_code=item_code,
			item_group=item_info.get("item_group"),
		)

		if not warehouse:
			warehouse = frappe.db.get_value(
				"Item Default",
				{"parent": item_code, "parenttype": "Item"},
				"default_warehouse",
			)

		if not warehouse:
			stock_meta = frappe.get_meta("Stock Settings")
			if stock_meta.has_field("default_warehouse"):
				warehouse = frappe.db.get_single_value("Stock Settings", "default_warehouse")

		warehouse_cache[item_code] = warehouse
		return warehouse

	def get_bin_qty(item_code, warehouse):
		key = (item_code, warehouse)
		if key in bin_cache:
			return bin_cache[key]

		if not warehouse:
			bin_cache[key] = {"actual_qty": 0, "projected_qty": 0}
			return bin_cache[key]

		bin_row = (
			frappe.db.get_value(
				"Bin",
				{"item_code": item_code, "warehouse": warehouse},
				["actual_qty", "projected_qty"],
				as_dict=True,
			)
			or {"actual_qty": 0, "projected_qty": 0}
		)

		bin_cache[key] = bin_row
		return bin_row

	def get_default_bom(item_code):
		return frappe.db.get_value(
			"BOM",
			{"item": item_code, "is_default": 1, "is_active": 1},
			"name",
		)

	def explode_bom(bom_no, qty):
		if not bom_no or qty is None:
			return

		bom = bom_cache.get(bom_no)
		if not bom:
			bom = frappe.get_doc("BOM", bom_no)
			bom_cache[bom_no] = bom

		base_qty = bom.quantity or 1
		for component in bom.items:
			component_item = component.get("item_code") or component.get("item")
			if not component_item:
				continue

			component_qty = (component.qty / base_qty) * qty
			sub_bom = component.get("bom_no")
			if sub_bom:
				explode_bom(sub_bom, component_qty)
				continue

			totals[component_item] = (totals.get(component_item) or 0) + component_qty

	for row in rows:
		item_code = row.get("item_code")
		if not item_code:
			continue

		qty = row.get("qty") or 0
		if qty <= 0:
			continue

		bom_no = row.get("bom_no") or get_default_bom(item_code)
		explode_bom(bom_no, qty)

	data = []
	for item_code in sorted(totals.keys()):
		required_qty = totals[item_code]
		item_info = get_item_info(item_code)
		warehouse = get_default_warehouse(item_code)
		bin_row = get_bin_qty(item_code, warehouse)
		available_qty = bin_row.get("actual_qty") or 0

		data.append(
			{
				"item_code": item_code,
				"item_name": item_info.get("item_name"),
				"required_qty": required_qty,
				"uom": item_info.get("stock_uom"),
				"warehouse": warehouse,
				"available_qty": available_qty,
				"projected_qty": bin_row.get("projected_qty") or 0,
				"to_request": max(required_qty - available_qty, 0),
			}
		)

	return data
