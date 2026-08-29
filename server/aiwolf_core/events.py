"""In-process game events and visibility-routed JSONL logging."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping


class EventVisibility(str, Enum):
    """The sole visibility source used for event subscribers and log routing."""

    PUBLIC = "public"
    PRIVATE = "private"
    AI = "ai"


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
        if self.recipient_player_id is not None and self.visibility is not EventVisibility.PRIVATE:
            raise ValueError("only private events may name a recipient player")


EventSubscriber = Callable[[GameEvent], None]


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
        recorded = replace(event, payload=dict(event.payload), sequence=self._next_sequence)
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
            "payload": event.payload,
        }
        if event.recipient_player_id is not None:
            entry["recipient_player_id"] = event.recipient_player_id
        with self._paths[event.visibility].open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True))
            stream.write("\n")
