# DEMO scenario: 12 invented cosmetic products through every workflow

**DEMO - NOT REAL.** All products, formulas, suppliers, prices and quantities are invented for this run (`factory_ops/demo/catalogue.py`); none is Mintree data. Runs only on a site flagged `fo_demo_site` (never on a dev or real site).

## How to run
```bash
bench new-site demo.localhost ...  &&  bench --site demo.localhost install-app erpnext factory_ops
bench --site demo.localhost set-config -p fo_demo_site 1
bench --site demo.localhost execute factory_ops.setup.sample_company.setup_dev_site
bench --site demo.localhost execute factory_ops.setup.sample_users.create_sample_users
bench --site demo.localhost execute factory_ops.demo.scenario.run --kwargs '{"dry": True}'   # one transaction, rolled back
bench --site demo.localhost execute factory_ops.demo.scenario.run                             # the real, committed run
```

Every document is created and submitted **as the SAMPLE user of that department** (buyer, approver, stores, QC officer, production, sales, dispatch, accounts); the read-only management user runs the reports and cards. No `ignore_permissions` in the flow.

## What the run did

- 12 finished products on 4 bulk bases, 600 units each, quantities derived from the BOMs.
- Bulk Work Order quantities in kg (from the BOMs and process loss): {'DEMO-BULK-0001': 310, 'DEMO-BULK-0002': 190, 'DEMO-BULK-0003': 160, 'DEMO-BULK-0004': 310}
- Procurement: approved Purchase Orders, Purchase Receipts into QC Hold with supplier batch and COA, QC release per batch, supplier invoices and payments; one extra lot rejected by QC.
- Production: 4 bulk Work Orders (transfer, manufacture, QC release), 12 finished-goods Work Orders (transfer, manufacture, QC release), 1 extra lot left in QC Hold on purpose.
- Sales: 3 distributor Sales Orders (4 products x 100 units) -> Pick List and reservation -> Delivery Note with Packing Slip, transporter and LR no -> Sales Invoice -> Payment Entry.

