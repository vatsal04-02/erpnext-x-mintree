# Milestone A: production reconciliation, yield and batch genealogy

Phase 3 (manufacturing) of the implementation directive. Desk only; no React. No ERPNext core
change (`CORE_MODIFICATION_LOG.md` in the fork stays empty for this milestone).

Why this was first: a gap check of the baseline code found nothing for batch genealogy, yield or
production reconciliation (`grep` for genealogy/yield/reconcil in `factory_ops` returned only
unrelated hits), while two-stage production, QC guards and wastage already existed and pass the
203-test baseline (`BASELINE_V16.md`).

## What was added

| Item | Type | Where |
|---|---|---|
| Batch genealogy (back: what went into a batch; forward: where it went) | Python module + whitelisted API `factory_ops.production.genealogy.trace` | `factory_ops/production/genealogy.py` |
| **Batch Genealogy** report | Script Report on `Batch` | `production/report/batch_genealogy/` |
| Production reconciliation and yield per Work Order | Python module + API `factory_ops.production.reconciliation.reconcile` | `factory_ops/production/reconciliation.py` |
| **Production Reconciliation** report (summary, or per component with "Show components", or only problems) | Script Report on `Work Order` | `production/report/production_reconciliation/` |
| `Stock Entry.fo_work_order` (Link to Work Order) | Custom Field, via `setup/custom_fields.py` (re-applied on migrate) | wastage write-offs only |
| `Factory Ops Settings.min_yield_percent` (Percent, blank = no check) | Field on existing single DocType | optional; **no value is assumed** |
| Validation `validate_wastage_work_order` | `doc_events` on Stock Entry (validate, before_submit) | `production/controls.py`, `hooks.py` |
| Desk actions: Batch > View > "Batch Genealogy"; Work Order > View > "Production Reconciliation" | `doctype_js` | `public/js/batch.js`, `public/js/work_order.js` |
| `report` right on Batch for Stock User and Manufacturing User | Custom DocPerm rows via `QC_DOCPERMS` | `setup/install.py` |

No new table: both features read ERPNext's own records, so they cannot drift from the stock ledger.
Nothing is written by them.

## Definitions (deterministic; also in the module docstrings)

Per Work Order: `processed_qty = produced_qty + process_loss_qty`;
`completion % = processed / planned`; `yield % = produced / processed`.
Per component: `standard = required x processed / planned`;
`variance = true consumption - standard`; `balance = transferred - stored consumed_qty - wastage
written off from the order's WIP warehouse`.
`Reconciliation`: **Open** until the order is Completed/Closed; then **Reconciled** when every
balance is zero, otherwise **Unreconciled**. `below_min_yield` only when a minimum is configured.

Genealogy: lineage edges are batch-creating events only (receipt, Manufacture, Repack); transfers,
QC release and transfer-to-WIP keep the batch identity and are not edges. A Manufacture/Repack
entry links all consumed batches to all output batches (exact for one finished item per entry,
which is how two-stage Work Orders run). Cancelled documents are excluded.

## Permissions (enforced in Python)

- Genealogy and reconciliation APIs and reports require read on the Batch / Work Order
  (`frappe.has_permission(..., throw=True)`), and Frappe additionally requires `report` on the
  reference DocType. Tested with a user who has no roles.
- Documents the caller cannot read are shown as `(restricted)` with their party hidden, not dropped.
- Roles on Batch Genealogy: Manufacturing User, Stock User, QC Officer, QA Manager, Management
  Viewer. On Production Reconciliation: Manufacturing User, Stock User, Management Viewer. A test
  asserts every listed role really holds `report` on the reference DocType.

## Upstream behaviour discovered (found by failing tests, then confirmed in source)

1. **ERPNext stores `Work Order Item.consumed_qty` as consumption plus returned quantity**
   (`erpnext/manufacturing/doctype/work_order/work_order.py`, `update_consumed_qty_for_required_items`:
   `consumed_qty = consumed_by_row + returned_qty`). The report therefore shows true consumption as
   `consumed_qty - returned_qty`. A first version that trusted the field showed 30 kg consumed
   after a 1.5 kg return.
2. **Wastage from WIP breaks ERPNext's automatic return entry.** `make_stock_return_entry` proposes
   `transferred - consumed` and does not know about a write-off, so it would return more than WIP
   holds and ERPNext refuses with a negative-batch-stock error. The operator must lower the return
   quantity to what is actually left. The reconciliation shows that leftover as the WIP balance.
3. **Standard reports are re-synced on migrate only when the JSON `modified` stamp is newer than
   the database copy.** Edit a report's JSON and bump `modified` to a time that is not in the past
   (never a future time: a future initial stamp silently blocked later updates here).
4. **Frappe needs the `report` right on a report's reference DocType**, not just a role on the
   report. Stock User and Manufacturing User had `read` but not `report` on Batch.

## Tests

`factory_ops/tests/test_genealogy_reconciliation.py`, 11 tests on the real two-stage flow
(released RM/PM -> BULK with 2 % process loss -> bulk QC release -> FG short-filled -> close ->
return), using the Stock Entry documents' own bundles as an independent oracle for lineage:
back and forward traces; cancelled manufacture leaves no lineage; masking of unreadable documents;
backend permission refusal; yield, variance and WIP balance (Unreconciled -> Reconciled after
return); wastage from stores vs from WIP; minimum-yield flag only when configured; report modes;
wastage-link validation; Desk buttons delivered; every report role can run its report.

Results: see "Results" in `IMPLEMENTATION_STATUS.md`.

## Known limitations

- By-products, scrap items and operation/labour time are not reconciled (ERPNext's Job Card and
  Process Loss reports cover those).
- Items that are not batch-tracked leave no lineage.
- Wastage links to a Work Order only when the operator sets `Production Work Order`; unlinked
  wastage is not attributed to any order.
- Skip-transfer orders show no WIP balance (nothing passes through WIP).
- The Desk buttons are verified as delivered with the doctype (server-side); they were not clicked
  in a browser in this milestone.
- Manufacturing Manager has no `read` on Batch in the current permission matrix (observed on the
  test site), so it cannot open Batch Genealogy.

## Decisions needed from Mintree

- Minimum acceptable yield per product or product family (the setting is blank until supplied).
- Whether Manufacturing Manager (and other managers) should read Batch and run these reports.
- Whether every wastage entry must be linked to a Work Order.
