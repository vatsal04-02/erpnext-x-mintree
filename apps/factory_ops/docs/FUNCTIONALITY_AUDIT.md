# Functionality audit: factory_ops and standard ERPNext v16.53

Status 2026-11-05. Evidence levels:

- **Tested**: covered by a `factory_ops` test module in the 203/203 baseline (`BASELINE_V16.md`)
  plus the 11 Milestone A tests. "Tested" means those tests pass on SAMPLE/DEMO data, not that
  Mintree's real process has been validated.
- **Standard**: the ERPNext doctype/report/setting exists in the pinned v16.53 tree (verified by
  looking for it); not exercised by this project.
- **Per earlier docs**: described in the earlier project's documents; not re-verified here.

## 1. Already working (Tested)

| Area | What | Test module(s) |
|---|---|---|
| QC quarantine and release | QC Hold / Rejected warehouses; QC Release (release, partial, conditional with approved deviation, reject); guards on every stock route; segregation of duties; retest; QC Deviation | test_qc_release, test_qc_retest, test_guards, test_lifecycle_routes, test_spike_qi_lifecycle |
| Purchase | Approval limits with no self-approval; GRN with supplier batch, dates, COA; batch created on receipt | test_purchase |
| Batch eligibility | One shelf-life/expiry eligibility rule shared by reservation, pick, dispatch | test_batch_eligibility |
| Production | Two-stage Work Orders (bulk then fill-and-pack), configurable warehouses, process loss, wastage with reasons, batch label | test_production |
| Sales to cash | Channels, credit limits, available-to-sell, FEFO pick and reservation, dispatch details, invoices from deliveries, payments | test_sales, test_acceptance_e2e |
| Master data | All-or-nothing import with templates, opening stock with batches | test_master_data |
| Desk workspaces | 7 role workspaces, Management Viewer | test_workspaces, test_factory_os_api |
| R&D | Ingredient categories, formulation versions with approval, trials, stability, BOM link | test_research |
| Maintenance | Equipment on ERPNext Asset, schedules, breakdowns, downtime, spares | test_maintenance |
| Marketplace / marketing (backend only) | Channel states derived on the server, never "Connected" by hand; no live connection | test_marketplace, test_marketing |
| Genealogy, yield, reconciliation | Milestone A | test_genealogy_reconciliation |

## 2. Reuse ERPNext unmodified (Standard)

Material Request (purchase requisition), Request for Quotation, Supplier Quotation and the
Supplier Quotation Comparison report, Purchase Order/Receipt/Invoice, Item Reorder, Stock Entry,
Stock Reconciliation, Stock Reservation Entry (Stock Settings: `enable_stock_reservation`), FEFO via
Stock Settings `pick_serial_and_batch_based_on = Expiry`, Pick List, Delivery Note, Sales Invoice,
Payment Entry, Quality Inspection and Templates, Non Conformance, Quality Action, Quality Goal,
BOM and BOM Creator, Production Plan, Master Production Schedule, Sales Forecast, Job Card,
Workstation, Plant Floor, Downtime Entry, Asset Maintenance/Repair, and the reports Work Order
Summary, Process Loss Report, Production Planning Report, Downtime Analysis, Quality Inspection
Summary, Stock Ageing, Batch-Wise Balance History, Purchase Analytics, Accounts Receivable.
The v16 banking app handles bank reconciliation. Do not rebuild any of these.

## 3. Needs configuration with real Mintree data (not built, not invented)

Legal entity, chart of accounts, GSTIN and state; UOMs and item groups; real warehouses (the SAMPLE
tree exists); QC specifications per RM/PM/bulk/FG (Quality Inspection Templates); approval limits;
reorder levels and lead times; wastage reasons; minimum remaining shelf life for dispatch; QC SLA
hours; minimum yield (setting added in Milestone A, blank). Verify that FEFO is actually set to
`Expiry` on the real site (not checked here). **India Compliance is not installed**; GST invoices,
e-invoice and e-way bill need it plus a verified GSTIN and an accountant's configuration, so they
are not claimed.

## 4. Needs a custom DocType, field or workflow (candidates, none started)

| Candidate | Reuse first | What is missing |
|---|---|---|
| CAPA | Quality Action / Quality Goal | Link from QC Deviation / Non Conformance to a Quality Action, effectiveness check |
| Supplier approval and quality history | Supplier, Quality Inspection | Approved-supplier flag per item, rejection rate by supplier report |
| Line clearance, electronic batch record | Job Card | Checklist gating job start; batch record steps (large) |
| Returns and credit notes for dispatch | Delivery Note / Sales Invoice return | Guard so returned stock re-enters via QC, not usable stock; tests |
| Calibration and utility logs | Asset Maintenance | Calibration due tracking |
| Complaint handling | Issue | Batch-linked complaint classification and quality escalation |

## 5. Needs an ERPNext core modification

**None identified.** Every gap above can be met with configuration, hooks, custom fields or
`factory_ops` DocTypes. `CORE_MODIFICATION_LOG.md` in the fork remains empty. Any future entry
must record the Mintree requirement, original behaviour, why `factory_ops` cannot do it, a
regression test and the upstream-merge impact.

## 6. Upstream behaviours that constrain design (verified in source)

1. `Work Order Item.consumed_qty` includes returned quantity.
2. ERPNext's automatic return entry ignores wastage written off from WIP.
3. Standard reports re-sync on migrate only when JSON `modified` is newer than the database copy.
4. A report needs the `report` right on its reference DocType, not just a role on the report.
5. A workspace only shows links; it never grants access.
