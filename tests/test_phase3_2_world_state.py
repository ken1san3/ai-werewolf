from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError

import pytest

from ai_client.network import (
    ClientLifecycle,
    ClientExitReason,
    ClientSnapshot,
    LifecycleChanged,
    NetworkClient,
    NetworkClientConfig,
    SequenceGapDetected,
    ServerEvent,
)
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
    KnownUnmodeledEventRecord,
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


class BlockingSource:
    def __init__(self) -> None:
        self._snapshot = ClientSnapshot(lifecycle=ClientLifecycle.CONNECTED)
        self.events_started = asyncio.Event()

    def snapshot(self) -> ClientSnapshot:
        return self._snapshot

    async def events(self):
        self.events_started.set()
        await asyncio.Future()
        yield None  # pragma: no cover


class NoopCredentialStore:
    async def load(self):
        return None

    async def save(self, checkpoint) -> None:
        del checkpoint


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
    from ai_client.world.memory import HistoryStore, logical_record_bytes

    store = HistoryStore(max_records=2, max_bytes=100_000)
    store.append(ChatRecord(1, 1, "day", "public", "p0", "A", "one"))
    store.append(ChatRecord(2, 1, "day", "public", "p0", "A", "two"))
    store.append(ChatRecord(3, 1, "day", "public", "p0", "A", "three"))
    retention = store.retention()
    assert [record.order for record in store.records] == [2, 3]
    assert retention.dropped_count == 1
    assert retention.dropped_through_order == 1
    assert store.query(HistoryQuery(after_order=1))[0].order == 2

    byte_limited = HistoryStore(
        max_records=3,
        max_bytes=logical_record_bytes(ChatRecord(1, 1, "day", "public", "p0", "A", "one")) * 2 - 1,
    )
    byte_limited.append(ChatRecord(1, 1, "day", "public", "p0", "A", "one"))
    byte_limited.append(ChatRecord(2, 1, "day", "public", "p0", "A", "two"))
    assert [record.order for record in byte_limited.records] == [2]
    assert byte_limited.retention().dropped_through_order == 1


@async_test
async def test_initial_lifecycle_stays_empty_until_sync_then_recovers_from_stale() -> None:
    source = ListSource([], last_seq=0, lifecycle=ClientLifecycle.NEW)
    world = WorldState(source)

    world._consume(LifecycleChanged(ClientLifecycle.NEW, ClientLifecycle.JOINING))
    world._consume(LifecycleChanged(ClientLifecycle.JOINING, ClientLifecycle.SYNCHRONIZING))
    world._consume(SequenceGapDetected(1, 2, 1))
    assert world.snapshot().freshness is Freshness.EMPTY

    source._snapshot = ClientSnapshot(lifecycle=ClientLifecycle.CONNECTED, last_seq=1)
    world._consume(event("game.state_sync", 1, sync_payload()))
    assert world.snapshot().freshness is Freshness.CURRENT

    world._consume(LifecycleChanged(ClientLifecycle.CONNECTED, ClientLifecycle.RECONNECT_WAIT))
    assert world.snapshot().freshness is Freshness.STALE
    source._snapshot = ClientSnapshot(lifecycle=ClientLifecycle.CONNECTED, last_seq=2)
    world._consume(event("game.state_sync", 2, sync_payload()))
    assert world.snapshot().freshness is Freshness.CURRENT


@async_test
async def test_history_player_filters_and_co_view_keep_global_order() -> None:
    source = ListSource([], last_seq=6, lifecycle=ClientLifecycle.CONNECTED)
    world = WorldState(source)
    events = (
        event("game.state_sync", 1, sync_payload()),
        event("game.event", 2, {"event_type": "CO_DECLARED", "event_payload": {"player_id": "p0", "claimed_role_id": "villager", "comment": "first"}}),
        event("game.event", 3, {"event_type": "CO_REPORTED", "event_payload": {"player_id": "p1", "kind": "inspect", "target_player_id": "p0", "claimed_result": "wolf"}}),
        event("game.event", 4, {"event_type": "CO_DECLARED", "event_payload": {"player_id": "p1", "claimed_role_id": "seer", "comment": "second"}}),
        event("game.event", 5, {"event_type": "VOTE_RESOLVED", "event_payload": {"day": 1, "phase": "vote", "result": "runoff", "tallies": {"p0": 2}, "lynched_player_id": "p1", "runoff_candidate_player_ids": ["p2"]}}),
    )
    for received in events:
        world._consume(received)

    assert [record.order for record in world.co_for_day(1).records] == [3, 4, 5]
    for player_id in ("p0", "p1", "p2"):
        records = world.history(HistoryQuery(player_id=player_id)).records
        assert any(isinstance(record, VoteResultRecord) for record in records)


