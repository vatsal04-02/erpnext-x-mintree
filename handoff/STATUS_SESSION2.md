# Status after session 2 (2026-11-05) — read after HANDOFF.md

## State of factory_ops (bundle `handoff/factory_ops.git.bundle`, branches inside)
| Branch | Commit | State |
|---|---|---|
| `main` | afdef47 | `ui/roles` + regional-permission fix + test register + **Administration on native Setup/Home**. Tested (see `docs/TEST_EXECUTION_REGISTER.md`). `apps/factory_ops/` in this repo is this tree. |
| `ui/roles` | b863d9d | historical tip; contained in main |
| `fix/regional-permission-state` | 152a1f4 | contained in main |
| `feature/native-admin-home` | 252e1f4 | contained in main |
| `feature/decision-centre` | befb2da | **UNTESTED**: HANDOFF's WIP + `test_decision_access.py`, seven summary cards, workspace wiring, Management Action History report, Settings JS. Based on b863d9d, **not yet rebased onto main** (it edits `setup/workspaces.py`, `setup/dashboard_data.py`: expect small conflicts; `ADMIN_HOME` moved there on main). |

Restore: `git clone handoff/factory_ops.git.bundle factory_ops && cd factory_ops && git checkout main`.

## What was done
1. Environment rebuilt (Frappe 16.55.2, ERPNext 16.53.0 fork on mintree/v16, MariaDB 10.11, Redis) — see
   register for differences and the gotchas below.
2. Clean per-module regression of `ui/roles` b863d9d: 339 tests / 26 modules, 24 OK first time; the other
   two were environment/permission findings, resolved and re-run (register sections A, B). Logs:
   `handoff/regression_e2/`.
3. `ui/roles` merged into factory_ops `main`.
4. Setup/Home customised into the Administration dashboard (UI_ARCHITECTURE.md section 11).

## New gotchas
- Bench needs built assets for `frappe.get_print` (`bench build --app frappe`; `--skip-assets` breaks
  `test_production`'s print-format assertion).
- Frappe throttles user creation to 60/hour: test sites need `bench set-config -p throttle_user_limit 1000`
  (a number, not a string).
- ERPNext's test bootstrap creates a South Africa company, whose regional setup adds System Manager rights and
  a restrictive Custom Role to the VAT Audit Report (handled in `ensure_admin_reports`).
- Never edit ERPNext's `home.json`: Administration is a `Custom Workspace` record for `Home`.
- The git proxy serves public clones only (`add_repo` then `git clone`); GitHub API/codeload is blocked, so
  Frappe was cloned from `frappe/frappe` `version-16`.

## Next
Rebase `feature/decision-centre` onto main, migrate, run its four test modules (never run), fix, update the
register, then merge. Then Stores and Inventory, Procurement, Production Floor ... as in HANDOFF.md section 7.

## Addendum: 12-product DEMO scenario
`docs/DEMO_SCENARIO.md` (in factory_ops), report and log in `handoff/demo_scenario/`. 84 of 84 checks passed on
a dedicated `demo.localhost` site; it found and fixed one product bug (`genealogy.trace_forward` SQL error,
regression test added). Rebuild the demo site with the commands in that document (about 5 minutes). Bench
details: run `bench ... execute` kwargs as Python (`{"dry": True}`), and a stale partial run needs the demo site
dropped and rebuilt (use `dry` first).
