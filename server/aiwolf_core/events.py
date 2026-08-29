"""In-process game events and visibility-routed JSONL logging."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol


class EventVisibility(str, Enum):
    """The sole visibility source used for event subscribers and log routing."""

    PUBLIC = "public"
    PRIVATE = "private"
    AI = "ai"
    SERVER = "server"


@dataclass(frozen=True)
class GameEvent:
    """An immutable game event recorded in sequence order."""

    type: str
    visibility: EventVisibility
    payload: Mapping[str, Any]
    recipient_player_id: str | None = None
    sequence: int | None = None

    def __post_init__(self) -> None:
        if not self.type:
            raise ValueError("event type must not be empty")
        if self.visibility is EventVisibility.PRIVATE and self.recipient_player_id is None:
            raise ValueError("private events require a recipient player")
        if self.visibility is not EventVisibility.PRIVATE and self.recipient_player_id is not None:
            raise ValueError("only private events may name a recipient player")


EventSubscriber = Callable[[GameEvent], None]


class EventSink(Protocol):
    """A destination that records events routed by the EventBus."""

    def record(self, event: GameEvent) -> None:
        ...


class InMemoryEventSink:
    """An event sink for game-core tests that must not touch the filesystem."""

    def __init__(self) -> None:
        self.events: list[GameEvent] = []

    def record(self, event: GameEvent) -> None:
        self.events.append(event)


class EventBus:
    """Publish events only to subscribers for their declared visibility."""

    def __init__(self) -> None:
        self._subscribers: dict[EventVisibility, list[EventSubscriber]] = {
            visibility: [] for visibility in EventVisibility
        }
        self._events: list[GameEvent] = []
        self._next_sequence = 1

    @property
    def events(self) -> tuple[GameEvent, ...]:
        """The complete server-side event history, including private events."""

        return tuple(self._events)

    def subscribe(self, visibility: EventVisibility, subscriber: EventSubscriber) -> None:
        self._subscribers[visibility].append(subscriber)

    def publish(self, event: GameEvent) -> GameEvent:
        if event.sequence is not None:
            raise ValueError("EventBus assigns event sequences")
        recorded = replace(event, payload=deepcopy(dict(event.payload)), sequence=self._next_sequence)
        self._next_sequence += 1
        self._events.append(recorded)
        for subscriber in tuple(self._subscribers[recorded.visibility]):
            subscriber(recorded)
        return recorded


class JsonlEventLog:
    """Append events to the file selected by the event's own visibility."""

    def __init__(self, logs_root: str | Path, game_id: str) -> None:
        self.directory = Path(logs_root) / game_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self._paths = {
            EventVisibility.PUBLIC: self.directory / "public.jsonl",
            EventVisibility.PRIVATE: self.directory / "private.jsonl",
            EventVisibility.AI: self.directory / "ai.jsonl",
            EventVisibility.SERVER: self.directory / "private.jsonl",
        }
        for path in self._paths.values():
            path.touch(exist_ok=True)

    @property
    def paths(self) -> Mapping[EventVisibility, Path]:
        return self._paths

    def record(self, event: GameEvent) -> None:
        entry: dict[str, Any] = {
            "sequence": event.sequence,
            "type": event.type,
            "visibility": event.visibility.value,
            "payload": event.payload,
        }
        if event.recipient_player_id is not None:
            entry["recipient_player_id"] = event.recipient_player_id
        with self._paths[event.visibility].open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True))
            stream.write("\n")
