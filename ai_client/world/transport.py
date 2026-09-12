"""Bounded storage for transport facts kept separate from semantic history."""

from __future__ import annotations

from .memory import logical_record_bytes
from .model import (
    TransportObservation,
    TransportObservationKind,
    TransportObservationQuery,
    TransportObservationRetention,
)


class TransportObservationStore:
    """Single-writer oldest-first window with deterministic byte accounting."""

    def __init__(self, retention: TransportObservationRetention) -> None:
        self.retention = retention
        self._records: list[TransportObservation] = []
        self._sizes: list[int] = []
        self._bytes = 0
        self._last_order: int | None = None
        self._dropped_through_order: int | None = None

    def append(self, record: TransportObservation) -> None:
        size = logical_record_bytes(record)
        self._records.append(record)
        self._sizes.append(size)
        self._bytes += size
        self._last_order = record.order
        while len(self._records) > 1 and (
            len(self._records) > self.retention.max_records
            or self._bytes > self.retention.max_bytes
        ):
            self._evict_oldest()

    def _evict_oldest(self) -> None:
        record = self._records.pop(0)
        self._bytes -= self._sizes.pop(0)
        self._dropped_through_order = record.order

    @property
    def first_retained_order(self) -> int | None:
        return self._records[0].order if self._records else None

    @property
    def last_order(self) -> int | None:
        return self._last_order

    def query(
        self, query: TransportObservationQuery
    ) -> tuple[TransportObservation, ...]:
        return tuple(
            record
            for record in self._records
            if (query.after_order is None or record.order > query.after_order)
            and (query.kinds is None or record.kind in query.kinds)
        )

    def gap_before_first(self, after_order: int | None) -> bool:
        return (
            after_order is not None
            and self._dropped_through_order is not None
            and after_order < self._dropped_through_order
        )
