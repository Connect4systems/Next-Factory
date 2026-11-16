# Continuous production costing

New Work Orders automatically enable this mode for the company configured by the
Continuous Cost Redistribution Account. Copied orders receive fresh costing state.
Existing orders retain their previous behavior unless explicitly enabled; historical
completed orders are never converted by the default migration. Migrate and configure
the company's clearing account before creating new production orders.

## Setup and operation

1. Configure **Continuous Cost Redistribution Account** in Manufacturing Settings.
   Use a non-stock ledger account in the Work Order company's currency. Workshop
   labor uses the existing Operation Cost Account.
2. Continuous Production Costing enables automatically on new Work Orders for
   the configured company. Enable it explicitly for an existing open order. Required materials
   remain the editable Work Order materials; the original BOM is not reloaded.
3. Receive actual material transfers, including Additional Material transfers.
   Draft Pick Lists and draft transfers never contribute quantity or cost.
4. Finish any positive quantity up to the outstanding output quantity. The first
   receipt consumes available actual transfer balances automatically. Subsequent
   receipts can finish without a new transfer. No manual per-product consumption
   entry is required. As agreed, transfers not returned are treated as consumed.
5. After each submitted receipt, a background settlement redistributes the actual
   material value and approved Timesheet costs over all submitted output receipts.
   Other approved additional costs and mold cost are preserved and redistributed.
6. Production remains In Process for partial quantities and Completed when all
   units are received. Cost status is a separate field.
7. If no active Timesheets exist, the final receipt settles material costs without
   requiring a Timesheet action. If Timesheets exist, **Finish Timesheet** appears
   after production completes. Submit or delete all drafts before using it.
8. A late first Timesheet can be added after material-only completion. It makes
   Finish Timesheet available. Once Timesheet costing is closed, managers must use
   **Reopen Timesheet Costing** before changing hours. This preserves output status.

## Cost and accounting

The pool uses actual Stock Ledger transfer values into/out of the order's WIP
warehouse, approved mold issues, submitted Timesheet costing amounts (not billing
amounts), actual scrap recovery, and other existing additional costs. This mode
does not also charge Job Cards. Unsupplied required materials add no cost.

Each batch receives its output-quantity share of each cost component. Signed
additional-cost differences move material value between receipts; materials are
not consumed twice. Cumulative decimal allocation preserves the rounded pool
total. Stock rows below the configured stock-quantity precision are not generated.
Historical sub-precision residual values remain visible through the settlement's
redistribution difference rather than generating an invalid zero-quantity row.

Every settlement stores before/after costs and references native **Repost Item
Valuation** requests. Receipt quantities, source links, and posting dates are
preserved. Native reposting rebuilds stock and accounting ledgers and propagates
valuation changes to later deliveries. Closure waits for successful reposting and
checks receipt ledger values and balanced GL entries. Closed accounting periods,
stock freezes, and native batch/serial recreation restrictions are respected;
an unsupported repost fails explicitly rather than marking costs closed.

Settlement reposts are dispatched immediately on the application-compatible worker
instead of waiting for ERPNext's hourly repost scheduler. The dispatcher shares
the native scheduler's site-scoped RQ job identity to avoid overlapping runners
and respects the configured reposting time window. A periodic status check also
recovers pending dispatches. The planned finished-item material basis is set by
the service and anchored with ERPNext's rate flag so native outgoing-material
recalculations cannot overwrite the pooled receipt cost. This requires no manual
rate entry by the user. The UI polls status without repeatedly reloading the form
and displays a single costing notice.

Stock/Timesheet changes pause while a settlement runs. A failed settlement exposes
**Retry Cost Settlement**. Receipts already posted are not duplicated on retry.
Cancellation of settled stock movements requires a separate controlled reversal;
this change deliberately blocks cancellation rather than leaving its settlement
audit and downstream values inconsistent.

## Validation

Pure allocation tests:

```sh
python -m unittest c4factory.c4_manufacturing.test_continuous_cost_plan -v
```

The Frappe acceptance tests in `c4factory.api.test_continuous_costing` are guarded
to run only on the explicitly authorized staging site and require the supplied
Work Order to start with 14 of 23 units produced. They roll back test transactions.
They cover the reported rounding incident, repeated batches with no new material,
additional materials outside original requirements, late workshop hours, no
Timesheets, final closure, and unchanged historical material quantities.

## Shared Bench staging

Production and staging currently share a Bench. Do not copy this checkout over
the shared application merely to test it. `deployment/continuous_test_runtime.py`
provides a staging-only WSGI entrypoint, a private worker queue, and a cost-status
sync process. It loads isolated hooks without writing them into shared Redis.
Its HTTP entrypoint rejects every hostname except the authorized staging site.
Only that site's upstream should point to the isolated runtime. Standard ERPNext
reposting must run with a compatible application version and an active worker.
