# Hand-off: Mintree Factory OS (continue in a second session)

Everything below was pushed to this branch because this session's GitHub access covers only this repo.
`factory_ops` is meant to be its own repo; here it lives in `apps/factory_ops/` as a snapshot.

## What is where
- `apps/factory_ops/` : working tree of branch `feature/decision-centre` (= `ui/roles` b863d9d + 2 WIP commits). It is the full `factory_ops` Frappe app (docs in `apps/factory_ops/docs/`; start with `docs/HANDOFF_DECISION_CENTRE.md`, `docs/UI_ARCHITECTURE.md`, `docs/DEV_ENVIRONMENT.md`).
- `handoff/factory_ops.git.bundle` : full git history of `main` (663cc21), `ui/roles` (b863d9d), `feature/decision-centre` (336c5ec). Restore: `git clone handoff/factory_ops.git.bundle factory_ops && cd factory_ops && git branch -a`.
- `handoff/regression_summary_partial.tsv`, `handoff/run_modules.sh` : per-module regression runner and its partial result.
- Fork code: branch `mintree/v16` has the v16 base with docs only; no ERPNext core modifications (see `docs/mintree/CORE_MODIFICATION_LOG.md` there).

## State
- Tested (targeted, on ui/roles): test_business_admin 43, test_operational_cards 12, test_admin_access 26; test_desk_ui, test_plant_maintenance, test_workspaces passed earlier.
- Full regression of ui/roles: was running, 19 of 26 modules finished, 0 failures at last look (see partial tsv). Not finished, not final. Baseline to compare: 284 tests / 24 modules.
- Decision Centre: written, compile-checked only. Never migrated, imported by a bench, or tested. Do not merge.
- Environment (Frappe v16.55.2, ERPNext v16.53.0, Python 3.14, MariaDB 11.8, Valkey) lived in the old container and must be rebuilt (see DEV_ENVIRONMENT.md; `bench worker` must run for Role Profile saves).

## Next steps
1. Rebuild the bench, install `apps/factory_ops` (or the cloned bundle), run a per-module regression like `run_modules.sh` on `ui/roles`; record exact results.
2. Merge ui/roles to main only after a full pass.
3. Bring Decision Centre into the bench, `bench migrate`, run test_decision_logic / test_decision_queues / test_decision_departments, fix fixtures, then write the remaining pieces listed in `docs/HANDOFF_DECISION_CENTRE.md`.
