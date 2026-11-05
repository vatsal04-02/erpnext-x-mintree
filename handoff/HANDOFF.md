# Mintree Factory OS: full hand-off for the next session

Written 2026-11-05. Read this first. Everything here is stated as verified, implemented-untested, or not started.

## 1. What this project is

**Mintree Factory OS** is an integrated, AI-assisted manufacturing ERP for Mintree (a cosmetics / skincare
manufacturer: raw materials, packaging, bulk, finished goods, QC, batches, expiry, distributors, marketplaces).

Decisions already made by the owner (do not re-litigate):
- Base: a **Mintree-owned fork of ERPNext on v16** (`vatsal04-02/erpnext-x-mintree`, branch `mintree/v16`, cut from `version-16`; v17/develop was rejected).
- Product code lives in a **separately installable Frappe app `factory_ops`**, not in ERPNext core.
- **ERPNext Desk is the primary UI.** No separate React main UI.
- ERPNext core is modified only when genuinely necessary and every change is logged in `docs/mintree/CORE_MODIFICATION_LOG.md`. **No core changes have been made.**
- No fake data. Synthetic data is labelled `SAMPLE` / `DEMO - NOT REAL`. The 227 real catalogue records and their validation file were kept.
- Never bypass stock, QC or accounting validations. No direct SQL writes for business transactions. Permissions enforced in the backend, not only the UI.
- Tests for every milestone. Report **tested** separately from **implemented but untested**. Commit in logical units. Document everything. Do not merge untested changes into the main branch.

## 2. Where everything is

GitHub access in the first session covered only `vatsal04-02/erpnext-x-mintree`, and `factory_ops` had no remote.
So `factory_ops` was pushed into this fork's branch **`claude/upbeat-euler-qcwuh1`**:

| Path on this branch | What it is |
|---|---|
| `apps/factory_ops/` | Working tree of `factory_ops` branch `feature/decision-centre` (= `ui/roles` + 2 WIP commits). Full app, with its own `docs/`. |
| `handoff/factory_ops.git.bundle` | Full git history: `main` (663cc21), `ui/roles` (b863d9d), `feature/decision-centre` (336c5ec). Restore with `git clone handoff/factory_ops.git.bundle factory_ops`. |
| `handoff/run_modules.sh` | Per-module regression runner. |
| `handoff/regression_ui_roles_summary.tsv`, `regression_ui_roles_counts.txt` | Final regression record (section 5). |
| `handoff/HANDOFF.md` | This file. |
| `apps/factory_ops/docs/` | `BASELINE_V16.md`, `DEV_ENVIRONMENT.md`, `FUNCTIONALITY_AUDIT.md`, `MILESTONE_A_RECONCILIATION_AND_GENEALOGY.md`, `UI_ARCHITECTURE.md` (sections 1 to 10), `HANDOFF_DECISION_CENTRE.md`. |

Other fork branches: `mintree/v16` (the v16 base; only `docs/mintree/` differs from upstream v16.53.0), `develop`.
`docs/mintree/` on `mintree/v16` holds `CORE_MODIFICATION_LOG.md`, `MILESTONE_0_REPORT.md`, `UPSTREAM_AND_BRANCHING_STRATEGY.md`, `catalogue/`.
**Recommendation:** give `factory_ops` its own GitHub repo and push the three branches from the bundle.

`factory_ops` local branches: `main` = 663cc21; `ui/roles` = b863d9d (main + roles/UI work, **fully regression-tested**); `feature/decision-centre` = 336c5ec (ui/roles + untested Decision Centre).

## 3. Environment (the old container is gone; rebuild it)

Frappe v16.55.2, ERPNext v16.53.0 (the fork), Python 3.14.10, Node 24.22.2, MariaDB 11.8.9, Valkey 8.1.11
(cache 13100, queue 11100). Site `test.localhost` (apps erpnext + factory_ops, `allow_tests`).
Bench runs as user `frappe` (bench refuses root); `apps/erpnext` is a symlink to the fork working tree on branch `mintree/v16`,
`apps/factory_ops` a symlink to the factory_ops repo. Full build and gotchas: `apps/factory_ops/docs/DEV_ENVIRONMENT.md` and `BASELINE_V16.md`.

