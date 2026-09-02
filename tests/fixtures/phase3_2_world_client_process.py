"""LLM-free World State driver for the Phase 3.2 completion test."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
import json
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_client.network import (
    AbilityAction,
    ChatAction,
    ClientEvent,
    ClientExitReason,
    CoDeclareAction,
    FileCredentialStore,
    NetworkClient,
    NetworkClientConfig,
    ReconnectPolicy,
    SequenceGapDetected,
    SequenceGapRecovered,
    ServerEvent,
    StaleActionError,
    VoteAction,
)
from ai_client.world import Freshness, WorldState


_SEMANTIC_SNAPSHOT_FIELDS = (
    "players",
    "alive_player_ids",
    "deaths",
    "phase",
    "self_view",
    "revealed_roles",
    "history_retention",
    "unknown_event_count",
    "known_unmodeled_event_count",
    "malformed_event_count",
)


def _jsonable(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        result = {
            field.name: _jsonable(getattr(value, field.name))
            for field in fields(value)
        }
        result["record_type"] = type(value).__name__
        record_kind = getattr(value, "record_kind", None)
        if isinstance(record_kind, str):
            result["record_kind"] = record_kind
        return result
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, frozenset)):
        return [_jsonable(item) for item in value]
    return value


def semantic_snapshot(snapshot: object, history: object | None = None) -> dict[str, object]:
    """Serialize typed World State facts, excluding transport metadata."""

    result = {
        name: _jsonable(getattr(snapshot, name))
        for name in _SEMANTIC_SNAPSHOT_FIELDS
    }
    if history is not None:
        result["history"] = _jsonable(getattr(history, "records"))
    return result


class ObservedEventSource:
    """Expose the NetworkClient source while recording its single event stream."""

    def __init__(self, client: NetworkClient) -> None:
        self._client = client
        self.events_seen: list[ClientEvent] = []

    def snapshot(self):
        return self._client.snapshot()

    async def events(self):
        async for event in self._client.events():
            self.events_seen.append(event)
            yield event

    def recovery_observation(self) -> dict[str, object]:
        server_events = [
            event for event in self.events_seen if isinstance(event, ServerEvent)
        ]
        resumed = [event for event in server_events if event.type == "session.resumed"]
        replay_sequences: list[int] = []
        resume_checkpoints: list[int] = []
        for resume in resumed:
            checkpoint = resume.payload.get("last_seq")
            if not isinstance(checkpoint, int) or isinstance(checkpoint, bool):
                continue
            resume_checkpoints.append(checkpoint)
            replay_sequences.extend(
                event.seq
                for event in server_events
                if checkpoint < event.seq < resume.seq
            )
        gaps_detected = [
            {
                "expected_seq": event.expected_seq,
                "received_seq": event.received_seq,
                "connection_generation": event.connection_generation,
            }
            for event in self.events_seen
            if isinstance(event, SequenceGapDetected)
        ]
        gaps_recovered = [
            {
                "previous_seq": event.previous_seq,
                "recovered_seq": event.recovered_seq,
                "connection_generation": event.connection_generation,
            }
            for event in self.events_seen
            if isinstance(event, SequenceGapRecovered)
        ]
        return {
            "session_resumed_count": len(resumed),
            "resume_checkpoints": resume_checkpoints,
            "resume_event_sequences": [event.seq for event in resumed],
            "replay_event_sequences": replay_sequences,
            "state_sync_sequences": [
                event.seq for event in server_events if event.type == "game.state_sync"
            ],
            "gaps_detected": gaps_detected,
            "gaps_recovered": gaps_recovered,
        }


async def run_driver(
    uri: str,
    game_id: str,
    entry_token: str,
    credentials_path: Path,
    status_path: Path,
    ready_path: Path,
) -> int:
    client = NetworkClient(
        NetworkClientConfig(uri, game_id, entry_token),
        FileCredentialStore(credentials_path),
        reconnect_policy=ReconnectPolicy(max_disconnected_seconds=10.0),
    )
    source = ObservedEventSource(client)
    world = WorldState(source)
    client_task = asyncio.create_task(client.run())
    world_task = asyncio.create_task(world.run())
    sent: set[tuple[int, str]] = set()
    send_errors: list[str] = []
    ready_written = False

    try:
        while not world_task.done():
            if not ready_written and world.snapshot().players:
                ready_path.write_text("ready", encoding="utf-8")
                ready_written = True
            actions = world.current_actions()
            for action in actions.actions:
                key = (getattr(action, "action_generation", -1), action.type)
                if key in sent:
                    continue
                try:
                    if isinstance(action, CoDeclareAction) and action.claimed_role_ids:
                        await client.send_co_declare(action, action.claimed_role_ids[0], "I claim this role.")
                    elif isinstance(action, ChatAction):
                        await client.send_chat(action, "World State driver speaking.")
                    elif isinstance(action, VoteAction) and action.valid_targets:
                        await client.send_vote(action, action.valid_targets[0])
                    elif isinstance(action, AbilityAction):
                        if action.uses_remaining == 0 or len(action.valid_targets) < action.target_count:
                            continue
                        await client.send_ability(action, list(action.valid_targets[: action.target_count]))
                    else:
                        continue
                    sent.add(key)
                except StaleActionError:
                    continue
                except Exception as error:
                    send_errors.append(type(error).__name__)
            await asyncio.sleep(0.005)
        world_exit = await world_task
        client_exit = await client_task
        snapshot = world.snapshot()
        status_path.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "game_end": snapshot.freshness is Freshness.ENDED
                    and client_exit.reason is ClientExitReason.GAME_ENDED,
                    "freshness": snapshot.freshness.value,
                    "last_applied_seq": snapshot.last_applied_seq,
                    "players": len(snapshot.players),
                    "alive": len(snapshot.alive_player_ids),
                    "actions_seen": len(sent),
                    "history_records": snapshot.history_retention.retained_count,
                    "unknown_event_count": snapshot.unknown_event_count,
                    "known_unmodeled_event_count": snapshot.known_unmodeled_event_count,
                    "malformed_event_count": snapshot.malformed_event_count,
                    "semantic_world": semantic_snapshot(snapshot, world.history()),
                    "recovery": source.recovery_observation(),
                    "send_errors": send_errors,
                    "world_exit_reason": world_exit.reason.value,
                    "exit_reason": client_exit.reason.value,
                    "production_import_guard": not any(
                        name == "server.aiwolf_core"
                        or name.startswith("server.aiwolf_core.")
                        or name == "server.network"
                        or name.startswith("server.network.")
                        for name in sys.modules
                    ),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return 0 if client_exit.reason is ClientExitReason.GAME_ENDED else 1
    finally:
        for task in (world_task, client_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(world_task, client_task, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uri", required=True)
    parser.add_argument("--game-id", required=True)
    parser.add_argument("--entry-token", required=True)
    parser.add_argument("--credentials", required=True, type=Path)
    parser.add_argument("--status", required=True, type=Path)
    parser.add_argument("--ready", required=True, type=Path)
    arguments = parser.parse_args()
    raise SystemExit(
        asyncio.run(
            run_driver(
                arguments.uri,
                arguments.game_id,
                arguments.entry_token,
                arguments.credentials,
                arguments.status,
                arguments.ready,
            )
        )
    )


if __name__ == "__main__":
    main()
