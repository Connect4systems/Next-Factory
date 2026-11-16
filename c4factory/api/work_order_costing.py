"""Continuous production: automatic pooling and audited native ledger reposts.

Quantities, source transfers and posting dates are never rewritten by settlement.
Only cost fields and additional-cost rows change, with before/after audit records.
ERPNext Repost Item Valuation performs ledger reconstruction and downstream GL.
"""

import json
from contextlib import contextmanager

import frappe
from frappe import _
from frappe.utils import cint, flt

from c4factory.c4_manufacturing.continuous_cost_plan import distribute, stock_quantity

REDISTRIBUTION = "Continuous Material Cost Redistribution"
MANAGED_COSTS = {REDISTRIBUTION, "Operation Cost", "Mold Cost"}
BUSY = {"Queued", "Reposting"}
NATIVE_REPOST_METHOD = "erpnext.stock.doctype.repost_item_valuation.repost_item_valuation.repost_entries"


@contextmanager
def audit_write():
	previous = frappe.flags.get("continuous_cost_service")
	frappe.flags.continuous_cost_service = True
	try:
		yield
	finally:
		frappe.flags.continuous_cost_service = previous


def lock_order(name):
	frappe.db.sql("SELECT name FROM `tabWork Order` WHERE name=%s FOR UPDATE", name)


def is_continuous(wo):
	return bool(cint(wo.get("custom_continuous_costing")))


def set_new_order_costing_mode(doc, method=None):
	"""Automatically enable the configured company, including copied Work Orders."""
	account = frappe.db.get_single_value("Manufacturing Settings", "custom_continuous_cost_account")
	company = frappe.get_cached_value("Account", account, "company") if account else None
	doc.custom_continuous_costing = cint(bool(company and company == doc.company))
	# A copied order must never inherit the previous order's financial closure.
	doc.custom_continuous_cost_status = "Provisional" if doc.custom_continuous_costing else None
	doc.custom_continuous_settlement = None
	doc.custom_timesheet_cost_closed = 0


def check_available(wo):
	if wo.get("custom_continuous_cost_status") in BUSY | {"Failed"}:
		frappe.throw(_("Wait for the current production cost settlement to finish."))
	if cint(wo.get("custom_timesheet_cost_closed")):
		frappe.throw(_("Reopen production costing before changing this Work Order."))


def transfer_precision():
	return cint(frappe.get_precision("Stock Entry Detail", "transfer_qty"))


def normalized_allocations(sources):
	"""A batch consumes the available actual sources, including additional items."""
	precision = transfer_precision()
	rows = []
	for source in sources:
		qty = float(stock_quantity(source.remaining_qty, precision))
		if qty <= 0:
			continue
		rate = flt(source.total_amount) / flt(source.total_qty) if source.total_qty else 0
		rows.append(
			{
				"item_code": source.item_code,
				"stock_uom": source.stock_uom,
				"qty": qty,
				"valuation_rate": rate,
				"basic_rate": rate,
				"amount": qty * rate,
				"custom_pick_list_item": source.custom_pick_list_item,
				"custom_work_order_item": source.custom_work_order_item,
				"source_transfer_detail": source.name,
			}
		)
	return rows


def timesheet_context(wo):
	filters = {"custom_work_order": wo.name, "docstatus": ("<", 2)}
	has_timesheets = bool(frappe.db.exists("Timesheet", filters))
	complete = flt(wo.produced_qty) >= flt(wo.qty) > 0
	return {
		"has_timesheets": has_timesheets,
		"finish_timesheet": bool(
			is_continuous(wo)
			and complete
			and has_timesheets
			and not cint(wo.get("custom_timesheet_cost_closed"))
			and wo.get("custom_continuous_cost_status") not in BUSY | {"Failed"}
		),
		"cost_status": wo.get("custom_continuous_cost_status") or "Provisional",
	}


def validate_order_mode(doc, method=None):
	previous = doc.get_doc_before_save()
	if previous and is_continuous(previous) != is_continuous(doc):
		if frappe.db.exists(
			"Stock Entry",
			{
				"work_order": doc.name,
				"docstatus": ("<", 2),
				"custom_continuous_costing": 1,
			},
		):
			frappe.throw(_("Continuous costing cannot be changed after a continuous Finish was created."))
	if is_continuous(doc):
		if previous and doc.get("custom_continuous_cost_status") in BUSY | {"Failed"}:
			frappe.throw(_("Wait for production cost settlement before editing the Work Order."))
		validate_account(
			frappe.db.get_single_value("Manufacturing Settings", "custom_continuous_cost_account"),
			doc.company,
		)


