"""Server-authoritative Phase 3.5 completion evidence values."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ExpectedReservation:
    player_id: str
    day: int
    phase: str
    action: str
    ability_id: str | None
    valid_targets: tuple[str, ...]
    target_count: int
    uses_remaining: int | None


@dataclass(frozen=True)
class AcceptedReservation:
    player_id: str
    day: int
    phase: str
    action: str
    ability_id: str | None
    vote_target_player_id: str | None
    ability_target_player_ids: tuple[str, ...]
    request_event_id: str
    accepted_at: int
    phase_deadline: int


def reservation_histogram(
    records: Iterable[AcceptedReservation],
) -> dict[tuple[str, int, str, str], int]:
    return dict(
        Counter(
            (record.player_id, record.day, record.phase, record.action)
            for record in records
        )
    )


def decision_sequence(records: Iterable[AcceptedReservation]) -> tuple[tuple, ...]:
    return tuple(
        (
            record.player_id,
            record.day,
            record.phase,
            record.action,
            record.ability_id,
            record.vote_target_player_id,
            record.ability_target_player_ids,
        )
        for record in records
    )