Rules learned the hard way:
- **Never `git checkout` another branch in the fork working tree while the bench or a test run uses it.** The bench imports ERPNext from that tree. During this session a checkout of the session branch (develop-based) to push the hand-off broke the last two modules of a running regression (`BOM has no attribute set_qty_based_on_percentage`, `Account Type ... Stock Delivered But Not Billed`). Use `git worktree add` for any other branch. Both modules passed once the fork was back on `mintree/v16`.
- Run a worker (`bench worker --queue short,default,long`) and keep the scheduler on: saving a Role Profile locks the document and enqueues a job; without a worker the next save fails with "document is locked".
- Restart the dev server with `pkill -f "bench_helper frappe --site test.localhost serve"`; hooks are read at process start.
- Standard report JSON re-syncs only if `modified` is newer; never stamp a future date.
- Workspace names must slug cleanly and must not collide with a DocType, Page or another Workspace or an ERPNext Module Def (hence "Plant Maintenance", "Research and Development").
- Test mode skips Version history and one workspace guard: use `production_mode()` / `production_auditing()` from `tests/utils.py`, narrowly.
- `bench execute` args are Python-evaluated. Importing a test class into another test module re-collects its tests: import the module instead.

## 4. What is done (all committed in `factory_ops`)

Baseline (before any change on the fork): 203 tests, 19 modules, all passing (`BASELINE_V16.md`).

History on `main` (oldest to newest, summarised): QC quarantine and release engine; QC Retest; purchase and inventory with approval limits and supplier batches;
production controls (two-stage, wastage reasons, SAMPLE BOMs); sales, dispatch and accounts on standard ERPNext; shelf-life eligibility (one rule for available-to-sell, reservation, Pick List, DN);
master-data import; role workspaces and dashboards; milestone 6; local demo; Factory OS front-end (earlier React work, not the primary UI now); R&D; maintenance on ERPNext assets; marketplaces; brand and marketing; Integration Centre.
Then in this effort:
- **Milestone A** (63ae9e2): batch genealogy (backward/forward trace + report), Work Order reconciliation and yield engine. Doc: `MILESTONE_A_...md`.
- **B1** (2206c15): workspace layout rows, no duplicate entry points, Research and Development workspace, Milestone A reports on workspaces.
- **B2** (ce4b271): role landing through `User.default_workspace` (fills only an empty value). Order: Management, Production Floor, Quality Control, Research and Development, Sales and Dispatch, Factory Accounts, Stores and Warehouse, Procurement, Plant Maintenance.
- **B3** (7506e5c, 97bd97f): Mintree branding: wordmark on login, square mark, product name, accent #01766B for buttons (white text reaches WCAG AA; raw brand teal #01877A does not). Customisations made in Desk are never overwritten.
- **B4** (9ac4901, 663cc21): Plant Maintenance workspace, count cards, global read-only visibility for System Manager.
- **Roles** (`ui/roles`: 54dc42b, f85aec9, b863d9d): two administrator roles kept apart. **System Administrator** = `System Manager` + `Workspace Manager` (technical; Role Profile "System Administrator"). **Business Administrator** (owner; Role Profile of the same name): read/report on all operational DocTypes, all standard reports, Version (audit trail), and the single business action "approve Purchase Order" (submit) which still requires an enabled Purchase Approval Limit, no self-approval, and `total > limit` refused. No technical settings and **no delete anywhere**. Visibility is never permission: neither role gets write/create/submit/cancel/delete beyond upstream. Audit trails (`track_changes`) enabled for sensitive DocTypes and checked against real Version rows (Quality Inspection, Material Request, Stock Reconciliation, Purchase Order). `approve_purchase_order(name)` is a whitelisted POST. Operational cards: real-record figures for Procurement, Stores, Sales, Management, Plant Maintenance (count cards return a bare int so they are not shown as money). See `UI_ARCHITECTURE.md` sections 8 to 10.

Factory controls that exist and must be preserved: QC Hold and Rejected warehouses, QC Release / Deviation / Retest, guards on restricted warehouses, Purchase Approval Limit, available-to-sell, batch eligibility, two-stage production, reconciliation engine. Defined in `factory_ops/constants.py` and the `inventory`, `quality`, `purchase`, `production`, `sales_ops` packages.

## 5. Test status (exact)

**Full regression of `ui/roles`, one module at a time (`run_modules.sh`): 26 modules, 339 tests, all OK, 0 failures, 0 errors.**
Per-module counts in `handoff/regression_ui_roles_counts.txt`. Earlier reference: 203 tests / 19 modules at the baseline, 284 tests / 24 modules before the roles/cards work.