def validate_account(account, company):
	if not account:
		frappe.throw(_("Set Continuous Cost Redistribution Account in Manufacturing Settings."))
	details = frappe.get_cached_value(
		"Account", account, ["company", "is_group", "account_type", "account_currency"], as_dict=True
	)
	currency = frappe.get_cached_value("Company", company, "default_currency")
	if not details or details.company != company or details.is_group or details.account_type == "Stock":
		frappe.throw(_("Cost redistribution requires a non-stock ledger account for the Work Order company."))
	if details.account_currency != currency:
		frappe.throw(_("Continuous costing requires cost accounts in company currency."))
	return account


def validate_stock_change(doc, method=None):
	if not doc.get("work_order"):
		return
	wo = frappe.get_doc("Work Order", doc.work_order)
	if not is_continuous(wo):
		return
	lock_order(wo.name)
	wo.reload()
	check_available(wo)
	if wo.get("custom_continuous_cost_status") == "Failed":
		frappe.throw(_("Retry the failed production cost settlement before posting more stock."))
	if doc.docstatus == 1 and method == "before_cancel":
		if frappe.db.exists("Continuous Cost Settlement", {"work_order": wo.name}):
			frappe.throw(
				_("Reopen and reverse production cost settlements before cancelling stock movements.")
			)


def validate_timesheet_change(doc, method=None):
	orders = {doc.get("custom_work_order")}
	previous = doc.get_doc_before_save()
	if previous:
		orders.add(previous.get("custom_work_order"))
	for name in sorted(order for order in orders if order):
		lock_order(name)
		wo = frappe.get_doc("Work Order", name)
		if is_continuous(wo):
			check_available(wo)


def on_stock_submit(doc, method=None):
	if not doc.get("work_order") or doc.get("stock_entry_type") != "Manufacture":
		return
	wo = frappe.get_doc("Work Order", doc.work_order)
	if is_continuous(wo):
		request_settlement(wo)


@frappe.whitelist(methods=["POST"])
def finish_timesheet(work_order):
	lock_order(work_order)
	wo = frappe.get_doc("Work Order", work_order)
	wo.check_permission("write")
	frappe.has_permission("Stock Entry", "submit", throw=True)
	frappe.has_permission("Repost Item Valuation", "submit", throw=True)
	if not timesheet_context(wo)["finish_timesheet"]:
		frappe.throw(
			_("Finish Timesheet is available after production completes and linked Timesheets exist.")
		)
	if frappe.db.exists("Timesheet", {"custom_work_order": wo.name, "docstatus": 0}):
		frappe.throw(_("Submit or delete all draft Timesheets before finishing production costing."))
	return request_settlement(wo, finalize_timesheets=True)


def request_settlement(wo, finalize_timesheets=False):
	lock_order(wo.name)
	wo.reload()
	check_available(wo)
	settlement = frappe.new_doc("Continuous Cost Settlement")
	settlement.flags.continuous_cost_service = True
	settlement.work_order = wo.name
	settlement.status = "Planned"
	settlement.finalize_timesheets = cint(finalize_timesheets)
	with audit_write():
		settlement.insert(ignore_permissions=True)
	frappe.db.set_value(
		"Work Order",
		wo.name,
		{
			"custom_continuous_cost_status": "Queued",
			"custom_continuous_settlement": settlement.name,
		},
	)
	frappe.enqueue(
		"c4factory.api.work_order_costing.run_settlement",
		queue=frappe.conf.get("continuous_costing_queue") or "long",
		timeout=1800,
		enqueue_after_commit=True,
		settlement_name=settlement.name,
	)
	return {"settlement": settlement.name, "status": "Queued"}


@frappe.whitelist(methods=["POST"])
def reopen_costing(work_order):
	frappe.only_for(("Manufacturing Manager", "Stock Manager", "System Manager"))
	lock_order(work_order)
	wo = frappe.get_doc("Work Order", work_order)
	wo.check_permission("write")
	if not is_continuous(wo) or not cint(wo.get("custom_timesheet_cost_closed")):
		frappe.throw(_("This Work Order has no closed Timesheet costing to reopen."))
	frappe.db.set_value(
		"Work Order",
		wo.name,
		{
			"custom_timesheet_cost_closed": 0,
			"custom_continuous_cost_status": "Awaiting Timesheet Finish",
		},
	)
	wo.add_comment("Info", _("Timesheet costing reopened; submitted production quantities are unchanged."))


