import frappe


def execute(filters=None):
	filters = filters or {}
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": "Operation", "fieldname": "operation", "fieldtype": "Link", "options": "Operation", "width": 200},
		{"label": "Total Time (mins)", "fieldname": "total_time", "fieldtype": "Float", "width": 140},
	]


def get_data(filters):
	rows = frappe.parse_json(filters.get("rows") or "[]")
	if not rows:
		return []

	totals = {}
	bom_cache = {}
	bom_operation_meta = frappe.get_meta("BOM Operation")

	def get_default_bom(item_code):
		return frappe.db.get_value(
			"BOM",
			{"item": item_code, "is_default": 1, "is_active": 1},
			"name",
		)

	def get_operation_time_per_unit(op_row, base_qty):
		if bom_operation_meta.has_field("time_in_mins_per_unit"):
			per_unit = op_row.get("time_in_mins_per_unit")
			if per_unit is not None:
				return per_unit

		if bom_operation_meta.has_field("time_in_mins"):
			time_in_mins = op_row.get("time_in_mins") or 0
			return time_in_mins / (base_qty or 1)

		return 0

	def add_bom_operations(bom_no, qty):
		if not bom_no or qty is None:
			return

		bom = bom_cache.get(bom_no)
		if not bom:
			bom = frappe.get_doc("BOM", bom_no)
			bom_cache[bom_no] = bom

		base_qty = bom.quantity or 1
		for op_row in bom.operations:
			operation = op_row.get("operation") or op_row.get("operation_name")
			if not operation:
				continue

			per_unit = get_operation_time_per_unit(op_row, base_qty)
			total_time = per_unit * qty
			totals[operation] = (totals.get(operation) or 0) + total_time

	for row in rows:
		item_code = row.get("item_code")
		if not item_code:
			continue

		qty = row.get("qty") or 0
		if qty <= 0:
			continue

		bom_no = row.get("bom_no") or get_default_bom(item_code)
		add_bom_operations(bom_no, qty)

	return [{"operation": operation, "total_time": totals[operation]} for operation in sorted(totals.keys())]