Honest note on how this number was obtained: in the first pass 24 modules passed and the last two, `test_spike_qi_lifecycle` (8) and `test_workspaces` (15), failed because I switched the fork's working tree to another branch mid-run (section 3). After restoring `mintree/v16` both were re-run on their own and passed. The summary tsv still shows rc 1 for those two; the reruns are `~/baseline/rerun_*.log` in the old container. Nothing else was re-run, so do one clean all-in-one pass before relying on this.

Targeted results on `ui/roles` (all OK): test_business_admin 43, test_operational_cards 12, test_admin_access 26, test_desk_ui 18, test_plant_maintenance 10, test_branding 16, test_workspaces 15.
Browser (Playwright, `deploy/dev/ui_check_desk.js`) screenshots were reviewed by hand for B1 to B3. The HTTP acceptance scripts and React unit tests of the earlier project are not run.
**`ui/roles` has now passed the full regression, so it is eligible to be merged into `factory_ops` `main`. That merge has not been done.**

**Decision Centre: nothing has been run.** The code was only compile-checked (`python -m compileall`).

## 6. In progress: the Management Decision Centre

### The request (summary of the owner's spec)
A functional workspace, not a dashboard: one place where the owner / Business Administrator sees what is pending, late, blocked or needs a decision across Procurement, Inventory & Stores, Production, Quality Control, Dispatch & Sales, Plant Maintenance and Accounts, using real ERPNext records.
- Each entry: issue, department, priority (Critical/High/Medium/Low), date created, pending duration, responsible person or department, status, link to the real document, available authorised action. Filters: department, priority, status, date, responsible. Top cards from real records: total pending, critical, overdue, QC approvals pending, procurement approvals pending, production blockers, dispatch delays.
- Categories asked for: procurement (PO approvals, purchase requests, supplier delays, shortages, POs over limit); inventory (reorder, expiring RM/FG, QC-held stock, discrepancies, critical shortages); production (approvals, overdue, blockers, yield, bulk QC); QC (incoming/bulk/FG, rejected, deviations, CAPA overdue); dispatch (past delivery, awaiting dispatch, blocked, complaints, returns); maintenance (critical breakdowns, approvals, overdue PM, downtime); accounts (outstanding customer, overdue supplier, invoices awaiting approval, reconciliation exceptions, transactions needing review).
- Configurable escalation rules with background jobs and notifications, **no duplicate notifications**.
- Permissions: Business Administrator global plus authorised actions; System Administrator technical; department managers see their queue plus items assigned across departments; employees only what is assigned; **viewing never grants approve/modify**; all actions through Frappe permissions and workflows.
- Technical: inside `factory_ops`, reuse ERPNext DocTypes, no duplicated pending records, a central aggregation layer (no copying), efficient queries and caching, no N+1, audit trail for management actions, resolved items disappear.
- Tests: each category, priority calculation, counts, resolved-disappear, role visibility, action permission, escalation and duplicate prevention, summary accuracy against real documents with controlled fixtures. Build order: (1) workspace + cards, (2) procurement/QC/production queues, (3) inventory/dispatch/maintenance, (4) accounts, (5) priority/filter/escalation, (6) action history/reporting. Document in `UI_ARCHITECTURE.md` and the roadmap.
- Priority follow-up from the owner: a consolidated decision queue (PO approvals, QC approvals, material shortages, overdue production orders, overdue deliveries, breakdowns, below-reorder, expiring batches) each linking to the real document; then real workflows in the order Stores and Inventory, Procurement, Production Floor, Quality Control, Dispatch, Accounts, not decorative dashboards.

