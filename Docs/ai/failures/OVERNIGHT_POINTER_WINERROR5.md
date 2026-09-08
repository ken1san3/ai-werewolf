# Overnight pointer WinError 5 (2026-09-08)

Observed on Local Windows / user PowerShell: run creation printed
`overnight-83d5771cba7d430db0b41999d464833a`, then atomic replace of
`C:/AIwolf/overnight.local.json.66c1eac1024c4e66a06a400ae3aa351d.tmp`
to `overnight.local.json` returned WinError 5. Run remained READY, with zero calls.
The old local pointer still selected the package. ACL inspection alone did not
establish the cause; do not diagnose permanent permission failure or reset ACLs.

D060 adds four bounded replace attempts, durable tmp hints, same-package run
lookup under the repository lock, refusal of ambiguity/incomplete evidence,
and recover-only operation. Persistent failure retains run/tmp and prints the
exact existing-run resume command. Never delete the target before replacement.

Actual recovery on Local Windows succeeded using `Run-Overnight.cmd recover`.
No new run/model/game work occurred. The original run and its expired deadline
were preserved. The new version-2 package is a separate unstarted configuration.
Regression tests are `tests/test_overnight_pointer.py` and
`tests/test_overnight_efficient.py`; final results live in the D060 handoff.