def _load_finishes(wo):
	names = frappe.get_all(
		"Stock Entry",
		filters={
			"work_order": wo.name,
			"docstatus": 1,
			"stock_entry_type": "Manufacture",
		},
		pluck="name",
		order_by="posting_date, posting_time, creation",
	)
	return [frappe.get_doc("Stock Entry", name) for name in names]


def _cost_snapshot(doc):
	return {
		"stock_entry": doc.name,
		"items": [
			{
				key: row.get(key)
				for key in (
					"name",
					"item_code",
					"qty",
					"transfer_qty",
					"basic_rate",
					"basic_amount",
					"additional_cost",
					"amount",
					"valuation_rate",
					"set_basic_rate_manually",
				)
			}
			for row in doc.items
		],
		"additional_costs": [row.as_dict() for row in doc.additional_costs],
	}


def _pool_costs(wo, entries):
	from c4factory.c4_manufacturing.work_order_hooks import _get_work_order_timesheet_cost

	# The stock ledger holds the actual valuation, including rounded stock quantities,
	# additional transfers and returns from WIP. A Pick List itself never adds cost.
	material = flt(
		frappe.db.sql(
			"""
        SELECT COALESCE(SUM(sle.stock_value_difference), 0)
        FROM `tabStock Ledger Entry` sle
        INNER JOIN `tabStock Entry` se ON se.name=sle.voucher_no AND sle.voucher_type='Stock Entry'
        WHERE se.work_order=%s AND se.docstatus=1 AND sle.is_cancelled=0
          AND se.stock_entry_type='Material Transfer for Manufacture' AND sle.warehouse=%s
    """,
			(wo.name, wo.wip_warehouse),
		)[0][0]
	)
	molds = frappe.db.sql(
		"""
        SELECT sed.expense_account, SUM(-sle.stock_value_difference) AS amount
        FROM `tabStock Ledger Entry` sle
        INNER JOIN `tabStock Entry` se ON se.name=sle.voucher_no AND sle.voucher_type='Stock Entry'
        INNER JOIN `tabStock Entry Detail` sed ON sed.name=sle.voucher_detail_no
        WHERE se.work_order=%s AND se.docstatus=1 AND sle.is_cancelled=0
          AND se.custom_is_mold_material_issue=1 AND sle.actual_qty<0
        GROUP BY sed.expense_account
    """,
		wo.name,
		as_dict=True,
	)
	accounts = {}
	for row in molds:
		accounts[(row.expense_account, "Mold Cost")] = flt(row.amount)
	labor = _get_work_order_timesheet_cost(wo.name)
	if labor:
		from c4factory.c4_manufacturing.stock_entry_hooks import _get_operation_cost_account

		accounts[(_get_operation_cost_account(wo.company), "Operation Cost")] = labor
	# Preserve and redistribute other actual additional costs, rather than dropping them.
	for entry in entries:
		for row in entry.additional_costs:
			if row.description not in MANAGED_COSTS:
				key = (row.expense_account, row.description)
				accounts[key] = accounts.get(key, 0) + flt(row.base_amount)
	scrap = sum(flt(row.amount) for entry in entries for row in entry.items if row.is_scrap_item)
	return material - scrap, accounts


def cost_summary_values(wo):
	entries = _load_finishes(wo)
	material, accounts = _pool_costs(wo, entries)
	scrap = sum(flt(row.amount) for entry in entries for row in entry.items if row.is_scrap_item)
	molds = sum(value for (_account, description), value in accounts.items() if description == "Mold Cost")
	labor = sum(
		value for (_account, description), value in accounts.items() if description == "Operation Cost"
	)
	return {
		"c4_raw_material_cost": material + scrap + molds,
		"c4_scrap_material_cost": scrap,
		"c4_operating_cost": labor,
		"c4_total_cost": material + sum(accounts.values()),
	}


def refresh_cost_summary(wo):
	frappe.db.set_value("Work Order", wo.name, cost_summary_values(wo))


