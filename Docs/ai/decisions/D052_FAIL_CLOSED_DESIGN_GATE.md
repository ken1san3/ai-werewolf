# D052 Fail-Closed Design Gate Resolution

## Status

Accepted (Implementer, 2026-09-01)

## Context

The first Design Gate implementation scanned every request containing
`DESIGN: REQUIRED`, interpreted free-form `Status:` prose, and treated a missing
request as `NOT REQUIRED`. That could expose an approved-looking result for the
wrong subphase or for an invalid status.

## Decision

The implementation target is declared by one `Target subphase: N.M` line in
`Docs/ai/CURRENT_STATE.md`. The gate selects exactly one matching
`*_REQUEST.md` whose body contains the standalone `DESIGN: REQUIRED` marker.
Its paired `*_DESIGN.md` is the only file whose `Status:` controls
implementation eligibility.

`Status:` accepts only `REQUESTED`, `DRAFT`, `IN_REVIEW`, `APPROVED`, or
`SUPERSEDED`, optionally followed by ` — note`. Only the exact `APPROVED`
value is allowed to pass. Missing files, missing targets, duplicate or missing
requests, and invalid status values produce `UNKNOWN` and keep implementation
blocked.

## Consequences

- A request cannot accidentally approve itself.
- An approved design for another subphase is not shown as the current gate.
- Missing or malformed declarations stop the implementer instead of silently
  allowing work to proceed.
- `check_docs.py` validates the target, the required request, and the Status
  vocabulary before a session uses the documents.
