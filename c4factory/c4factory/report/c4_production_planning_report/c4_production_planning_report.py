# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt
from pypika import Order

from c4factory.c4_manufacturing.work_order_hooks import get_default_source_warehouse
from erpnext.stock.doctype.warehouse.warehouse import get_child_warehouses


def execute(filters=None):
	return ProductionPlanReport(filters).execute_report()


class ProductionPlanReport:
	def __init__(self, filters=None):
		self.filters = frappe._dict(filters or {})
		self.raw_materials_dict = {}
		self.data = []
		self.item_group_cache = {}
		self.item_group_warehouse_cache = {}

	def execute_report(self):
		self.get_open_orders()
		self.get_raw_materials()
		self.get_item_details()
		self.set_item_group_warehouses()
		self.get_bin_details()
		self.get_purchase_details()
		self.get_material_request_details()
		self.prepare_data()
		self.add_total_row()
		self.get_columns()

		return self.columns, self.data

	def get_open_orders(self):
		doctype, order_by = self.filters.based_on, self.filters.order_by

		parent = frappe.qb.DocType(doctype)
		query = None

		if doctype == "Work Order":
			query = (
				frappe.qb.from_(parent)
				.select(
					parent.production_item,
					parent.item_name.as_("production_item_name"),
					parent.planned_start_date,
					parent.stock_uom,
					parent.qty.as_("qty_to_manufacture"),
					parent.name,
					parent.bom_no,
					parent.fg_warehouse.as_("warehouse"),
				)
				.where(parent.status.notin(["Completed", "Stopped", "Closed"]))
			)

			if order_by == "Planned Start Date":
				query = query.orderby(parent.planned_start_date, order=Order.asc)

			if self.filters.docnames:
				query = query.where(parent.name.isin(self.filters.docnames))

		else:
			child = frappe.qb.DocType(f"{doctype} Item")
			query = (
				frappe.qb.from_(parent)
				.from_(child)
				.select(
					child.bom_no,
					child.stock_uom,
					child.warehouse,
					child.parent.as_("name"),
					child.item_code.as_("production_item"),
					child.stock_qty.as_("qty_to_manufacture"),
					child.item_name.as_("production_item_name"),
				)
				.where(parent.name == child.parent)
			)

			if self.filters.docnames:
				query = query.where(child.parent.isin(self.filters.docnames))

			if doctype == "Sales Order":
				query = query.select(
					child.delivery_date,
					parent.base_grand_total,
				).where(
					(child.stock_qty > child.produced_qty)
					& (parent.per_delivered < 100.0)
					& (parent.status.notin(["Completed", "Closed"]))
				)

				if order_by == "Delivery Date":
					query = query.orderby(child.delivery_date, order=Order.asc)
				elif order_by == "Total Amount":
					query = query.orderby(parent.base_grand_total, order=Order.desc)

			elif doctype == "Material Request":
				query = query.select(
					child.schedule_date,
				).where(
					(parent.per_ordered < 100)
					& (parent.material_request_type == "Manufacture")
					& (parent.status != "Stopped")
				)

				if order_by == "Required Date":
					query = query.orderby(child.schedule_date, order=Order.asc)

		query = query.where(parent.docstatus == 1)

		if self.filters.company:
			query = query.where(parent.company == self.filters.company)

		self.orders = query.run(as_dict=True)

	def get_raw_materials(self):
		if not self.orders:
			return

		self.warehouses = [d.warehouse for d in self.orders if d.warehouse]
		self.item_codes = [d.production_item for d in self.orders if d.production_item]

		if self.filters.based_on == "Work Order":
			work_orders = [d.name for d in self.orders]

			raw_materials = (
				frappe.get_all(
					"Work Order Item",
					fields=[
						"parent",
						"item_code",
						"item_name as raw_material_name",
						"source_warehouse as warehouse",
						"required_qty",
					],
					filters={"docstatus": 1, "parent": ("in", work_orders), "source_warehouse": ("!=", "")},
				)
				or []
			)
			if self.filters.raw_material_item:
				raw_materials = [d for d in raw_materials if d.item_code == self.filters.raw_material_item]
			self.warehouses.extend([d.warehouse for d in raw_materials if d.warehouse])

		else:
			bom_nos = []

			for d in self.orders:
				bom_no = d.bom_no or frappe.get_cached_value("Item", d.production_item, "default_bom")

				if not d.bom_no:
					d.bom_no = bom_no

				if bom_no:
					bom_nos.append(bom_no)

			bom_item_doctype = (
				"BOM Explosion Item" if self.filters.include_subassembly_raw_materials else "BOM Item"
			)

			bom = frappe.qb.DocType("BOM")
			bom_item = frappe.qb.DocType(bom_item_doctype)

			if self.filters.include_subassembly_raw_materials:
				qty_field = bom_item.qty_consumed_per_unit
			else:
				qty_field = bom_item.qty / bom.quantity

			raw_materials = (
				frappe.qb.from_(bom)
				.from_(bom_item)
				.select(
					bom_item.parent,
					bom_item.item_code,
					bom_item.item_name.as_("raw_material_name"),
					qty_field.as_("required_qty_per_unit"),
				)
				.where((bom_item.parent.isin(bom_nos)) & (bom_item.parent == bom.name) & (bom.docstatus == 1))
			)

			if self.filters.raw_material_item:
				raw_materials = raw_materials.where(bom_item.item_code == self.filters.raw_material_item)

			raw_materials = raw_materials.run(as_dict=True)

		if not raw_materials:
			return

		self.item_codes.extend([d.item_code for d in raw_materials if d.item_code])

		for d in raw_materials:
			if d.parent not in self.raw_materials_dict:
				self.raw_materials_dict.setdefault(d.parent, [])

			rows = self.raw_materials_dict[d.parent]
			rows.append(d)

	def get_item_details(self):
		if not (self.orders and self.item_codes):
			return

		self.item_details = {}
		for d in frappe.get_all(
			"Item Default",
			fields=["parent", "default_warehouse"],
			filters={"company": self.filters.company, "parent": ("in", self.item_codes)},
		):
			self.item_details[d.parent] = d

	def set_item_group_warehouses(self):
		if not self.raw_materials_dict:
			return

		for rows in self.raw_materials_dict.values():
			for row in rows:
				warehouse = self.get_item_group_warehouse(row.item_code)
				if warehouse:
					row.item_group_warehouse = warehouse
					if warehouse not in self.warehouses:
						self.warehouses.append(warehouse)

	def get_item_group_warehouse(self, item_code):
		if not item_code:
			return None

		if item_code in self.item_group_warehouse_cache:
			return self.item_group_warehouse_cache[item_code]

		item_group = self.item_group_cache.get(item_code)
		if item_group is None:
			item_group = frappe.db.get_value("Item", item_code, "item_group")
			self.item_group_cache[item_code] = item_group

		warehouse = get_default_source_warehouse(
			item_code=item_code,
			company=self.filters.company,
			item_group=item_group,
		)
		self.item_group_warehouse_cache[item_code] = warehouse

		return warehouse

	def get_bin_details(self):
		if not (self.orders and self.raw_materials_dict):
			return

		self.bin_details = {}
		self.mrp_warehouses = []
		if self.filters.raw_material_warehouse:
			self.mrp_warehouses.extend(get_child_warehouses(self.filters.raw_material_warehouse))
			self.warehouses.extend(self.mrp_warehouses)

		for d in frappe.get_all(
			"Bin",
			fields=["warehouse", "item_code", "actual_qty", "ordered_qty", "projected_qty"],
			filters={"item_code": ("in", self.item_codes), "warehouse": ("in", self.warehouses)},
		):
			key = (d.item_code, d.warehouse)
			if key not in self.bin_details:
				self.bin_details.setdefault(key, d)

	def get_purchase_details(self):
		if not (self.orders and self.raw_materials_dict):
			return

		self.purchase_details = {}

		purchased_items = frappe.get_all(
			"Purchase Order Item",
			fields=[
				"item_code",
				"min(schedule_date) as arrival_date",
				"qty as arrival_qty",
				"warehouse",
			],
			filters={
				"item_code": ("in", self.item_codes),
				"warehouse": ("in", self.warehouses),
				"docstatus": 1,
			},
			group_by="item_code, warehouse",
		)
		for d in purchased_items:
			key = (d.item_code, d.warehouse)
			if key not in self.purchase_details:
				self.purchase_details.setdefault(key, d)

	def get_material_request_details(self):
		if not (self.orders and self.raw_materials_dict):
			return

		self.material_request_details = {}

		material_requests = frappe.get_all(
			"Material Request",
			fields=["name"],
			filters={
				"docstatus": 1,
				"company": self.filters.company,
				"status": ("not in", ["Stopped", "Closed"]),
			},
			pluck="name",
		)
		if not material_requests:
			return

		child_meta = frappe.get_meta("Material Request Item")
		fields = ["item_code", "warehouse", "qty"]
		if child_meta.has_field("received_qty"):
			fields.append("received_qty")

		requested_items = frappe.get_all(
			"Material Request Item",
			fields=["parent"] + fields,
			filters={
				"parent": ("in", material_requests),
				"item_code": ("in", self.item_codes),
				"warehouse": ("in", self.warehouses),
				"docstatus": 1,
			},
		)

		for d in requested_items:
			pending_qty = max(flt(d.qty) - flt(d.get("received_qty")), 0)
			if not pending_qty:
				continue

			key = (d.item_code, d.warehouse)
			if key not in self.material_request_details:
				self.material_request_details[key] = frappe._dict({"requested_qty": 0, "material_requests": []})

			self.material_request_details[key].requested_qty += pending_qty
			if d.parent not in self.material_request_details[key].material_requests:
				self.material_request_details[key].material_requests.append(d.parent)

	def prepare_data(self):
		if not self.orders:
			return

		for d in self.orders:
			key = d.name if self.filters.based_on == "Work Order" else d.bom_no

			if not self.raw_materials_dict.get(key):
				continue

			bin_data = self.bin_details.get((d.production_item, d.warehouse)) or {}
			d.update({"for_warehouse": d.warehouse, "available_qty": 0})

			if bin_data and bin_data.get("actual_qty") > 0 and d.qty_to_manufacture:
				d.available_qty = (
					bin_data.get("actual_qty")
					if (d.qty_to_manufacture > bin_data.get("actual_qty"))
					else d.qty_to_manufacture
				)

				bin_data["actual_qty"] -= d.available_qty

			self.update_raw_materials(d, key)

	def update_raw_materials(self, data, key):
		self.index = 0

		base_warehouses = self.mrp_warehouses or []
		for d in self.raw_materials_dict.get(key):
			warehouses = list(base_warehouses)
			if self.filters.based_on != "Work Order":
				d.required_qty = d.required_qty_per_unit * data.qty_to_manufacture

			if not warehouses:
				warehouses = [data.warehouse]

			item_group_warehouse = d.get("item_group_warehouse") or self.get_item_group_warehouse(d.item_code)
			if item_group_warehouse:
				warehouses = [item_group_warehouse]
			elif self.filters.based_on == "Work Order" and d.warehouse:
				warehouses = [d.warehouse]
			else:
				item_details = self.item_details.get(d.item_code)
				if item_details:
					warehouses = [item_details["default_warehouse"]]

			if self.filters.raw_material_warehouse:
				warehouses = get_child_warehouses(self.filters.raw_material_warehouse)

			d.remaining_qty = d.required_qty
			self.pick_materials_from_warehouses(d, data, warehouses)

			if d.remaining_qty and self.filters.raw_material_warehouse and d.remaining_qty != d.required_qty:
				row = self.get_args()
				d.warehouse = self.filters.raw_material_warehouse
				d.required_qty = d.remaining_qty
				d.allotted_qty = 0
				d.raw_available_qty = 0
				d.requested_qty = self.get_requested_qty(d.item_code, d.warehouse)
				d.material_request_reference = self.get_material_request_reference(d.item_code, d.warehouse)
				d.request_qty = flt(d.required_qty)
				row.update(d)
				self.data.append(row)

	def pick_materials_from_warehouses(self, args, order_data, warehouses):
		for index, warehouse in enumerate(warehouses):
			if not args.remaining_qty:
				return

			row = self.get_args()

			key = (args.item_code, warehouse)
			bin_data = self.bin_details.get(key)

			if bin_data:
				row.update(bin_data)

			args.allotted_qty = 0
			args.raw_available_qty = flt(bin_data.get("actual_qty")) if bin_data else 0
			args.requested_qty = self.get_requested_qty(args.item_code, warehouse)
			args.material_request_reference = self.get_material_request_reference(args.item_code, warehouse)
			args.request_qty = max(flt(args.required_qty) - flt(args.raw_available_qty), 0)

			if bin_data and bin_data.get("actual_qty") > 0:
				args.allotted_qty = (
					bin_data.get("actual_qty")
					if (args.required_qty > bin_data.get("actual_qty"))
					else args.required_qty
				)

				args.remaining_qty -= args.allotted_qty
				bin_data["actual_qty"] -= args.allotted_qty

			if (
				self.mrp_warehouses and (args.allotted_qty or index == len(warehouses) - 1)
			) or not self.mrp_warehouses:
				if not self.index:
					row.update(order_data)
					self.index += 1

				args.warehouse = warehouse
				row.update(args)
				if self.purchase_details.get(key):
					row.update(self.purchase_details.get(key))

				self.data.append(row)

	def get_requested_qty(self, item_code, warehouse):
		if not item_code or not warehouse:
			return 0

		details = getattr(self, "material_request_details", {}).get((item_code, warehouse)) or {}
		return flt(details.get("requested_qty"))

	def get_material_request_reference(self, item_code, warehouse):
		if not item_code or not warehouse:
			return ""

		details = getattr(self, "material_request_details", {}).get((item_code, warehouse)) or {}
		return ", ".join(details.get("material_requests") or [])

	def add_total_row(self):
		if not self.data:
			return

		qty_fields = [
			"qty_to_manufacture",
			"available_qty",
			"required_qty",
			"raw_available_qty",
			"requested_qty",
			"request_qty",
			"allotted_qty",
			"arrival_qty",
		]

		total_row = frappe._dict({"name": _("Total")})
		for fieldname in qty_fields:
			total_row[fieldname] = sum(flt(row.get(fieldname)) for row in self.data)

		self.data.append(total_row)

	def get_args(self):
		return frappe._dict(
			{
				"work_order": "",
				"sales_order": "",
				"production_item": "",
				"production_item_name": "",
				"qty_to_manufacture": "",
				"produced_qty": "",
			}
		)

	def get_columns(self):
		based_on = self.filters.based_on

		self.columns = [
			{"label": _(based_on), "options": based_on, "fieldname": "name", "fieldtype": "Link", "width": 100},
			{
				"label": _("Item Code"),
				"fieldname": "production_item",
				"fieldtype": "Link",
				"options": "Item",
				"width": 120,
			},
			{
				"label": _("Item Name"),
				"fieldname": "production_item_name",
				"fieldtype": "Data",
				"width": 130,
			},
			{
				"label": _("Warehouse"),
				"options": "Warehouse",
				"fieldname": "for_warehouse",
				"fieldtype": "Link",
				"width": 100,
			},
			{"label": _("Order Qty"), "fieldname": "qty_to_manufacture", "fieldtype": "Float", "width": 80},
			{"label": _("Available"), "fieldname": "available_qty", "fieldtype": "Float", "width": 80},
		]

		fieldname, fieldtype = "delivery_date", "Date"
		if self.filters.based_on == "Sales Order" and self.filters.order_by == "Total Amount":
			fieldname, fieldtype = "base_grand_total", "Currency"
		elif self.filters.based_on == "Material Request":
			fieldname = "schedule_date"
		elif self.filters.based_on == "Work Order":
			fieldname = "planned_start_date"

		self.columns.append(
			{
				"label": _(self.filters.order_by),
				"fieldname": fieldname,
				"fieldtype": fieldtype,
				"width": 100,
			}
		)

		self.columns.extend(
			[
				{
					"label": _("Raw Material Code"),
					"fieldname": "item_code",
					"fieldtype": "Link",
					"options": "Item",
					"width": 120,
				},
				{
					"label": _("Raw Material Name"),
					"fieldname": "raw_material_name",
					"fieldtype": "Data",
					"width": 130,
				},
				{
					"label": _("Warehouse"),
					"options": "Warehouse",
					"fieldname": "warehouse",
					"fieldtype": "Link",
					"width": 110,
				},
				{"label": _("Required Qty"), "fieldname": "required_qty", "fieldtype": "Float", "width": 100},
				{
					"label": _("Available Qty"),
					"fieldname": "raw_available_qty",
					"fieldtype": "Float",
					"width": 110,
				},
				{"label": _("Requested Qty"), "fieldname": "requested_qty", "fieldtype": "Float", "width": 110},
				{
					"label": _("Material Request Reference"),
					"fieldname": "material_request_reference",
					"fieldtype": "Data",
					"width": 180,
				},
				{"label": _("To Request"), "fieldname": "request_qty", "fieldtype": "Float", "width": 100},
				{"label": _("Allotted Qty"), "fieldname": "allotted_qty", "fieldtype": "Float", "width": 100},
				{
					"label": _("Expected Arrival Date"),
					"fieldname": "arrival_date",
					"fieldtype": "Date",
					"width": 160,
				},
				{
					"label": _("Arrival Quantity"),
					"fieldname": "arrival_qty",
					"fieldtype": "Float",
					"width": 140,
				},
			]
		)
