# factory_ops

Mintree Factory OS customisations for ERPNext v16, by FlowHQ. Licensed GPL-3.0 (see `license.txt`).

All factory-specific behaviour lives here; ERPNext and Frappe are never modified.

| Module | What |
| --- | --- |
| Factory Ops | Factory Ops Settings, setup (roles, warehouse types, custom fields, permissions) |
| Quality | QC Release, QC Deviation, QC Queue report, derived batch QC status |
| Inventory | Server-side QC guards on every stock route (`inventory/guards.py`) |

Requires: Frappe v16.55.2, ERPNext v16.53.0. Development setup and test commands are in
`docs/DEPLOYMENT_RUNBOOK.md` at the repository root; design in `docs/DATABASE_AND_WORKFLOW_DESIGN.md`.

```bash
bench --site test.localhost run-tests --app factory_ops
```
