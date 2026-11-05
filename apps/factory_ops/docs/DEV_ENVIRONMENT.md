# Development environment (as built 2026-11-05)

Development only: weak passwords, no TLS, SAMPLE/DEMO data. Versions and hashes are in
`BASELINE_V16.md`.

## Layout

- Bench: `/home/frappe/frappe-bench` (user `frappe`; bench refuses root). Apps are symlinks:
  `apps/erpnext` -> the Mintree fork working tree (branch `mintree/v16`), `apps/factory_ops` -> this
  repository. Edit in the working trees; no reinstall needed.
- Services (Docker, host network): MariaDB 11.8 on 3307, Valkey 8.1 cache on 13100 and queue on
  11100 (`deploy/dev/compose.yaml`).
- Site `test.localhost` (erpnext + factory_ops, `allow_tests`). Administrator password `ci-admin`.

## Everyday commands (as `frappe`, after `. ~/.devenv`)

```bash
bench --site test.localhost migrate                      # applies setup, workspaces, branding, landing
bench --site test.localhost run-tests --module factory_ops.tests.test_desk_ui
bench --site test.localhost serve --port 8000 --noreload  # then http://test.localhost:8000
```

Full suite, one module at a time with a log and exit code each: `~/run_modules.sh` writes
`~/baseline/summary.tsv` and `~/baseline/<module>.log`.

Screenshots for visual review (needs the server running and `playwright` available):

```bash
node deploy/dev/ui_check_desk.js <outdir> [user] [password] [workspace-slug ...]
```

## Gotchas found the hard way

1. **Restarting the server.** The process is `python -m frappe.utils.bench_helper frappe --site
   test.localhost serve ...`. `pkill -f "bench --site test.localhost serve"` does not match it, the
   old process keeps port 8000, the new one fails to bind, and you keep testing stale hooks. Use
   `pkill -f "bench_helper frappe --site test.localhost serve"` and check the new PID. Hooks
   (`hooks.py`) are read at process start; `migrate` and `clear-cache` are not enough.
2. **`bench build` fails on ERPNext's banking app** because the fork is soft-linked from outside the
   bench (its Vite config resolves `../../sites/common_site_config.json` relative to the real path).
   The JS/CSS bundles and `/assets` links are built before that step, so Desk works; only the
   bank-reconciliation UI is not built. Build it from a bench-resident clone if it is needed.
3. **Standard report JSON is re-synced only if `modified` is newer than the database copy.** Never
   stamp a new file with a future date.
4. **Workspace names must slug cleanly** (`&` is not allowed in practice) and may not clash with a
   DocType, Page or another Workspace.
5. **A report needs the `report` right on its reference DocType**, not only a role on the report.
6. `git checkout` as root leaves root-owned files; run `chown -R frappe:frappe` on the repositories.
7. Node 24, Python 3.14 and the proxy CA settings live in `~/.devenv` for the `frappe` user.

## Background worker

Run a worker next to the server (`bench worker --queue short,default,long`, as `frappe`). Saving a Role
Profile (and other documents that use `queue_action`) locks the document and enqueues a job; without a
worker the lock stays and the next save fails with "This document is currently locked and queued". If a
stale lock remains after a crash and no Frappe process is running, delete the hash-named `.lock` files in
`sites/<site>/locks/` (leave `bench_*`, `install_app` and `site_config`). The escalation jobs of the
Decision Centre need a worker and the scheduler as well.

## Tests that need test mode off

Frappe skips Version history and one workspace-permission guard when `frappe.in_test` is true. Use
`production_mode()` (or `production_auditing()`) from `factory_ops/tests/utils.py` around the single call
under test; keep it narrow.

## Not covered

No staging or production deployment exists. Backups, worker/scheduler processes and socket.io are
not configured in this development environment.
