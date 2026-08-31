# D042 Public Activity Counter

## Status

Accepted (2026-08-31)

## Context

Sudden death depends on whether a living player performed a public operation during the day.
Keeping one counter per operation made the resolution code enumerate operation types and coupled
the CO declaration quota to an unrelated death rule.

## Decision

`GameState.public_activity_counts[(day, player_id)]` is the sole state used by sudden-death
resolution. `PlayerInteractions._record_public_activity` is the single mutation helper for
public operations. `co_declaration_counts` remains only for `rules.co.max_per_day`.

## Consequences

- Adding a public operation updates its receipt path but does not require a sudden-death change.
- Tests exercise chat, CO declaration, and CO report independently, so removing any one receipt
  call makes its corresponding sudden-death assertion fail.
