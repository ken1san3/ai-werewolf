# D025 Core service boundaries and runtime capability validation

## Status

Accepted (2026-08-30) / Phase 1.5 review fix

## Context

Phase 1.5 introduced night resolution beside the existing phase, voting, and
death responsibilities. Keeping all of them in `GameState` would make later
winner evaluation and available-action work extend one large state class.

The content registry defines legal vocabulary, but some registered role
features are intentionally scheduled for later phases. A preset that selects
one of those features must not start and silently ignore it.

## Decision

- `GameState` holds authoritative mutable state and preserves the core API as
  thin forwarding methods. Focused services own flow mutation:
  `PhaseManager`, `VoteResolver`, `ActionResolver`, and `DeathResolver`.
- State records shared by those services live in `state.py`; player/target and
  effective-attribute helpers live in `targets.py`. Services depend on the
  state container, never on a network or AI layer.
- `capabilities.py` is the single core-side registry for effect, passive,
  selector, and restriction IDs implemented by the current build. Runtime
  resolution keeps its explicit errors as a second safety check.
- A full content pack can include future expansion roles. Therefore the loader
  validates implemented capabilities when `load_preset` selects the concrete
  role set used to start a game. A preset containing such a role fails at
  startup; unrelated future roles do not prevent the standard preset loading.

## Consequences

- Phase 1.6 winner evaluation can be introduced as a separate service rather
  than appended to `GameState`.
- Adding a role remains YAML-only when it uses capabilities implemented by the
  selected build; selecting an unimplemented role produces a deterministic
  startup validation error instead of a silent no-op.
- Phase 1 presets select roles only. If Phase 3 adds preset-selected Modifiers,
  its loader must pass their selected definitions to
  `unsupported_runtime_references(..., modifiers=...)` before game startup.

## Verification

- `tests/test_action_resolver.py` compares the complete public event sequences
  for a successful guard and an attack on a fox.
- `tests/test_content_models.py` verifies a baker preset fails during preset
  loading because its passive is not implemented.
