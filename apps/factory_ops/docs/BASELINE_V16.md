# factory_ops baseline on the Mintree ERPNext v16 fork

Recorded 2026-11-05, before any business-logic change in this repository's Mintree-fork era.

## Environment (verified, not assumed)

| Component | Version | Evidence |
|---|---|---|
| Frappe | v16.55.2, `3d1284c5fdb52207a516a49bbf2fd186cb43b3ab` | `git describe`, `bench version` |
| ERPNext (Mintree fork `vatsal04-02/erpnext-x-mintree`) | 16.53.0 on branch `mintree/v16`; code identical to upstream tag `v16.53.0` (`9cd11b195`) plus docs-only commits (`fbf64b1cb`) | `git diff v16.53.0..mintree/v16 --stat` touches only `docs/mintree/` |
| factory_ops | 0.0.1, `main` at `0389fcd` (history split from `mintree-crm-project` `apps/factory_ops`, 23 commits) | `git rev-parse` |
| Python | 3.14.10 (uv) | `env/bin/python --version` |
| Node / Yarn | 24.22.2 / 1.22.22 | |
| MariaDB server | 11.8.9 (`mariadb:11.8` container) | `docker exec ... mariadb --version` |
| Valkey | 8.1.11 (`valkey/valkey:8.1-alpine`, two instances: cache 13100, queue 11100) | |
| Site | `test.localhost` (erpnext + factory_ops, `allow_tests`), created with the same steps as the earlier CI | |

## Result

Command, run once per module so a slow or failing module cannot hide the others:

```bash
bench --site test.localhost run-tests --module factory_ops.tests.<module>
```

| Module | Tests | Result | Time |
|---|---:|---|---:|
| test_acceptance_e2e | 4 | OK | 13.6 s |
| test_batch_eligibility | 10 | OK | 31.7 s |
| test_demo | 3 | OK | 0.2 s |
| test_factory_os_api | 3 | OK | 11.3 s |
| test_guards | 10 | OK | 12.8 s |
| test_install_and_queue | 5 | OK | 32.3 s |
| test_lifecycle_routes | 13 | OK | 21.6 s |
| test_maintenance | 8 | OK | 11.2 s |
| test_marketing | 7 | OK | 8.6 s |
| test_marketplace | 15 | OK | 10.2 s |
| test_master_data | 15 | OK | 63.5 s |
| test_production | 16 | OK | 36.0 s |
| test_purchase | 14 | OK | 33.3 s |
| test_qc_release | 18 | OK | 16.1 s |
| test_qc_retest | 5 | OK | 12.8 s |
| test_research | 13 | OK | 9.6 s |
| test_sales | 21 | OK | 45.1 s |
| test_spike_qi_lifecycle | 8 | OK | 11.7 s |
| test_workspaces | 15 | OK | 53.9 s |
| **Total** | **203** | **203 passed, 0 failed, 0 errors, 0 skipped** | |

- Compatibility failures: **none**. Environment-related failures: **none**. Application-logic
  failures: **none**. No code was changed to obtain this result.
- An earlier all-in-one run was interrupted on purpose (40 passes, 0 failures seen, no summary);
  it is not counted. The table above is the baseline.

## Not covered by this baseline

- The HTTP acceptance scripts, browser (Playwright) checks and React unit tests from the earlier
  project (`deploy/dev/*`, `apps/web`) were **not** run: they need a running dev server and live
  only in `mintree-crm-project`. ERPNext Desk is now the primary interface, so the React app is
  not part of this baseline.
- One run, one machine. No repeat-run flakiness measurement.
- Upstream `version-16` tip is 911 commits ahead of v16.53.0; this baseline says nothing about it.
  Move to a newer commit only after re-running this suite.
- The suite uses SAMPLE/DEMO data. It says nothing about real Mintree data.
