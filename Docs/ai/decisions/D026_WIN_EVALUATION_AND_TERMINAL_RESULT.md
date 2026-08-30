# D026 Win evaluation and terminal result boundary

## Status

Accepted (2026-08-30) / Phase 1.6 implementation shape

## Context

DESIGN.md §8 requires content-driven victory evaluation immediately after all
Execution or Night deaths resolve. D025 prohibits adding that flow to
`GameState`.

## Decision

- `WinEvaluator` evaluates team conditions in `rules.win_evaluation_order`.
  `eliminate_role_tag` reads living Role tags; `count_parity` and final player
  results use Role + Modifier effective attributes.
- If no player is alive, it returns `draw` before evaluating any team. Every
  player result is `lost` for a draw.
- A living player with `survive_when_others_win` is applied after a primary
  team wins. `replaces: true` replaces the primary team. With `replaces: false`,
  the primary `winner_team` remains unchanged and the surviving player gets an
  individual `won` result. This preserves the current single-team result model;
  Phase 8 may expand `winner_team` when it introduces multiple winner teams.
- `VoteResolver` invokes the evaluator after Execution death resolution;
  `PhaseManager` invokes it after Night death resolution. The first terminal
  result is saved on `GameState`, emitted once as a public `GAME_ENDED` event,
  then transitions to `GameEnd`.

## Consequences

- `GameState` holds `game_result` but does not contain evaluation logic.
- No role ID is a win-evaluation branch; team YAML, role tags, and effective
  attributes supply all game-specific inputs.

## Verification

`tests/test_win_evaluator.py` covers parity, tag elimination, evaluation order,
fox replacement, draw precedence, cat death chains, and both Execution and
Night terminal paths.
