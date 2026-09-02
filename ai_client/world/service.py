"""World State consumer service and read-only facade."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Protocol

from ai_client.network import (
    ActionRejected,
    ClientEvent,
    ClientLifecycle,
    ClientSnapshot,
    DeliveryUnknown,
    FatalTermination,
    GameEnded,
    LifecycleChanged,
    NotDelivered,
    PhaseDeadlineReached,
    SequenceGapDetected,
    SequenceGapRecovered,
    ServerEvent,
)

from .model import (
    AbilityResultView,
    CoView,
    CurrentActionsView,
    Freshness,
    HistoryQuery,
    HistoryView,
    WorldSnapshot,
    WorldStateConfig,
    WorldStateExit,
    WorldStateExitReason,
)
from .reducer import WorldReducer


class NetworkEventSource(Protocol):
    def snapshot(self) -> ClientSnapshot:
        ...

    def events(self) -> AsyncIterator[ClientEvent]:
        ...


_KNOWN_NOTICE_TYPES = (
    ActionRejected,
    DeliveryUnknown,
    FatalTermination,
    GameEnded,
    LifecycleChanged,
    NotDelivered,
    PhaseDeadlineReached,
    SequenceGapDetected,
    SequenceGapRecovered,
)


class WorldState:
    """Materialize one Network Client's event stream into immutable snapshots."""

    def __init__(
        self,
        source: NetworkEventSource,
        *,
        config: WorldStateConfig = WorldStateConfig(),
    ) -> None:
        self._source = source
        self.config = config
        self._reducer = WorldReducer(config)
        self._freshness = Freshness.EMPTY
        self._version = 0
        self._has_sync = False
        self._stop_requested = False
        self._run_started = False
        self._run_task: asyncio.Task[WorldStateExit] | None = None
        self._update_event = asyncio.Event()
        self._snapshot = self._make_snapshot()

    async def run(self) -> WorldStateExit:
        """Consume the exclusive source iterator until it closes or fails."""

        if self._run_started:
            raise RuntimeError("WorldState.run() may only be called once")
        self._run_started = True
        self._run_task = asyncio.current_task()
        try:
            async for event in self._source.events():
                if self._stop_requested:
                    break
                self._consume(event)
        except asyncio.CancelledError:
            self._stop_requested = True
            self._set_freshness(Freshness.ENDED)
            return self._exit(WorldStateExitReason.STOPPED)
        except Exception as error:  # reducer/source failures are fail-fast
            self._set_freshness(Freshness.FAILED)
            return self._exit(WorldStateExitReason.FAILED, str(error))
        finally:
            self._run_task = None

        if self._stop_requested:
            self._set_freshness(Freshness.ENDED)
            return self._exit(WorldStateExitReason.STOPPED)
        if self._freshness is Freshness.ENDED:
            return self._exit(WorldStateExitReason.CLIENT_ENDED)
        if self._freshness is Freshness.FAILED:
            return self._exit(WorldStateExitReason.FAILED)

        lifecycle = self._network_lifecycle()
        if lifecycle is ClientLifecycle.ENDED:
            self._set_freshness(Freshness.ENDED)
            return self._exit(WorldStateExitReason.CLIENT_ENDED)
        self._set_freshness(Freshness.FAILED)
        return self._exit(WorldStateExitReason.SOURCE_CLOSED)

    async def stop(self) -> None:
        """Stop only this consumer; ownership of the network client is unchanged."""

        self._stop_requested = True
        task = self._run_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        elif task is None and self._freshness not in {Freshness.ENDED, Freshness.FAILED}:
            self._set_freshness(Freshness.ENDED)

    def snapshot(self) -> WorldSnapshot:
        return self._snapshot

    def current_actions(self) -> CurrentActionsView:
        network = self._source.snapshot()
        caught_up = self._reducer.last_applied_seq == network.last_seq
        actions: tuple[object, ...] = ()
        if caught_up and network.lifecycle is ClientLifecycle.CONNECTED:
            actions = tuple(network.actions)
        return CurrentActionsView(
            world_version=self._version,
            world_last_applied_seq=self._reducer.last_applied_seq,
            network_last_seq=network.last_seq,
            is_caught_up=caught_up,
            actions=actions,
        )

    def history(self, query: HistoryQuery = HistoryQuery()) -> HistoryView:
        records = self._reducer.memory.query(query)
        retention = self._reducer.memory.retention()
        return HistoryView(records, self._query_complete(query, retention), retention)

    def co_for_day(self, day: int) -> CoView:
        query = HistoryQuery(kinds=frozenset({"co_declaration", "co_report"}), day=day)
        records = self._reducer.memory.query(query)
        retention = self._reducer.memory.retention()
        declarations = tuple(record for record in records if record.record_kind == "co_declaration")
        reports = tuple(record for record in records if record.record_kind == "co_report")
        return CoView(declarations, reports, self._query_complete(query, retention), retention)

    def ability_results(self) -> AbilityResultView:
        query = HistoryQuery(kinds=frozenset({"ability_result"}))
        retention = self._reducer.memory.retention()
        records = tuple(self._reducer.memory.query(query))
        return AbilityResultView(records, retention.complete, retention)

    async def wait_for_update(self, after_version: int) -> WorldSnapshot:
        """Wait until a committed version is newer than ``after_version``."""

        while True:
            snapshot = self._snapshot
            if snapshot.version > after_version or snapshot.freshness in {
                Freshness.ENDED,
                Freshness.FAILED,
            }:
                return snapshot
            event = self._update_event
            await event.wait()

    def _consume(self, event: ClientEvent) -> None:
        if isinstance(event, ServerEvent):
            result = self._reducer.apply_server_event(event)
            if result.state_sync and self._freshness not in {Freshness.ENDED, Freshness.FAILED}:
                self._has_sync = True
                self._freshness = (
                    Freshness.CURRENT
                    if self._network_lifecycle() is ClientLifecycle.CONNECTED
                    else Freshness.STALE
                )
            if (
                event.type == "game.event"
                and isinstance(event.payload.get("event_type"), str)
                and event.payload.get("event_type") == "GAME_ENDED"
                and self._freshness is not Freshness.FAILED
            ):
                self._freshness = Freshness.ENDED
            self._commit()
            return
        if isinstance(event, LifecycleChanged):
            self._apply_lifecycle(event.current)
            self._commit()
            return
        if isinstance(event, SequenceGapDetected):
            if self._has_sync and self._freshness not in {Freshness.ENDED, Freshness.FAILED}:
                self._freshness = Freshness.STALE
            self._commit()
            return
        if isinstance(event, FatalTermination):
            self._freshness = Freshness.FAILED
            self._commit()
            return
        if isinstance(event, GameEnded):
            self._freshness = Freshness.ENDED
            self._commit()
            return
        if isinstance(event, _KNOWN_NOTICE_TYPES):
            # The corresponding ServerEvent, if any, is the world fact.  A
            # transport notice must not create a duplicate history record.
            self._commit()
            return
        self._reducer.apply_unknown_notice()
        self._commit()

    def _apply_lifecycle(self, lifecycle: ClientLifecycle) -> None:
        if lifecycle in {
            ClientLifecycle.JOINING,
            ClientLifecycle.RESUMING,
            ClientLifecycle.SYNCHRONIZING,
            ClientLifecycle.RECONNECT_WAIT,
            ClientLifecycle.STOPPING,
        }:
            if self._has_sync and self._freshness not in {Freshness.ENDED, Freshness.FAILED}:
                self._freshness = Freshness.STALE
        elif lifecycle is ClientLifecycle.CONNECTED:
            if self._has_sync and self._freshness not in {Freshness.ENDED, Freshness.FAILED}:
                self._freshness = Freshness.CURRENT
        elif lifecycle is ClientLifecycle.ENDED:
            self._freshness = Freshness.ENDED
        elif lifecycle is ClientLifecycle.FAILED:
            self._freshness = Freshness.FAILED

    def _commit(self) -> None:
        self._version += 1
        self._snapshot = self._make_snapshot()
        previous = self._update_event
        self._update_event = asyncio.Event()
        previous.set()

    def _set_freshness(self, freshness: Freshness) -> None:
        if self._freshness is freshness:
            return
        self._freshness = freshness
        self._commit()

    def _make_snapshot(self) -> WorldSnapshot:
        network_last_seq = 0
        try:
            network_last_seq = self._source.snapshot().last_seq
        except Exception:
            pass
        return WorldSnapshot(
            version=self._version,
            freshness=self._freshness,
            is_caught_up=self._reducer.last_applied_seq == network_last_seq,
            last_applied_seq=self._reducer.last_applied_seq,
            players=tuple(self._reducer.players.values()),
            alive_player_ids=tuple(
                player_id
                for player_id, player in self._reducer.players.items()
                if player.alive
            ),
            deaths=tuple(self._reducer.deaths.values()),
            phase=self._reducer.phase,
            self_view=self._reducer.self_view,
            history_retention=self._reducer.memory.retention(),
            unknown_event_count=self._reducer.unknown_event_count,
            known_unmodeled_event_count=self._reducer.known_unmodeled_event_count,
            malformed_event_count=self._reducer.malformed_event_count,
        )

    def _network_lifecycle(self) -> ClientLifecycle | None:
        try:
            return self._source.snapshot().lifecycle
        except Exception:
            return None

    def _query_complete(self, query: HistoryQuery, retention) -> bool:
        if retention.complete:
            return True
        return (
            query.after_order is not None
            and retention.dropped_through_order is not None
            and query.after_order >= retention.dropped_through_order
        )

    def _exit(self, reason: WorldStateExitReason, error: str | None = None) -> WorldStateExit:
        return WorldStateExit(reason, self._freshness, self._reducer.last_applied_seq, error)