@async_test
async def test_oversized_record_creates_a_consistent_suffix_window() -> None:
    source = ListSource([], last_seq=0, lifecycle=ClientLifecycle.CONNECTED)
    world = WorldState(source, config=WorldStateConfig(max_history_records=4, max_history_bytes=180))
    memory = world._reducer.memory
    memory.append(ChatRecord(1, 1, "day", "public", "p0", "A", "small"))
    memory.append(ChatRecord(2, 1, "day", "public", "p0", "A", "x" * 500))
    memory.append(ChatRecord(3, 1, "day", "public", "p0", "A", "later"))

    retention = memory.retention()
    assert [record.order for record in memory.records] == [3]
    assert retention.dropped_count == 2
    assert retention.dropped_through_order == 2
    assert retention.first_retained_order == 3
    assert retention.last_order == 3
    assert world.history().complete is False
    assert world.history(HistoryQuery(after_order=2)).complete is True


@async_test
async def test_timing_event_requires_nullable_deadline_without_mutating_current_view() -> None:
    source = ListSource([], last_seq=5, lifecycle=ClientLifecycle.CONNECTED)
    world = WorldState(source)
    world._consume(event("game.state_sync", 1, sync_payload()))
    initial_records = len(world.history().records)

    invalid_payloads = (
        {"day": 1, "phase": "day", "extensions_used": 1},
        {"day": 1, "phase": "day", "phase_ends_at": "late", "extensions_used": 1},
    )
    for seq, payload in enumerate(invalid_payloads, start=2):
        world._consume(event("game.event", seq, {"event_type": "DAY_EXTENDED", "event_payload": payload}))
        assert world.snapshot().phase is not None
        assert world.snapshot().phase.phase_ends_at == 120
        assert not any(isinstance(record, PhaseTimingChangedRecord) for record in world.history().records[initial_records:])

    world._consume(event("game.event", 4, {"event_type": "DAY_SHORTENED", "event_payload": {"day": 1, "phase": "day", "phase_ends_at": None, "extensions_used": 1}}))
    assert world.snapshot().phase is not None
    assert world.snapshot().phase.phase_ends_at is None
    assert isinstance(world.history().records[-1], PhaseTimingChangedRecord)
    world._consume(event("game.event", 5, {"event_type": "DAY_EXTENDED", "event_payload": {"day": 1, "phase": "day", "phase_ends_at": 180, "extensions_used": 2}}))
    assert world.snapshot().phase is not None
    assert world.snapshot().phase.phase_ends_at == 180
    assert world.snapshot().malformed_event_count == 2


@async_test
async def test_sync_and_incremental_history_normalize_to_the_same_records() -> None:
    history = [
        {"type": "game.event", "payload": {"event_type": "PHASE_STARTED", "event_payload": {"phase": "day", "day": 1, "phase_ends_at": 120}}},
        {"type": "chat.message", "payload": {"channel": "public", "message": {"player_id": "p0", "display_name": "Alice", "message": "hello"}}},
        {"type": "game.event", "payload": {"event_type": "CO_DECLARED", "event_payload": {"player_id": "p0", "claimed_role_id": "villager", "comment": "claim"}}},
    ]
    sync = sync_payload()
    sync["history"] = history
    source = ListSource([], last_seq=4, lifecycle=ClientLifecycle.CONNECTED)
    from_sync = WorldState(source)
    from_live = WorldState(source)
    from_sync._consume(event("game.state_sync", 1, sync))

    initial = sync_payload()
    initial["history"] = []
    from_live._consume(event("game.state_sync", 1, initial))
    for seq, entry in enumerate(history, start=2):
        from_live._consume(event(entry["type"], seq, entry["payload"]))

    assert from_sync.history().records == from_live.history().records


