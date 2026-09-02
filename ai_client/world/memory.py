"""Bounded chronological history storage for World State."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
import json
from typing import Any

from .model import (
    HistoryQuery,
    HistoryRecord,
    HistoryRetention,
)


def _record_kind(record: HistoryRecord) -> str:
    return getattr(record, "record_kind", type(record).__name__)


def _json_value(value: Any) -> Any:
    if is_dataclass(value):
        result = {
            item.name: _json_value(getattr(value, item.name))
            for item in fields(value)
        }
        record_kind = getattr(value, "record_kind", None)
        if isinstance(record_kind, str):
            result["kind"] = record_kind
        return result
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if hasattr(value, "items"):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, frozenset)):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def logical_record_bytes(record: HistoryRecord) -> int:
    encoded = json.dumps(
        _json_value(record),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return len(encoded)


def _record_player_ids(record: HistoryRecord) -> frozenset[str]:
    values: list[str] = []
    for name in (
        "player_id",
        "target_player_id",
        "voter_player_id",
        "selected_player_id",
        "lynched_player_id",
    ):
        value = getattr(record, name, None)
        if isinstance(value, str):
            values.append(value)
    values.extend(
        value for value in getattr(record, "candidate_player_ids", ()) if isinstance(value, str)
    )
    values.extend(
        value
        for value in getattr(record, "runoff_candidate_player_ids", ())
        if isinstance(value, str)
    )
    tallies = getattr(record, "tallies", {})
    if hasattr(tallies, "keys"):
        values.extend(value for value in tallies.keys() if isinstance(value, str))
    player_results = getattr(record, "player_results", {})
    if hasattr(player_results, "keys"):
        values.extend(value for value in player_results.keys() if isinstance(value, str))
    values.extend(
        player.player_id
        for player in getattr(record, "players", ())
        if isinstance(getattr(player, "player_id", None), str)
    )
    for entry in getattr(record, "final_votes", ()):
        for name in ("voter_player_id", "target_player_id"):
            value = getattr(entry, name, None)
            if isinstance(value, str):
                values.append(value)
    return frozenset(values)


def _kind_matches(record_kind: str, requested: str) -> bool:
    aliases = {
        "ChatRecord": "chat",
        "CoDeclarationRecord": "co_declaration",
        "CoReportRecord": "co_report",
        "VoteResultRecord": "vote_result",
        "VoteRevealRecord": "vote_reveal",
        "DeathRecord": "death",
        "PhaseTransitionRecord": "phase_transition",
        "PhaseTimingChangedRecord": "phase_timing_changed",
        "AbilityResultRecord": "ability_result",
        "GameLifecycleRecord": "game_lifecycle",
        "PublicNotifyRecord": "public_notify",
        "TieResolvedRandomRecord": "tie_resolved_random",
        "KnownUnmodeledEventRecord": "known_unmodeled",
        "UnknownEventRecord": "unknown",
        "MalformedEventRecord": "malformed",
    }
    return record_kind == aliases.get(requested, requested)


class HistoryStore:
    """Single-writer bounded record window with query-time filtering."""

    def __init__(self, max_records: int, max_bytes: int) -> None:
        self.max_records = max_records
        self.max_bytes = max_bytes
        self._records: list[HistoryRecord] = []
        self._sizes: list[int] = []
        self._bytes = 0
        self._total_seen = 0
        self._dropped_count = 0
        self._dropped_through_order: int | None = None

    def reset(self) -> None:
        self._records.clear()
        self._sizes.clear()
        self._bytes = 0
        self._total_seen = 0
        self._dropped_count = 0
        self._dropped_through_order = None

    def append(self, record: HistoryRecord) -> None:
        size = logical_record_bytes(record)
        self._total_seen += 1
        if size > self.max_bytes:
            while self._records:
                self._evict_oldest()
            self._dropped_count += 1
            self._dropped_through_order = record.order
            return
        self._records.append(record)
        self._sizes.append(size)
        self._bytes += size
        while len(self._records) > self.max_records or self._bytes > self.max_bytes:
            self._evict_oldest()

    def _evict_oldest(self) -> None:
        if not self._records:
            return
        record = self._records.pop(0)
        self._bytes -= self._sizes.pop(0)
        self._dropped_count += 1
        self._dropped_through_order = record.order

    @property
    def records(self) -> tuple[HistoryRecord, ...]:
        return tuple(self._records)

    def retention(self) -> HistoryRetention:
        first = self._records[0].order if self._records else None
        last = self._records[-1].order if self._records else self._dropped_through_order
        return HistoryRetention(
            total_seen=self._total_seen,
            retained_count=len(self._records),
            retained_bytes=self._bytes,
            dropped_count=self._dropped_count,
            dropped_through_order=self._dropped_through_order,
            first_retained_order=first,
            last_order=last,
            max_history_records=self.max_records,
            max_history_bytes=self.max_bytes,
            complete=self._dropped_count == 0,
        )

    def query(self, query: HistoryQuery = HistoryQuery()) -> tuple[HistoryRecord, ...]:
        records: list[HistoryRecord] = []
        for record in self._records:
            if query.kinds and not any(_kind_matches(_record_kind(record), kind) for kind in query.kinds):
                continue
            record_day = getattr(record, "day", None)
            if query.day is not None and record_day != query.day:
                continue
            if query.player_id is not None and query.player_id not in _record_player_ids(record):
                continue
            if query.after_order is not None and record.order <= query.after_order:
                continue
            records.append(record)
            if query.limit is not None and len(records) >= query.limit:
                break
        return tuple(records)
