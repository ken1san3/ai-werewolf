# Python TemporaryDirectory access failure during Integrator checks

Date: 2026-09-10
Environment: local Windows, Python 3.13, Codex filesystem sandbox.

`python -m pytest -q tests/test_ai_status.py` produced 4 failed / 20 passed in 0.42s.
The failures were WinError 5 in TemporaryDirectory child creation and cleanup, before
the affected assertions. Repeating with TEMP/TMP under
`C:\AIwolf\.tmp\integrator-t001` produced the same 4 failed / 20 passed in 0.41s.

The identical test command outside the sandbox passed: exit 0, 24 passed in 0.09s.
This isolates the observed failure to execution context; the underlying Windows/sandbox
permission mechanism was not investigated. No application or test repair was made.

For a recurrence, distinguish filesystem-access failures from assertion failures and
use an approved execution context before diagnosing a game regression.
