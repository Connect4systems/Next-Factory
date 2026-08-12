from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt

from c4factory.api.work_order_pick_list import is_continuous_manufacture_item
from c4factory.c4_manufacturing.work_order_hooks import get_default_source_warehouse


REALTIME_EVENT = "c4factory_mold_issue"
CHANNEL_CONTINUOUS = "Continuous"
CHANNEL_STANDARD = "Standard"


def _get_bom_type_fieldname() -> str | None:
	meta = frappe.get_meta("BOM")
	for fieldname in ("custom_bom_type", "bom_type"):
		if meta.has_field(fieldname):
			return fieldname

	for field in meta.fields:
		if (field.label or "").strip().lower() == "bom type":
			return field.fieldname
	return None


def _get_bom_type(bom) -> str:
	fieldname = _get_bom_type_fieldname()
	return cstr(bom.get(fieldname)).strip() if fieldname else ""


def _validate_bom(bom_no: str, production_item: str, expected_type: str) -> object:
	if not bom_no:
		frappe.throw(_("{0} BOM is required.").format(expected_type))

	bom = frappe.get_doc("BOM", bom_no)
	bom.check_permission("read")
	if bom.docstatus != 1 or not flt(bom.get("is_active")):
		frappe.throw(_("BOM {0} must be submitted and active.").format(frappe.bold(bom_no)))
	if bom.item != production_item:
		frappe.throw(
			_("BOM {0} belongs to item {1}, not Work Order item {2}.").format(
				frappe.bold(bom_no), frappe.bold(bom.item), frappe.bold(production_item)
			)
		)

	type_field = _get_bom_type_fieldname()
	if expected_type == "Mold" and not type_field:
		frappe.throw(_("The BOM Type field is not installed on BOM."))
	if type_field and _get_bom_type(bom).lower() != expected_type.lower():
		frappe.throw(
			_("BOM {0} must have BOM Type {1}.").format(
				frappe.bold(bom_no), frappe.bold(expected_type)
			)
		)
	return bom


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def bom_query(doctype, txt, searchfield, start, page_len, filters):
	if not frappe.has_permission("BOM", "read"):
		return []
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	production_item = filters.get("production_item")
	expected_type = filters.get("bom_type") or "Product"
	type_field = _get_bom_type_fieldname()
	if not type_field:
		return []

	return frappe.db.sql(
		f"""
		SELECT name, item
		FROM `tabBOM`
		WHERE docstatus = 1
		  AND COALESCE(is_active, 0) = 1
		  AND item = %(production_item)s
		  AND LOWER(COALESCE(`{type_field}`, '')) = LOWER(%(bom_type)s)
		  AND (name LIKE %(txt)s OR item LIKE %(txt)s)
		ORDER BY is_default DESC, modified DESC
		LIMIT %(start)s, %(page_len)s
		""",
		{
			"production_item": production_item,
			"bom_type": expected_type,
			"txt": f"%{txt}%",
			"start": int(start),
			"page_len": int(page_len),
		},
	)


def _get_mold_material_values(
	mold_bom_no: str,
	mold_qty: float,
	company: str | None,
	use_multi_level_bom: bool,
) -> list[dict]:
	bom = frappe.get_doc("BOM", mold_bom_no)
	table = "BOM Explosion Item" if use_multi_level_bom else "BOM Item"
	bom_rows = frappe.get_all(
		table,
		filters={"parent": mold_bom_no, "docstatus": ("<", 2)},
		fields=["item_code", "stock_qty"],
	)
	scale = flt(mold_qty) / (flt(bom.quantity) or 1.0)
	required_by_item = {}
	for row in bom_rows:
		if row.item_code:
			required_by_item[row.item_code] = flt(required_by_item.get(row.item_code)) + flt(
				row.stock_qty
			) * scale

	materials = []
	for item_code, required_qty in required_by_item.items():
		if required_qty <= 0:
			continue
		item = frappe.get_cached_value(
			"Item", item_code, ["item_name", "item_group", "stock_uom"], as_dict=True
		)
		source_warehouse = get_default_source_warehouse(
			item_code=item_code,
			item_group=item.item_group,
			company=company,
		)
		if not source_warehouse:
			frappe.throw(
				_("Default source warehouse is required for mold material {0}.").format(
					frappe.bold(item_code)
				)
			)
		materials.append(
			{
				"item_code": item_code,
				"item_name": item.item_name,
				"item_group": item.item_group,
				"source_warehouse": source_warehouse,
				"required_qty": required_qty,
				"stock_uom": item.stock_uom,
				"issued_qty": 0,
				"balance_qty": required_qty,
			}
		)
	return materials


