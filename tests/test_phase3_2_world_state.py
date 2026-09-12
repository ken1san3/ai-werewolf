from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace
import json

import pytest
from websockets.asyncio.client import connect

from ai_client.network import (
    ActionAccepted,
    ActionRejected,
    ChatAction,
    ClientLifecycle,
    ClientExitReason,
    ClientSnapshot,
    GameEnded,
    LifecycleChanged,
    NetworkClient,
    NetworkClientConfig,
    PhaseTimingMapped,
    PROTOCOL_VERSION,
    ResumeRecoveryCompleted,
    SequenceGapDetected,
    ServerEvent,
    SessionCheckpoint,
)
from ai_client.network.types import immutable_mapping
from ai_client.world import (
    ActionAcceptedObservation,
    ActionRejectionObservation,
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
    RevealedRoleView,
    ResumeRecoveryBarrier,
    TieResolvedRandomRecord,
    TransportObservationQuery,
    TransportObservationKind,
    TransportObservationRetention,
    UnknownEventRecord,
    VoteRevealRecord,
    VoteResultRecord,
    WorldState,
    WorldStateConfig,
)
from server.aiwolf_core import EventVisibility, GameEvent
from server.network import GameRegistry, SessionManager, WebSocketGameServer
from tests.test_network_sessions import GAME_ID, client_message, join_message, make_game


