"""World State consumer service and read-only facade."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, replace
from typing import Protocol

from ai_client.network import (
    ActionAccepted,
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
    PhaseTimingMapped,
    ResumeRecoveryCompleted,
    SequenceGapDetected,
    SequenceGapRecovered,
    ServerEvent,
)

from .model import (
    AbilityResultRecord,
    AbilityResultView,
    ActionAcceptedObservation,
    ActionRejectionObservation,
    CoView,
    CurrentPhaseDeadline,
    CurrentActionsView,
    Freshness,
    HistoryQuery,
    HistoryView,
    PhaseDeadlineReachedObservation,
    PhaseTimingObservation,
    RevealedRoleView,
    ResumeRecoveryBarrier,
    TransportObservationQuery,
    TransportObservationRetention,
    TransportObservationView,
    WorldSnapshot,
    WorldStateConfig,
    WorldStateExit,
    WorldStateExitReason,
)
from .reducer import WorldReducer
from .transport import TransportObservationStore
from .inbound_authority_v2 import InboundAuthorityRuntimeV2, InboundAuthoritySnapshotV2


@dataclass(frozen=True)
class _AbortPublishBundleV2:
    expected_current_world_object: WorldSnapshot
    expected_current_authority_object: InboundAuthorityRuntimeV2
    failed_world: WorldSnapshot
    invalid_authority: InboundAuthorityRuntimeV2
    successor_update_event: asyncio.Event


class NetworkEventSource(Protocol):
    def snapshot(self) -> ClientSnapshot:
        ...

    def events(self) -> AsyncIterator[ClientEvent]:
        ...


_KNOWN_NOTICE_TYPES = (
    ActionAccepted,
    ActionRejected,
    DeliveryUnknown,
    FatalTermination,
    GameEnded,
    LifecycleChanged,
    NotDelivered,
    PhaseDeadlineReached,
    PhaseTimingMapped,
    ResumeRecoveryCompleted,
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
        transport_retention: TransportObservationRetention = TransportObservationRetention(),
        inbound_authority: InboundAuthorityRuntimeV2 | None = None,
    ) -> None:
        if not isinstance(transport_retention, TransportObservationRetention):
            raise TypeError("transport_retention must be TransportObservationRetention")
        if (inbound_authority is not None
                and getattr(source, "_authority_capability", None)
                is not getattr(inbound_authority, "_composition_capability", None)):
            raise TypeError("authority source and sink composition do not match")
        self._source = source
        self.config = config
        self.transport_retention = transport_retention
        self._reducer = WorldReducer(config)
        self._transport = TransportObservationStore(transport_retention)
        self._next_transport_order = 1
        self._current_deadline: CurrentPhaseDeadline | None = None
        self._freshness = Freshness.EMPTY
        self._version = 0
        self._has_sync = False
        self._last_committed_sync_seq: int | None = None
        self._recovery_sync_accepted = False
        self._terminal_notice_rejected = False
        self._stop_requested = False
        self._run_started = False
        self._run_task: asyncio.Task[WorldStateExit] | None = None
        self._inbound_authority = inbound_authority
        self._next_authority_delta = None
        self._next_lifecycle_observation = None
        self._invalidate_authority_next = False
        self._update_event = asyncio.Event()
        self._snapshot = self._make_snapshot()
        self._abort_bundle = (
            None if self._inbound_authority is None else
            self._prepare_abort_bundle(self._snapshot, self._inbound_authority)
        )

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
        if (
            lifecycle is ClientLifecycle.ENDED
            and self._recovery_sync_accepted
            and not self._terminal_notice_rejected
        ):
            self._set_freshness(Freshness.ENDED)
            return self._exit(WorldStateExitReason.CLIENT_ENDED)
        if lifecycle is ClientLifecycle.ENDED and self._freshness is Freshness.FAILED:
            return self._exit(WorldStateExitReason.FAILED)
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

    def inbound_authority_snapshot(self) -> InboundAuthoritySnapshotV2 | None:
        return None if self._inbound_authority is None else self._inbound_authority.snapshot()

    def current_actions(self) -> CurrentActionsView:
        network = self._source.snapshot()
        caught_up = self._reducer.last_applied_seq == network.last_seq
        actions: tuple[object, ...] = ()
        if (
            caught_up
            and network.lifecycle is ClientLifecycle.CONNECTED
            and self._freshness is Freshness.CURRENT
        ):
            actions = tuple(network.actions)
        return CurrentActionsView(
            world_version=self._version,
            world_last_applied_seq=self._reducer.last_applied_seq,
            network_last_seq=network.last_seq,
            is_caught_up=caught_up,
            actions=actions,
        )

    def revealed_role(self, player_id: str) -> RevealedRoleView | None:
        for revealed_role in self._snapshot.revealed_roles:
            if revealed_role.player_id == player_id:
                return revealed_role
        return None

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

    def transport_observations(
        self, query: TransportObservationQuery = TransportObservationQuery()
    ) -> TransportObservationView:
        if not isinstance(query, TransportObservationQuery):
            raise TypeError("query must be TransportObservationQuery")
        return TransportObservationView(
            world_version=self._version,
            first_retained_order=self._transport.first_retained_order,
            last_order=self._transport.last_order,
            gap_before_first=self._transport.gap_before_first(query.after_order),
            observations=self._transport.query(query),
            current_deadline=self._current_deadline,
        )

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
        try:
            self._consume_inner(event)
        except Exception:
            if self._abort_bundle is not None:
                self._abort_inbound_authority()
            raise

    def _consume_inner(self, event: ClientEvent) -> None:
        if isinstance(event, ServerEvent):
            observation = None
            if self._inbound_authority is not None:
                claim = getattr(self._source, "claim_committed_inbound", None)
                observation = None if claim is None else claim(event)
                if event.type in {"player.action_state", "game.state_sync", "game.event"} and observation is None:
                    self._invalidate_authority_next = True
                elif observation is not None:
                    self._inbound_authority.begin(observation, event, self._version,
                        self._reducer.last_applied_seq, self._reducer.phase)
            if event.type == "action.accepted":
                # This is a transport receipt fact, not semantic history.  Keep
                # the reducer's drain cursor coherent without creating an
                # unknown or history record; the following typed notice carries
                # the queryable correlation data.
                self._reducer.last_applied_seq = max(
                    self._reducer.last_applied_seq,
                    event.seq,
                )
                self._commit()
                return
            result = self._reducer.apply_server_event(
                event, observer=self._inbound_authority if observation is not None else None
            )
            if observation is not None:
                self._next_authority_delta = self._inbound_authority.finish(self._reducer, result)
            if self._server_event_invalidates_deadline(event):
                self._current_deadline = None
            if result.state_sync and self._freshness not in {Freshness.ENDED, Freshness.FAILED}:
                self._has_sync = True
                self._last_committed_sync_seq = event.seq
                self._recovery_sync_accepted = True
                self._terminal_notice_rejected = False
                self._freshness = (
                    Freshness.CURRENT
                    if self._network_lifecycle() is ClientLifecycle.CONNECTED
                    else Freshness.STALE
                )
            elif event.type == "game.state_sync":
                self._recovery_sync_accepted = False
                if self._has_sync and self._freshness not in {Freshness.ENDED, Freshness.FAILED}:
                    self._freshness = Freshness.STALE
            self._commit()
            return
        if isinstance(event, LifecycleChanged):
            if self._inbound_authority is not None:
                claim = getattr(self._source, "claim_lifecycle_observation", None)
                self._next_lifecycle_observation = None if claim is None else claim(event)
                if self._next_lifecycle_observation is None:
                    self._invalidate_authority_next = True
            self._apply_lifecycle(event.current)
            self._commit()
            return
        if isinstance(event, ActionAccepted):
            self._transport.append(
                ActionAcceptedObservation(
                    order=self._take_transport_order(),
                    world_version=self._version + 1,
                    action=event.action,
                    request_event_id=event.request_event_id,
                    seq=event.seq,
                    observation_connection_generation=(
                        event.observation_connection_generation
                    ),
                    observed_at_monotonic=event.observed_at_monotonic,
                )
            )
            self._commit()
            return
        if isinstance(event, ActionRejected):
            order = self._take_transport_order()
            self._transport.append(
                ActionRejectionObservation(
                    order=order,
                    world_version=self._version + 1,
                    action=event.action,
                    reason=event.reason,
                    seq=event.seq,
                    connection_generation=event.connection_generation,
                    observed_at_monotonic=event.observed_at_monotonic,
                    request_event_id=event.request_event_id,
                )
            )
            self._commit()
            return
        if isinstance(event, ResumeRecoveryCompleted):
            if self._reducer.last_applied_seq < event.state_sync_seq:
                raise ValueError("resume recovery barrier preceded its state sync")
            self._transport.append(
                ResumeRecoveryBarrier(
                    order=self._take_transport_order(),
                    world_version=self._version + 1,
                    connection_generation=event.connection_generation,
                    requested_last_seq=event.requested_last_seq,
                    replay_first_seq=event.replay_first_seq,
                    replay_last_seq=event.replay_last_seq,
                    replay_contiguous=event.replay_contiguous,
                    replay_gap_or_floor=event.replay_gap_or_floor,
                    resumed_seq=event.resumed_seq,
                    state_sync_seq=event.state_sync_seq,
                    complete=event.complete,
                )
            )
            self._commit()
            return
        if isinstance(event, PhaseTimingMapped):
            order = self._take_transport_order()
            observation = PhaseTimingObservation(
                order=order,
                world_version=self._version + 1,
                phase=event.phase,
                day=event.day,
                source_seq=event.source_seq,
                connection_generation=event.connection_generation,
                action_generation=event.action_generation,
                server_timestamp=event.server_timestamp,
                phase_ends_at=event.phase_ends_at,
                mapped_at_monotonic=event.mapped_at_monotonic,
                local_deadline_monotonic=event.local_deadline_monotonic,
            )
            self._transport.append(observation)
            if self._timing_is_current(event):
                self._current_deadline = CurrentPhaseDeadline(
                    mapping_order=order,
                    phase=event.phase,
                    day=event.day,
                    connection_generation=event.connection_generation,
                    action_generation=event.action_generation,
                    local_deadline_monotonic=event.local_deadline_monotonic,
                )
            self._commit()
            return
        if isinstance(event, PhaseDeadlineReached):
            self._transport.append(
                PhaseDeadlineReachedObservation(
                    order=self._take_transport_order(),
                    world_version=self._version + 1,
                    phase=event.phase,
                    day=event.day,
                    connection_generation=event.connection_generation,
                    action_generation=event.action_generation,
                    local_deadline_monotonic=event.local_deadline_monotonic,
                    reached_at_monotonic=event.reached_at_monotonic,
                )
            )
            self._commit()
            return
        if isinstance(event, SequenceGapDetected):
            self._invalidate_authority_next = True
            if self._has_sync and self._freshness not in {Freshness.ENDED, Freshness.FAILED}:
                self._freshness = Freshness.STALE
            self._commit()
            return
        if isinstance(event, FatalTermination):
            self._invalidate_authority_next = True
            self._freshness = Freshness.FAILED
            self._commit()
            return
        if isinstance(event, GameEnded):
            if (
                not self._has_sync
                or not self._recovery_sync_accepted
                or (
                    event.event.type == "game.state_sync"
                    and event.event.seq != self._last_committed_sync_seq
                )
                or (
                    event.event.type == "game.event"
                    and event.event.payload.get("event_type") != "GAME_ENDED"
                )
                or event.event.type not in {"game.event", "game.state_sync"}
            ):
                self._terminal_notice_rejected = True
                return
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
            self._current_deadline = None
            self._recovery_sync_accepted = False
            if self._has_sync and self._freshness not in {Freshness.ENDED, Freshness.FAILED}:
                self._freshness = Freshness.STALE
        elif lifecycle is ClientLifecycle.CONNECTED:
            if self._has_sync and self._freshness not in {Freshness.ENDED, Freshness.FAILED}:
                self._freshness = (
                    Freshness.CURRENT
                    if self._recovery_sync_accepted
                    else Freshness.STALE
                )
        elif lifecycle is ClientLifecycle.ENDED:
            self._current_deadline = None
            if self._recovery_sync_accepted:
                self._freshness = Freshness.ENDED
        elif lifecycle is ClientLifecycle.FAILED:
            self._current_deadline = None
            self._freshness = Freshness.FAILED

    def _commit(self) -> None:
        next_version = self._version + 1
        prepared_authority = None
        prepared_abort_bundle = None
        try:
            prepared_world = self._make_snapshot(version=next_version)
            prepared_update_event = asyncio.Event()
            if self._inbound_authority is not None:
                prepared_authority = self._inbound_authority.prepare_commit(
                    next_version, self._reducer.last_applied_seq,
                    self._next_authority_delta, self._next_lifecycle_observation,
                    self._invalidate_authority_next,
                    frozenset(record.order for record in self._reducer.memory.query()
                              if isinstance(record, AbilityResultRecord)))
                authority_snapshot = prepared_authority.snapshot()
                if (authority_snapshot.world_version != prepared_world.version
                        or authority_snapshot.last_committed_server_seq != prepared_world.last_applied_seq):
                    raise ValueError("world and authority snapshots diverged")
                prepared_abort_bundle = self._prepare_abort_bundle(
                    prepared_world, prepared_authority)
        except Exception:
            self._abort_inbound_authority()
            raise
        if prepared_authority is not None:
            self._inbound_authority = prepared_authority
            self._abort_bundle = prepared_abort_bundle
        self._version = next_version
        self._snapshot = prepared_world
        self._next_authority_delta = None; self._next_lifecycle_observation = None
        self._invalidate_authority_next = False
        previous = self._update_event
        self._update_event = prepared_update_event
        previous.set()

    def _abort_inbound_authority(self) -> None:
        if self._inbound_authority is None:
            return
        bundle = self._abort_bundle
        if bundle is None:
            raise RuntimeError("inbound authority abort bundle was already consumed")
        previous = self._update_event
        self._inbound_authority = bundle.invalid_authority
        self._freshness = Freshness.FAILED
        self._version = bundle.failed_world.version
        self._snapshot = bundle.failed_world
        self._abort_bundle = None
        self._update_event = bundle.successor_update_event
        self._next_authority_delta = None; self._next_lifecycle_observation = None
        self._invalidate_authority_next = False
        previous.set()

    def _prepare_abort_bundle(
        self,
        current_world: WorldSnapshot,
        current_authority: InboundAuthorityRuntimeV2,
    ) -> _AbortPublishBundleV2:
        invalid_authority = current_authority.prepare_invalid_empty(
            current_world.version + 1, current_world.last_applied_seq)
        failed_world = replace(
            current_world, version=current_world.version + 1,
            freshness=Freshness.FAILED, is_caught_up=False)
        successor = asyncio.Event()
        invalid_snapshot = invalid_authority.snapshot()
        if (invalid_snapshot.world_version != failed_world.version
                or invalid_snapshot.last_committed_server_seq != failed_world.last_applied_seq):
            raise ValueError("abort world and authority snapshots diverged")
        return _AbortPublishBundleV2(
            expected_current_world_object=current_world,
            expected_current_authority_object=current_authority,
            failed_world=failed_world,
            invalid_authority=invalid_authority,
            successor_update_event=successor,
        )

    def _set_freshness(self, freshness: Freshness) -> None:
        if self._freshness is freshness:
            return
        self._freshness = freshness
        if freshness in {Freshness.ENDED, Freshness.FAILED}:
            self._invalidate_authority_next = True
        self._commit()

    def _make_snapshot(self, *, version: int | None = None) -> WorldSnapshot:
        network_last_seq = 0
        try:
            network_last_seq = self._source.snapshot().last_seq
        except Exception:
            pass
        return WorldSnapshot(
            version=self._version if version is None else version,
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
            revealed_roles=tuple(self._reducer.revealed_roles),
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

    def _timing_is_current(self, event: PhaseTimingMapped) -> bool:
        try:
            network = self._source.snapshot()
        except Exception:
            return False
        phase = self._reducer.phase
        return (
            network.lifecycle is ClientLifecycle.CONNECTED
            and network.connection_generation == event.connection_generation
            and network.action_generation == event.action_generation
            and self._reducer.last_applied_seq == event.source_seq
            and phase is not None
            and phase.phase == event.phase
            and phase.day == event.day
        )

    @staticmethod
    def _server_event_invalidates_deadline(event: ServerEvent) -> bool:
        if event.type in {"game.state_sync", "player.action_state"}:
            return True
        if event.type != "game.event":
            return False
        return event.payload.get("event_type") in {"DAY_EXTENDED", "DAY_SHORTENED"}

    def _take_transport_order(self) -> int:
        order = self._next_transport_order
        self._next_transport_order += 1
        return order

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