@frappe.whitelist()
def get_mold_materials(
	production_item: str,
	mold_bom_no: str,
	mold_qty: float,
	company: str | None = None,
	use_multi_level_bom: int = 0,
) -> list[dict]:
	_validate_bom(mold_bom_no, production_item, "Mold")
	if flt(mold_qty) <= 0:
		return []
	return _get_mold_material_values(
		mold_bom_no, flt(mold_qty), company, bool(cint(use_multi_level_bom))
	)


def validate_and_set_mold_materials(wo) -> None:
	if not wo.meta.has_field("custom_mold_bom_no"):
		return

	mold_bom_no = wo.get("custom_mold_bom_no")
	mold_qty = flt(wo.get("custom_mold_qty"))
	if not mold_bom_no:
		if wo.get("bom_no"):
			_validate_bom(wo.bom_no, wo.production_item, "Product")
		wo.set("custom_mold_materials", [])
		return
	if mold_qty <= 0:
		frappe.throw(_("Mold QTY must be greater than zero."))

	if wo.get("bom_no"):
		_validate_bom(wo.bom_no, wo.production_item, "Product")
	_validate_bom(mold_bom_no, wo.production_item, "Mold")
	should_rebuild = wo.docstatus == 0 or (
		getattr(wo, "_action", None) == "submit" and not wo.get("custom_mold_materials")
	)
	if should_rebuild:
		materials = _get_mold_material_values(
			mold_bom_no,
			mold_qty,
			wo.get("company"),
			bool(cint(wo.get("use_multi_level_bom"))),
		)
		if not materials:
			frappe.throw(_("Mold BOM {0} has no material rows.").format(frappe.bold(mold_bom_no)))
		wo.set("custom_mold_materials", [])
		for values in materials:
			wo.append("custom_mold_materials", values)


def _get_mold_channels(wo) -> tuple[list, list]:
	continuous_rows = []
	standard_rows = []
	item_group_cache = {}
	continuous_group_cache = {}
	for row in wo.get("custom_mold_materials") or []:
		if not row.get("item_code") or flt(row.get("required_qty")) <= 0:
			continue
		if is_continuous_manufacture_item(
			row,
			item_group_cache=item_group_cache,
			continuous_group_cache=continuous_group_cache,
		):
			continuous_rows.append(row)
		else:
			standard_rows.append(row)
	return continuous_rows, standard_rows


def _get_channel_issued_qty(
	work_order: str, channel: str, exclude_stock_entry: str | None = None
) -> float:
	return flt(
		frappe.db.sql(
			"""
			SELECT COALESCE(SUM(custom_mold_issue_qty), 0)
			FROM `tabStock Entry`
			WHERE work_order = %(work_order)s
			  AND COALESCE(custom_is_mold_material_issue, 0) = 1
			  AND custom_mold_issue_channel = %(channel)s
			  AND docstatus < 2
			  AND name != %(exclude_stock_entry)s
			""",
			{
				"work_order": work_order,
				"channel": channel,
				"exclude_stock_entry": exclude_stock_entry or "",
			},
		)[0][0]
	)


def _get_remaining_mold_qty(wo, continuous_rows=None, standard_rows=None) -> float:
	if continuous_rows is None or standard_rows is None:
		continuous_rows, standard_rows = _get_mold_channels(wo)
	covered = []
	if continuous_rows:
		covered.append(_get_channel_issued_qty(wo.name, CHANNEL_CONTINUOUS))
	if standard_rows:
		covered.append(_get_channel_issued_qty(wo.name, CHANNEL_STANDARD))
	shared_covered_qty = min(covered) if covered else 0.0
	return max(flt(wo.custom_mold_qty) - shared_covered_qty, 0.0)