def event(message_type: str, seq: int, payload: dict) -> ServerEvent:
    return ServerEvent(
        type=message_type,
        protocol_version=PROTOCOL_VERSION,
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


def test_transport_observation_extension_is_versioned_bounded_and_generation_aware() -> None:
    source = ListSource([], last_seq=1, lifecycle=ClientLifecycle.CONNECTED)
    action = ChatAction(1, 1, "day", 1, "chat", "public")
    source._snapshot = replace(  # noqa: SLF001 - explicit source contract fixture
        source._snapshot,
        connection_generation=1,
        action_generation=1,
        actions=(action,),
    )
    world = WorldState(
        source,
        transport_retention=TransportObservationRetention(max_records=1, max_bytes=1),
    )
    world._consume(event("game.state_sync", 1, sync_payload()))  # noqa: SLF001
    world._consume(  # noqa: SLF001
        PhaseTimingMapped("day", 1, 1, 1, 1, 1, 120, 5.0, 124.0)
    )
    self_timing_version = world.snapshot().version
    self_deadline = world.transport_observations().current_deadline
    assert self_deadline is not None
    assert self_deadline.mapping_order == 1

    source._snapshot = replace(source._snapshot, last_seq=2)  # noqa: SLF001
    world._consume(event("action.rejected", 2, {"action": "chat.send", "reason": "late"}))  # noqa: SLF001
    world._consume(ActionRejected("chat.send", "late", 2, 1, 6.0))  # noqa: SLF001
    view = world.transport_observations(TransportObservationQuery(after_order=0))
    assert view.world_version == self_timing_version + 2
    assert view.gap_before_first
    assert len(view.observations) == 1
    assert isinstance(view.observations[0], ActionRejectionObservation)
    assert view.observations[0].world_version == view.world_version
    assert world.snapshot().complete
    assert world.snapshot().history_retention.complete


def test_acceptance_rejection_and_resume_barrier_are_exact_bounded_world_facts() -> None:
    source = ListSource([], last_seq=5, lifecycle=ClientLifecycle.CONNECTED)
    source._snapshot = replace(  # noqa: SLF001 - explicit source contract fixture
        source._snapshot,
        connection_generation=2,
        action_generation=3,
    )
    world = WorldState(
        source,
        transport_retention=TransportObservationRetention(
            max_records=2,
            max_bytes=100_000,
        ),
    )
    world._consume(event("game.state_sync", 1, sync_payload()))  # noqa: SLF001
    world._consume(event("action.accepted", 2, {  # noqa: SLF001
        "action": "vote.cast",
        "request_event_id": "00000000-0000-0000-0000-000000000201",
    }))
    world._consume(  # noqa: SLF001
        ActionAccepted(
            action="vote.cast",
            request_event_id="00000000-0000-0000-0000-000000000201",
            seq=2,
            observation_connection_generation=2,
            observed_at_monotonic=8.0,
        )
    )
    world._consume(event("action.rejected", 3, {  # noqa: SLF001
        "action": "ability.use",
        "reason": "invalid_target",
        "request_event_id": "00000000-0000-0000-0000-000000000202",
    }))
    world._consume(  # noqa: SLF001
        ActionRejected(
            "ability.use",
            "invalid_target",
            3,
            2,
            8.5,
            "00000000-0000-0000-0000-000000000202",
        )
    )
    world._consume(event("game.state_sync", 5, sync_payload()))  # noqa: SLF001
    world._consume(  # noqa: SLF001
        ResumeRecoveryCompleted(
            connection_generation=2,
            requested_last_seq=1,
            replay_first_seq=2,
            replay_last_seq=3,
            replay_contiguous=True,
            replay_gap_or_floor=False,
            resumed_seq=4,
            state_sync_seq=5,
        )
    )

    view = world.transport_observations(
        TransportObservationQuery(after_order=0)
    )
    assert view.gap_before_first
    assert len(view.observations) == 2
    rejection, barrier = view.observations
    assert isinstance(rejection, ActionRejectionObservation)
    assert rejection.request_event_id == "00000000-0000-0000-0000-000000000202"
    assert rejection.observation_connection_generation == 2
    assert isinstance(barrier, ResumeRecoveryBarrier)
    assert barrier.world_version == world.snapshot().version
    assert barrier.requested_last_seq == 1
    assert (barrier.replay_first_seq, barrier.replay_last_seq) == (2, 3)
    assert barrier.replay_contiguous
    assert not barrier.replay_gap_or_floor
    assert barrier.complete
    accepted_only = world.transport_observations(
        TransportObservationQuery(
            kinds=frozenset({TransportObservationKind.ACTION_ACCEPTED})
        )
    )
    assert accepted_only.observations == ()  # evicted with explicit gap metadata above
    assert world.snapshot().complete


def test_resume_barrier_cannot_precede_world_sync_commit() -> None:
    world = WorldState(ListSource([], last_seq=2, lifecycle=ClientLifecycle.CONNECTED))
    with pytest.raises(ValueError, match="preceded its state sync"):
        world._consume(  # noqa: SLF001
            ResumeRecoveryCompleted(
                connection_generation=2,
                requested_last_seq=1,
                replay_first_seq=None,
                replay_last_seq=None,
                replay_contiguous=False,
                replay_gap_or_floor=True,
                resumed_seq=2,
                state_sync_seq=3,
            )
        )


class NoopCredentialStore:
    async def load(self):
        return None

    async def save(self, checkpoint) -> None:
        del checkpoint


class MemoryCredentialStore:
    def __init__(self, checkpoint: SessionCheckpoint | None = None) -> None:
        self.checkpoint = checkpoint

    async def load(self) -> SessionCheckpoint | None:
        return self.checkpoint

    async def save(self, checkpoint: SessionCheckpoint) -> None:
        self.checkpoint = checkpoint


class RecordingSource:
    def __init__(self, client: NetworkClient) -> None:
        self.client = client
        self.events_seen: list[object] = []

    def snapshot(self) -> ClientSnapshot:
        return self.client.snapshot()

    async def events(self):
        async for item in self.client.events():
            self.events_seen.append(item)
            yield item


async def _recover_terminal_from_real_server(*, retain_terminal_event: bool):
    game = make_game()
    registry = GameRegistry({GAME_ID: game})
    server = WebSocketGameServer(
        registry,
        sessions=SessionManager(registry, replay_history_limit=128),
        tick_interval_seconds=3600,
    )
    listener = await server.start("127.0.0.1", 0)
    uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
    old_socket = await connect(uri)
    client_task: asyncio.Task | None = None
    world_task: asyncio.Task | None = None
    try:
        await old_socket.send(json.dumps(join_message(registry, "player-0")))
        joined = json.loads(await asyncio.wait_for(old_socket.recv(), 2))
        initial_sync = json.loads(await asyncio.wait_for(old_socket.recv(), 2))
        await old_socket.send(json.dumps(client_message("session.ready", {})))
        ready = json.loads(await asyncio.wait_for(old_socket.recv(), 2))
        checkpoint = SessionCheckpoint(
            joined["payload"]["connection_token"],
            ready["seq"],
            PROTOCOL_VERSION,
        )

        if retain_terminal_event:
            async with server._dispatch_lock:  # noqa: SLF001 - deterministic fixture boundary
                game.event_bus.publish(
                    GameEvent(
                        "GAME_ENDED",
                        EventVisibility.PUBLIC,
                        {
                            "winner_team": "village",
                            "outcome": "team_victory",
                            "player_results": {"player-0": "won"},
                        },
                    )
                )
                server._flush_outbound_deliveries()  # noqa: SLF001
            terminal_replay = server.sessions.session_for(GAME_ID)._history_by_player["player-0"][-1]  # noqa: SLF001
            assert terminal_replay.type == "game.event"
        else:
            await old_socket.close()
            await asyncio.wait_for(old_socket.wait_closed(), 2)
            for _ in range(100):
                if server.sessions.session_for(GAME_ID).connection_count("player-0") == 0:
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("old connection did not leave the server")
            async with server._dispatch_lock:  # noqa: SLF001 - deterministic fixture boundary
                game.event_bus.publish(
                    GameEvent(
                        "GAME_ENDED",
                        EventVisibility.PUBLIC,
                        {
                            "winner_team": "village",
                            "outcome": "team_victory",
                            "player_results": {"player-0": "won"},
                        },
                    )
                )
                server._flush_outbound_deliveries()  # noqa: SLF001
            assert not any(
                reply.type == "game.event" and reply.seq > checkpoint.last_seq
                for reply in server.sessions.session_for(GAME_ID)._history_by_player["player-0"]  # noqa: SLF001
            )

        await old_socket.close()
        await asyncio.wait_for(old_socket.wait_closed(), 2)
        store = MemoryCredentialStore(checkpoint)
        client = NetworkClient(
            NetworkClientConfig(uri, GAME_ID, None),
            store,
        )
        source = RecordingSource(client)
        world = WorldState(source)
        client_task = asyncio.create_task(client.run())
        world_task = asyncio.create_task(world.run())
        client_exit, world_exit = await asyncio.wait_for(
            asyncio.gather(client_task, world_task), 5
        )
        return client_exit, world_exit, world.snapshot(), world.history(), source.events_seen
    finally:
        for task in (world_task, client_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (world_task, client_task) if task is not None),
            return_exceptions=True,
        )
        await old_socket.close()
        await server.close()


