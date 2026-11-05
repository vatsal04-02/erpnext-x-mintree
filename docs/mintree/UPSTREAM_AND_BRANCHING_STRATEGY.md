# Upstream merge path and branching

## Principles

1. Mintree behaviour lives in `factory_ops` (a separate Frappe app) by default: doc events, custom
   fields, roles, permissions, workflows, reports, API.
2. The fork carries a patch **only** when `factory_ops` cannot achieve the requirement through a
   supported extension point (hooks, `override_doctype_class`, custom fields, property setters,
   client scripts). Each patch gets an entry in `CORE_MODIFICATION_LOG.md` (requirement, original
   behaviour, why insufficient, smallest change, regression test, upstream-merge implication).
3. Frappe Framework is not modified without a documented necessity.
4. Upstream licence notices (`license.txt`, GPL-3.0 headers, `attributions.md`) are retained.

## Branches

| Branch | Purpose |
|---|---|
| `develop` | Mirror of upstream `frappe/erpnext` `develop`. Never receives Mintree commits |
| `mintree/v17-base` (proposed) | Integration branch: upstream snapshot + Mintree core patches only |
| `claude/*`, feature branches | Work branches, merged into the integration branch by PR |

## Upgrade procedure

```bash
git remote add upstream https://github.com/frappe/erpnext.git
git fetch upstream develop
git checkout develop && git merge --ff-only upstream/develop
git checkout mintree/v17-base && git merge develop
```

After each merge: run the `factory_ops` suite on a fresh bench, then re-verify each entry in the
core-modification log (each lists the upstream files it touches, so conflicts are predictable).
Use named upstream commits, not "latest develop", for anything that will reach staging.

## Status

The fork is currently byte-identical to upstream `develop` (`7c629e7e`); no patches exist and no
`upstream` remote was configured in the inspected clone.
