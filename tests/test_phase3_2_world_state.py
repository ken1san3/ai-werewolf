from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError

import pytest

from ai_client.network import ClientLifecycle, ClientSnapshot, ServerEvent
from ai_client.network.types import immutable_mapping
from ai_client.world import (
    AbilityResultRecord,
    ChatRecord,
    CoDeclarationRecord,
    CoReportRecord,
    CurrentActionsView,
    DeathRecord,
    Freshness,
    GameLifecycleRecord,
    HistoryQuery,
    MalformedEventRecord,
    PhaseTimingChangedRecord,
    PhaseTransitionRecord,
    TieResolvedRandomRecord,
    UnknownEventRecord,
    VoteRevealRecord,
    VoteResultRecord,
    WorldState,
    WorldStateConfig,
)


def event(message_type: str, seq: int, payload: dict) -> ServerEvent:
    return ServerEvent(
        type=message_type,
        protocol_version="1.0",
        event_id=f"event-{seq}",
        game_id="game-1",
        seq=seq,
        timestamp=seq,
        payload=immutable_mapping(payload),
    )


def sync_payload() -> dict:
    return {
        "players": [
            {"player_id": "p0", "display_name": "Alice"},
            {"player_id": "p1", "display_name": "Bob"},
        ],
        "deaths": [{"player_id": "p1", "day": 1, "public_cause": "died_in_night"}],
        "action_state": {
            "phase": "day",
            "day": 1,
            "phase_ends_at": 120,
            "actions": [{"type": "chat", "channel": "public"}],
        },
        "self": {"player_id": "p0", "role_id": "seer", "modifier_ids": ["mad"]},
        "revealed_roles": [],
        "history": [
            {
                "type": "game.event",
                "payload": {
                    "event_type": "PHASE_STARTED",
                    "event_payload": {"phase": "day", "day": 1, "phase_ends_at": 120},
                },
            },
            {
                "type": "chat.message",
                "payload": {
                    "channel": "public",
                    "message": {"player_id": "p0", "display_name": "Alice", "message": "hello"},
                },
            },
        ],
    }


class ListSource:
    def __init__(self, events: list[object], *, last_seq: int, lifecycle: ClientLifecycle) -> None:
        self._events = events
        self._snapshot = ClientSnapshot(lifecycle=lifecycle, last_seq=last_seq)

    def snapshot(self) -> ClientSnapshot:
        return self._snapshot

    async def events(self):
        for item in self._events:
            await asyncio.sleep(0)
            yield item