### Design that was written (branch `feature/decision-centre`, `apps/factory_ops/factory_ops/decisions/`)
- `model.py`: `Entry` dataclass (kind, category, department, issue, priority, ref_doctype/ref_name, status, state, since, responsible, raised_by, responsible_department, also, amount, overdue, blocker, precedence, action); entries are **derived, never stored**.
- `sources/` (registered with `@source`): procurement (PO approval, over-limit, purchase requests, supplier delay, material shortage via three aggregate queries), quality (QC-held batches by RM/PM/BULK/FG with QC SLA from Factory Ops Settings, rejected, deviations, CAPA), production (WO approval, overdue/late start, blocked by shortage, yield deviation via the Milestone A reconciliation), inventory (below reorder, expiry window, negative stock, draft stock reconciliation), dispatch (orders overdue/due soon/blocked using available-to-sell, complaints, returns), maintenance (breakdowns by severity, closures, overdue PM), accounts (overdue receivables/payables, invoices awaiting approval, financial review, bank reconciliation).
- `queue.py`: `collect()` (permission-aware reads via `frappe.get_list`, de-duplication by department+document with `precedence`, escalation rules applied, scope), `apply_filters`, `summary`, per-user cache (`cache_seconds`, 0 in tests), whitelisted `get_decision_queue`, `get_summary`. `MANAGER_ROLES` see everything they can read; others see only items assigned to them.
- `actions.py`: `run_decision_action(entry_key, action, note, assignee)` actions approve_po (reuses `approve_purchase_order`), submit, acknowledge, note, assign. Re-reads the queue for the viewer, re-checks permission, savepoint + rollback on refusal, every attempt (including refusals) logged in **Management Action**. QC release is never executed from here.
- `escalation.py`: hourly job (`hooks.py` scheduler) reads the whole queue, for each enabled rule creates one Notification Log per recipient and one **Escalation Event** keyed by hash(entry, rule) so it never repeats; events of resolved items are deleted so a recurrence escalates again.
- DocTypes: Decision Centre Settings (cache_seconds 30, expiry_window_days 30, dispatch_window_days 3, max_rows_per_source 200, rules table of Decision Rule), Decision Rule, Escalation Event, Management Action (read-only for Business Administrator, Management Viewer, System Manager).
- `setup.py` seeds example rules, **disabled with 0 hours** (thresholds are Mintree's decision). Desk page `decision-centre` (`factory_ops/page/decision_centre/`): summary cards, department counts, filters (Department, Priority, Status, From/To, Responsible, Search), table, Approve/Acknowledge/Assign/Note/Open buttons, 60 s refresh.
- Tests written, **never run**: `tests/dc_helpers.py`, `test_decision_logic.py` (pure logic), `test_decision_queues.py` (procurement, QC, production, inventory, N+1 statement count, approval-limit boundaries 10,000.00 vs 10,001.00), `test_decision_departments.py` (dispatch, breakdowns, accounts).

### Not written yet
- Tests: role visibility and scope (manager vs operator vs outsider vs Management Viewer), action permission and refusal logging, escalation with duplicate prevention and cleanup, summary accuracy against real documents, PM overdue, CAPA, deviation approval.
- Management workspace integration and the seven summary Number Cards (method cards on document_type "Management Action", returning bare ints), landing/page roles, Settings JS (kind Select), a Script Report for action history/export, `UI_ARCHITECTURE.md` and roadmap updates. `CORE_MODIFICATION_LOG.md` stays empty.

### Likely first failures when you run it
Management Action `action` options must include "Escalated" (they do, check on migrate); `qc_release(..., submit=False)` return type assumed; the production unblock and dispatch tests are loosely written; Issue Priority records; maintenance fixture copy in `TestBreakdowns`; earlier Business Administrator / System Administrator tests may need updating for the new DocTypes; `ensure_single_defaults` is called in `setup/install.py` for Decision Centre Settings (check it exists); Role Profile saves during migrate need the worker.

## 7. Next steps, in order
1. Rebuild the environment (section 3), clone the bundle, install `factory_ops`, run `migrate`.
2. Run one clean full per-module regression on `ui/roles`; record exact results next to section 5.
3. Merge `ui/roles` into `factory_ops` `main` (now eligible). Never merge the Decision Centre before it is tested.
4. Rebase `feature/decision-centre`, migrate, run the three new test modules, fix, then write the missing tests and pieces (section 6), updating docs as you go.
5. Run the full regression again; only then merge. Report tested vs untested separately.
6. Then the owner's workflow priorities: Stores and Inventory, Procurement, Production Floor, Quality Control, Dispatch, Accounts, as real working workflows.

## 8. Open decisions for Mintree (not mine to make)
Favicon and approval of the derived square mark; confirmation of accent shade; whether the role landing order is right; maintenance rollout timing; real escalation thresholds and recipients (rules ship disabled); QC SLA hours (existing Factory Ops Settings); who holds Purchase Approval Limits and their amounts; a second finance approver for large payments.

## 9. Working rules from the owner
Continue without waiting; keep isolated work separate while a regression runs; do not lose commits; targeted tests plus a full regression before merging; keep audit and architecture docs current; report tested separately from untested; no fake data; no core changes without logging them.