def _apply_costs(wo, entries):
	quantities, net_materials = [], []
	for entry in entries:
		finished = [row for row in entry.items if row.is_finished_item and not row.is_scrap_item]
		if len(finished) != 1 or finished[0].item_code != wo.production_item:
			frappe.throw(
				_("Continuous settlement requires one Work Order finished item per Manufacture entry.")
			)
		quantities.append(flt(finished[0].transfer_qty))
		net_materials.append(
			sum(flt(row.amount) for row in entry.items if row.s_warehouse)
			- sum(flt(row.amount) for row in entry.items if row.is_scrap_item)
		)
	if abs(sum(quantities) - flt(wo.produced_qty)) > 0.000001:
		frappe.throw(_("Submitted Manufacture quantities do not match the Work Order produced quantity."))
	material, accounts = _pool_costs(wo, entries)
	if material < -0.01:
		frappe.throw(_("Net material cost cannot be negative."))
	precision = entries[0].items[0].precision("amount")
	material_shares = distribute(material, quantities, precision)
	account_shares = {key: distribute(value, quantities, precision) for key, value in accounts.items()}
	clearing = validate_account(
		frappe.db.get_single_value("Manufacturing Settings", "custom_continuous_cost_account"), wo.company
	)
	for account, _description in accounts:
		validate_account(account, wo.company)
	for index, entry in enumerate(entries):
		finished = next(row for row in entry.items if row.is_finished_item and not row.is_scrap_item)
		# A previous native repost can refresh outgoing raw valuation. Rebuild the
		# receipt's material basis before adding the new redistribution difference.
		finished.basic_amount = net_materials[index]
		finished.basic_rate = net_materials[index] / quantities[index]
		# Native future-ledger recalculation must retain this automatically planned
		# receipt basis instead of recalculating it from one batch's outgoing rows.
		# This is a system-controlled rate, not a manual entry by the factory user.
		finished.set_basic_rate_manually = 1
		entry.set("additional_costs", [])
		costs = [(clearing, REDISTRIBUTION, flt(material_shares[index]) - net_materials[index])]
		costs += [
			(account, description, shares[index]) for (account, description), shares in account_shares.items()
		]
		for account, description, amount in costs:
			if flt(amount, precision):
				entry.append(
					"additional_costs",
					{
						"expense_account": account,
						"description": description,
						"amount": flt(amount, precision),
						"base_amount": flt(amount, precision),
						"exchange_rate": 1,
					},
				)
		# Keep original raw quantities and costs; redistribute value using signed charges.
		entry.distribute_additional_costs()
		entry.update_valuation_rate()
		entry.set_total_incoming_outgoing_value()
		entry.custom_allocated_operation_cost = sum(
			flt(row.base_amount) for row in entry.additional_costs if row.description == "Operation Cost"
		)
		entry.custom_allocated_mold_cost = sum(
			flt(row.base_amount) for row in entry.additional_costs if row.description == "Mold Cost"
		)
	total = material + sum(accounts.values())
	actual = sum(flt(row.amount) for entry in entries for row in entry.items if row.is_finished_item)
	if abs(flt(total, precision) - flt(actual, precision)) > 10 ** (-precision):
		frappe.throw(
			_("Production cost settlement does not balance: expected {0}, receipts {1}.").format(
				flt(total, precision), flt(actual, precision)
			)
		)
	return total


