# D052 Fail-Closed Design Gate Resolution

## Status

Accepted (Implementer, 2026-09-01)

## Context

The first Design Gate implementation scanned every request containing
`DESIGN: REQUIRED`, interpreted free-form `Status:` prose, and treated a missing
request as `NOT REQUIRED`. That could expose an approved-looking result for the
wrong subphase or for an invalid status.

## Decision

The implementation target is declared by one `Target subphase: N.M` line and
one `Design gate: REQUIRED` or `Design gate: NOT REQUIRED` line in
`Docs/ai/CURRENT_STATE.md`. This does not narrow D051's two-value decision:
`NOT REQUIRED` is a normal result and has no request document. For
`REQUIRED`, the gate selects exactly one matching `*_REQUEST.md` whose body
contains the standalone `DESIGN: REQUIRED` marker. Its paired `*_DESIGN.md`
is the only file whose `Status:` controls implementation eligibility.

`Status:` accepts only `REQUESTED`, `DRAFT`, `IN_REVIEW`, `APPROVED`, or
`SUPERSEDED`, optionally followed by ` — note`. Only the exact `APPROVED`
value is allowed to pass. A `NOT REQUIRED` declaration without a matching
request allows implementation. Missing or malformed declarations, missing
targets, duplicate or missing requests for `REQUIRED`, a request paired with
`NOT REQUIRED`, and invalid status values produce `UNKNOWN` and keep
implementation blocked.

## Consequences

- A request cannot accidentally approve itself.
- An approved design for another subphase is not shown as the current gate.
- Missing or malformed declarations stop the implementer instead of silently
  allowing work to proceed.
- `check_docs.py` validates the target, the gate declaration and its request
  relationship, and the Status vocabulary before a session uses the documents.
