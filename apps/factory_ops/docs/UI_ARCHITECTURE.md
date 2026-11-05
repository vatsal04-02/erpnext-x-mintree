# Mintree Factory OS: Desk UI architecture

Status 2026-11-05. Decision (confirmed): **ERPNext Desk is the primary UI**, customised through
`factory_ops`. No separate React front end. React only for a specialised screen where Desk is
demonstrably unsuitable (production tablet, barcode-heavy station), decided case by case.

Everything below marked *verified* was read from the installed source (Frappe v16.55.2, ERPNext
v16.53.0) or the site on 2026-11-05. Nothing here has been shown to users yet.

## 1. How Desk v16 is assembled (verified)

| Layer | Frappe/ERPNext mechanism | Role in the experience |
|---|---|---|
| Home screen tiles | `Desktop Icon` (fields incl. `roles`, `link_to`, `bg_color`, `logo_url`, `parent_icon`, `hidden`) and `Desktop Settings.icon_style` | What a user sees first; filtered by role |
| Left menu | `Workspace Sidebar` (+ `Workspace Sidebar Item`), per module, `for_user` optional | Navigation inside a department |
| Department page | `Workspace` (shortcuts, number cards, charts, link cards, quick lists, custom blocks; `roles`, `module`, `public`) | The department's day-to-day page |
| Forms | `Custom Field`, `Property Setter`, `doctype_js` client scripts, `Print Format` | Simplified forms, contextual buttons, printouts |
| Lists | `List View Settings`, saved filters, `Number Card` | Saved filters, counts |
| Who lands where | `User.default_workspace`, `Role Profile`, `Module Profile`, `Block Module` | Role-specific entry point |
| Branding / login | `Website Settings` (`app_name`, `app_logo`, `splash_image`, `favicon`, `brand_html`, `banner_image`, `hide_login`), `Navbar Settings` (`app_logo`, settings/help dropdowns), hooks `app_include_css/js` | Product name, logo, login page, navbar |

Constraint that shapes the design: **a workspace only shows links. Every link opens with the
user's own permissions**, so a workspace can never widen access (this is also stated in
`setup/workspaces.py`). Role-based visibility therefore has two separate parts: what is *shown*
(workspace/icon roles) and what is *allowed* (DocPerm / Custom DocPerm, enforced in Python).

## 2. What already exists in `factory_ops` (verified)

`factory_ops/setup/workspaces.py` (821 lines) creates, idempotently on every migrate, native
records for **seven** role-based workspaces, each with a sidebar and a Desktop Icon limited to
its roles:

| Workspace | Roles | Number cards | Charts | Link groups |
|---|---|---:|---:|---|
| Management | Management Viewer, System Manager | 12 | 4 | Stock and quality; Production; Sales; Finance |
| Procurement | Purchase User/Manager | 3 | 1 | Documents; Suppliers; Reports |
| Stores and Warehouse | Stock User/Manager | 4 | 0 | Stock; Batches and expiry; Movements; Setup and opening stock |
| Production Floor | Manufacturing User/Manager | 4 | 1 | Production; Reports; Setup |
| Quality Control | QC Officer, QA Manager | 4 | 1 | Inspection and release; Stock under QC; Setup |
| Sales and Dispatch | Sales User/Manager, Delivery User | 4 | 2 | Orders; Dispatch; Billing |
| Factory Accounts | Accounts User/Manager | 3 | 2 | Documents; Receivables and payables; Ledgers and statements; Stock in the books |

Plus 16 custom number cards (ledger-based stock values, QC pending counts, open POs, wastage...),
and the read-only role **Management Viewer**. 15 tests in `tests/test_workspaces.py` check, among
other things, that every link/card/chart exists, each role sees its own workspaces and icons, every
card computes for its users, Management Viewer never changes data, and no screen lets anyone move
held stock.

**Not present (verified):** any branding or login configuration; `default_workspace` / Role Profile /
Module Profile use; an R&D workspace (R&D Chemist and R&D Manager roles and the research doctypes
exist, with no Desk workspace); Maintenance, E-commerce and Marketing workspaces (their doctypes
exist from the earlier project, whose UI was React); saved list filters; simplified forms; the two
new reports from Milestone A on any workspace.

## 3. Gap list against the requested UI

