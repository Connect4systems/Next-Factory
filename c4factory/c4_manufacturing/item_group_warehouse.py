from __future__ import annotations

from c4factory.c4_manufacturing.work_order_hooks import get_default_source_warehouse


def set_warehouses_from_item_group(doc, method: str | None = None) -> None:
	"""Fill empty transaction warehouses from the item's Item Group defaults."""
	if doc.doctype == "Stock Entry":
		_set_stock_entry_source_warehouses(doc)
	elif doc.doctype == "Material Request":
		_set_material_request_source_warehouses(doc)
	elif doc.doctype == "Purchase Receipt":
		_set_purchase_receipt_warehouses(doc)


def _set_stock_entry_source_warehouses(doc) -> None:
	purpose = (doc.get("purpose") or doc.get("stock_entry_type") or "").strip()
	if purpose == "Material Receipt":
		return

	for row in doc.get("items") or []:
		if (
			not row.get("item_code")
			or row.get("s_warehouse")
			or (
				purpose in {"Manufacture", "Repack", "Process Loss"}
				and (row.get("is_finished_item") or row.get("is_scrap_item"))
			)
		):
			continue

		row.s_warehouse = get_default_source_warehouse(
			item_code=row.item_code,
			company=doc.get("company"),
			item_group=row.get("item_group"),
		)


def _set_material_request_source_warehouses(doc) -> None:
	if doc.get("material_request_type") != "Material Transfer":
		return

	for row in doc.get("items") or []:
		if not row.get("item_code") or row.get("from_warehouse"):
			continue

		row.from_warehouse = get_default_source_warehouse(
			item_code=row.item_code,
			company=doc.get("company"),
			item_group=row.get("item_group"),
		)


def _set_purchase_receipt_warehouses(doc) -> None:
	for row in doc.get("items") or []:
		if not row.get("item_code") or row.get("warehouse"):
			continue

		row.warehouse = get_default_source_warehouse(
			item_code=row.item_code,
			company=doc.get("company"),
			item_group=row.get("item_group"),
		)