def run_settlement(settlement_name):
	"""One transaction prepares all cost changes and all native repost requests."""
	frappe.db.savepoint("continuous_cost_settlement")
	try:
		settlement = frappe.get_doc("Continuous Cost Settlement", settlement_name)
		lock_order(settlement.work_order)
		settlement.reload()
		if settlement.status != "Planned":
			return
		wo = frappe.get_doc("Work Order", settlement.work_order)
		entries = _load_finishes(wo)
		if not entries:
			frappe.throw(_("No submitted production receipts exist."))
		if frappe.db.exists(
			"Stock Entry",
			{
				"work_order": wo.name,
				"docstatus": 0,
				"stock_entry_type": "Manufacture",
			},
		):
			frappe.throw(_("Submit or delete the draft Finish before settling costs."))
		before = [_cost_snapshot(entry) for entry in entries]
		total = _apply_costs(wo, entries)
		reposts = []
		# Native validation checks closed accounting periods, freezes and batch/serial limits
		# BEFORE source cost fields change. Any validation error rolls back every request.
		for entry in entries:
			repost = frappe.get_doc(
				{
					"doctype": "Repost Item Valuation",
					"based_on": "Transaction",
					"voucher_type": "Stock Entry",
					"voucher_no": entry.name,
					"company": entry.company,
					"posting_date": entry.posting_date,
					"posting_time": entry.posting_time,
					"recreate_stock_ledgers": 1,
					"allow_zero_rate": 1,
				}
			)
			repost.insert(ignore_permissions=True)
			repost.flags.ignore_permissions = True
			repost.submit()
			reposts.append(repost.name)
		settlement.flags.continuous_cost_service = True
		settlement.plan = json.dumps(
			{"before": before, "after": [_cost_snapshot(e) for e in entries]}, default=str
		)
		settlement.reposts = json.dumps(reposts)
		settlement.total_cost = total
		settlement.produced_qty = wo.produced_qty
		settlement.status = "Reposting"
		with audit_write():
			settlement.save(ignore_permissions=True)
		for entry in entries:
			# Deliberately bypass quantity recalculation on submitted receipts. The immutable
			# settlement above audits every changed cost and native reposts rebuild SL/GL.
			for row in entry.items:
				frappe.db.set_value(
					"Stock Entry Detail",
					row.name,
					{
						key: row.get(key)
						for key in (
							"basic_rate",
							"basic_amount",
							"additional_cost",
							"amount",
							"valuation_rate",
							"set_basic_rate_manually",
						)
					},
				)
			entry.update_children()
			frappe.db.set_value(
				"Stock Entry",
				entry.name,
				{
					key: entry.get(key)
					for key in (
						"total_additional_costs",
						"total_incoming_value",
						"total_outgoing_value",
						"value_difference",
						"custom_allocated_operation_cost",
						"custom_allocated_mold_cost",
					)
				},
			)
		frappe.db.set_value("Work Order", wo.name, "custom_continuous_cost_status", "Reposting")
		refresh_cost_summary(wo)
		wo.add_comment(
			"Info",
			_("Production cost settlement {0} prepared; ledger reposting is pending.").format(
				settlement.name
			),
		)
		queue_pending_settlement_reposts()
	except Exception:
		frappe.db.rollback(save_point="continuous_cost_settlement")
		settlement = frappe.get_doc("Continuous Cost Settlement", settlement_name)
		settlement.db_set({"status": "Failed", "error": frappe.get_traceback()})
		frappe.db.set_value("Work Order", settlement.work_order, "custom_continuous_cost_status", "Failed")
		frappe.log_error(frappe.get_traceback(), "Continuous production cost settlement failed")
		if frappe.flags.in_test:
			raise


def queue_pending_settlement_reposts():
	"""Dispatch native reposts immediately, without waiting for the hourly scheduler.

	Use the scheduler's own RQ identity so it cannot concurrently enqueue another
	native repost runner for this site. Only our settlement references are processed.
	"""
	from erpnext.stock.doctype.repost_item_valuation.repost_item_valuation import in_configured_timeslot
	from frappe.utils.background_jobs import is_job_enqueued

	job_id = f"scheduled_job::{NATIVE_REPOST_METHOD}"
	if is_job_enqueued(job_id) or not in_configured_timeslot():
		return
	if not frappe.db.exists("Continuous Cost Settlement", {"status": "Reposting"}):
		return
	return frappe.enqueue(
		"c4factory.api.work_order_costing.execute_pending_settlement_reposts",
		queue=frappe.conf.get("continuous_costing_queue") or "long",
		timeout=3600,
		job_id=job_id,
		deduplicate=True,
		enqueue_after_commit=True,
	)


def execute_pending_settlement_reposts():
	"""Run native stock/GL reconstruction in the application-compatible worker."""
	names = frappe.get_all(
		"Continuous Cost Settlement",
		filters={"status": "Reposting"},
		pluck="name",
		order_by="creation asc",
	)
	for name in names:
		settlement = frappe.get_doc("Continuous Cost Settlement", name)
		for reference in json.loads(settlement.reposts or "[]"):
			repost = frappe.get_doc("Repost Item Valuation", reference)
			if repost.status == "Queued":
				repost.repost_now()
		sync_pending_settlements()