@async_test
async def test_repeated_sync_replaces_history_and_known_unmodeled_stays_distinct() -> None:
    source = ListSource([], last_seq=3, lifecycle=ClientLifecycle.CONNECTED)
    world = WorldState(source)
    first = sync_payload()
    second = sync_payload()
    second["history"] = [
        {"type": "game.event", "payload": {"event_type": "CO_DECLARED", "event_payload": {"player_id": "p1", "claimed_role_id": "seer", "comment": "new"}}},
    ]
    world._consume(event("game.state_sync", 1, first))
    world._consume(event("game.state_sync", 2, second))
    assert len(world.history().records) == 1
    assert isinstance(world.history().records[0], CoDeclarationRecord)

    world._consume(event("game.event", 3, {"event_type": "ACTION_SUBMITTED", "event_payload": {}}))
    assert isinstance(world.history().records[-1], KnownUnmodeledEventRecord)
    assert world.snapshot().known_unmodeled_event_count == 1
    assert world.snapshot().unknown_event_count == 0


@async_test
async def test_remaining_public_event_shapes_are_typed_without_unknown_records() -> None:
    source = ListSource([], last_seq=3, lifecycle=ClientLifecycle.CONNECTED)
    world = WorldState(source)
    world._consume(event("game.state_sync", 1, sync_payload()))
    world._consume(event("game.event", 2, {"event_type": "GAME_CREATED", "event_payload": {"game_id": "game-1", "day": 1, "phase": "day", "players": [{"player_id": "p0", "display_name": "Alice"}, {"player_id": "p1", "display_name": "Bob"}]}}))
    world._consume(event("game.event", 3, {"event_type": "DAY_SHORTENED", "event_payload": {"day": 1, "phase": "day", "phase_ends_at": 90, "extensions_used": None}}))

    assert isinstance(world.history().records[-2], GameLifecycleRecord)
    assert isinstance(world.history().records[-1], PhaseTimingChangedRecord)
    assert world.snapshot().unknown_event_count == 0
    assert world.snapshot().malformed_event_count == 0
    assert world.snapshot().phase is not None
    assert world.snapshot().phase.phase_ends_at == 90


@async_test
async def test_burst_consumption_and_stop_release_waiters_without_stopping_source() -> None:
    burst = [event("game.state_sync", 1, sync_payload())]
    burst.extend(
        event("chat.message", seq, {"channel": "public", "message": {"message": str(seq)}})
        for seq in range(2, 66)
    )
    burst_source = ListSource(burst, last_seq=65, lifecycle=ClientLifecycle.ENDED)
    burst_world = WorldState(burst_source)
    await burst_world.run()
    assert burst_world.snapshot().last_applied_seq == 65

    source = BlockingSource()
    world = WorldState(source)
    runner = asyncio.create_task(world.run())
    await source.events_started.wait()
    waiter = asyncio.create_task(world.wait_for_update(world.snapshot().version))
    await world.stop()
    exited = await runner
    waited = await waiter
    assert exited.reason.value == "STOPPED"
    assert waited.freshness is Freshness.ENDED
    assert source._snapshot.lifecycle is ClientLifecycle.CONNECTED


@async_test
async def test_consumer_failure_leaves_network_queue_to_consumer_overrun() -> None:
    client = NetworkClient(
        NetworkClientConfig(
            "ws://127.0.0.1:1",
            "game-1",
            "entry-token",
            inbound_event_capacity=2,
        ),
        NoopCredentialStore(),
    )
    world = WorldState(client)

    def fail_reducer(_event: ServerEvent):
        raise RuntimeError("reducer failure")

    world._reducer.apply_server_event = fail_reducer  # type: ignore[method-assign]
    await client._publish(event("game.event", 1, {"event_type": "PUBLIC_NOTIFY", "event_payload": {"notify_id": "n"}}))
    exited = await world.run()
    assert exited.freshness is Freshness.FAILED

    notice = LifecycleChanged(ClientLifecycle.CONNECTED, ClientLifecycle.RECONNECT_WAIT)
    await client._publish(notice)
    await client._publish(notice)
    with pytest.raises(Exception) as raised:
        await client._publish(notice)
    assert getattr(raised.value, "reason", None) is ClientExitReason.CONSUMER_OVERRUN