| Requested | State | What is needed |
|---|---|---|
| Mintree branding and login | Missing | Config-driven `Website Settings` / `Navbar Settings` application on migrate. Text (product name "Mintree Factory OS") can be done now; **logo and colours need the real brand assets from Mintree**, none are invented |
| Production workspace | Exists (Production Floor) | Add Milestone A reports and Work Orders needing attention |
| Inventory and procurement | Exist as two workspaces (different roles) | Keep separate; add reorder/shortage view built on ERPNext's Item Reorder and the existing RM/PM Requirements report |
| Quality control | Exists | Add Batch Genealogy; CAPA entry point (reuse Quality Action) |
| R&D | **Missing** | New workspace for R&D Chemist / R&D Manager over existing doctypes |
| Dispatch and sales | Exists | Review against dispatch register and returns flow |
| Accounts | Exists | Review only |
| Management dashboard | Exists (12 cards, 4 charts) | Add yield / unreconciled orders once real data exists |
| Simplified navigation and sidebar | Partly (per-department sidebars) | Landing per role (`default_workspace`), consistent ordering, remove duplicate entry points |
| Role-based visibility and permissions | Workspaces + Custom DocPerm exist | Role-to-landing map; keep the "workspace never widens access" test |
| Maintenance workspace | Missing | Same pattern, over Asset / Asset Repair / Asset Maintenance with Maintenance User/Manager |

## 4. Implementation plan (small milestones, each with tests)