def async_test(function):
    def wrapper(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return wrapper


@async_test
async def test_sync_builds_current_view_and_history_without_raw_payloads() -> None:
    source = ListSource([event("game.state_sync", 1, sync_payload())], last_seq=1, lifecycle=ClientLifecycle.ENDED)
    world = WorldState(source)
    exit_state = await world.run()

    snapshot = world.snapshot()
    assert exit_state.freshness is Freshness.ENDED
    assert snapshot.freshness is Freshness.ENDED
    assert snapshot.players[0].alive is True
    assert snapshot.players[1].alive is False
    assert snapshot.alive_player_ids == ("p0",)
    assert snapshot.deaths[0].public_cause == "died_in_night"
    assert snapshot.phase is not None and snapshot.phase.day == 1
    assert snapshot.self_view is not None and snapshot.self_view.role_id == "seer"
    assert [type(record) for record in world.history().records] == [PhaseTransitionRecord, ChatRecord]
    assert not hasattr(world.history().records[0], "payload")


@async_test
async def test_live_events_are_typed_and_queries_are_indexed() -> None:
    events = [
        event("game.state_sync", 1, sync_payload()),
        event("game.event", 2, {"event_type": "CO_DECLARED", "event_payload": {"player_id": "p0", "claimed_role_id": "villager", "comment": "claim"}}),
        event("game.event", 3, {"event_type": "CO_REPORTED", "event_payload": {"player_id": "p1", "kind": "inspect", "target_player_id": "p0", "claimed_result": "wolf"}}),
        event("game.event", 4, {"event_type": "VOTE_RESOLVED", "event_payload": {"day": 1, "phase": "vote", "result": "lynch", "tallies": {"p0": 1}, "lynched_player_id": "p0", "runoff_candidate_player_ids": []}}),
        event("game.event", 5, {"event_type": "VOTES_REVEALED_AFTER", "event_payload": {"day": 1, "phase": "vote", "votes": [{"voter_player_id": "p1", "target_player_id": "p0"}]}}),
        event("game.event", 6, {"event_type": "TIE_RESOLVED_RANDOM", "event_payload": {"day": 1, "phase": "vote", "candidate_player_ids": ["p0", "p1"], "selected_player_id": "p0"}}),
        event("game.event", 7, {"event_type": "DAY_EXTENDED", "event_payload": {"day": 1, "phase": "day", "phase_ends_at": 180, "extensions_used": 1}}),
        event("game.event", 8, {"event_type": "INSPECT_RESULT", "event_payload": {"target_player_id": "p1", "result": "not_wolf"}}),
        event("game.event", 9, {"event_type": "PUBLIC_NOTIFY", "event_payload": {"notify_id": "dawn_notice"}}),
        event("game.event", 10, {"event_type": "GAME_ENDED", "event_payload": {"winner_team": "village", "outcome": "team_victory", "player_results": {"p0": "won"}}}),
    ]
    source = ListSource(events, last_seq=10, lifecycle=ClientLifecycle.CONNECTED)
    world = WorldState(source)
    await world.run()

    records = world.history().records
    assert isinstance(records[2], CoDeclarationRecord)
    assert isinstance(records[3], CoReportRecord)
    assert isinstance(records[4], VoteResultRecord)
    assert isinstance(records[5], VoteRevealRecord)
    assert isinstance(records[6], TieResolvedRandomRecord)
    assert isinstance(records[7], PhaseTimingChangedRecord)
    assert isinstance(records[8], AbilityResultRecord)
    assert isinstance(records[10], GameLifecycleRecord)
    assert world.snapshot().unknown_event_count == 0
    assert world.snapshot().malformed_event_count == 0
    assert world.snapshot().phase is not None and world.snapshot().phase.phase_ends_at == 180
    assert len(world.co_for_day(1).declarations) == 1
    assert len(world.ability_results().results) == 1


@async_test
async def test_unknown_and_malformed_events_are_nonfatal_and_do_not_mutate_view() -> None:
    events = [
        event("game.state_sync", 1, sync_payload()),
        event("game.event", 2, {"event_type": "FUTURE_EVENT", "event_payload": {"secret": "must-not-leak"}}),
        event("game.event", 3, {"event_type": "CO_DECLARED", "event_payload": {"player_id": "p0"}}),
        event("unknown.future", 4, {}),
        event("game.event", 5, {"event_type": "GAME_ENDED", "event_payload": {"winner_team": "village", "outcome": "done", "player_results": {}}}),
    ]
    world = WorldState(ListSource(events, last_seq=5, lifecycle=ClientLifecycle.CONNECTED))
    await world.run()

    snapshot = world.snapshot()
    assert snapshot.freshness is Freshness.ENDED
    assert snapshot.unknown_event_count == 2
    assert snapshot.malformed_event_count == 1
    assert any(isinstance(record, UnknownEventRecord) for record in world.history().records)
    assert any(isinstance(record, MalformedEventRecord) for record in world.history().records)
    assert all("secret" not in repr(record) for record in world.history().records)


@async_test
async def test_current_actions_are_exposed_only_when_network_is_caught_up() -> None:
    source = ListSource([event("game.state_sync", 1, sync_payload())], last_seq=1, lifecycle=ClientLifecycle.CONNECTED)
    world = WorldState(source)
    await world.run()
    actions = world.current_actions()
    assert isinstance(actions, CurrentActionsView)
    assert actions.is_caught_up is True
    assert actions.actions == ()


@async_test
async def test_sync_history_uses_preceding_phase_context_and_keeps_final_action_state() -> None:
    payload = sync_payload()
    payload["action_state"] = {
        "phase": "night",
        "day": 2,
        "phase_ends_at": 240,
        "actions": [],
    }
    payload["history"] = [
        {
            "type": "chat.message",
            "payload": {"channel": "public", "message": {"message": "before phase"}},
        },
        {
            "type": "game.event",
            "payload": {
                "event_type": "PHASE_STARTED",
                "event_payload": {"phase": "day", "day": 1, "phase_ends_at": 120},
            },
        },
        {
            "type": "chat.message",
            "payload": {"channel": "public", "message": {"message": "after phase"}},
        },
    ]
    world = WorldState(ListSource([event("game.state_sync", 1, payload)], last_seq=1, lifecycle=ClientLifecycle.ENDED))
    await world.run()

    chats = tuple(record for record in world.history().records if isinstance(record, ChatRecord))
    assert chats[0].day is None and chats[0].phase is None
    assert chats[1].day == 1 and chats[1].phase == "day"
    assert world.snapshot().phase is not None
    assert (world.snapshot().phase.day, world.snapshot().phase.phase) == (2, "night")


def test_public_models_are_deeply_immutable() -> None:
    from ai_client.world import PhaseView, PlayerView, WorldSnapshot

    player = PlayerView("p0", "Alice")
    snapshot = WorldSnapshot(players=(player,), phase=PhaseView("day", 1))
    with pytest.raises(FrozenInstanceError):
        snapshot.version = 3  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        player.alive = False  # type: ignore[misc]


def test_history_retention_is_global_oldest_first_and_reports_loss() -> None:
    from ai_client.world.memory import HistoryStore

    store = HistoryStore(max_records=2, max_bytes=100_000)
    store.append(ChatRecord(1, 1, "day", "public", "p0", "A", "one"))
    store.append(ChatRecord(2, 1, "day", "public", "p0", "A", "two"))
    store.append(ChatRecord(3, 1, "day", "public", "p0", "A", "three"))
    retention = store.retention()
    assert [record.order for record in store.records] == [2, 3]
    assert retention.dropped_count == 1
    assert retention.dropped_through_order == 1
    assert store.query(HistoryQuery(after_order=1))[0].order == 2
