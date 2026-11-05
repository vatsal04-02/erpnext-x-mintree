# Decision Centre hand-off (WIP, untested)

Branch `feature/decision-centre` (worktree /home/user/factory_ops_dc), based on `ui/roles` b863d9d. Nothing here has been imported by a running bench, migrated or tested. Do not merge until the steps below pass.

## Written (uncommitted until this note; compile-checked only)
- `factory_ops/decisions/`: model, priority, queue (collect/dedupe/filters/summary/cache/API), context, settings, groups, actions (run_decision_action + Management Action log), escalation (hourly, hash-keyed events, cleanup), setup (disabled example rules), sources/{procurement,quality,production,inventory,dispatch,maintenance,accounts}.
- DocTypes: Decision Centre Settings, Decision Rule, Escalation Event, Management Action. Desk page `decision-centre` (JS). Hourly job in hooks.py. `setup/install.py` seeds settings and example rules.
- Tests (not run): tests/dc_helpers.py, test_decision_logic.py (pure), test_decision_queues.py (procurement/QC/production/inventory/N+1), test_decision_departments.py (dispatch, breakdowns, accounts).

## Not written yet
- Tests: role visibility/scope, action permission and refusal logging, escalation + duplicate prevention + cleanup, summary accuracy vs real documents, PM overdue, CAPA, deviation.
- Management workspace integration + 7 summary Number Cards (method cards, document_type "Management Action"; return bare ints), landing/page roles, Settings JS (kind select), Script Report for action history, docs (UI_ARCHITECTURE.md, roadmap), CORE_MODIFICATION_LOG stays empty.

## Known risks to check first when running
- Management Action `action` options include "Escalated"; escalation uses log_action as Administrator.
- Test helpers: `qc_release(..., submit=False)` return type is assumed (name or doc); production unblock test and sales "blocked" test are loosely written; Issue Priority records; `test_maintenance` fixture copy in TestBreakdowns.
- Earlier BA/System Manager tests may need adjusting for new doctype permissions (Management Action read-only; Settings write).
- Role Profile saves during migrate need `bench worker` running.

## Order of work
1. Wait for the full regression on `ui/roles` (`~/run_modules.sh`, logs and summary.tsv in ~/baseline; 16+ of 26 modules done, 0 failures at last check). Record exact results. Do not touch the bench tree before it ends.
2. Merge `ui/roles` into `main` only after a full pass.
3. Rebase `feature/decision-centre`, copy into the bench tree, `bench migrate`, run the new test modules, fix, then add the missing pieces above, then a full regression.