| # | Milestone | Real functionality | Tests |
|---|---|---|---|
| B1 | Wire Milestone A into Desk; R&D workspace | Add the two reports to Production Floor, Quality Control, Management; create the R&D workspace from the existing `WORKSPACES` mechanism | Existing generic workspace tests cover new entries automatically; add: R&D roles see it, others do not; every R&D link resolves |
| B2 | Role landing | On User save, when `default_workspace` is empty set it from a role-to-workspace map in code (never overwrite a user's choice) | Per-role landing; no overwrite; Administrator untouched |
| B3 | Branding and login | `apply_branding()` on migrate: app name, navbar, login title from a branding config; logo/colours only when the files are provided | Settings applied idempotently; login page renders the configured name; missing logo leaves Frappe's default, no broken image |
| B4 | Maintenance workspace | Same pattern | Same generic tests plus role visibility |
| B5 | Saved list filters and list settings | Shared filters for the daily lists (e.g. QC Hold batches, POs to receive, Work Orders in process), as supported by Frappe | Filters exist, return only expected documents, respect permissions |
| B6 | Form simplification | Only where observed friction is confirmed with users: Property Setters hiding fields that no Mintree process uses, never mandatory or stock/QC-relevant fields | For every hidden field a regression test of the owning workflow |
| B7 | Production tablet / barcode screens | Only if Desk proves unsuitable in use | Decided with evidence |

Order: B1 and B2 first (highest value, no assets needed), then B3 (blocked on assets for the
logo only), B4, B5; B6 and B7 depend on user feedback.

## 5. Rules for this work

1. Standard configuration first, then hooks / `factory_ops` records, then new DocTypes, and an
   ERPNext core change only when nothing supported can do it. **No UI requirement identified so far
   needs a core change**; `CORE_MODIFICATION_LOG.md` in the fork stays empty.
2. A workspace or tile is created only when its link works for the roles shown it. No placeholder
   screens; anything not built is not shown.
3. Report JSON edits need a `modified` stamp newer than the database copy (and never a future one),
   see `MILESTONE_A_RECONCILIATION_AND_GENEALOGY.md`.
4. Branding values come from Mintree, not from this repository.

## 6. Decisions needed from Mintree

- Brand assets: the wordmark was supplied (teal #01877A). Still needed: a favicon, approval of the
  square mark derived from the wordmark (see section 7), and confirmation of the accent shade.
- Whether the implemented landing order is right (see section 7). People with several roles open
  on the first match in this order: Management, Production Floor, Quality Control, Research and
  Development, Sales and Dispatch, Factory Accounts, Stores and Warehouse, Procurement. Stores
  staff (Stock + Purchase) therefore land on Stores, not Procurement.
- Whether the factory wants a Maintenance workspace now or after the first production rollout.
- Which form fields staff find confusing (input to B6).

## 7. Status of B1 to B3 (implemented 2026-11-05)

Verified in a real browser (Playwright against `bench serve`, screenshots reviewed) and by tests.
Full-suite results are in `IMPLEMENTATION_STATUS.md`.

| Milestone | Done |
|---|---|
| B1 | Workspace generator rewritten: one kind of widget per row, separated by spacers; no repeated page title; a target is never both a shortcut and a link; quarter-width link cards when there are four groups. Batch Genealogy and Production Reconciliation placed on Production Floor, Quality Control and Management. New **Research and Development** workspace (R&D Chemist / R&D Manager) with four cards over real Formulation, Formulation Trial and Stability Study statuses, four shortcuts, a trials-per-week chart |
| B2 | `setup/landing.py`: `User.default_workspace` filled from department roles when empty (never overwrites a choice; a system administrator lands on the global Administration page, see section 8). On user save and once per migrate for existing users |
| B3 | `setup/branding.py`, `public/css/mintree.css`, assets: Mintree wordmark on the login page, square mark on the department shells, product name "Mintree Factory OS", teal buttons (default theme only, destructive red untouched, light theme only), splash screen. Replaces only Frappe/ERPNext defaults |

Findings that shaped the implementation (all observed, not assumed):

1. The old generator put a chart with no data (an empty 57 px strip) beside shortcuts, and shortcuts
   beside link cards stretched to the cards' height (tall empty boxes). Fixed by row separation.
2. A workspace name with `&` gives the URL slug `r&d`; renamed to "Research and Development".
3. `app_logo_url` cannot brand the logo with three apps defining it (Frappe takes the first), so
   Navbar Settings / Website Settings are used.
4. Solid buttons in Frappe v16 use `--bt-solid-*` tokens, not `--primary`; the default theme is
   `.es-button` and destructive red is `[data-theme="red"]`.
5. White on the raw brand teal #01877A is 4.4:1 (below WCAG AA 4.5:1), so buttons use #01766B
   (5.5:1); tests compute this.
6. The department shell tile comes from the app's own `app_logo_url` hook; the ERPNext shells keep
   the blue ERPNext tile because it comes from `erpnext/hooks.py`. Changing it needs a core patch,
   which is not justified for a logo, so it was not done.

Known limitations:

- ERPNext's own shells (Setup, Accounting, Buying...) still show the ERPNext tile.
- No favicon (none supplied). The square mark is a crop of the supplied wordmark's first letter and
  needs Mintree's approval.
- Manufacturing Manager alone cannot open Work Orders or Batch genealogy: ERPNext v16.53 grants Work
  Order only to Manufacturing User and Stock User, and gives Manufacturing Manager an all-zero Batch
  row. Pages hide what a role cannot open. Users should hold the "User" role too (ERPNext's
  convention), or Mintree decides to grant managers explicitly.
- E-commerce and Marketing have no workspace yet.
- Not yet done: saved list filters (B5), form simplification (B6), tablet screens (B7).

## 8. Role and permission architecture (Super Admin and departments)

**Visibility and action permission are separate layers, and only the lower two decide what anyone can do.**

| Layer | Decided by | What it controls | Where enforced |
|---|---|---|---|
| 1. Visibility | Workspace / Desktop Icon / Report roles, and read access | Which pages, tiles and reports appear | Frappe (server builds the page list per user) |
| 2. Action permission | DocPerm / Custom DocPerm per role | Create, write, submit, cancel, delete, amend, QC release | Frappe `has_permission`, server-side on every call |
| 3. Business controls | `factory_ops` validation (QC guards, approval limits, segregation of duties) | What a permitted user may do *to this record now* | `doc_events` on the server; no role or workspace is consulted |

Seeing a page grants nothing: every link and card opens with the viewer's own layer-2 rights, and layer 3
runs for everyone, including the built-in Administrator.

### Super Admin (role System Manager)

Implemented in `setup/admin_access.py`, re-applied on every migrate (idempotent):

- **Every Mintree workspace and desktop icon** carries System Manager (`page_roles`). ERPNext's standard
  workspaces have no role restriction, so they need no change.
- **Read and report only**, on every standalone ERPNext and `factory_ops` DocType (System Manager has a custom
  permission row on 136 DocTypes on the dev site, some from earlier setup), plus read on higher permlevels (so Batch QC status is visible). Never write, create,
  submit, cancel, delete or amend. A row the module creates is written right by right (Frappe's
  `add_permission` does not produce a clean read-only row: see finding 1); a row that already existed
  keeps its upstream rights untouched.
- **Reports:** a Custom Role per standard report restricted to roles, holding the union of its roles and
  System Manager (for a report Frappe *replaces* the role list with the Custom Role's). Standard Report
  documents are never saved.
- **Administration** (System Manager only; since section 11 it is ERPNext's own Setup/Home workspace, not a page of its own): four real figures (enabled users, failed logins,
  errors and failed scheduled jobs in 7 days), 16 tiles for every department and the core ERPNext modules,
  and groups for users and access, the audit trail, and settings and integrations. Admins land here, not
  on a department; they switch departments from the tiles, the shell switcher or the home screen, with
  no account change.
- **Admin search:** with read access, the standard permission-aware search and lists return records from
  every department.

What the administrator still cannot do, because layers 2 and 3 say so: QC Release, Deviation and Retest
are read-only for System Manager (submit/cancel belong to QC Officer and QA Manager); transaction
documents (Sales and Purchase documents, Stock Entry, Work Order, Journal and Payment Entry, ...) are
view-only; held stock cannot be moved, and a user cannot release a batch they received, whoever they are.

### Departments and operators

- A department manager sees their department's page (and any other page for which they hold a role),
  and opens only what their roles permit; they do not see Administration. Cross-department access is
  granted by **roles** (more pages, more DocPerm), not by a special workspace. The read-only
  **Management Viewer** role gives managers cross-department visibility without any action right.
- Operators land on their own simplified page (`landing.py`). Manager-only actions are separate
  permissions: for example only QA Manager may cancel a QC Release and only QC roles may create one.

### Tests

`test_admin_access.py` (26 tests) asserts: the administrator sees every Mintree page and every ERPNext
standard page; reads and reports on every operational DocType; every link and report on every page opens;
**no action right exists beyond what upstream ERPNext itself grants System Manager** (checked over every
operational DocType); transaction documents are view-only; R&D masters keep the explicit rights their
own DocType declares; grants and report roles are idempotent and heal drift; department users never see
Administration and cannot open other departments' documents; operator limits on QC documents; QC release
refused to System Manager; held stock and dispatch guards hold for the Administrator account and a
System Manager; the Administrator account cannot release a batch it received; Administration tiles and
cards are real.

### Findings (observed, then confirmed in source)

1. **`add_permission` creates a permissive row.** The first version of the grant gave System Manager
   write and create (and export) on every DocType where upstream gave it no row. The test for "no action
   right beyond upstream" caught it before any use; the code now writes every right explicitly and the
   dev-site rows were repaired. The Management Viewer code already zeroed rights for this reason.
2. **ERPNext v16.53 gives System Manager read on only about half of its operational DocTypes** and on
   none of the reports behind these pages (122 of 269 DocTypes lacked it), so showing a page to an
   administrator without this would have shown empty pages.
3. **Custom DocPerm shadows upstream.** Once a DocType has any custom row, Frappe ignores its standard
   rows. Other roles' rights are copied across, so nothing changes today, but upstream permission
   changes to these DocTypes no longer apply. After an ERPNext upgrade run migrate and review
   `admin_access.shadowed_doctypes()` against upstream.
4. A report's Custom Role *replaces* its standard roles; a workspace's *adds* to them. A role that does
   not exist on the site (e.g. "Employee Self Service" from HRMS) cannot be referenced and is skipped.
5. **A workspace name that equals another app's module name takes that module over.** "Maintenance" is
   ERPNext's module (Maintenance Schedule, Visit); the page is "Plant Maintenance" and `ensure_navigation_module`
   now refuses a clashing name.
6. Frappe fills every Number Card's `currency` with the site default and the widget formats such a card
   as money, so count cards from a method must return a bare number (they showed "₹ 0.00").
7. `User.default_workspace` set automatically earlier is never overwritten, so existing users keep the
   landing they were given before a new page existed; change it by hand or clear it and re-run migrate.
8. System Manager cannot open Website Settings (Frappe reserves it for Website Manager), so it is not on
   the Administration page.

### Limits and decisions needed

- **The built-in `Administrator` account bypasses Frappe's permission checks** (by design) and holds
  every role, so it can do anything the code does not forbid. Business controls still apply to it. Use
  named System Manager accounts for daily administration and keep the built-in account for emergencies.
- **R&D masters** (Formulation, Research Project, Ingredient Category) deliberately give System Manager
  create, write and delete in their own DocType definition. Decision for Mintree: keep, or make the
  administrator read-only there too.
- **Upstream lets Stock User delete draft Stock Entries and Delivery Notes.** Not changed.
- Admin read access covers ERPNext and `factory_ops` DocTypes, not Frappe's system DocTypes (System
  Manager already administers those) and not single (settings) DocTypes.
- Landing and the page list are conveniences, not security: remove neither the layer-2 permissions nor
  the layer-3 controls on the strength of them.

## 9. Two administrator roles

Section 8 described one administrator. A factory needs two different people, so there are two roles,
kept apart. Neither is "everything".

| | **System Administrator** | **Business Administrator** (the owner) |
|---|---|---|
| Frappe role | `System Manager` + `Workspace Manager` (Role Profile "System Administrator") | `Business Administrator` (Role Profile "Business Administrator") |
| Job | Technical administration | The business as a whole |
| Users and roles, system settings, integrations, workspace configuration, technical maintenance | **Yes** (Frappe's own System Manager rights) | **No** (asserted for 25 configuration DocTypes) |
| Pages | Every business page **and** the technical Administration page | Every business page, **never** Administration |
| Landing | Administration (= Setup/Home) | Management (the consolidated dashboard) |
| Operational records | Read and report, all departments | Read and report, all departments |
| Business actions | None | **Submit Purchase Orders**, and only within an approval limit an accountant gave them (below) |
| Document audit trail (Version) | Yes | Read |
| Delete, anywhere | Only what ERPNext already grants System Manager | **No delete right on any DocType** (asserted) |
| QC release, dispatch of held stock, financial postings | No | No |

Someone who is both lands on Management (the business view first) and holds both sets of rights.

### What "authorised approvals" means

Approving a Purchase Order *is* submitting it. The Business Administrator has the `submit` right on
Purchase Order and nothing else beyond reading it. Frappe v16's `Document.submit()` saves the document and
saving also asks for `write`, so the standard form cannot submit for a role that must not edit orders. The
owner approves through `factory_ops.purchase.approval.approve_purchase_order` (the Decision Centre's
approve action uses it): it first checks the real submit permission on that document with Frappe's own
`has_permission`, then submits without the write check. The approval-limit and no-self-approval rules run
in `before_submit` exactly as for anyone else, and the change goes on Frappe's Version history. The right authorises nothing on its own: the server-side
rule in `factory_ops.purchase.approval` still requires an enabled **Purchase Approval Limit** for the
person that covers the order total, and refuses self-approval. So an owner with no limit approves nothing,
and one with a limit approves up to it. Who may set limits is unchanged (System Manager and Accounts
Manager); the owner cannot write or create limits, so cannot authorise themselves.

Other approvals stay with the people authorised for them: QC release, deviations and retests with
QC Officer and QA Manager; accounting postings with the Accounts roles; credit-limit overrides with the
credit controller. Giving the owner one of those is a deliberate, separate act (assign that role).

Boundaries tested (43 tests in `test_business_admin.py`): an order exactly at the limit is approved; one
paisa above is refused; a disabled limit counts as none; a limit of zero approves nothing; a limit for
another company does not apply; a person with a low limit meets the same boundary; a submitted order cannot
be approved twice; and the function is no back door for a user without the submit right.

### Controls that did not change (all re-tested for both roles)

- QC Release, Deviation, Retest and Quality Inspection: no create, submit, cancel or amend. Held stock
  cannot be moved and a held batch cannot be dispatched (a Delivery Note from QC Hold is refused).
- Journal Entry, Payment Entry, Sales and Purchase Invoice, GL Entry, Account, Bank Account: no write,
  create, submit, cancel, amend or delete.
- Every transaction document is view-only for both roles, except the one Purchase Order approval.
- **Audit trail** (`setup/audit_trail.py`): 24 sensitive DocTypes must record changes (Frappe Version).
  Quality Inspection, Material Request and Stock Reconciliation did not; they are switched on with a
  Property Setter, and a test fails if any listed DocType stops tracking. An approval by the owner leaves
  a Version record that the owner can read.
- Workspace visibility never implies permission: pages show links, and every link opens with the
  viewer's own rights.

### Implementation

`setup/admin_access.py` serves both roles from one tested mechanism (`READ_ROLES`): read and report on
operational DocTypes, higher permlevel read, report visibility. `BUSINESS_ACTIONS` lists the owner's only
extras (Purchase Order read and submit; Version read). `ensure_role_profiles()` creates the two profiles.
The `technical` flag on a workspace definition (Administration) keeps it from the owner (`page_roles`).

### Decisions needed from Mintree

1. **Does the System Administrator keep read access to business data?** It does now, from the earlier
   requirement that the administrator can see everything. A strict least-privilege reading would remove
   it, leaving technical administration only (one line in `READ_ROLES`).
2. Should the owner manage Purchase Approval Limits? Today no: the owner cannot write them.
3. Are Purchase Orders the only approval the owner should hold directly? Sales credit-limit overrides and
   deviation approval are separate roles today.
4. `Version` and `Purchase Order` now have custom permission rows (Frappe shadows upstream rows once a
   custom row exists); review them after an ERPNext or Frappe upgrade (`admin_access.shadowed_doctypes()`).

## 10. Operational workspaces: what each page shows, from where

Every figure is read from a real ERPNext or `factory_ops` record with the viewer's own permissions; a
user who may not read the data gets a permission error, not a number. Method cards are used only where a
filter cannot express the figure (a date compared with today, or a join).

| Page | Roles | Cards (source) |
|---|---|---|
| Procurement | Purchase User/Manager | Material Requests to order; **Purchase Orders awaiting approval** (draft POs); POs to receive; **POs overdue** (approved, still to receive, required-by date passed) |
| Stores and Warehouse | Stock User/Manager | Total stock value; Batches awaiting QC; **Batches expiring in 30 days** (stock held, not yet expired); **Items below reorder level** (Item Reorder against Bin). The billing figure "Dispatches Not Billed" was removed from this page |
| Production Floor | Manufacturing User/Manager | Work Orders in progress; batches made (30 days); wastage written off; batches awaiting QC; reports: Production Reconciliation, Batch Genealogy |
| Quality Control | QC Officer, QA Manager | Batches awaiting QC; QC decisions pending; deviations awaiting approval; rejected batches |
| Research and Development | R&D Chemist/Manager | Formulations in draft or review / in trial; trials awaiting outcome; stability studies running |
| Plant Maintenance | Maintenance User/Manager | Open breakdowns; machines under breakdown; maintenance due (14 days); spares below reorder |
| Sales and Dispatch | Sales User/Manager, Delivery User | Orders to dispatch (value); **orders past delivery date**; pick lists open; dispatches not billed |
| Factory Accounts | Accounts User/Manager | Receivables outstanding; overdue invoices; payables outstanding |
| Management | Management Viewer, Business Administrator, System Administrator | **First row: what waits for a decision** (POs awaiting approval, QC decisions pending, deviations awaiting approval, Work Orders in progress); then stock values, QC, production, sales and receivables |
| Administration | System Administrator only | Enabled users, failed logins, errors, failed scheduled jobs (7 days) |

Not done: a separate "Dispatch" page (dispatch staff share Sales and Dispatch); QC "past SLA" figures;
working "create" actions on tiles (Frappe shortcuts open lists; creation stays in the list and form).

### Findings from validating sections 9 and 10 (observed, then confirmed in source)

1. **Frappe skips audit history and one permission guard under the test runner.** `Document._save` sets
   `ignore_version = frappe.in_test`, so a test database has no Version rows at all, and
   `disable_saving_as_public()` lets anyone edit public workspaces. A Property Setter was never going to be
   proven by a normal test save. Tests now run the change inside `production_mode()` (test-mode off) and
   read the Version row: Quality Inspection, Material Request and Stock Reconciliation each record the
   field changed and who changed it, and a Purchase Order approval records the docstatus change.
2. **`Workspace Manager` is a separate role from System Manager.** Frappe reserves editing and deleting
   public workspaces for it and says the two do not imply each other. The System Administrator profile
   therefore holds both; the owner holds neither and is refused by the Workspace controller. (Frappe's
   standard permissions give the `Desk User` role DocPerm write on Workspace, for personal workspaces: the
   controller, not the DocPerm, is the protection.)
3. **A Role Profile save queues a locked background job.** Without a worker the document stays locked and
   the next save raises "locked and queued for execution". The dev bench now runs a worker
   (`docs/DEV_ENVIRONMENT.md`); production needs workers anyway.
4. `User`, private workspaces, cards and charts are self-service in Frappe, so "no technical configuration"
   is tested against a record someone else owns.
5. A grant check that compared only read and report could never see a `submit` right, so a
   "grant is idempotent" test failed; existing rows are now read with every right.
6. The built-in `Administrator` implicitly holds every role (including Business Administrator); it lands on
   Administration, because it is the technical account.

## 11. Administration is ERPNext's own Setup/Home workspace (2026-11-05)

**Decision.** The administrator's page is not a workspace we add: it is the existing, standard `Home`
workspace of ERPNext's Setup shell (`erpnext/setup/workspace/home/home.json`, module Setup, route
`/desk/setup/home`), customised. The earlier separate "Administration" workspace (own sidebar, desktop
icon, navigation module) is retired by `retire_standalone_administration()` on migrate, and anyone whose
landing page was it is moved to `Home`.

**Mechanism (the least invasive supported one).** Frappe v16 protects a standard workspace from in-place
edits ("Standard workspaces can't be edited directly") and offers a site-customization layer instead: a
**Custom Workspace** record whose name is the workspace's. `setup/admin_home.py` writes one for `Home`:

- `content`: a heading block "Administration", the four number cards, the launcher tiles and three link
  cards. ERPNext's `Organization Onboarding` block, its chart and its three cards are no longer referenced.
- `widgets`: the number cards, URL tiles and link cards those blocks use (`ensure_admin_home` builds them
  from `ADMIN_HOME` in `setup/workspaces.py`; a link whose target does not exist is left out).
- roles: `Desk User` removed, `System Manager` added, as a delta against the live base, so the page is the
  System Administrator's only (the Business Administrator and department users do not see it).

ERPNext's files and its base `Home` record are not changed (no entry in `CORE_MODIFICATION_LOG.md` is
needed), ERPNext upgrades keep arriving, and the customization is re-applied idempotently on every migrate.
`landing.ADMIN_PAGE` is `Home`. The only CSS (`public/css/mintree.css`, one block scoped to
`body[data-route="Workspaces/Home"]`) lets tile and card names wrap, because Frappe cuts them with "...".

**What the page shows** (all real, nothing typed in): Enabled Users (System Users enabled), Failed Logins,
Errors Logged and Failed Scheduled Jobs, each over the same last-7-days window (Number Cards on `User`,
`Activity Log` operation Login / status Failed, `Error Log`, `Scheduled Job Log` status Failed); 9 factory
workspaces plus 7 ERPNext module pages as tiles; Users and access, Audit trail, Settings and integrations.
A failed login is a real Frappe record (two wrong passwords against the live server raised the card from 0
to 2). ERPNext's onboarding stays reachable as **Setup Onboarding** (Module Onboarding list).

**Permissions.** Frappe returns a workspace's layout without a role gate, but each figure is computed by the
Number Card API under the caller's own rights: department users, Management Viewer and the Business
Administrator are refused the audit figures server-side (test
`test_the_audit_figures_are_refused_by_the_server_to_those_without_access`); the menu entry is hidden for
them as well. Viewing grants nothing.

**Limitations, stated plainly.**
1. A standard workspace keeps its app-owned title and route: the tab/title bar and the Setup sidebar still say
   "Home" and the URL stays `/desk/setup/home`. The page itself is headed "Administration". Renaming the
   title would mean editing ERPNext's `home.json` (a core modification) or developer mode.
2. Frappe opens URL tiles in a **new tab** (all 16 verified; each opens its real workspace route). There is
   no Workspace-type shortcut in this Frappe version.
3. The Failed Scheduled Jobs card reads 0 unless a job really fails; it is not seeded.
4. Verified in a real browser (Playwright, Chromium) in light and dark themes, at 1440 px and 390 px; no
   page errors, no clipped name. Not verified: Safari/Firefox.