def _get_channel_request_quantities(
	wo, requested_qty: float, continuous_rows: list, standard_rows: list
) -> dict[str, float]:
	channel_totals = {}
	if continuous_rows:
		channel_totals[CHANNEL_CONTINUOUS] = _get_channel_issued_qty(
			wo.name, CHANNEL_CONTINUOUS
		)
	if standard_rows:
		channel_totals[CHANNEL_STANDARD] = _get_channel_issued_qty(wo.name, CHANNEL_STANDARD)
	if not channel_totals:
		return {}

	shared_covered_qty = min(channel_totals.values())
	target_qty = min(shared_covered_qty + flt(requested_qty), flt(wo.custom_mold_qty))
	return {
		channel: max(target_qty - issued_qty, 0.0)
		for channel, issued_qty in channel_totals.items()
	}


@frappe.whitelist()
def get_mold_issue_context(work_order: str) -> dict:
	wo = frappe.get_doc("Work Order", work_order)
	wo.check_permission("read")
	continuous_rows, standard_rows = _get_mold_channels(wo)
	_sync_mold_material_balances(wo)
	return {
		"has_eligible_items": bool(continuous_rows or standard_rows),
		"has_continuous_items": bool(continuous_rows),
		"has_standard_items": bool(standard_rows),
		"remaining_qty": _get_remaining_mold_qty(wo, continuous_rows, standard_rows),
		"pending": bool(wo.get("custom_mold_issue_pending")),
	}


def _validate_work_order_for_create(wo) -> None:
	if wo.docstatus != 1:
		frappe.throw(_("Work Order must be submitted before creating the mold."))
	if wo.get("status") in {"Stopped", "Closed", "Completed", "Cancelled"}:
		frappe.throw(
			_("Mold material cannot be created while Work Order status is {0}.").format(
				wo.status
			)
		)


@frappe.whitelist()
def enqueue_mold_material_issue(work_order: str, qty: float) -> dict:
	qty = flt(qty)
	if qty <= 0:
		frappe.throw(_("Mold quantity must be greater than zero."))

	wo = frappe.get_doc("Work Order", work_order)
	wo.check_permission("read")
	_validate_work_order_for_create(wo)
	continuous_rows, standard_rows = _get_mold_channels(wo)
	if not continuous_rows and not standard_rows:
		frappe.throw(_("No Mold Material rows are available."))
	if not frappe.has_permission("Stock Entry", "create"):
		frappe.throw(_("You do not have permission to create Stock Entries."))

	locked = frappe.db.sql(
		"""
		SELECT custom_mold_issue_pending
		FROM `tabWork Order`
		WHERE name = %s
		FOR UPDATE
		""",
		(wo.name,),
		as_dict=True,
	)
	if not locked:
		frappe.throw(_("Work Order {0} does not exist.").format(wo.name))
	if locked[0].custom_mold_issue_pending:
		return {
			"status": "already_queued",
			"message": _("Mold Material Issue documents are already being created."),
		}

	remaining_qty = _get_remaining_mold_qty(wo, continuous_rows, standard_rows)
	if qty > remaining_qty + 0.000001:
		frappe.throw(
			_("Mold quantity {0} exceeds the remaining quantity {1}.").format(
				qty, remaining_qty
			)
		)
	channel_quantities = _get_channel_request_quantities(
		wo, qty, continuous_rows, standard_rows
	)
	if flt(channel_quantities.get(CHANNEL_CONTINUOUS)) and not frappe.has_permission(
		"Stock Entry", "submit"
	):
		frappe.throw(_("You do not have permission to submit Stock Entries."))

	request_id = frappe.generate_hash(length=20)
	frappe.db.set_value(
		"Work Order",
		wo.name,
		{
			"custom_mold_issue_pending": 1,
			"custom_mold_issue_request_id": request_id,
		},
		update_modified=False,
	)
	frappe.enqueue(
		"c4factory.api.work_order_mold.create_mold_material_issues",
		queue="default",
		timeout=900,
		enqueue_after_commit=True,
		job_id=f"c4-mold-issue-{request_id}",
		deduplicate=True,
		work_order=wo.name,
		qty=qty,
		request_id=request_id,
		initiated_by=frappe.session.user,
	)
	return {
		"status": "queued",
		"message": _("Mold Material Issue creation started in the background."),
	}


