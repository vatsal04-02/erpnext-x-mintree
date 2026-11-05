# Mintree Factory OS — Milestone 0 report

Date: 2026-11-05. Scope: source and architecture foundation. No application code was changed in
this milestone; it is an audit plus the decisions the later milestones depend on.

## 1. What was inspected (evidence)

| Item | Finding | How verified |
|---|---|---|
| Fork content | `HEAD` = `7c629e7e0d3517055156f6b810ae123e27d71022`, **identical to upstream `frappe/erpnext` `develop`** at the time of inspection | `git ls-remote https://github.com/frappe/erpnext develop` returned the same SHA; `git diff develop..HEAD` is empty |
| Mintree-specific commits | None. No file mentions Mintree. Authors are upstream contributors only | `git log --all --format=%an`, `grep -ril mintree` |
| ERPNext version | `17.0.0-dev` (`erpnext/__init__.py`) | file read |
| Frappe requirement | `frappe >=17.0.0-dev,<18.0.0`; Python `>=3.14` | `pyproject.toml` |
| `factory_ops` app | **Not in this repository** (no directory, no hook, no reference) | `find`, `grep` |
| Local clone | Shallow (`git rev-parse --is-shallow-repository` = true); no `upstream` remote configured | git |
| Source modifications to audit | None exist, so the core-modification register starts empty (`CORE_MODIFICATION_LOG.md`) | see above |

### Where the earlier work actually is

The earlier project is in a **separate repository**, `vatsal04-02/mintree-crm-project` (read-only
clone inspected, single commit `68a7bb1`). It contains:

- `apps/factory_ops` — Frappe app, GPL-3.0, modules Factory Ops, Quality, Inventory, Purchase,
  Production, Sales Ops, Research, Marketplace, Marketing; its own `hooks.py` doc_events guards for
  QC Hold / Rejected stock on Stock Entry, Purchase Receipt/Invoice, Delivery Note, Sales Invoice
  and Pick List.
- `apps/web` — a React front end served at `/factory`.
- `tools/website_extraction` — public-catalogue extractor, with unit tests and fixtures.
- `deploy/dev`, `deploy/demo` — environment setup, acceptance scripts, demo reset/scenario.
- `docs/` — 46 recorded decisions (`DECISIONS.md`), status, open issues, runbooks.
- CI (`factory-ops-ci.yml`) pinned to **Frappe v16.55.2 / ERPNext v16.53.0**, Python 3.14, MariaDB 11.8,
  Valkey 8.1.

Test claims in those docs (203 backend tests, 44+47 browser checks, 22 front-end unit tests) are the
earlier project's own reports. **They were not re-run in this milestone** and are not claimed here.

## 2. Conflicts between the new brief and the earlier project

These must be resolved before Milestone 1 code is written.

| # | Topic | Earlier project | New brief | Impact |
|---|---|---|---|---|
| C1 | Base version | ERPNext **v16.53** / Frappe v16.55 (stable tags) | Mintree **fork** at **v17.0.0-dev** (floating `develop`) | `factory_ops` was never run on v17; v17 changes (e.g. new Desk workspace/sidebar JSON, banking app, Master Production Schedule) can break hooks, patches and tests |
| C2 | Core modification | "ERPNext core is never modified" (client-approved, handoff notes) | Core may be changed where documented and necessary | Needs an explicit policy: `factory_ops` first, fork patch only with a register entry |
| C3 | Primary UI | React app at `/factory` is the main UI | React *not* the primary interface; customise Desk; React only where demonstrably necessary | The existing `apps/web` is either demoted, kept for specific screens, or retired |
| C4 | Where `factory_ops` lives | Subdirectory of the CRM repo, symlinked into a bench | "Existing `factory_ops` app" reviewed for compatibility with the fork | Needs one home with its own history |
| C5 | Catalogue extraction | Never run (network blocked, 2026-11-05) | Real catalogue is the demo source | Unblocked: `vmintree.in/robots.txt` returns HTTP 200 from this environment |

## 3. What v17-dev already provides (avoid rebuilding)

