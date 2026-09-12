# D065 — Autodev freeze, archive, and removal

Date: 2026-09-09

Status: Accepted by explicit user instruction.

## Decision

Freeze the complete pre-removal AIwolf repository, including Git metadata, dirty tracked
files, untracked work, ignored local controller state, campaign/workpackage evidence, logs,
and handoffs. Verify that archive before removing the legacy autonomous-development runtime
and its dedicated documentation from the active repository.

The archive is a historical source for a future `Autodev v2`, not an active AIwolf runner.
The game source, game specifications, game-phase detailed designs, Design Gate, normal
tests, and review history remain active.

## Freeze point and archive

- Branch: `main`
- HEAD/tag target: `994188a8ccc7cc14c4d6626caee4fa633cdc642c`
- Tag: `autodev-freeze-2026-09-09`
- ZIP: `C:\AIagent\backups\AIwolf_pre_astra_autodev_freeze_2026-09-09.zip`
- ZIP bytes: `9046688`
- SHA-256: `80111634582ee2c4bd70793b22087ffd5a17324c89188b3decbb37e59758ecb4`
- Sidecar: `C:\AIagent\backups\AIwolf_pre_astra_autodev_freeze_2026-09-09.zip.sha256`
- Verification report: `C:\AIagent\backups\AIwolf_pre_astra_autodev_freeze_2026-09-09.verification.txt`

The tag identifies committed HEAD only. The ZIP is authoritative for the pre-removal dirty
working tree.

## Verification evidence

- Full-entry decompression/integrity read: PASS
- Unsafe/path-traversal entry check: PASS
- Extract into a separate directory: PASS
- Required assets: PASS (17/17)
- Extracted `.infra-runs` files: 2143
- Extracted Git HEAD and freeze tag: PASS
- Freeze information and known-recovery issues: PASS
- Final A/B classification correction and second full verification: PASS

## Consequence

No autonomous launcher, local runner integration, campaign/workpackage controller, provider
or risk routing, token accounting, dedicated infrastructure test, runtime state, or operator
guide remains executable from the active AIwolf repository. Restoring any of it requires a
new explicit user task beginning with the ZIP verification and the known-issue checklist.

D034/D043, D053, the general local-LLM setup, and token-efficiency history are retained as
shared migration evidence. They are not active runner or model-routing authority; current
instructions are `AGENTS.md` and `RUNBOOK.md`.

## Post-removal validation

- Dedicated runtime/config/state paths remaining: 0
- Runner processes remaining or started by this task: 0
- Game implementation paths changed by this removal: 0
- Imports and bytecode compilation: PASS
- Full normal regression: 305 passed / 612 subtests passed / 4 existing deprecation warnings
- Documentation consistency and diff whitespace checks: PASS
- Lint/type check: not configured in `pyproject.toml` or CI, so skipped