def create_mold_material_issues(
	work_order: str, qty: float, request_id: str, initiated_by: str
) -> None:
	try:
		existing = frappe.get_all(
			"Stock Entry",
			filters={
				"custom_mold_issue_request_id": request_id,
				"custom_is_mold_material_issue": 1,
				"docstatus": ("<", 2),
			},
			fields=["name", "custom_mold_issue_channel", "docstatus"],
		)
		if existing:
			_clear_pending_request(work_order, request_id)
			frappe.db.commit()
			_publish_result(initiated_by, work_order, "success", existing)
			return

		wo = frappe.get_doc("Work Order", work_order)
		_validate_work_order_for_create(wo)
		continuous_rows, standard_rows = _get_mold_channels(wo)
		qty = flt(qty)
		remaining_qty = _get_remaining_mold_qty(wo, continuous_rows, standard_rows)
		if qty <= 0 or qty > remaining_qty + 0.000001:
			frappe.throw(
				_("Mold quantity {0} exceeds the remaining quantity {1}.").format(
					qty, remaining_qty
				)
			)

		channel_quantities = _get_channel_request_quantities(
			wo, qty, continuous_rows, standard_rows
		)
		created = []
		continuous_qty = flt(channel_quantities.get(CHANNEL_CONTINUOUS))
		if continuous_rows and continuous_qty > 0:
			continuous_entry = _build_mold_material_issue(
				wo, continuous_rows, continuous_qty, request_id, CHANNEL_CONTINUOUS
			)
			continuous_entry.insert()
			continuous_entry.submit()
			created.append(
				frappe._dict(
					{
						"name": continuous_entry.name,
						"custom_mold_issue_channel": CHANNEL_CONTINUOUS,
						"docstatus": continuous_entry.docstatus,
					}
				)
			)
		standard_qty = flt(channel_quantities.get(CHANNEL_STANDARD))
		if standard_rows and standard_qty > 0:
			standard_entry = _build_mold_material_issue(
				wo, standard_rows, standard_qty, request_id, CHANNEL_STANDARD
			)
			standard_entry.insert()
			created.append(
				frappe._dict(
					{
						"name": standard_entry.name,
						"custom_mold_issue_channel": CHANNEL_STANDARD,
						"docstatus": standard_entry.docstatus,
					}
				)
			)

		_sync_mold_material_balances(wo)
		_clear_pending_request(wo.name, request_id)
		frappe.db.commit()
		_publish_result(initiated_by, wo.name, "success", created)
	except Exception as exc:
		error_message = cstr(exc) or _("Unable to create Mold Material Issue documents.")
		traceback = frappe.get_traceback()
		frappe.db.rollback()
		_clear_pending_request(work_order, request_id)
		frappe.db.commit()
		frappe.log_error(traceback, f"C4Factory mold issue failed ({work_order})")
		_publish_result(
			initiated_by, work_order, "error", [], error_message=error_message
		)


def _build_mold_material_issue(wo, rows, qty, request_id, channel):
	mold_account = _get_mold_clearing_account(wo.company)
	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = "Material Issue"
	se.purpose = "Material Issue"
	se.company = wo.company
	se.work_order = wo.name
	se.from_bom = 0
	se.fg_completed_qty = 0
	se.custom_is_mold_material_issue = 1
	se.custom_mold_issue_channel = channel
	se.custom_mold_issue_qty = qty
	se.custom_mold_issue_request_id = request_id
	if se.meta.has_field("custom_work_order"):
		se.custom_work_order = wo.name

	scale = qty / (flt(wo.custom_mold_qty) or 1.0)
	for mold_row in rows:
		row_qty = flt(mold_row.required_qty) * scale
		if row_qty <= 0:
			continue
		se.append(
			"items",
			{
				"item_code": mold_row.item_code,
				"qty": row_qty,
				"uom": mold_row.stock_uom,
				"stock_uom": mold_row.stock_uom,
				"conversion_factor": 1,
				"s_warehouse": mold_row.source_warehouse,
				"is_finished_item": 0,
				"is_scrap_item": 0,
				"expense_account": mold_account,
				"custom_mold_material": mold_row.name,
			},
		)

	if not se.items:
		frappe.throw(_("No Mold Material rows are eligible for {0}.").format(channel))
	se.set_missing_values()
	# ERPNext clears the standard Work Order link for a Material Issue while
	# applying purpose defaults. Restore both links after missing values run.
	se.work_order = wo.name
	if se.meta.has_field("custom_work_order"):
		se.custom_work_order = wo.name
	for row, mold_row in zip(se.items, rows, strict=False):
		row.s_warehouse = mold_row.source_warehouse
		row.t_warehouse = None
		row.expense_account = mold_account
		row.custom_mold_material = mold_row.name
	return se


