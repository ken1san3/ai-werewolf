"""Phase 3.4 controller for bounded, deadline-aware chat opportunities."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, replace
import inspect
import time
from typing import Callable

from ai_client.brain import (
    BrainDecision,
    BrainInvocationArbiter,
    BrainInvocationPriority,
    ChatDecision,
    CoDeclareDecision,
    CoReportDecision,
    DecisionOutcome,
    DecisionStatus,
    DispatchDeadline,
    FeatureControllerExit,
    FeatureControllerExitReason,
)
from ai_client.network import ChatAction, CoDeclareAction, CoReportAction
from ai_client.world import (
    ActionRejectionObservation,
    ChatRecord,
    CoDeclarationRecord,
    CoReportRecord,
    Freshness,
    HistoryQuery,
    PhaseDeadlineReachedObservation,
    TransportObservationQuery,
    WorldState,
)

from .randomness import deterministic_jitter_seconds
from .frequency import (
    FrequencyDecision,
    FrequencySuppression,
    PreparedSpeakingOpportunity,
    SpeakingFrequencyPolicy,
    SpeakingFrequencyState,
    SpeakingOpportunity,
    SpeakingProfile,
)
from .types import (
    CoGenerationState,
    ReactionChatConfig,
    ReactionChatLifecycle,
    ReactionChatSnapshot,
    ReactionOutcome,
    ReactionOutcomeStatus,
    ReactionPhaseKey,
    ReactionTrigger,
    ReactionTriggerKind,
)


@dataclass(frozen=True)
class _PendingOpportunity:
    trigger: ReactionTrigger
    due: float
    channel: str | None = None
    mapping_order: int | None = None
    source_player_id: str | None = None
    source_message: str | None = None
    cooldown_deferred: bool = False


@dataclass(frozen=True)
class _FrequencyEvidence:
    evaluation_ordinal: int | None = None
    event_importance: float | None = None
    threshold: float | None = None
    draw: float | None = None
    source_fingerprint: str | None = None
    suppression: FrequencySuppression | None = None


class ReactionChatController:
    """Coalesce World triggers and submit bounded shared-arbiter calls."""

    def __init__(
        self,
        *,
        world: WorldState,
        invoker: BrainInvocationArbiter,
        master_seed: int,
        config: ReactionChatConfig = ReactionChatConfig(),
        frequency_policy: SpeakingFrequencyPolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(config, ReactionChatConfig):
            raise TypeError("config must be ReactionChatConfig")
        if not isinstance(invoker, BrainInvocationArbiter):
            raise TypeError("invoker must be BrainInvocationArbiter")
        if isinstance(master_seed, bool) or not isinstance(master_seed, int):
            raise ValueError("master_seed must be an integer")
        if not callable(clock):
            raise TypeError("clock must be callable")
        frequency_profile: SpeakingProfile | None = None
        if frequency_policy is not None:
            try:
                callback_parameter = inspect.signature(
                    invoker.invoke
                ).parameters.get("on_brain_start")
            except (TypeError, ValueError):
                callback_parameter = None
            if callback_parameter is None or callback_parameter.kind not in {
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            } or callback_parameter.default is not None:
                raise RuntimeError(
                    "frequency_policy requires BrainInvocationArbiter.on_brain_start"
                )
            if not callable(getattr(frequency_policy, "prepare", None)) or not callable(
                getattr(frequency_policy, "evaluate", None)
            ):
                raise TypeError("frequency_policy must implement prepare() and evaluate()")
            frequency_profile = getattr(frequency_policy, "profile", None)
            if not isinstance(frequency_profile, SpeakingProfile):
                raise TypeError("frequency_policy must expose a SpeakingProfile as profile")
            if (
                frequency_profile.cooldown_seconds
                < config.minimum_accepted_chat_interval_seconds
            ):
                raise ValueError(
                    "frequency cooldown must be at least the minimum accepted-chat interval"
                )
        self.world = world
        self.invoker = invoker
        self.master_seed = master_seed
        self.config = config
        self.frequency_policy = frequency_policy
        self._frequency_profile = frequency_profile
        self._clock = clock

        self._lifecycle = ReactionChatLifecycle.NEW
        self._task: asyncio.Task[FeatureControllerExit] | None = None
        self._exit: FeatureControllerExit | None = None
        self._started = False
        self._phase_key: ReactionPhaseKey | None = None
        self._mapping_order: int | None = None
        self._mapping_ready = False
        self._history_cursor = 0
        self._transport_cursor = 0
        self._phase_chat_invocations = 0
        self._chat_brain_invocations = 0
        self._send_count = 0
        self._accepted_count = 0
        self._rejected_count = 0
        self._deadline_suppressed_count = 0
        self._intentional_silence_count = 0
        self._last_accepted_chat_at: float | None = None
        if frequency_profile is not None:
            self._frequency_evaluation_count = 0
            self._frequency_source_fingerprints: deque[str] = deque(
                maxlen=frequency_profile.repetition_window
            )
        self._initial_generated = False
        self._history_gap_closed = False
        self._transport_gap_closed = False
        self._chat_deadline_closed = False
        self._chat_rejected_closed = False
        self._history_gap_recorded = False
        self._transport_gap_recorded = False
        self._co_state: CoGenerationState | None = None
        self._pending_initial: _PendingOpportunity | None = None
        self._pending_reaction: _PendingOpportunity | None = None
        self._pending_co: _PendingOpportunity | None = None
        self._outcomes: deque[ReactionOutcome] = deque(maxlen=config.outcome_retention)

    def start(self) -> None:
        if self._started:
            raise RuntimeError("ReactionChatController.start() may only be called once")
        self._started = True
        self._lifecycle = ReactionChatLifecycle.RUNNING
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._exit is not None:
            task = self._task
            if task is not None and task is not asyncio.current_task():
                await asyncio.shield(task)
            return
        if not self._started:
            self._started = True
            self._lifecycle = ReactionChatLifecycle.STOPPED
            self._exit = FeatureControllerExit(
                "reaction_chat", FeatureControllerExitReason.STOP_REQUESTED
            )
            return
        self._lifecycle = ReactionChatLifecycle.STOPPING
        task = self._task
        if task is not None and task is not asyncio.current_task():
            if not task.done():
                task.cancel()
            await asyncio.shield(task)

    async def wait(self) -> FeatureControllerExit:
        """Wait for one immutable terminal result without exposing the own task."""

        if not self._started:
            raise RuntimeError("ReactionChatController.wait() requires start()")
        if self._exit is not None:
            return self._exit
        task = self._task
        if task is None:
            raise RuntimeError("ReactionChatController has no running task")
        return await asyncio.shield(task)

    def snapshot(self) -> ReactionChatSnapshot:
        return ReactionChatSnapshot(
            lifecycle=self._lifecycle,
            current_phase_key=self._phase_key,
            history_cursor=self._history_cursor,
            transport_cursor=self._transport_cursor,
            chat_brain_invocations=self._chat_brain_invocations,
            send_count=self._send_count,
            accepted_count=self._accepted_count,
            rejected_count=self._rejected_count,
            deadline_suppressed_count=self._deadline_suppressed_count,
            intentional_silence_count=self._intentional_silence_count,
            co_generation_state=self._co_state,
            outcomes=tuple(self._outcomes),
            frequency_state=(
                None
                if self.frequency_policy is None
                else SpeakingFrequencyState(
                    phase_key=self._phase_key,
                    evaluation_count=self._frequency_evaluation_count,
                    committed_brain_invocations=self._phase_chat_invocations,
                    last_accepted_chat_at=self._last_accepted_chat_at,
                    recent_source_fingerprints=tuple(
                        self._frequency_source_fingerprints
                    ),
                )
            ),
        )

    async def _run(self) -> FeatureControllerExit:
        after_version = -1
        reason = FeatureControllerExitReason.STOP_REQUESTED
        error_type: str | None = None
        try:
            while self._lifecycle is ReactionChatLifecycle.RUNNING:
                snapshot = self.world.snapshot()
                after_version = max(after_version, snapshot.version)
                self._observe()
                snapshot = self.world.snapshot()
                after_version = max(after_version, snapshot.version)
                if snapshot.freshness in {Freshness.ENDED, Freshness.FAILED}:
                    reason = (
                        FeatureControllerExitReason.WORLD_ENDED
                        if snapshot.freshness is Freshness.ENDED
                        else FeatureControllerExitReason.WORLD_FAILED
                    )
                    break
                pending = self._next_pending()
                if pending is None:
                    await self.world.wait_for_update(after_version)
                    continue
                delay = pending.due - self._clock()
                if delay > 0:
                    await self._wait_for_update_or_due(after_version, delay)
                    continue
                await self._execute(pending)
                after_version = max(after_version, self.world.snapshot().version)
        except asyncio.CancelledError:
            if self._lifecycle is not ReactionChatLifecycle.STOPPING:
                reason = FeatureControllerExitReason.FAILED
                error_type = "CancelledError"
        except Exception as error:
            reason = FeatureControllerExitReason.FAILED
            error_type = type(error).__name__
        finally:
            self._clear_pending()
        self._exit = FeatureControllerExit("reaction_chat", reason, error_type)
        self._lifecycle = (
            ReactionChatLifecycle.FAILED
            if reason
            in {
                FeatureControllerExitReason.WORLD_FAILED,
                FeatureControllerExitReason.FAILED,
            }
            else ReactionChatLifecycle.STOPPED
        )
        return self._exit

    async def _wait_for_update_or_due(self, after_version: int, delay: float) -> None:
        world_task = asyncio.create_task(self.world.wait_for_update(after_version))
        timer_task = asyncio.create_task(asyncio.sleep(delay))
        try:
            await asyncio.wait(
                {world_task, timer_task}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for task in (world_task, timer_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(world_task, timer_task, return_exceptions=True)

    def _observe(self) -> None:
        snapshot = self.world.snapshot()
        if snapshot.freshness is Freshness.CURRENT and not snapshot.is_caught_up:
            # The Network receiver may already have advanced beyond the World
            # consumer.  Preserve phase work while the exclusive consumer
            # drains instead of treating this transient as a phase reset.
            self._mapping_ready = False
            return
        actions = self.world.current_actions()
        transport = self.world.transport_observations(
            TransportObservationQuery(after_order=self._transport_cursor)
        )
        key = self._current_key(snapshot, actions.actions, transport.current_deadline)
        if key != self._phase_key:
            previous_connection = (
                self._phase_key.connection_generation if self._phase_key is not None else None
            )
            self._reset_phase(key)
            if key is not None and previous_connection not in {None, key.connection_generation}:
                self._history_cursor = 0

        if transport.gap_before_first and key is not None:
            self._transport_gap_closed = True
            self._chat_deadline_closed = True
            self._close_co()
            if not self._transport_gap_recorded:
                self._record_gap(ReactionOutcomeStatus.TRANSPORT_GAP)
                self._transport_gap_recorded = True
        for observation in transport.observations:
            if isinstance(observation, ActionRejectionObservation) and observation.action in {
                "chat.send",
                "co.declare",
                "co.report",
            }:
                self._rejected_count += 1
        if transport.last_order is not None:
            self._transport_cursor = max(self._transport_cursor, transport.last_order)

        retention = snapshot.history_retention
        if retention.last_order is not None and retention.last_order < self._history_cursor:
            self._history_cursor = 0
        history = self.world.history(HistoryQuery(after_order=self._history_cursor))
        if not history.complete and key is not None:
            self._history_gap_closed = True
            if not self._history_gap_recorded:
                self._record_gap(ReactionOutcomeStatus.HISTORY_GAP)
                self._history_gap_recorded = True
        self._observe_history(history.records, actions.actions)
        if history.retention.last_order is not None:
            self._history_cursor = history.retention.last_order

        if key is None:
            self._clear_pending()
            return
        deadline = transport.current_deadline
        if deadline is None:
            self._mapping_ready = False
            return
        self._mapping_ready = True
        previous_mapping_order = self._mapping_order
        if deadline.mapping_order != previous_mapping_order:
            self._mapping_order = deadline.mapping_order
            self._rebase_pending_for_mapping()
            if (
                previous_mapping_order is not None
                and (
                    self.frequency_policy is not None
                    or self._phase_chat_invocations
                    < self.config.max_chat_attempts_per_phase
                )
                and not self._transport_gap_closed
                and not self._chat_rejected_closed
            ):
                self._chat_deadline_closed = False
                if self._pending_initial is None and self._pending_reaction is None:
                    self._pending_initial = self._make_pending(
                        ReactionTriggerKind.INITIAL_CHAT,
                        source_order=None,
                        attempt_ordinal=self._phase_chat_invocations,
                    )

        chat_handles = tuple(handle for handle in actions.actions if isinstance(handle, ChatAction))
        co_handles = tuple(
            handle
            for handle in actions.actions
            if isinstance(handle, (CoDeclareAction, CoReportAction))
        )
        if chat_handles and not self._initial_generated:
            self._initial_generated = True
            if not self._transport_gap_closed and not self._chat_rejected_closed:
                self._pending_initial = self._make_pending(
                    ReactionTriggerKind.INITIAL_CHAT,
                    source_order=None,
                    attempt_ordinal=self._phase_chat_invocations,
                )
        if co_handles and self._co_state is None:
            self._co_state = CoGenerationState(key.action_generation, False, False)
            if not self._transport_gap_closed:
                self._pending_co = self._make_pending(
                    ReactionTriggerKind.CO_ACTION,
                    source_order=None,
                    attempt_ordinal=0,
                )
        elif not co_handles and self._co_state is not None and not self._co_state.invoked:
            self._close_co()

        if (
            self.frequency_policy is None
            and self._phase_chat_invocations >= self.config.max_chat_attempts_per_phase
        ):
            self._pending_initial = None
            self._pending_reaction = None

    def _observe_history(self, records: tuple[object, ...], actions: tuple[object, ...]) -> None:
        key = self._phase_key
        snapshot = self.world.snapshot()
        player_id = snapshot.self_view.player_id if snapshot.self_view is not None else None
        if key is None or player_id is None:
            return
        channels = {
            handle.channel for handle in actions if isinstance(handle, ChatAction)
        }
        for record in records:
            if not isinstance(record, ChatRecord):
                continue
            if record.day != key.day or record.phase != key.phase:
                continue
            if record.player_id == player_id:
                if self.frequency_policy is None:
                    self._last_accepted_chat_at = self._clock()
                continue
            if (
                record.player_id is None
                or record.channel not in channels
                or self._history_gap_closed
                or self._transport_gap_closed
                or self._chat_rejected_closed
                or (
                    self.frequency_policy is None
                    and self._phase_chat_invocations
                    >= self.config.max_chat_attempts_per_phase
                )
            ):
                continue
            self._pending_reaction = self._make_pending(
                ReactionTriggerKind.REACTION_CHAT,
                source_order=record.order,
                attempt_ordinal=self._phase_chat_invocations,
                channel=record.channel,
                observed_at=self._clock(),
                source_player_id=record.player_id,
                source_message=record.message,
            )

    @staticmethod
    def _current_key(snapshot, actions: tuple[object, ...], deadline) -> ReactionPhaseKey | None:
        if (
            snapshot.freshness is not Freshness.CURRENT
            or not snapshot.is_caught_up
            or snapshot.phase is None
            or snapshot.self_view is None
            or snapshot.phase.day < 1
        ):
            return None
        if deadline is None:
            if not actions:
                return None
            first = actions[0]
            connection_generation = getattr(first, "connection_generation", None)
            action_generation = getattr(first, "action_generation", None)
            if not isinstance(connection_generation, int) or not isinstance(
                action_generation, int
            ):
                return None
            if any(
                getattr(handle, "connection_generation", None) != connection_generation
                or getattr(handle, "action_generation", None) != action_generation
                for handle in actions
            ):
                return None
            return ReactionPhaseKey(
                connection_generation=connection_generation,
                day=snapshot.phase.day,
                phase=snapshot.phase.phase,
                action_generation=action_generation,
            )
        if deadline.phase != snapshot.phase.phase or deadline.day != snapshot.phase.day:
            return None
        if actions and any(
            getattr(handle, "connection_generation", None) != deadline.connection_generation
            or getattr(handle, "action_generation", None) != deadline.action_generation
            for handle in actions
        ):
            return None
        return ReactionPhaseKey(
            connection_generation=deadline.connection_generation,
            day=deadline.day,
            phase=deadline.phase,
            action_generation=deadline.action_generation,
        )

    def _reset_phase(self, key: ReactionPhaseKey | None) -> None:
        self._phase_key = key
        self._mapping_order = None
        self._mapping_ready = False
        self._phase_chat_invocations = 0
        self._last_accepted_chat_at = None
        if self.frequency_policy is not None:
            self._frequency_evaluation_count = 0
            self._frequency_source_fingerprints.clear()
        self._initial_generated = False
        self._history_gap_closed = False
        self._transport_gap_closed = False
        self._chat_deadline_closed = False
        self._chat_rejected_closed = False
        self._history_gap_recorded = False
        self._transport_gap_recorded = False
        self._co_state = None
        self._clear_pending()

    def _make_pending(
        self,
        kind: ReactionTriggerKind,
        *,
        source_order: int | None,
        attempt_ordinal: int,
        channel: str | None = None,
        observed_at: float | None = None,
        source_player_id: str | None = None,
        source_message: str | None = None,
        cooldown_deferred: bool = False,
    ) -> _PendingOpportunity:
        key = self._phase_key
        snapshot = self.world.snapshot()
        assert key is not None and snapshot.self_view is not None
        lower, upper = (
            self.config.reaction_jitter_seconds
            if kind is ReactionTriggerKind.REACTION_CHAT
            else self.config.initial_jitter_seconds
        )
        trigger = ReactionTrigger(kind, key, source_order, attempt_ordinal)
        base = self._clock() if observed_at is None else observed_at
        due = base + deterministic_jitter_seconds(
            master_seed=self.master_seed,
            player_id=snapshot.self_view.player_id,
            phase_key=key,
            trigger_kind=kind,
            source_order=source_order,
            attempt_ordinal=attempt_ordinal,
            lower_seconds=lower,
            upper_seconds=upper,
        )
        if kind is ReactionTriggerKind.REACTION_CHAT and self._last_accepted_chat_at is not None:
            due = max(
                due,
                self._last_accepted_chat_at
                + self.config.minimum_accepted_chat_interval_seconds,
            )
        return _PendingOpportunity(
            trigger,
            due,
            channel,
            self._mapping_order,
            source_player_id,
            source_message,
            cooldown_deferred,
        )

    def _next_pending(self) -> _PendingOpportunity | None:
        if not self._mapping_ready:
            return None
        if self._pending_co is not None:
            return self._pending_co
        if self._pending_initial is not None:
            return self._pending_initial
        return self._pending_reaction

    async def _execute(self, pending: _PendingOpportunity) -> None:
        self._observe()
        if self._lifecycle is not ReactionChatLifecycle.RUNNING:
            return
        if self._phase_key != pending.trigger.phase_key:
            self._record_outcome(pending, None, None, ReactionOutcomeStatus.DEADLINE_SUPPRESSED)
            return
        if not self._pending_is_current(pending):
            return
        self._remove_pending(pending)
        is_chat = pending.trigger.kind is not ReactionTriggerKind.CO_ACTION
        if is_chat and self.frequency_policy is not None:
            await self._execute_with_frequency(pending)
            return
        if is_chat and (
            self._phase_chat_invocations >= self.config.max_chat_attempts_per_phase
            or self._transport_gap_closed
            or self._chat_rejected_closed
            or self._chat_deadline_closed
        ):
            return

        transport = self.world.transport_observations()
        deadline = transport.current_deadline
        now = self._clock()
        if (
            deadline is None
            or not self._deadline_matches_key(deadline, pending.trigger.phase_key)
            or pending.mapping_order != deadline.mapping_order
            or deadline.local_deadline_monotonic is None
        ):
            self._suppress_deadline(pending, is_chat)
            return
        cutoff = deadline.local_deadline_monotonic - self.config.deadline_guard_seconds
        if cutoff - now < self.config.minimum_start_budget_seconds:
            self._suppress_deadline(pending, is_chat)
            return
        if (
            is_chat
            and self._last_accepted_chat_at is not None
            and now
            < self._last_accepted_chat_at
            + self.config.minimum_accepted_chat_interval_seconds
        ):
            rescheduled = replace(
                pending,
                due=self._last_accepted_chat_at
                + self.config.minimum_accepted_chat_interval_seconds,
            )
            self._store_pending(rescheduled)
            return

        actions = self.world.current_actions().actions
        if pending.trigger.kind is ReactionTriggerKind.CO_ACTION:
            relevant = tuple(
                handle
                for handle in actions
                if isinstance(handle, (CoDeclareAction, CoReportAction))
            )
        elif pending.channel is not None:
            relevant = tuple(
                handle
                for handle in actions
                if isinstance(handle, ChatAction) and handle.channel == pending.channel
            )
        else:
            relevant = tuple(handle for handle in actions if isinstance(handle, ChatAction))
        if not relevant:
            if is_chat:
                self._chat_deadline_closed = True
            else:
                self._close_co()
            return
        if is_chat:
            self._phase_chat_invocations += 1
            self._chat_brain_invocations += 1
        else:
            assert self._co_state is not None
            self._co_state = replace(self._co_state, invoked=True)
        timeout = min(
            self.config.brain_timeout_seconds,
            cutoff - self._clock(),
        )
        if timeout <= 0:
            self._suppress_deadline(pending, is_chat)
            return
        dispatch_deadline = DispatchDeadline(
            mapping_order=deadline.mapping_order,
            phase=deadline.phase,
            day=deadline.day,
            connection_generation=deadline.connection_generation,
            action_generation=deadline.action_generation,
            not_after_monotonic=cutoff,
        )
        result = await self.invoker.invoke(
            owner="reaction_chat",
            priority=BrainInvocationPriority.REACTION,
            allowed_handles=relevant,
            timeout_seconds=timeout,
            dispatch_deadline=dispatch_deadline,
        )
        outcome = result.outcome
        action_kind = self._action_kind(relevant, outcome)
        if outcome.status is DecisionStatus.SENT:
            self._send_count += 1
            dispatched_decision = result.dispatched_decision
            assert dispatched_decision is not None
            status = await self._await_finalization(
                pending,
                action_kind,
                dispatched_decision,
                history_after=self._history_cursor,
                transport_after=self._transport_cursor,
            )
            self._record_outcome(pending, outcome, action_kind, status)
            if status is ReactionOutcomeStatus.ACCEPTED:
                self._accepted_count += 1
                if is_chat:
                    self._last_accepted_chat_at = self._clock()
            elif status is ReactionOutcomeStatus.REJECTED:
                if is_chat:
                    self._chat_rejected_closed = True
                else:
                    self._close_co()
            elif status is ReactionOutcomeStatus.TRANSPORT_GAP:
                self._transport_gap_closed = True
                if is_chat:
                    self._chat_deadline_closed = True
                else:
                    self._close_co()
            if not is_chat:
                self._close_co()
        else:
            status = self._map_brain_outcome(outcome)
            self._record_outcome(pending, outcome, action_kind, status)
            if status is ReactionOutcomeStatus.NO_DECISION:
                self._intentional_silence_count += 1
            elif status is ReactionOutcomeStatus.DEADLINE_SUPPRESSED:
                self._deadline_suppressed_count += 1
                if is_chat:
                    self._chat_deadline_closed = True
            elif status is ReactionOutcomeStatus.TRANSPORT_GAP:
                self._transport_gap_closed = True
            if not is_chat:
                self._close_co()

        if self._phase_chat_invocations >= self.config.max_chat_attempts_per_phase:
            self._pending_initial = None
            self._pending_reaction = None

    async def _execute_with_frequency(self, pending: _PendingOpportunity) -> None:
        """Apply the approved pre-Brain gate without changing the legacy path."""

        policy = self.frequency_policy
        profile = self._frequency_profile
        assert policy is not None and profile is not None

        # Existing controller safety checks are the first, authoritative gate.
        if (
            self._transport_gap_closed
            or self._chat_rejected_closed
            or self._chat_deadline_closed
            or (
                self._history_gap_closed
                and pending.trigger.kind is ReactionTriggerKind.REACTION_CHAT
            )
        ):
            return
        transport = self.world.transport_observations()
        deadline = transport.current_deadline
        now = self._clock()
        if (
            deadline is None
            or not self._deadline_matches_key(deadline, pending.trigger.phase_key)
            or pending.mapping_order != deadline.mapping_order
            or deadline.local_deadline_monotonic is None
        ):
            self._suppress_deadline(pending, True)
            return
        cutoff = deadline.local_deadline_monotonic - self.config.deadline_guard_seconds
        if cutoff - now < self.config.minimum_start_budget_seconds:
            self._suppress_deadline(pending, True)
            return

        actions = self.world.current_actions().actions
        if pending.channel is not None:
            relevant = tuple(
                handle
                for handle in actions
                if isinstance(handle, ChatAction) and handle.channel == pending.channel
            )
        else:
            relevant = tuple(handle for handle in actions if isinstance(handle, ChatAction))
        if not relevant:
            self._chat_deadline_closed = True
            return

        if self._phase_chat_invocations >= self.config.max_chat_attempts_per_phase:
            self._record_frequency_suppression(
                pending, FrequencySuppression.INVOCATION_CAP
            )
            return
        if (
            self._frequency_evaluation_count
            >= profile.max_trigger_evaluations_per_phase
        ):
            self._record_frequency_suppression(
                pending, FrequencySuppression.EVALUATION_CAP
            )
            return

        opportunity = self._speaking_opportunity(pending)
        prepared = policy.prepare(opportunity)
        if not isinstance(prepared, PreparedSpeakingOpportunity):
            raise TypeError("frequency prepare() must return PreparedSpeakingOpportunity")
        PreparedSpeakingOpportunity(
            prepared.opportunity,
            prepared.event_importance,
            prepared.source_message_sha256,
        )
        if prepared.opportunity != opportunity:
            raise ValueError("frequency prepare() returned an inconsistent opportunity")
        prepared_evidence = _FrequencyEvidence(
            event_importance=prepared.event_importance,
            source_fingerprint=prepared.source_message_sha256,
        )
        if (
            prepared.source_message_sha256 is not None
            and prepared.source_message_sha256 in self._frequency_source_fingerprints
        ):
            self._record_frequency_suppression(
                pending,
                FrequencySuppression.REPETITION,
                evidence=prepared_evidence,
            )
            return
        if self._newest_chat_is_self(pending):
            self._record_frequency_suppression(
                pending,
                FrequencySuppression.SELF_CHAIN,
                evidence=prepared_evidence,
            )
            return

        if self._last_accepted_chat_at is not None:
            cooldown_due = self._last_accepted_chat_at + profile.cooldown_seconds
            if now < cooldown_due:
                if not pending.cooldown_deferred and cooldown_due < cutoff:
                    self._store_pending(
                        replace(
                            pending,
                            due=cooldown_due,
                            cooldown_deferred=True,
                        )
                    )
                    return
                self._record_frequency_suppression(
                    pending,
                    FrequencySuppression.COOLDOWN,
                    evidence=prepared_evidence,
                )
                return

        decision = policy.evaluate(prepared)
        self._validate_frequency_decision(decision, prepared, profile)
        self._frequency_evaluation_count += 1
        evaluation_ordinal = self._frequency_evaluation_count
        if prepared.source_message_sha256 is not None:
            self._frequency_source_fingerprints.append(
                prepared.source_message_sha256
            )
        evaluated_evidence = _FrequencyEvidence(
            evaluation_ordinal=evaluation_ordinal,
            event_importance=decision.event_importance,
            threshold=decision.threshold,
            draw=decision.draw,
            source_fingerprint=prepared.source_message_sha256,
            suppression=decision.suppression,
        )
        if not decision.should_invoke:
            self._record_frequency_suppression(
                pending,
                FrequencySuppression.PROBABILITY,
                evidence=evaluated_evidence,
            )
            return

        timeout = min(self.config.brain_timeout_seconds, cutoff - self._clock())
        if timeout <= 0:
            self._suppress_deadline(
                pending, True, frequency=evaluated_evidence
            )
            return
        dispatch_deadline = DispatchDeadline(
            mapping_order=deadline.mapping_order,
            phase=deadline.phase,
            day=deadline.day,
            connection_generation=deadline.connection_generation,
            action_generation=deadline.action_generation,
            not_after_monotonic=cutoff,
        )

        committed = False

        def on_brain_start() -> None:
            nonlocal committed
            if committed:
                raise RuntimeError("frequency Brain invocation was committed more than once")
            committed = True
            self._phase_chat_invocations += 1
            self._chat_brain_invocations += 1

        invoke_arguments = dict(
            owner="reaction_chat",
            priority=BrainInvocationPriority.REACTION,
            allowed_handles=relevant,
            timeout_seconds=timeout,
            dispatch_deadline=dispatch_deadline,
        )
        result = await self.invoker.invoke(
            **invoke_arguments,
            on_brain_start=on_brain_start,
        )

        outcome = result.outcome
        action_kind = self._action_kind(relevant, outcome)
        if outcome.status is DecisionStatus.SENT:
            self._send_count += 1
            dispatched_decision = result.dispatched_decision
            assert dispatched_decision is not None
            status = await self._await_finalization(
                pending,
                action_kind,
                dispatched_decision,
                history_after=self._history_cursor,
                transport_after=self._transport_cursor,
            )
            self._record_outcome(
                pending, outcome, action_kind, status, frequency=evaluated_evidence
            )
            if status is ReactionOutcomeStatus.ACCEPTED:
                self._accepted_count += 1
                self._last_accepted_chat_at = self._clock()
            elif status is ReactionOutcomeStatus.REJECTED:
                self._chat_rejected_closed = True
            elif status is ReactionOutcomeStatus.TRANSPORT_GAP:
                self._transport_gap_closed = True
                self._chat_deadline_closed = True
        else:
            status = self._map_brain_outcome(outcome)
            self._record_outcome(
                pending, outcome, action_kind, status, frequency=evaluated_evidence
            )
            if status is ReactionOutcomeStatus.NO_DECISION:
                self._intentional_silence_count += 1
            elif status is ReactionOutcomeStatus.DEADLINE_SUPPRESSED:
                self._deadline_suppressed_count += 1
                self._chat_deadline_closed = True
            elif status is ReactionOutcomeStatus.TRANSPORT_GAP:
                self._transport_gap_closed = True

    def _speaking_opportunity(self, pending: _PendingOpportunity) -> SpeakingOpportunity:
        snapshot = self.world.snapshot()
        self_view = snapshot.self_view
        if self_view is None:
            raise RuntimeError("frequency evaluation requires current self view")
        self_player = next(
            (player for player in snapshot.players if player.player_id == self_view.player_id),
            None,
        )
        if self_player is None:
            raise RuntimeError("frequency evaluation requires current player display name")
        is_initial = pending.trigger.kind is ReactionTriggerKind.INITIAL_CHAT
        return SpeakingOpportunity(
            phase_key=pending.trigger.phase_key,
            trigger_kind=pending.trigger.kind,
            source_order=None if is_initial else pending.trigger.source_order,
            source_player_id=None if is_initial else pending.source_player_id,
            source_channel=None if is_initial else pending.channel,
            source_message=None if is_initial else pending.source_message,
            self_player_id=self_view.player_id,
            self_display_name=self_player.display_name,
            attempt_ordinal=self._phase_chat_invocations,
        )

    def _newest_chat_is_self(self, pending: _PendingOpportunity) -> bool:
        snapshot = self.world.snapshot()
        self_view = snapshot.self_view
        if self_view is None:
            return False
        records = self.world.history(
            HistoryQuery(kinds=frozenset({"chat"}), day=pending.trigger.phase_key.day)
        ).records
        current = tuple(
            record
            for record in records
            if isinstance(record, ChatRecord)
            and record.phase == pending.trigger.phase_key.phase
            and (pending.channel is None or record.channel == pending.channel)
        )
        return (
            bool(current)
            and max(current, key=lambda record: record.order).player_id
            == self_view.player_id
        )

    @staticmethod
    def _validate_frequency_decision(
        decision: object,
        prepared: PreparedSpeakingOpportunity,
        profile: SpeakingProfile,
    ) -> None:
        if not isinstance(decision, FrequencyDecision):
            raise TypeError("frequency evaluate() must return FrequencyDecision")
        FrequencyDecision(
            decision.should_invoke,
            decision.suppression,
            decision.event_importance,
            decision.threshold,
            decision.draw,
        )
        if decision.event_importance != prepared.event_importance:
            raise ValueError("frequency decision changed event importance")
        expected_threshold = profile.talkativeness * prepared.event_importance
        if decision.threshold != expected_threshold:
            raise ValueError("frequency decision returned an inconsistent threshold")
        if decision.should_invoke != (decision.draw < decision.threshold):
            raise ValueError("frequency decision is inconsistent with draw and threshold")

    def _record_frequency_suppression(
        self,
        pending: _PendingOpportunity,
        suppression: FrequencySuppression,
        *,
        evidence: _FrequencyEvidence = _FrequencyEvidence(),
    ) -> None:
        self._record_outcome(
            pending,
            None,
            "chat.send",
            ReactionOutcomeStatus.FREQUENCY_SUPPRESSED,
            frequency=replace(evidence, suppression=suppression),
        )

    async def _await_finalization(
        self,
        pending: _PendingOpportunity,
        action_kind: str | None,
        dispatched_decision: BrainDecision,
        *,
        history_after: int,
        transport_after: int,
    ) -> ReactionOutcomeStatus:
        while self._lifecycle is ReactionChatLifecycle.RUNNING:
            self._observe()
            history = self.world.history(HistoryQuery(after_order=history_after))
            transport = self.world.transport_observations(
                TransportObservationQuery(after_order=transport_after)
            )
            if transport.gap_before_first:
                return ReactionOutcomeStatus.TRANSPORT_GAP
            if self._phase_key != pending.trigger.phase_key:
                return ReactionOutcomeStatus.TRANSPORT_GAP
            current = transport.current_deadline
            if current is None:
                version = self.world.snapshot().version
                await self.world.wait_for_update(version)
                continue
            if not self._deadline_matches_key(current, pending.trigger.phase_key):
                return ReactionOutcomeStatus.TRANSPORT_GAP
            if self._accepted_record_exists(
                pending, action_kind, dispatched_decision, history.records
            ):
                return ReactionOutcomeStatus.ACCEPTED
            for observation in transport.observations:
                if (
                    isinstance(observation, ActionRejectionObservation)
                    and observation.action == action_kind
                    and observation.connection_generation
                    == pending.trigger.phase_key.connection_generation
                ):
                    return ReactionOutcomeStatus.REJECTED
            deadline_reached = any(
                isinstance(observation, PhaseDeadlineReachedObservation)
                and observation.connection_generation
                == pending.trigger.phase_key.connection_generation
                and observation.action_generation
                == pending.trigger.phase_key.action_generation
                and observation.local_deadline_monotonic
                == current.local_deadline_monotonic
                for observation in transport.observations
            )
            if deadline_reached:
                return ReactionOutcomeStatus.TRANSPORT_GAP
            version = self.world.snapshot().version
            await self.world.wait_for_update(version)
        return ReactionOutcomeStatus.DEADLINE_SUPPRESSED

    def _accepted_record_exists(
        self,
        pending: _PendingOpportunity,
        action_kind: str | None,
        dispatched_decision: BrainDecision,
        records: tuple[object, ...],
    ) -> bool:
        snapshot = self.world.snapshot()
        player_id = snapshot.self_view.player_id if snapshot.self_view is not None else None
        if player_id is None:
            return False
        key = pending.trigger.phase_key
        if pending.trigger.kind is not ReactionTriggerKind.CO_ACTION:
            if not isinstance(dispatched_decision, ChatDecision):
                return False
            return any(
                isinstance(record, ChatRecord)
                and record.player_id == player_id
                and record.day == key.day
                and record.phase == key.phase
                and (pending.channel is None or record.channel == pending.channel)
                and record.message == dispatched_decision.message
                for record in records
            )
        if action_kind == "co.declare" and isinstance(
            dispatched_decision, CoDeclareDecision
        ):
            return any(
                isinstance(record, CoDeclarationRecord)
                and record.player_id == player_id
                and record.day == key.day
                and record.phase == key.phase
                and record.claimed_role_id == dispatched_decision.claimed_role_id
                and record.comment == dispatched_decision.comment
                for record in records
            )
        if action_kind == "co.report" and isinstance(
            dispatched_decision, CoReportDecision
        ):
            return any(
                isinstance(record, CoReportRecord)
                and record.player_id == player_id
                and record.day == key.day
                and record.phase == key.phase
                and record.kind == dispatched_decision.kind
                and record.target_player_id == dispatched_decision.target_player_id
                and record.claimed_result == dispatched_decision.claimed_result
                for record in records
            )
        return False

    @staticmethod
    def _deadline_matches_key(deadline, key: ReactionPhaseKey) -> bool:
        return (
            deadline.phase == key.phase
            and deadline.day == key.day
            and deadline.connection_generation == key.connection_generation
            and deadline.action_generation == key.action_generation
        )

    def _suppress_deadline(
        self,
        pending: _PendingOpportunity,
        is_chat: bool,
        *,
        frequency: _FrequencyEvidence | None = None,
    ) -> None:
        self._deadline_suppressed_count += 1
        self._record_outcome(
            pending,
            None,
            None,
            ReactionOutcomeStatus.DEADLINE_SUPPRESSED,
            frequency=frequency,
        )
        if is_chat:
            self._chat_deadline_closed = True
        else:
            self._close_co()

    @staticmethod
    def _map_brain_outcome(outcome: DecisionOutcome) -> ReactionOutcomeStatus:
        return {
            DecisionStatus.NO_DECISION: ReactionOutcomeStatus.NO_DECISION,
            DecisionStatus.INVALID_DECISION: ReactionOutcomeStatus.INVALID,
            DecisionStatus.BRAIN_FAILED: ReactionOutcomeStatus.BRAIN_FAILED,
            DecisionStatus.TIMED_OUT: ReactionOutcomeStatus.TIMED_OUT,
            DecisionStatus.DEADLINE_SUPPRESSED: ReactionOutcomeStatus.DEADLINE_SUPPRESSED,
            DecisionStatus.STALE: ReactionOutcomeStatus.DEADLINE_SUPPRESSED,
            DecisionStatus.CANCELLED: ReactionOutcomeStatus.DEADLINE_SUPPRESSED,
            DecisionStatus.SEND_NOT_DELIVERED: ReactionOutcomeStatus.TRANSPORT_GAP,
            DecisionStatus.SEND_DELIVERY_UNKNOWN: ReactionOutcomeStatus.TRANSPORT_GAP,
        }.get(outcome.status, ReactionOutcomeStatus.INVALID)

    @staticmethod
    def _action_kind(
        allowed_handles: tuple[object, ...], outcome: DecisionOutcome
    ) -> str | None:
        if outcome.option_id is None:
            return None
        option = next(
            (
                handle
                for index, handle in enumerate(allowed_handles)
                if f"action:{index}" == outcome.option_id
            ),
            None,
        )
        if option is None:
            return None
        if isinstance(option, ChatAction):
            return "chat.send"
        if isinstance(option, CoDeclareAction):
            return "co.declare"
        if isinstance(option, CoReportAction):
            return "co.report"
        return None

    def _record_outcome(
        self,
        pending: _PendingOpportunity,
        brain_outcome: DecisionOutcome | None,
        action_kind: str | None,
        status: ReactionOutcomeStatus,
        *,
        frequency: _FrequencyEvidence | None = None,
    ) -> None:
        evidence = frequency or _FrequencyEvidence()
        self._outcomes.append(
            ReactionOutcome(
                trigger=pending.trigger,
                scheduled_due_monotonic=max(0.0, pending.due),
                brain_outcome=(
                    brain_outcome.status.value if brain_outcome is not None else None
                ),
                action_kind=action_kind,
                status=status,
                frequency_evaluation_ordinal=evidence.evaluation_ordinal,
                frequency_event_importance=evidence.event_importance,
                frequency_threshold=evidence.threshold,
                frequency_draw=evidence.draw,
                frequency_source_fingerprint=evidence.source_fingerprint,
                frequency_suppression=evidence.suppression,
            )
        )

    def _record_gap(self, status: ReactionOutcomeStatus) -> None:
        key = self._phase_key
        if key is None:
            return
        pending = _PendingOpportunity(
            ReactionTrigger(
                ReactionTriggerKind.REACTION_CHAT,
                key,
                None,
                self._phase_chat_invocations,
            ),
            max(0.0, self._clock()),
        )
        self._record_outcome(pending, None, "chat.send", status)

    def _store_pending(self, pending: _PendingOpportunity) -> None:
        if pending.trigger.kind is ReactionTriggerKind.CO_ACTION:
            self._pending_co = pending
        elif pending.trigger.kind is ReactionTriggerKind.INITIAL_CHAT:
            self._pending_initial = pending
        else:
            self._pending_reaction = pending

    def _rebase_pending_for_mapping(self) -> None:
        observed_at = self._clock()
        for attribute in ("_pending_initial", "_pending_reaction", "_pending_co"):
            pending = getattr(self, attribute)
            if pending is None or pending.mapping_order == self._mapping_order:
                continue
            setattr(
                self,
                attribute,
                self._make_pending(
                    pending.trigger.kind,
                    source_order=pending.trigger.source_order,
                    attempt_ordinal=pending.trigger.attempt_ordinal,
                    channel=pending.channel,
                    observed_at=observed_at,
                    source_player_id=pending.source_player_id,
                    source_message=pending.source_message,
                    cooldown_deferred=pending.cooldown_deferred,
                ),
            )

    def _pending_is_current(self, pending: _PendingOpportunity) -> bool:
        if pending.trigger.kind is ReactionTriggerKind.CO_ACTION:
            return self._pending_co is pending
        if pending.trigger.kind is ReactionTriggerKind.INITIAL_CHAT:
            return self._pending_initial is pending
        return self._pending_reaction is pending

    def _remove_pending(self, pending: _PendingOpportunity) -> None:
        if self._pending_co is pending:
            self._pending_co = None
        if self._pending_initial is pending:
            self._pending_initial = None
        if self._pending_reaction is pending:
            self._pending_reaction = None

    def _close_co(self) -> None:
        self._pending_co = None
        if self._co_state is not None:
            self._co_state = replace(self._co_state, closed=True)

    def _clear_pending(self) -> None:
        self._pending_initial = None
        self._pending_reaction = None
        self._pending_co = None
