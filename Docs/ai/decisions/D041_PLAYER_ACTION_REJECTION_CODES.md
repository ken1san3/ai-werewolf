# D041 Player Action Rejection Codes

## Status

Accepted (2026-08-31) / R-20260831-56 review fix

## Context

`action.rejected` already has a machine-readable `reason`, but only chat and CO emitted
specific values. Vote and ability validation raised ordinary `ValueError`, so the network
boundary collapsed unrelated causes to `invalid_action`. AI clients could not change the
request that caused the rejection.

## Decision

The game core raises `ActionRejected(reason, detail)` for player-correctable action failures.
`reason` is the only value sent on the wire; `detail` remains local for direct core callers and
tests. The network boundary neither parses details nor decides action legality.

The initial reason vocabulary is:

- `unknown_ability`, `action_unavailable`, `action_deadline_passed`, `action_closed`,
  `ability_uses_exhausted`, `invalid_target`, `actor_unavailable`
- `vote_unavailable`, `self_vote_disabled`, `unknown_target`, `abstention_disabled`,
  `abstention_limit_reached`
- the existing chat / CO codes such as `action_unavailable`, `co_limit_reached`, and
  `unknown_claimed_role`

Future player-correctable rejection reasons are added at this core boundary with an
accompanying test; internal exceptions remain server errors and are not exposed as details.

## Consequences

- `SessionManager` sends the core-provided code without a message-string mapping.
- Existing direct core callers can continue to catch `ValueError`, because `ActionRejected`
  is its subclass.
- The protocol schema remains a non-empty string for forward-compatible reason codes; the
  vocabulary is owned here rather than duplicated in the wire schema.

## Verification

`tests/test_network_sessions.py` verifies that self-vote, an unknown vote target, and an
unknown ability produce distinct reason codes and no local detail in the outbound payload.
