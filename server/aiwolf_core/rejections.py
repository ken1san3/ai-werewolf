"""Safe, machine-readable player-action rejection reasons."""

from __future__ import annotations


PLAYER_ACTION_REJECTION_REASONS = frozenset(
    {
        "ability_uses_exhausted",
        "abstention_disabled",
        "abstention_limit_reached",
        "action_closed",
        "action_deadline_passed",
        "action_unavailable",
        "actor_unavailable",
        "claim_not_allowed",
        "co_limit_reached",
        "invalid_claimed_result",
        "invalid_comment",
        "invalid_message",
        "invalid_report_kind",
        "invalid_target",
        "self_vote_disabled",
        "unknown_ability",
        "unknown_claimed_role",
        "unknown_target",
        "unsupported_action",
        "vote_unavailable",
    }
)


class ActionRejected(ValueError):
    """A core rejection that preserves a local detail but exposes only ``reason``."""

    def __init__(self, reason: str, detail: str | None = None) -> None:
        if not reason:
            raise ValueError("rejection reason must not be empty")
        if reason not in PLAYER_ACTION_REJECTION_REASONS:
            raise ValueError(f"unregistered rejection reason: {reason}")
        super().__init__(detail if detail is not None else reason)
        self.reason = reason