def validate_mold_material_issue(doc, method: str | None = None) -> None:
	if not flt(doc.get("custom_is_mold_material_issue")):
		return
	work_order = _resolve_mold_work_order(doc)
	if not work_order:
		frappe.throw(_("Mold Material Issue requires a Work Order."))

	doc.work_order = work_order
	if doc.meta.has_field("custom_work_order"):
		doc.custom_work_order = work_order
	wo = frappe.get_doc("Work Order", work_order)
	if wo.docstatus != 1 or wo.get("status") in {"Stopped", "Closed", "Completed", "Cancelled"}:
		frappe.throw(_("Work Order {0} is not available for Mold Material Issue.").format(wo.name))
	draft_finish = frappe.db.get_value(
		"Stock Entry",
		{"work_order": wo.name, "docstatus": 0, "stock_entry_type": "Manufacture"},
		"name",
	)
	if draft_finish:
		frappe.throw(
			_(
				"Mold material cannot be issued while draft Finish Stock Entry {0} exists. "
				"Submit or delete that Finish entry first."
			).format(frappe.bold(draft_finish))
		)

	channel = doc.get("custom_mold_issue_channel")
	if channel not in {CHANNEL_CONTINUOUS, CHANNEL_STANDARD}:
		frappe.throw(_("Mold Issue Channel must be Continuous or Standard."))
	issue_qty = flt(doc.get("custom_mold_issue_qty"))
	if issue_qty <= 0:
		frappe.throw(_("Mold Issue Qty must be greater than zero."))

	continuous_rows, standard_rows = _get_mold_channels(wo)
	expected_rows = continuous_rows if channel == CHANNEL_CONTINUOUS else standard_rows
	remaining_without_current = max(
		flt(wo.custom_mold_qty)
		- _get_channel_issued_qty(wo.name, channel, exclude_stock_entry=doc.name),
		0,
	)
	if issue_qty > remaining_without_current + 0.000001:
		frappe.throw(
			_("Mold Issue Qty {0} exceeds the available balance {1}.").format(
				issue_qty, remaining_without_current
			)
		)

	expected = {row.name: row for row in expected_rows}
	actual_qty = {}
	doc.stock_entry_type = "Material Issue"
	doc.purpose = "Material Issue"
	doc.company = wo.company
	doc.fg_completed_qty = 0
	mold_account = _get_mold_clearing_account(wo.company)
	if doc.meta.has_field("custom_work_order"):
		doc.custom_work_order = wo.name

	for row in doc.get("items") or []:
		mold_row = expected.get(row.get("custom_mold_material"))
		if not mold_row or row.item_code != mold_row.item_code:
			frappe.throw(_("Every Stock Entry row must match its Mold Material row."))
		row.s_warehouse = mold_row.source_warehouse
		row.t_warehouse = None
		row.is_finished_item = 0
		row.is_scrap_item = 0
		row.expense_account = mold_account
		actual_qty[mold_row.name] = flt(actual_qty.get(mold_row.name)) + abs(
			flt(row.get("transfer_qty")) or flt(row.get("qty"))
		)

	scale = issue_qty / (flt(wo.custom_mold_qty) or 1.0)
	for mold_row in expected_rows:
		expected_qty = flt(mold_row.required_qty) * scale
		if abs(flt(actual_qty.get(mold_row.name)) - expected_qty) > 0.000001:
			frappe.throw(
				_("Mold material {0} must have quantity {1}.").format(
					frappe.bold(mold_row.item_code), expected_qty
				)
			)