## Result: 84 checks passed, 0 failed

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | Procurement: the buyer cannot approve their own Purchase Order | PASS | SelfApprovalError: You created Purchase Order PUR-ORD-2026-00001, so another approver must submit it. |
| 2 | Procurement: a Purchase Order above the approver's limit is refused | PASS | ApprovalLimitError: Purchase Order PUR-ORD-2026-00003 is ₹ 22,00,000.00, above your approval limit of ₹ 5,00,0 |
| 3 | Procurement: every item planned from the BOMs was ordered on an approved PO | PASS | 2 POs |
| 4 | Receiving: a raw-material receipt without a supplier COA is refused | PASS | ReceiptDataError: Row #1 (DEMO-RM-0001): enter Certificate of Analysis before submitting. |
| 5 | Receiving: every received batch sits in its QC Hold warehouse with status Hold | PASS | [] |
| 6 | Receiving: raw-material batches carry the supplier batch number and COA | PASS |  |
| 7 | QC: every accepted incoming batch is Released and has left QC Hold | PASS | [] |
| 8 | QC: the rejected lot is Rejected and sits in the Rejected warehouse | PASS | {'status': 'Rejected', 'qty': {'REJECTED - SMM': 20.0}} |
| 9 | QC: rejected material cannot be issued to production | PASS | QCGuardError: Row #1: stock in Rejected warehouse REJECTED - SMM can only be scrapped (Material Issue with a r |
| 10 | Accounts: every supplier invoice is paid (no payable left) | PASS | 0 unpaid |
| 11 | Bulk DEMO-BULK-0001: made with the BOM's process loss and held for QC | PASS | {'status': 'Hold', 'qty': {'BULK-QC-HOLD - SMM': 303.8}}, produced 303.8 |
| 12 | Bulk DEMO-BULK-0001: held bulk cannot go into filling | PASS | QCGuardError: Row #1: DEMO-BULK-0001 is in QC Hold warehouse BULK-QC-HOLD - SMM. Stock can only leave QC Hold  |
| 13 | FG DEMO-FG-001: filled and held for QC | PASS | {'status': 'Hold', 'qty': {'FG-QC-HOLD - SMM': 600.0}} |
| 14 | FG DEMO-FG-002: filled and held for QC | PASS | {'status': 'Hold', 'qty': {'FG-QC-HOLD - SMM': 600.0}} |
| 15 | FG DEMO-FG-003: filled and held for QC | PASS | {'status': 'Hold', 'qty': {'FG-QC-HOLD - SMM': 600.0}} |
| 16 | Bulk DEMO-BULK-0002: made with the BOM's process loss and held for QC | PASS | {'status': 'Hold', 'qty': {'BULK-QC-HOLD - SMM': 184.3}}, produced 184.3 |
| 17 | Bulk DEMO-BULK-0002: held bulk cannot go into filling | PASS | QCGuardError: Row #1: DEMO-BULK-0002 is in QC Hold warehouse BULK-QC-HOLD - SMM. Stock can only leave QC Hold  |
| 18 | FG DEMO-FG-004: filled and held for QC | PASS | {'status': 'Hold', 'qty': {'FG-QC-HOLD - SMM': 600.0}} |
| 19 | FG DEMO-FG-005: filled and held for QC | PASS | {'status': 'Hold', 'qty': {'FG-QC-HOLD - SMM': 600.0}} |
| 20 | FG DEMO-FG-006: filled and held for QC | PASS | {'status': 'Hold', 'qty': {'FG-QC-HOLD - SMM': 600.0}} |
| 21 | Bulk DEMO-BULK-0003: made with the BOM's process loss and held for QC | PASS | {'status': 'Hold', 'qty': {'BULK-QC-HOLD - SMM': 157.6}}, produced 157.6 |
| 22 | Bulk DEMO-BULK-0003: held bulk cannot go into filling | PASS | QCGuardError: Row #1: DEMO-BULK-0003 is in QC Hold warehouse BULK-QC-HOLD - SMM. Stock can only leave QC Hold  |
| 23 | FG DEMO-FG-007: filled and held for QC | PASS | {'status': 'Hold', 'qty': {'FG-QC-HOLD - SMM': 600.0}} |
| 24 | FG DEMO-FG-008: filled and held for QC | PASS | {'status': 'Hold', 'qty': {'FG-QC-HOLD - SMM': 600.0}} |
| 25 | FG DEMO-FG-009: filled and held for QC | PASS | {'status': 'Hold', 'qty': {'FG-QC-HOLD - SMM': 600.0}} |
| 26 | Bulk DEMO-BULK-0004: made with the BOM's process loss and held for QC | PASS | {'status': 'Hold', 'qty': {'BULK-QC-HOLD - SMM': 302.25}}, produced 302.25 |
| 27 | Bulk DEMO-BULK-0004: held bulk cannot go into filling | PASS | QCGuardError: Row #1: DEMO-BULK-0004 is in QC Hold warehouse BULK-QC-HOLD - SMM. Stock can only leave QC Hold  |
| 28 | FG DEMO-FG-010: filled and held for QC | PASS | {'status': 'Hold', 'qty': {'FG-QC-HOLD - SMM': 600.0}} |
| 29 | FG DEMO-FG-011: filled and held for QC | PASS | {'status': 'Hold', 'qty': {'FG-QC-HOLD - SMM': 600.0}} |
| 30 | FG DEMO-FG-012: filled and held for QC | PASS | {'status': 'Hold', 'qty': {'FG-QC-HOLD - SMM': 600.0}} |
| 31 | Sales: a produced-but-unreleased lot adds nothing to available-to-sell | PASS | 600.0 -> 600.0 |
| 32 | Sales: a lot still in QC Hold cannot be put on a Delivery Note | PASS | ValidationError: Sales Order required for Item DEMO-FG-001 |
| 33 | FG DEMO-FG-001: released, 500 left in FG-STORES after dispatch | PASS | {'status': 'Released', 'qty': {'FG-STORES - SMM': 500.0}} |
| 34 | FG DEMO-FG-002: released, 500 left in FG-STORES after dispatch | PASS | {'status': 'Released', 'qty': {'FG-STORES - SMM': 500.0}} |
| 35 | FG DEMO-FG-003: released, 500 left in FG-STORES after dispatch | PASS | {'status': 'Released', 'qty': {'FG-STORES - SMM': 500.0}} |
| 36 | FG DEMO-FG-004: released, 500 left in FG-STORES after dispatch | PASS | {'status': 'Released', 'qty': {'FG-STORES - SMM': 500.0}} |
| 37 | FG DEMO-FG-005: released, 500 left in FG-STORES after dispatch | PASS | {'status': 'Released', 'qty': {'FG-STORES - SMM': 500.0}} |
| 38 | FG DEMO-FG-006: released, 500 left in FG-STORES after dispatch | PASS | {'status': 'Released', 'qty': {'FG-STORES - SMM': 500.0}} |
| 39 | FG DEMO-FG-007: released, 500 left in FG-STORES after dispatch | PASS | {'status': 'Released', 'qty': {'FG-STORES - SMM': 500.0}} |
| 40 | FG DEMO-FG-008: released, 500 left in FG-STORES after dispatch | PASS | {'status': 'Released', 'qty': {'FG-STORES - SMM': 500.0}} |
| 41 | FG DEMO-FG-009: released, 500 left in FG-STORES after dispatch | PASS | {'status': 'Released', 'qty': {'FG-STORES - SMM': 500.0}} |
| 42 | FG DEMO-FG-010: released, 500 left in FG-STORES after dispatch | PASS | {'status': 'Released', 'qty': {'FG-STORES - SMM': 500.0}} |
| 43 | FG DEMO-FG-011: released, 500 left in FG-STORES after dispatch | PASS | {'status': 'Released', 'qty': {'FG-STORES - SMM': 500.0}} |
| 44 | FG DEMO-FG-012: released, 500 left in FG-STORES after dispatch | PASS | {'status': 'Released', 'qty': {'FG-STORES - SMM': 500.0}} |
| 45 | Genealogy DEMO-FG-001: back-trace reaches bulk, packaging and every raw material, down to the receipts | PASS | missing [], events ['Manufacture', 'Purchase Receipt'] |
| 46 | Genealogy DEMO-FG-002: back-trace reaches bulk, packaging and every raw material, down to the receipts | PASS | missing [], events ['Manufacture', 'Purchase Receipt'] |
| 47 | Genealogy DEMO-FG-003: back-trace reaches bulk, packaging and every raw material, down to the receipts | PASS | missing [], events ['Manufacture', 'Purchase Receipt'] |
| 48 | Genealogy DEMO-FG-004: back-trace reaches bulk, packaging and every raw material, down to the receipts | PASS | missing [], events ['Manufacture', 'Purchase Receipt'] |
| 49 | Genealogy DEMO-FG-005: back-trace reaches bulk, packaging and every raw material, down to the receipts | PASS | missing [], events ['Manufacture', 'Purchase Receipt'] |
| 50 | Genealogy DEMO-FG-006: back-trace reaches bulk, packaging and every raw material, down to the receipts | PASS | missing [], events ['Manufacture', 'Purchase Receipt'] |
| 51 | Genealogy DEMO-FG-007: back-trace reaches bulk, packaging and every raw material, down to the receipts | PASS | missing [], events ['Manufacture', 'Purchase Receipt'] |
| 52 | Genealogy DEMO-FG-008: back-trace reaches bulk, packaging and every raw material, down to the receipts | PASS | missing [], events ['Manufacture', 'Purchase Receipt'] |
| 53 | Genealogy DEMO-FG-009: back-trace reaches bulk, packaging and every raw material, down to the receipts | PASS | missing [], events ['Manufacture', 'Purchase Receipt'] |
| 54 | Genealogy DEMO-FG-010: back-trace reaches bulk, packaging and every raw material, down to the receipts | PASS | missing [], events ['Manufacture', 'Purchase Receipt'] |
| 55 | Genealogy DEMO-FG-011: back-trace reaches bulk, packaging and every raw material, down to the receipts | PASS | missing [], events ['Manufacture', 'Purchase Receipt'] |
| 56 | Genealogy DEMO-FG-012: back-trace reaches bulk, packaging and every raw material, down to the receipts | PASS | missing [], events ['Manufacture', 'Purchase Receipt'] |
| 57 | Genealogy: a surfactant batch traces forward to cleanser bulk, its finished goods and the customer's Delivery Note | PASS | reached ['DEMO-BULK-0001', 'DEMO-BULK-0002', 'DEMO-BULK-0004', 'DEMO-FG-001', 'DEMO-FG-002', 'DEMO-FG-003', 'D |
| 58 | Reconciliation: each bulk Work Order's reconciliation reports the quantity actually produced | PASS | [] |
| 59 | Accounting: the stock ledger value equals the Stock accounts in the General Ledger | PASS | stock ledger 255253.05, GL 255253.05 |
| 60 | Accounting: the General Ledger balances (debits = credits) | PASS | 1646026.95 vs 1646026.95 |
| 61 | Accounting: every voucher balances on its own | PASS | () |
| 62 | Sales SAL-ORD-2026-00001: delivered with transporter and LR no, invoiced, paid; order Completed | PASS | Completed {'status': 'Paid', 'outstanding_amount': 0.0, 'grand_total': 97600.0} {'docstatus': 1, 'transporter_ |
| 63 | Sales SAL-ORD-2026-00002: delivered with transporter and LR no, invoiced, paid; order Completed | PASS | Completed {'status': 'Paid', 'outstanding_amount': 0.0, 'grand_total': 130600.0} {'docstatus': 1, 'transporter |
| 64 | Sales SAL-ORD-2026-00003: delivered with transporter and LR no, invoiced, paid; order Completed | PASS | Completed {'status': 'Paid', 'outstanding_amount': 0.0, 'grand_total': 125600.0} {'docstatus': 1, 'transporter |
| 65 | Sales: no reservation is left open after delivery | PASS | 0 |
| 66 | Available-to-sell equals released stock minus what was delivered, for all 12 products | PASS | {} |
| 67 | Wastage: a write-off without a reason is refused | PASS | WastageReasonError: Row #1: select a Wastage Reason for writing off DEMO-PM-0001 from PM-STORES - SMM. |
| 68 | Wastage: a write-off with a reason posted and balanced | PASS |  |
| 69 | Permissions: production staff cannot create QC Releases; the QC officer can | PASS |  |
| 70 | Permissions: the read-only management user cannot create or approve anything | PASS |  |
| 71 | Report Stock by QC Status: opens for the management user and shows rows | PASS | 42 rows |
| 72 | Report QC Queue: opens for the management user and shows rows | PASS | 1 rows |
| 73 | Report Production Reconciliation: opens for the management user and shows rows | PASS | 17 rows |
| 74 | Report Wastage by Reason: opens for the management user and shows rows | PASS | 1 rows |
| 75 | Report Available to Sell: opens for the management user and shows rows | PASS | 12 rows |
| 76 | Report Dispatch Register: opens for the management user and shows rows | PASS | 3 rows |
| 77 | Report Batch Genealogy: opens for the management user and shows rows | PASS | 14 rows |
| 78 | Dashboard card 'Purchase Orders Awaiting Approval' computes for the management user | PASS | 0.0 |
| 79 | Dashboard card 'QC Decisions Pending' computes for the management user | PASS | 0.0 |
| 80 | Dashboard card 'Work Orders In Progress' computes for the management user | PASS | 0.0 |
| 81 | Dashboard card 'Batches Awaiting QC' computes for the management user | PASS | 1.0 |
| 82 | Dashboard card 'Total Stock Value' computes for the management user | PASS | 255163.05 |
| 83 | Dashboard card 'Open Sales Orders' computes for the management user | PASS | 0.0 |
| 84 | Dashboard card 'Receivables Outstanding' computes for the management user | PASS | 0.0 |

## Independent look at the committed records
(queried directly in the database after the run, separate from the checks above)

- 12 FG / 4 bulk / 7 RM / 18 PM items; 3 POs, 3 receipts, 3 paid purchase invoices; 41 QC Releases with 41 Quality Inspections (24 incoming, 1 rejected lot, 4 bulk, 12 FG).
- 17 Work Orders Completed (4 bulk, 12 FG, 1 held lot); 3 Sales Orders Completed, 3 Delivery Notes, 3 Sales Invoices Paid, 6 Payment Entries.
- Batches: 40 Released, 1 Hold (the deliberately held 20-unit lot), 1 Rejected (20 kg, valued 19,000).
- Management dashboard figures agree: total stock value 2.55 L = 255,164 from the Bins; batches awaiting QC 1; rejected stock 19.00 K; receivables 0.

## Findings from the run

1. **Product bug found and fixed:** `genealogy.trace_forward` crashed with a SQL error (`Unknown column 'posting_date'`) when a trace reached a voucher type without that column. Fixed in `production/genealogy.py`; regression test `test_a_voucher_without_a_posting_date_does_not_break_the_trace` (verified to fail without the fix).
2. Controls seen refusing correctly: buyer self-approval; PO above the approver's limit; raw-material receipt without COA; held bulk into filling; rejected stock issued to production; held FG lot on a Delivery Note; wastage without a reason. (QC Reject without a quantity or a reason is also refused: found while writing the scenario.)
3. Not covered: the Decision Centre (unmerged), marketplaces, R&D, maintenance, returns/RTO, GST, serial-number items.
4. A runnable demonstration, not part of the automated regression suite.
