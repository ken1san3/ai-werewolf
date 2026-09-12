"""Server-grounded Phase 3.4 completion evidence helpers."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Mapping

from tests.fixtures.completion_evidence import (
    BOUNDARY_REJECTION_REASONS,
    DEFECT_REJECTION_REASONS,
)


@dataclass(frozen=True)
class AcceptedChatEvidence:
    player_id: str
    day: int
    phase: str
    channel: str
    accepted_at: int
    phase_deadline: int
    message: str


def accepted_histogram(
    records: Iterable[AcceptedChatEvidence],
) -> dict[tuple[str, int, str], int]:
    return dict(Counter((record.player_id, record.day, record.phase) for record in records))


def first_speaker_by_phase(
    records: Iterable[AcceptedChatEvidence],
) -> dict[tuple[int, str], str]:
    result: dict[tuple[int, str], str] = {}
    for record in records:
        result.setdefault((record.day, record.phase), record.player_id)
    return result


def validate_rejections(statuses: Mapping[str, Mapping[str, object]]) -> None:
    for player_id, status in statuses.items():
        reaction = status.get("reaction")
        if not isinstance(reaction, Mapping):
            raise AssertionError(f"{player_id}: missing reaction evidence")
        rejections = reaction.get("rejections")
        if not isinstance(rejections, list):
            raise AssertionError(f"{player_id}: invalid rejection evidence")
        for rejection in rejections:
            if not isinstance(rejection, Mapping):
                raise AssertionError(f"{player_id}: malformed rejection evidence")
            reason = rejection.get("reason")
            if reason in BOUNDARY_REJECTION_REASONS:
                continue
            if reason in DEFECT_REJECTION_REASONS:
                raise AssertionError(f"{player_id}: defect rejection {reason}")
            raise AssertionError(f"{player_id}: unknown rejection {reason}")