def _resolve_mold_work_order(doc) -> str | None:
	work_order = doc.get("work_order") or doc.get("custom_work_order")
	if work_order:
		return work_order

	request_id = doc.get("custom_mold_issue_request_id")
	if request_id:
		work_order = frappe.db.get_value(
			"Work Order", {"custom_mold_issue_request_id": request_id}, "name"
		)
		if work_order:
			return work_order

	for row in doc.get("items") or []:
		mold_material = row.get("custom_mold_material")
		if not mold_material:
			continue
		work_order = frappe.db.get_value("Mold Material", mold_material, "parent")
		if work_order:
			return work_order

	return None


def _get_mold_clearing_account(company: str) -> str:
	account = frappe.db.get_single_value(
		"Manufacturing Settings", "custom_mold_cost_expense_account"
	)
	if not account:
		frappe.throw(_("Set Mold Cost Expense Account in Manufacturing Settings."))
	account_details = frappe.get_cached_value(
		"Account", account, ["company", "is_group"], as_dict=True
	)
	if (
		not account_details
		or account_details.company != company
		or flt(account_details.is_group)
	):
		frappe.throw(
			_("Mold Cost Expense Account must be a ledger account for {0}.").format(
				frappe.bold(company)
			)
		)
	return account


def sync_mold_material_balances(doc, method: str | None = None) -> None:
	if not flt(doc.get("custom_is_mold_material_issue")) or not doc.get("work_order"):
		return
	if frappe.db.exists("Work Order", doc.work_order):
		_sync_mold_material_balances(frappe.get_doc("Work Order", doc.work_order))


def _sync_mold_material_balances(wo) -> None:
	if not wo.get("custom_mold_materials"):
		return
	rows = frappe.db.sql(
		"""
		SELECT
			sed.custom_mold_material,
			SUM(CASE WHEN se.docstatus = 1 THEN ABS(sed.qty) ELSE 0 END) AS issued_qty,
			SUM(CASE WHEN se.docstatus < 2 THEN ABS(sed.qty) ELSE 0 END) AS reserved_qty
		FROM `tabStock Entry Detail` sed
		INNER JOIN `tabStock Entry` se ON se.name = sed.parent
		WHERE se.work_order = %s
		  AND COALESCE(se.custom_is_mold_material_issue, 0) = 1
		  AND COALESCE(sed.custom_mold_material, '') != ''
		GROUP BY sed.custom_mold_material
		""",
		(wo.name,),
		as_dict=True,
	)
	by_material = {row.custom_mold_material: row for row in rows}
	for material in wo.custom_mold_materials:
		values = by_material.get(material.name) or {}
		issued_qty = flt(values.get("issued_qty"))
		balance_qty = max(flt(material.required_qty) - flt(values.get("reserved_qty")), 0)
		frappe.db.set_value(
			"Mold Material",
			material.name,
			{"issued_qty": issued_qty, "balance_qty": balance_qty},
			update_modified=False,
		)


def _clear_pending_request(work_order: str, request_id: str) -> None:
	current_request = frappe.db.get_value(
		"Work Order", work_order, "custom_mold_issue_request_id"
	)
	if current_request == request_id:
		frappe.db.set_value(
			"Work Order",
			work_order,
			{"custom_mold_issue_pending": 0, "custom_mold_issue_request_id": None},
			update_modified=False,
		)


def _publish_result(user, work_order, status, entries, error_message=None) -> None:
	try:
		frappe.publish_realtime(
			REALTIME_EVENT,
			{
				"work_order": work_order,
				"status": status,
				"message": error_message or _("Mold Material Issue documents were created."),
				"stock_entries": [
					{
						"name": row.name,
						"channel": row.custom_mold_issue_channel,
						"docstatus": row.docstatus,
					}
					for row in entries
				],
			},
			user=user,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"C4Factory mold notification failed ({work_order})")
