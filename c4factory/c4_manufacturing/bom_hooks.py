from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cstr


PRODUCT_BOM_TYPE = "Product"
MOLD_BOM_TYPE = "Mold"
MOLD_BOM_PREFIX = "MLD"


def autoname(doc, method=None):
	"""Keep standard Product BOM names and use MLD for Mold BOMs."""
	bom_type = cstr(doc.get("custom_bom_type")).strip()
	if not bom_type:
		bom_type = PRODUCT_BOM_TYPE
		doc.custom_bom_type = bom_type

	if bom_type == PRODUCT_BOM_TYPE:
		# ERPNext has already generated BOM-{item}-{###}; preserve it exactly.
		return

	if bom_type != MOLD_BOM_TYPE:
		frappe.throw(_("BOM Type must be either Product or Mold."))

	item = cstr(doc.get("item")).strip()
	if not item:
		frappe.throw(_("Item is required to generate the BOM name."))

	search_key = f"{MOLD_BOM_PREFIX}-{item}%"
	existing_boms = frappe.get_all(
		"BOM",
		filters={
			"name": ("like", search_key),
			"amended_from": ("is", "not set"),
		},
		pluck="name",
	)
	index = doc.get_index_for_bom(existing_boms)
	doc.name = _build_bom_name(MOLD_BOM_PREFIX, item, index)


def _build_bom_name(prefix: str, item: str, index: int) -> str:
	"""Build a BOM name with the same length and suffix rules as ERPNext."""
	suffix = f"{index:03d}"
	name = f"{prefix}-{item}-{suffix}"
	if len(name) <= 140:
		return name

	truncated_length = 140 - (len(prefix) + len(suffix) + 2)
	truncated_item = item[:truncated_length].rsplit(" ", 1)[0]
	return f"{prefix}-{truncated_item}-{suffix}"