def async_test(function):
    def wrapper(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return wrapper


@async_test
async def test_f007_retained_terminal_replay_ends_after_authoritative_sync() -> None:
    client_exit, world_exit, snapshot, history, events_seen = await _recover_terminal_from_real_server(
        retain_terminal_event=True
    )

    assert client_exit.reason is ClientExitReason.GAME_ENDED
    assert world_exit.reason.value == "CLIENT_ENDED"
    assert snapshot.freshness is Freshness.ENDED
    terminal_events = [item for item in events_seen if isinstance(item, ServerEvent)]
    assert [item.type for item in terminal_events] == [
        "game.event",
        "session.resumed",
        "game.state_sync",
    ]
    ended = [item for item in events_seen if isinstance(item, GameEnded)]
    assert len(ended) == 1
    assert ended[0].event.type == "game.state_sync"
    assert len([record for record in history.records if isinstance(record, GameLifecycleRecord)]) == 1


@async_test
async def test_f007_sync_only_terminal_fact_ends_after_authoritative_sync() -> None:
    client_exit, world_exit, snapshot, history, events_seen = await _recover_terminal_from_real_server(
        retain_terminal_event=False
    )

    assert client_exit.reason is ClientExitReason.GAME_ENDED
    assert world_exit.reason.value == "CLIENT_ENDED"
    assert snapshot.freshness is Freshness.ENDED
    terminal_events = [item for item in events_seen if isinstance(item, ServerEvent)]
    assert [item.type for item in terminal_events] == [
        "session.resumed",
        "game.state_sync",
    ]
    ended = [item for item in events_seen if isinstance(item, GameEnded)]
    assert len(ended) == 1
    assert ended[0].event.type == "game.state_sync"
    assert len([record for record in history.records if isinstance(record, GameLifecycleRecord)]) == 1


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
async def test_state_sync_exposes_revealed_roles_through_public_api() -> None:
    payload = sync_payload()
    payload["revealed_roles"] = [{"player_id": "p1", "role_id": "opaque_role"}]
    world = WorldState(
        ListSource(
            [event("game.state_sync", 1, payload)],
            last_seq=1,
            lifecycle=ClientLifecycle.ENDED,
        )
    )

    await world.run()

    expected = RevealedRoleView("p1", "opaque_role")
    assert world.snapshot().revealed_roles == (expected,)
    assert world.revealed_role("p1") == expected
    assert world.revealed_role("p0") is None


@async_test
async def test_later_empty_sync_clears_revealed_roles() -> None:
    first = sync_payload()
    first["revealed_roles"] = [{"player_id": "p1", "role_id": "opaque_role"}]
    source = ListSource([], last_seq=2, lifecycle=ClientLifecycle.CONNECTED)
    world = WorldState(source)

    world._consume(event("game.state_sync", 1, first))
    assert world.revealed_role("p1") == RevealedRoleView("p1", "opaque_role")

    world._consume(event("game.state_sync", 2, sync_payload()))

    assert world.snapshot().revealed_roles == ()
    assert world.revealed_role("p1") is None


@async_test
async def test_revealed_roles_are_deeply_immutable() -> None:
    payload = sync_payload()
    payload["revealed_roles"] = [{"player_id": "p1", "role_id": "opaque_role"}]
    world = WorldState(ListSource([], last_seq=1, lifecycle=ClientLifecycle.CONNECTED))
    world._consume(event("game.state_sync", 1, payload))

    snapshot = world.snapshot()
    with pytest.raises(FrozenInstanceError):
        snapshot.revealed_roles[0].role_id = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        snapshot.revealed_roles += (RevealedRoleView("p0", "other"),)  # type: ignore[misc]
    assert world.revealed_role("p1") == RevealedRoleView("p1", "opaque_role")


@async_test
async def test_revealed_role_duplicate_rejects_entire_sync() -> None:
    first = sync_payload()
    first["revealed_roles"] = [{"player_id": "p1", "role_id": "opaque_role"}]
    invalid = sync_payload()
    invalid["revealed_roles"] = [
        {"player_id": "p0", "role_id": "first"},
        {"player_id": "p0", "role_id": "duplicate"},
    ]
    world = WorldState(ListSource([], last_seq=2, lifecycle=ClientLifecycle.CONNECTED))
    world._consume(event("game.state_sync", 1, first))
    before = world.snapshot()
    before_records = world.history().records

    world._consume(event("game.state_sync", 2, invalid))

    after = world.snapshot()
    assert after.players == before.players
    assert after.revealed_roles == before.revealed_roles
    assert world.history().records[: len(before_records)] == before_records
    assert isinstance(world.history().records[-1], MalformedEventRecord)
    assert after.malformed_event_count == before.malformed_event_count + 1
    assert after.freshness is Freshness.STALE


@async_test
async def test_revealed_role_for_unknown_player_rejects_entire_sync() -> None:
    first = sync_payload()
    first["revealed_roles"] = [{"player_id": "p1", "role_id": "opaque_role"}]
    invalid = sync_payload()
    invalid["revealed_roles"] = [{"player_id": "p9", "role_id": "unknown_target"}]
    world = WorldState(ListSource([], last_seq=2, lifecycle=ClientLifecycle.CONNECTED))
    world._consume(event("game.state_sync", 1, first))
    before = world.snapshot()
    before_records = world.history().records

    world._consume(event("game.state_sync", 2, invalid))

    after = world.snapshot()
    assert after.players == before.players
    assert after.revealed_roles == before.revealed_roles
    assert world.history().records[: len(before_records)] == before_records
    assert isinstance(world.history().records[-1], MalformedEventRecord)
    assert after.malformed_event_count == before.malformed_event_count + 1
    assert after.freshness is Freshness.STALE


@async_test
async def test_repeated_sync_advances_version_with_revealed_roles_in_same_commit() -> None:
    payload = sync_payload()
    payload["revealed_roles"] = [{"player_id": "p1", "role_id": "opaque_role"}]
    world = WorldState(ListSource([], last_seq=2, lifecycle=ClientLifecycle.CONNECTED))

    world._consume(event("game.state_sync", 1, payload))
    first = world.snapshot()
    world._consume(event("game.state_sync", 2, payload))
    second = world.snapshot()

    assert second.version == first.version + 1
    assert second.revealed_roles == first.revealed_roles
    assert first is not second


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
        GameEnded(event("game.event", 10, {"event_type": "GAME_ENDED", "event_payload": {"winner_team": "village", "outcome": "team_victory", "player_results": {"p0": "won"}}})),
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
        GameEnded(event("game.event", 5, {"event_type": "GAME_ENDED", "event_payload": {"winner_team": "village", "outcome": "done", "player_results": {}}})),
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
async def test_invalid_game_ended_notice_cannot_promote_rejected_sync_to_ended() -> None:
    source = ListSource([], last_seq=2, lifecycle=ClientLifecycle.ENDED)
    world = WorldState(source)
    world._consume(event("game.state_sync", 1, sync_payload()))
    invalid_sync = sync_payload()
    invalid_sync["revealed_roles"] = [{"player_id": "missing", "role_id": "opaque"}]
    world._consume(event("game.state_sync", 2, invalid_sync))

    world._consume(GameEnded(event("game.state_sync", 2, invalid_sync)))
    world._consume(LifecycleChanged(ClientLifecycle.CONNECTED, ClientLifecycle.ENDED))

    assert world.snapshot().freshness is Freshness.STALE


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
async def test_rejected_recovery_sync_stays_stale_until_a_valid_sync() -> None:
    source = ListSource([], last_seq=1, lifecycle=ClientLifecycle.CONNECTED)
    world = WorldState(source)
    world._consume(event("game.state_sync", 1, sync_payload()))

    action = ChatAction(
        connection_generation=1,
        action_generation=1,
        phase="day",
        day=1,
        type="chat",
        channel="public",
    )
    source._snapshot = ClientSnapshot(
        lifecycle=ClientLifecycle.RECONNECT_WAIT,
        last_seq=2,
        actions=(action,),
    )
    world._consume(LifecycleChanged(ClientLifecycle.CONNECTED, ClientLifecycle.RECONNECT_WAIT))
    assert world.snapshot().freshness is Freshness.STALE
    assert world.current_actions().actions == ()

    source._snapshot = ClientSnapshot(
        lifecycle=ClientLifecycle.SYNCHRONIZING,
        last_seq=2,
        actions=(action,),
    )
    world._consume(LifecycleChanged(ClientLifecycle.RECONNECT_WAIT, ClientLifecycle.SYNCHRONIZING))
    invalid = sync_payload()
    invalid["revealed_roles"] = [
        {"player_id": "p0", "role_id": "first"},
        {"player_id": "p0", "role_id": "duplicate"},
    ]
    world._consume(event("game.state_sync", 2, invalid))

    source._snapshot = ClientSnapshot(
        lifecycle=ClientLifecycle.CONNECTED,
        last_seq=2,
        actions=(action,),
    )
    world._consume(LifecycleChanged(ClientLifecycle.SYNCHRONIZING, ClientLifecycle.CONNECTED))
    assert world.snapshot().freshness is Freshness.STALE
    assert world.current_actions().actions == ()

    source._snapshot = ClientSnapshot(
        lifecycle=ClientLifecycle.CONNECTED,
        last_seq=3,
        actions=(action,),
    )
    world._consume(event("game.state_sync", 3, sync_payload()))
    assert world.snapshot().freshness is Freshness.CURRENT
    assert world.current_actions().actions == (action,)


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
async def test_replay_and_full_sync_recovery_matches_uninterrupted_world() -> None:
    live_chat = {
        "channel": "public",
        "message": {"player_id": "p0", "display_name": "Alice", "message": "after reconnect"},
    }
    uninterrupted = WorldState(ListSource([], last_seq=2, lifecycle=ClientLifecycle.CONNECTED))
    uninterrupted._consume(event("game.state_sync", 1, sync_payload()))
    uninterrupted._consume(event("chat.message", 2, live_chat))

    recovered_sync = sync_payload()
    recovered_sync["history"].append({"type": "chat.message", "payload": live_chat})
    recovered = WorldState(ListSource([], last_seq=4, lifecycle=ClientLifecycle.CONNECTED))
    recovered._consume(event("game.state_sync", 1, sync_payload()))
    recovered._consume(SequenceGapDetected(expected_seq=2, received_seq=4, connection_generation=1))
    recovered._consume(event("game.state_sync", 4, recovered_sync))

    uninterrupted_snapshot = uninterrupted.snapshot()
    recovered_snapshot = recovered.snapshot()
    assert recovered_snapshot.players == uninterrupted_snapshot.players
    assert recovered_snapshot.alive_player_ids == uninterrupted_snapshot.alive_player_ids
    assert recovered_snapshot.deaths == uninterrupted_snapshot.deaths
    assert recovered_snapshot.phase == uninterrupted_snapshot.phase
    assert recovered_snapshot.self_view == uninterrupted_snapshot.self_view
    assert recovered_snapshot.revealed_roles == uninterrupted_snapshot.revealed_roles
    assert recovered.history().records == uninterrupted.history().records
    assert recovered.history().retention == uninterrupted.history().retention


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