def sync_pending_settlements():
	for name in frappe.get_all("Continuous Cost Settlement", filters={"status": "Reposting"}, pluck="name"):
		settlement = frappe.get_doc("Continuous Cost Settlement", name)
		statuses = [
			frappe.db.get_value("Repost Item Valuation", ref, "status")
			for ref in json.loads(settlement.reposts or "[]")
		]
		if not statuses or any(status not in {"Completed", "Skipped", "Failed"} for status in statuses):
			continue
		lock_order(settlement.work_order)
		wo = frappe.get_doc("Work Order", settlement.work_order)
		if "Failed" in statuses:
			settlement.db_set("status", "Failed")
			frappe.db.set_value("Work Order", wo.name, "custom_continuous_cost_status", "Failed")
			continue
		try:
			verify_reposted_costs(settlement)
		except frappe.ValidationError as error:
			settlement.db_set({"status": "Failed", "error": str(error)})
			frappe.db.set_value("Work Order", wo.name, "custom_continuous_cost_status", "Failed")
			continue
		settlement.db_set("status", "Completed")
		context = timesheet_context(wo)
		complete = flt(wo.produced_qty) >= flt(wo.qty) > 0
		status = "Provisional"
		if complete:
			status = "Awaiting Timesheet Finish" if context["has_timesheets"] else "Material Cost Closed"
		if settlement.finalize_timesheets:
			status = "Cost Closed"
		frappe.db.set_value(
			"Work Order",
			wo.name,
			{
				"custom_continuous_cost_status": status,
				"custom_timesheet_cost_closed": cint(settlement.finalize_timesheets),
			},
		)
	queue_pending_settlement_reposts()


def verify_reposted_costs(settlement):
	"""Do not declare costs closed merely because the background jobs completed."""
	for entry in json.loads(settlement.plan)["after"]:
		for row in entry["items"]:
			# The plan stores original raw rows too; identify the incoming finished
			# row by its live immutable source document, not by a guessed row index.
			if not frappe.db.get_value("Stock Entry Detail", row["name"], "is_finished_item"):
				continue
			ledger_value = frappe.db.sql(
				"""
				SELECT SUM(stock_value_difference) FROM `tabStock Ledger Entry`
				WHERE voucher_type='Stock Entry' AND voucher_no=%s AND voucher_detail_no=%s
				  AND is_cancelled=0 AND actual_qty>0
			""",
				(entry["stock_entry"], row["name"]),
			)[0][0]
			if ledger_value is None or abs(flt(ledger_value) - flt(row["amount"])) > 0.05:
				frappe.throw(
					_("Reposted stock value does not match the cost settlement for {0}.").format(
						entry["stock_entry"]
					)
				)
		balance = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(debit-credit), 0) FROM `tabGL Entry`
			WHERE voucher_type='Stock Entry' AND voucher_no=%s AND is_cancelled=0
		""",
			entry["stock_entry"],
		)[0][0]
		if abs(flt(balance)) > 0.01:
			frappe.throw(
				_("Reposted accounting entries do not balance for {0}.").format(entry["stock_entry"])
			)


@frappe.whitelist(methods=["POST"])
def retry_settlement(work_order):
	lock_order(work_order)
	wo = frappe.get_doc("Work Order", work_order)
	wo.check_permission("write")
	frappe.has_permission("Repost Item Valuation", "submit", throw=True)
	if wo.get("custom_continuous_cost_status") != "Failed":
		frappe.throw(_("Only a failed cost settlement can be retried."))
	latest = frappe.get_doc("Continuous Cost Settlement", wo.custom_continuous_settlement)
	refs = json.loads(latest.reposts or "[]")
	if refs:
		statuses = [frappe.db.get_value("Repost Item Valuation", name, "status") for name in refs]
		if all(status in {"Completed", "Skipped"} for status in statuses):
			# The native jobs finished but the independent value check failed.
			# Replan from current actual ledger costs; retain the failed audit intact.
			frappe.db.set_value("Work Order", wo.name, "custom_continuous_cost_status", "Provisional")
			return request_settlement(wo, finalize_timesheets=bool(latest.finalize_timesheets))
		for name in refs:
			repost = frappe.get_doc("Repost Item Valuation", name)
			if repost.status == "Failed":
				repost.restart_reposting()
			elif repost.status not in {"Completed", "Skipped", "Queued", "In Progress"}:
				frappe.throw(_("Review the linked Repost Item Valuation before retrying."))
		latest.db_set("status", "Reposting")
		frappe.db.set_value("Work Order", wo.name, "custom_continuous_cost_status", "Reposting")
		queue_pending_settlement_reposts()
		return {"settlement": latest.name, "status": "Reposting"}
	frappe.db.set_value("Work Order", wo.name, "custom_continuous_cost_status", "Provisional")
	return request_settlement(wo, finalize_timesheets=bool(latest.finalize_timesheets))