Present in the fork under `erpnext/`: Master Production Schedule, Sales Forecast, Production Plan,
Work Order, Job Card, Plant Floor, Downtime Entry, BOM / BOM Creator; Quality Inspection (+
Templates), Batch, Serial and Batch Bundle, Stock Reservation Entry, Putaway Rule, Pick List; Asset
Maintenance / Asset Repair; Non Conformance, Quality Action, Quality Goal; Maintenance Schedule;
`banking/` (bank reconciliation UI). Modules 01, 11, 06 and parts of 05 therefore start from
standard features; Mintree-specific rules (QC hold, two-stage approval, FEFO eligibility) belong in
`factory_ops`.

Not present in standard ERPNext and therefore custom: R&D/formulation workflow with approval
states, marketplace connectors, Meta connector, AI gateway, EHS, compliance document register,
WhatsApp/courier adapters. (HR is a separate app — Frappe HRMS — and must be checked against v17.)

## 4. Environment findings (this cloud sandbox)

- Present: `uv`, `redis-server`, `docker` binary, Node 22, Python 3.13.
- Missing: `bench`, `frappe`, MariaDB; Python 3.14 (required by the fork) can be installed with
  `uv python install 3.14`; Node 24 is required by Frappe v16 and likely v17.
- Network: `pypi.org` and `vmintree.in` reachable. Docker daemon state not yet verified.
- Consequence: **no ERPNext test run was executed in Milestone 0.** Standing up a bench is the first
  task of Milestone 1 and a prerequisite for any claim of working workflows.

## 5. Decisions

Decided by the client on 2026-11-05:

1. **Base version (C1): fork on v17, port `factory_ops`.** Pin `mintree/v17-base` to a named
   upstream commit rather than floating `develop`; use the earlier suite as the compatibility check.
2. **Extraction:** the earlier extractor may be run against `vmintree.in`.

Still open (recommendations in brackets, to be confirmed):

3. **UI direction (C3)** — [Desk workspaces first; React only for tablet production screens and the
   Command Center if Desk cannot meet them].
4. **`factory_ops` home (C4)** — [its own repository, installed as a bench app beside the fork].
5. **Core-modification policy (C2)** — [`factory_ops` first; fork patch only with a register entry].

## 6. Public catalogue extraction (real output)

Run 2026-11-05 17:27–17:29 UTC with `tools/website_extraction/extract.py` from
`vatsal04-02/mintree-crm-project` (the same tool, unmodified). `robots.txt` returned 200 and was
obeyed; 107 HTTP requests; Shopify storefront JSON; no login or bypass. Output:
`docs/mintree/catalogue/` (images are referenced by public URL only; none were downloaded).

| Measure | Count |
|---|---:|
| Products / sellable variants | 227 / 227 |
| Variants with published SKU | **0** |
| Variants with barcode | 0 |
| Variants with compare-at price (MRP) | 226 |
| Variants with a pack size parsed from the title | **56** |
| Image URLs | 869 |
| Product types / collections | 22 / 48 |
| Recorded problems (all `missing_field`) | 230 |

Consequences and caveats:

- Most products are retail kits, combos and gift bags. Many titles carry no parseable pack size,
  so 171 of 227 variants need a pack-size decision before they become stock items.
- No SKU is published; Mintree must supply item codes or approve generated ones.
- Several collections look internal or operational (`REELUP (DO NOT DELETE)`, `parag`,
  `NEW TAX SLAB PRODUCTS`, `Bulk Jars`). Do not treat collection names as an item-group taxonomy
  without Mintree review.
- Product type is empty on some products (e.g. the first two rows), so category mapping needs
  `category_rules.csv` plus review.
- Prices are public selling prices on 2026-11-05, not factory costs. Product images are Mintree's
  content: keep them out of Git and confirm usage rights before any non-local use.
- Not available from the website and not invented here: formulas, RM/PM stock, suppliers, supplier
  prices, production quantities, batches, QC limits, expiry. These stay synthetic and labelled
  `DEMO - NOT REAL`.

## 7. Next milestone

Milestone 1 — core factory operations: stand up bench + MariaDB + Valkey on Python 3.14, install
the fork and `factory_ops`, run the existing suite to get a real v17 baseline, then fix
compatibility failures one by one with regression tests. No test was run in Milestone 0.
