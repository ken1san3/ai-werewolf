"""World-observed vote/ability reservation lifecycle."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import math
import time
from typing import Callable

from ai_client.brain import (
    AbilityDecision,
    BrainInvocationArbiter,
    BrainInvocationPriority,
    DecisionOutcome,
    DecisionStatus,
    DispatchDeadline,
    FeatureControllerExit,
    FeatureControllerExitReason,
    VoteDecision,
)
from ai_client.discussion import (
    BoundDiscussionContext,
    DiscussionObservationStatus,
    DiscussionTerminalReason,
    DiscussionTrigger,
    EvidenceRef,
    evidence_ref_for_record,
)
from ai_client.network import AbilityAction, VoteAction
from ai_client.world import (
    ActionAcceptedObservation,
    ActionRejectionObservation,
    Freshness,
    PhaseTimingObservation,
    ResumeRecoveryBarrier,
    TransportObservation,
    TransportObservationQuery,
    TransportObservationView,
    WorldState,
)

from .types import (
    OpportunityKey,
    ReservationKey,
    UnresolvedReservation,
    VoteAbilityConfig,
    VoteAbilityLifecycle,
    VoteAbilityOutcome,
    VoteAbilityOutcomeStatus,
    VoteAbilitySnapshot,
)


@dataclass(frozen=True)
class _Candidate:
    key: OpportunityKey
    mapping_order: int
    cutoff: float
    handles: tuple[VoteAction | AbilityAction, ...]
    action: str


@dataclass(frozen=True)
class _ObservationResolution:
    outcome_status: VoteAbilityOutcomeStatus
    discussion_status: DiscussionObservationStatus
    reason: DiscussionTerminalReason
    evidence_record: TransportObservation | None = None
    observation_generation: int | None = None
    rejection_reason: str | None = None


class VoteAbilityController:
    """Send one deterministic final reservation per server reservation slot."""

    def __init__(
        self,
        *,
        world: WorldState,
        invoker: BrainInvocationArbiter,
        discussion_context: BoundDiscussionContext | None = None,
        config: VoteAbilityConfig = VoteAbilityConfig(),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(world, WorldState):
            raise TypeError("world must be WorldState")
        if not isinstance(invoker, BrainInvocationArbiter):
            raise TypeError("invoker must be BrainInvocationArbiter")
        if discussion_context is not None and not isinstance(
            discussion_context, BoundDiscussionContext
        ):
            raise TypeError(
                "discussion_context must be BoundDiscussionContext when supplied"
            )
        if not isinstance(config, VoteAbilityConfig):
            raise TypeError("config must be VoteAbilityConfig")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self.world = world
        self.invoker = invoker
        self.discussion_context = discussion_context
        self.config = config
        self._clock = clock
        self._lifecycle = VoteAbilityLifecycle.NEW
        self._task: asyncio.Task[FeatureControllerExit] | None = None
        self._exit: FeatureControllerExit | None = None
        self._started = False
        self._current_phase: tuple[int, str] | None = None
        self._current_opportunity: OpportunityKey | None = None
        self._transport_cursor = 0
        self._pending_count = 0
        self._unresolved: UnresolvedReservation | None = None
        self._finalization_task: asyncio.Task[None] | None = None
        self._terminal: set[ReservationKey] = set()
        self._invoked_mappings: set[tuple[OpportunityKey, int]] = set()
        self._attempts: dict[ReservationKey, int] = {}
        self._rearms: dict[ReservationKey, int] = {}
        self._pre_send_failures: dict[
            ReservationKey, tuple[OpportunityKey, int]
        ] = {}
        self._not_delivered: dict[ReservationKey, tuple[int, int]] = {}
        self._accepted_count = 0
        self._rejected_count = 0
        self._unknown_count = 0
        self._deadline_suppressed_count = 0
        self._outcomes: deque[VoteAbilityOutcome] = deque(
            maxlen=config.outcome_retention
        )

    def start(self) -> None:
        if self._started:
            raise RuntimeError("VoteAbilityController.start() may only be called once")
        self._started = True
        self._lifecycle = VoteAbilityLifecycle.RUNNING
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._exit is not None:
            task = self._task
            if task is not None and task is not asyncio.current_task():
                await asyncio.shield(task)
            return
        if not self._started:
            self._started = True
            self._lifecycle = VoteAbilityLifecycle.STOPPED
            self._exit = FeatureControllerExit(
                "vote_ability", FeatureControllerExitReason.STOP_REQUESTED
            )
            return
        self._lifecycle = VoteAbilityLifecycle.STOPPING
        task = self._task
        if task is not None and task is not asyncio.current_task():
            if not task.done():
                task.cancel()
            await asyncio.shield(task)

    async def wait(self) -> FeatureControllerExit:
        if not self._started:
            raise RuntimeError("VoteAbilityController.wait() requires start()")
        if self._exit is not None:
            return self._exit
        task = self._task
        if task is None:
            raise RuntimeError("VoteAbilityController has no running task")
        return await asyncio.shield(task)

    def snapshot(self) -> VoteAbilitySnapshot:
        return VoteAbilitySnapshot(
            lifecycle=self._lifecycle,
            current_phase=self._current_phase,
            current_opportunity=self._current_opportunity,
            transport_cursor=self._transport_cursor,
            pending_count=self._pending_count,
            unresolved_reservation=self._unresolved,
            accepted_count=self._accepted_count,
            rejected_count=self._rejected_count,
            unknown_count=self._unknown_count,
            deadline_suppressed_count=self._deadline_suppressed_count,
            outcomes=tuple(self._outcomes),
        )

    async def _run(self) -> FeatureControllerExit:
        reason = FeatureControllerExitReason.STOP_REQUESTED
        error_type: str | None = None
        try:
            while self._lifecycle is VoteAbilityLifecycle.RUNNING:
                await self._observe_transport()
                snapshot = self.world.snapshot()
                self._current_phase = (
                    None
                    if snapshot.phase is None
                    else (snapshot.phase.day, snapshot.phase.phase)
                )
                if snapshot.freshness in {Freshness.ENDED, Freshness.FAILED}:
                    if self._unresolved is not None:
                        cancelled = await self._finalize_unresolved(
                            _ObservationResolution(
                                VoteAbilityOutcomeStatus.UNKNOWN,
                                DiscussionObservationStatus.RECOVERY_UNKNOWN,
                                DiscussionTerminalReason.PHASE_CHANGED,
                            )
                        )
                        if cancelled:
                            raise asyncio.CancelledError
                    reason = (
                        FeatureControllerExitReason.WORLD_ENDED
                        if snapshot.freshness is Freshness.ENDED
                        else FeatureControllerExitReason.WORLD_FAILED
                    )
                    break
                candidate = self._candidate()
                if candidate is None or self._unresolved is not None:
                    await self.world.wait_for_update(snapshot.version)
                    continue
                key = candidate.key.reservation
                if key in self._terminal:
                    await self.world.wait_for_update(snapshot.version)
                    continue
                not_delivered = self._not_delivered.get(key)
                if not_delivered is not None:
                    prior_generation, retries = not_delivered
                    if (
                        candidate.key.connection_generation <= prior_generation
                        or retries >= self.config.max_not_delivered_retries_per_reservation
                    ):
                        await self.world.wait_for_update(snapshot.version)
                        continue
                invocation_key = (candidate.key, candidate.mapping_order)
                if invocation_key in self._invoked_mappings:
                    await self.world.wait_for_update(snapshot.version)
                    continue
                if not self._prepare_pre_send_rearm(candidate):
                    await self.world.wait_for_update(snapshot.version)
                    continue
                if not self._handles_are_eligible(candidate.handles):
                    self._record(
                        candidate,
                        VoteAbilityOutcomeStatus.NO_ELIGIBLE_SELECTION,
                        terminal=True,
                    )
                    continue
                now = self._now()
                if candidate.cutoff - now < self.config.minimum_start_budget_seconds:
                    self._invoked_mappings.add(invocation_key)
                    self._record_pre_send_failure(
                        candidate, VoteAbilityOutcomeStatus.DEADLINE_SUPPRESSED
                    )
                    continue
                self._invoked_mappings.add(invocation_key)
                self._attempts[key] = self._attempts.get(key, 0) + 1
                self._pending_count = 1
                try:
                    timeout = min(
                        self.config.brain_timeout_seconds,
                        candidate.cutoff - self._now(),
                        self.invoker.controller.config.max_decision_seconds,
                    )
                    if timeout <= 0:
                        self._record_pre_send_failure(
                            candidate, VoteAbilityOutcomeStatus.DEADLINE_SUPPRESSED
                        )
                        continue
                    result = await self.invoker.invoke(
                        owner="vote_ability",
                        priority=BrainInvocationPriority.RESERVATION_ACTION,
                        allowed_handles=candidate.handles,
                        timeout_seconds=timeout,
                        dispatch_deadline=DispatchDeadline(
                            mapping_order=candidate.mapping_order,
                            phase=key.phase,
                            day=key.day,
                            connection_generation=candidate.key.connection_generation,
                            action_generation=candidate.key.action_generation,
                            not_after_monotonic=candidate.cutoff,
                            discussion_trigger=self._discussion_trigger(candidate),
                        ),
                    )
                finally:
                    self._pending_count = 0
                outcome = result.outcome
                if outcome.status is DecisionStatus.SENT:
                    self._pre_send_failures.pop(key, None)
                    receipt = outcome.receipt
                    decision = result.dispatched_decision
                    if receipt is None or not isinstance(
                        decision, (VoteDecision, AbilityDecision)
                    ):
                        raise RuntimeError("reservation SENT result is incomplete")
                    discussion = outcome.discussion
                    if self.discussion_context is None:
                        if discussion is not None:
                            raise RuntimeError(
                                "context-free reservation returned discussion correlation"
                            )
                    elif discussion is None:
                        raise RuntimeError(
                            "contextful reservation omitted discussion correlation"
                        )
                    elif (
                        discussion.context_sha256
                        != self.discussion_context.context_sha256
                    ):
                        raise RuntimeError(
                            "discussion correlation context does not match sealed context"
                        )
                    self._unresolved = UnresolvedReservation(
                        opportunity_key=candidate.key,
                        mapping_order=candidate.mapping_order,
                        action=candidate.action,
                        ability_id=(
                            self._ability_id(candidate.handles, decision)
                            if isinstance(decision, AbilityDecision)
                            else None
                        ),
                        vote_target_player_id=(
                            decision.target_player_id
                            if isinstance(decision, VoteDecision)
                            else None
                        ),
                        ability_target_player_ids=(
                            decision.target_player_ids
                            if isinstance(decision, AbilityDecision)
                            else ()
                        ),
                        request_event_id=receipt.event_id,
                        send_connection_generation=receipt.connection_generation,
                        attempt_ordinal=self._attempts[key],
                        transport_after_order=self._transport_cursor,
                        discussion=discussion,
                    )
                    continue
                await self._handle_non_sent(candidate, outcome)
        except asyncio.CancelledError:
            self._clear_current_cancellation()
            try:
                if self._finalization_task is not None:
                    await self._join_finalization()
                elif self._unresolved is not None:
                    await self._finalize_unresolved(
                        _ObservationResolution(
                            VoteAbilityOutcomeStatus.CANCELLED,
                            DiscussionObservationStatus.RECOVERY_UNKNOWN,
                            DiscussionTerminalReason.OWNER_STOPPED,
                        )
                    )
            except BaseException as finalization_error:
                reason = FeatureControllerExitReason.FAILED
                error_type = type(finalization_error).__name__
            else:
                if self._lifecycle is not VoteAbilityLifecycle.STOPPING:
                    reason = FeatureControllerExitReason.FAILED
                    error_type = "CancelledError"
        except Exception as error:
            try:
                if self._finalization_task is not None:
                    await self._join_finalization()
                elif self._unresolved is not None:
                    await self._finalize_unresolved(
                        _ObservationResolution(
                            VoteAbilityOutcomeStatus.UNKNOWN,
                            DiscussionObservationStatus.RECOVERY_UNKNOWN,
                            DiscussionTerminalReason.OWNER_STOPPED,
                        )
                    )
            except BaseException as finalization_error:
                error = finalization_error
            reason = FeatureControllerExitReason.FAILED
            error_type = type(error).__name__
        self._exit = FeatureControllerExit("vote_ability", reason, error_type)
        self._lifecycle = (
            VoteAbilityLifecycle.FAILED
            if reason
            in {FeatureControllerExitReason.WORLD_FAILED, FeatureControllerExitReason.FAILED}
            else VoteAbilityLifecycle.STOPPED
        )
        return self._exit

    def _candidate(self) -> _Candidate | None:
        snapshot = self.world.snapshot()
        actions = self.world.current_actions()
        transport = self.world.transport_observations()
        if not (
            snapshot.freshness is Freshness.CURRENT
            and snapshot.is_caught_up
            and snapshot.complete
            and snapshot.phase is not None
            and snapshot.self_view is not None
            and any(
                player.player_id == snapshot.self_view.player_id and player.alive
                for player in snapshot.players
            )
            and actions.is_caught_up
            and actions.world_version == snapshot.version
            and actions.world_last_applied_seq == snapshot.last_applied_seq
            and actions.network_last_seq == snapshot.last_applied_seq
        ):
            self._current_opportunity = None
            return None
        relevant = tuple(
            action
            for action in actions.actions
            if isinstance(action, (VoteAction, AbilityAction))
        )
        if not relevant:
            self._current_opportunity = None
            return None
        vote_family = all(isinstance(action, VoteAction) for action in relevant)
        ability_family = all(isinstance(action, AbilityAction) for action in relevant)
        if not (vote_family or ability_family):
            self._current_opportunity = None
            return None
        family = "vote" if vote_family else "ability"
        expected_phases = {"vote", "runoff"} if vote_family else {"night0", "night"}
        if snapshot.phase.phase not in expected_phases:
            self._current_opportunity = None
            return None
        first = relevant[0]
        if any(
            action.day != snapshot.phase.day
            or action.phase != snapshot.phase.phase
            or action.connection_generation != first.connection_generation
            or action.action_generation != first.action_generation
            for action in relevant
        ):
            self._current_opportunity = None
            return None
        deadline = transport.current_deadline
        if (
            deadline is None
            or deadline.local_deadline_monotonic is None
            or deadline.day != snapshot.phase.day
            or deadline.phase != snapshot.phase.phase
            or deadline.connection_generation != first.connection_generation
            or deadline.action_generation != first.action_generation
        ):
            self._current_opportunity = None
            return None
        key = OpportunityKey(
            ReservationKey(snapshot.phase.day, snapshot.phase.phase, family),
            first.connection_generation,
            first.action_generation,
        )
        self._current_opportunity = key
        return _Candidate(
            key,
            deadline.mapping_order,
            deadline.local_deadline_monotonic - self.config.deadline_guard_seconds,
            relevant,
            "vote.cast" if vote_family else "ability.use",
        )

    @staticmethod
    def _handles_are_eligible(
        handles: tuple[VoteAction | AbilityAction, ...]
    ) -> bool:
        if all(isinstance(handle, VoteAction) for handle in handles):
            if len(handles) != 1:
                return False
            handle = handles[0]
            return bool(
                isinstance(handle.target_count, int)
                and not isinstance(handle.target_count, bool)
                and handle.target_count == 1
                and len(set(handle.valid_targets)) == len(handle.valid_targets)
                and all(
                    isinstance(target, str) and target
                    for target in handle.valid_targets
                )
                and (handle.valid_targets or handle.allows_abstain)
            )
        if not handles or not all(isinstance(handle, AbilityAction) for handle in handles):
            return False
        ability_ids = tuple(handle.ability_id for handle in handles)
        if (
            any(not isinstance(ability_id, str) or not ability_id for ability_id in ability_ids)
            or len(set(ability_ids)) != len(ability_ids)
        ):
            return False
        return any(
            handle.uses_remaining != 0
            and isinstance(handle.target_count, int)
            and not isinstance(handle.target_count, bool)
            and handle.target_count >= 1
            and len(handle.valid_targets) >= handle.target_count
            and len(set(handle.valid_targets)) == len(handle.valid_targets)
            and all(isinstance(target, str) and target for target in handle.valid_targets)
            and (
                handle.uses_remaining is None
                or (
                    isinstance(handle.uses_remaining, int)
                    and not isinstance(handle.uses_remaining, bool)
                    and handle.uses_remaining > 0
                )
            )
            for handle in handles
        )

    async def _handle_non_sent(
        self, candidate: _Candidate, outcome: DecisionOutcome
    ) -> None:
        status = outcome.status
        mapped = {
            DecisionStatus.NO_DECISION: VoteAbilityOutcomeStatus.NO_DECISION,
            DecisionStatus.INVALID_DECISION: VoteAbilityOutcomeStatus.INVALID_DECISION,
            DecisionStatus.BRAIN_FAILED: VoteAbilityOutcomeStatus.BRAIN_FAILED,
            DecisionStatus.TIMED_OUT: VoteAbilityOutcomeStatus.TIMED_OUT,
            DecisionStatus.DEADLINE_SUPPRESSED: VoteAbilityOutcomeStatus.DEADLINE_SUPPRESSED,
            DecisionStatus.STALE: VoteAbilityOutcomeStatus.STALE,
            DecisionStatus.CANCELLED: VoteAbilityOutcomeStatus.CANCELLED,
            DecisionStatus.SEND_NOT_DELIVERED: VoteAbilityOutcomeStatus.NOT_DELIVERED,
            DecisionStatus.SEND_DELIVERY_UNKNOWN: VoteAbilityOutcomeStatus.UNKNOWN,
        }.get(status, VoteAbilityOutcomeStatus.INVALID_DECISION)
        key = candidate.key.reservation
        if status not in {
            DecisionStatus.STALE,
            DecisionStatus.DEADLINE_SUPPRESSED,
        }:
            self._pre_send_failures.pop(key, None)
        if status is DecisionStatus.SEND_NOT_DELIVERED:
            previous = self._not_delivered.get(key)
            retries = 0 if previous is None else previous[1] + 1
            self._not_delivered[key] = (
                candidate.key.connection_generation,
                retries,
            )
            terminal = retries >= self.config.max_not_delivered_retries_per_reservation
            self._record(candidate, mapped, terminal=terminal)
            return
        if status is DecisionStatus.SEND_DELIVERY_UNKNOWN:
            if (
                outcome.attempt_action is not None
                and outcome.attempt_action != candidate.action
            ):
                raise RuntimeError("delivery-unknown action identity is inconsistent")
            self._record(
                candidate,
                mapped,
                terminal=True,
                ability_id=outcome.ability_id,
                vote_target_player_id=outcome.vote_target_player_id,
                ability_target_player_ids=outcome.ability_target_player_ids,
                request_event_id=outcome.request_event_id,
                send_connection_generation=outcome.send_connection_generation,
            )
            self._unknown_count += 1
            return
        if status in {DecisionStatus.STALE, DecisionStatus.DEADLINE_SUPPRESSED}:
            self._record_pre_send_failure(candidate, mapped)
            return
        self._record(candidate, mapped, terminal=True)

    def _prepare_pre_send_rearm(self, candidate: _Candidate) -> bool:
        key = candidate.key.reservation
        previous = self._pre_send_failures.get(key)
        if previous is None:
            return True
        previous_key, previous_mapping_order = previous
        if (
            (candidate.key, candidate.mapping_order)
            == (previous_key, previous_mapping_order)
            or candidate.mapping_order <= previous_mapping_order
        ):
            return False
        count = self._rearms.get(key, 0)
        if count >= self.config.max_pre_send_rearms_per_reservation:
            return False
        self._rearms[key] = count + 1
        return True

    def _record_pre_send_failure(
        self,
        candidate: _Candidate,
        status: VoteAbilityOutcomeStatus,
    ) -> None:
        key = candidate.key.reservation
        self._pre_send_failures[key] = (candidate.key, candidate.mapping_order)
        terminal = (
            self._rearms.get(key, 0)
            >= self.config.max_pre_send_rearms_per_reservation
        )
        self._record(candidate, status, terminal=terminal)
        if status is VoteAbilityOutcomeStatus.DEADLINE_SUPPRESSED:
            self._deadline_suppressed_count += 1

    def _discussion_trigger(self, candidate: _Candidate) -> DiscussionTrigger | None:
        if self.discussion_context is None:
            return None
        return DiscussionTrigger(
            owner="vote_ability",
            kind=(
                "PRE_VOTE"
                if candidate.key.reservation.family == "vote"
                else "ABILITY"
            ),
            day=candidate.key.reservation.day,
            phase=candidate.key.reservation.phase,
            connection_generation=candidate.key.connection_generation,
            action_generation=candidate.key.action_generation,
            mapping_order=candidate.mapping_order,
            source=None,
        )

    async def _observe_transport(self) -> None:
        view = self.world.transport_observations(
            TransportObservationQuery(after_order=self._transport_cursor)
        )
        unresolved = self._unresolved
        if unresolved is not None and self._finalization_task is None:
            resolution = (
                self._resolve_context_free_observation_batch(unresolved, view)
                if unresolved.discussion is None
                else self._resolve_observation_batch(unresolved, view)
            )
            if resolution is not None:
                cancelled = await self._finalize_unresolved(resolution)
                if cancelled:
                    raise asyncio.CancelledError
        if view.last_order is not None:
            self._transport_cursor = max(self._transport_cursor, view.last_order)

    @staticmethod
    def _resolve_context_free_observation_batch(
        unresolved: UnresolvedReservation,
        view: TransportObservationView,
    ) -> _ObservationResolution | None:
        # Preserve the Phase 3--5 public observer contract: retained exact
        # responses are considered in order, independently of retention-gap
        # metadata, and only their absence permits a later complete barrier.
        for observation in view.observations:
            if isinstance(observation, ActionAcceptedObservation) and (
                observation.action == unresolved.action
                and observation.request_event_id == unresolved.request_event_id
                and observation.observation_connection_generation
                >= unresolved.send_connection_generation
            ):
                return _ObservationResolution(
                    VoteAbilityOutcomeStatus.ACCEPTED,
                    DiscussionObservationStatus.ACCEPTED,
                    DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
                    observation_generation=(
                        observation.observation_connection_generation
                    ),
                )
            if isinstance(observation, ActionRejectionObservation) and (
                observation.action == unresolved.action
                and observation.request_event_id == unresolved.request_event_id
                and observation.observation_connection_generation
                >= unresolved.send_connection_generation
            ):
                return _ObservationResolution(
                    VoteAbilityOutcomeStatus.REJECTED,
                    DiscussionObservationStatus.REJECTED,
                    DiscussionTerminalReason.AUTHORITATIVE_REJECTED,
                    observation_generation=(
                        observation.observation_connection_generation
                    ),
                    rejection_reason=observation.reason,
                )
        for observation in view.observations:
            if (
                isinstance(observation, ResumeRecoveryBarrier)
                and observation.complete
                and observation.connection_generation
                > unresolved.send_connection_generation
            ):
                return _ObservationResolution(
                    VoteAbilityOutcomeStatus.UNKNOWN,
                    DiscussionObservationStatus.RECOVERY_UNKNOWN,
                    DiscussionTerminalReason.AUTHORITATIVE_AMBIGUOUS,
                    observation_generation=observation.connection_generation,
                )
        return None

    def _resolve_observation_batch(
        self,
        unresolved: UnresolvedReservation,
        view: TransportObservationView,
    ) -> _ObservationResolution | None:
        observations = tuple(
            observation
            for observation in view.observations
            if observation.order > unresolved.transport_after_order
        )

        # A missing prefix prevents a uniqueness proof even when one matching
        # response survived retention.
        if view.gap_before_first:
            return _ObservationResolution(
                VoteAbilityOutcomeStatus.UNKNOWN,
                DiscussionObservationStatus.RECOVERY_UNKNOWN,
                DiscussionTerminalReason.RECOVERY_GAP,
            )

        exact: list[
            tuple[
                VoteAbilityOutcomeStatus,
                DiscussionObservationStatus,
                DiscussionTerminalReason,
                ActionAcceptedObservation | ActionRejectionObservation,
                str | None,
            ]
        ] = []
        insufficient: list[ActionRejectionObservation] = []
        for observation in observations:
            if (
                isinstance(observation, ActionAcceptedObservation)
                and observation.action == unresolved.action
                and observation.request_event_id == unresolved.request_event_id
                and observation.observation_connection_generation
                >= unresolved.send_connection_generation
            ):
                exact.append(
                    (
                        VoteAbilityOutcomeStatus.ACCEPTED,
                        DiscussionObservationStatus.ACCEPTED,
                        DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,
                        observation,
                        None,
                    )
                )
            elif (
                isinstance(observation, ActionRejectionObservation)
                and observation.action == unresolved.action
                and observation.observation_connection_generation
                >= unresolved.send_connection_generation
            ):
                if observation.request_event_id == unresolved.request_event_id:
                    exact.append(
                        (
                            VoteAbilityOutcomeStatus.REJECTED,
                            DiscussionObservationStatus.REJECTED,
                            DiscussionTerminalReason.AUTHORITATIVE_REJECTED,
                            observation,
                            observation.reason,
                        )
                    )
                elif observation.request_event_id is None:
                    insufficient.append(observation)

        if len(exact) == 1 and not insufficient:
            outcome_status, discussion_status, reason, record, rejection = exact[0]
            return _ObservationResolution(
                outcome_status,
                discussion_status,
                reason,
                record,
                record.observation_connection_generation,
                rejection,
            )
        if len(exact) > 1 or (exact and insufficient):
            return _ObservationResolution(
                VoteAbilityOutcomeStatus.UNKNOWN,
                DiscussionObservationStatus.RECOVERY_UNKNOWN,
                DiscussionTerminalReason.AUTHORITATIVE_AMBIGUOUS,
            )

        gap_barriers = tuple(
            observation
            for observation in observations
            if isinstance(observation, ResumeRecoveryBarrier)
            and observation.complete is True
            and observation.connection_generation
            > unresolved.send_connection_generation
            and observation.replay_gap_or_floor is True
            and observation.replay_contiguous is False
        )
        if gap_barriers:
            return _ObservationResolution(
                VoteAbilityOutcomeStatus.UNKNOWN,
                DiscussionObservationStatus.RECOVERY_UNKNOWN,
                DiscussionTerminalReason.RECOVERY_GAP,
                gap_barriers[0] if len(gap_barriers) == 1 else None,
                max(item.connection_generation for item in gap_barriers),
            )

        snapshot = self.world.snapshot()
        phase_changed = snapshot.phase is not None and (
            snapshot.phase.day,
            snapshot.phase.phase,
        ) != (
            unresolved.opportunity_key.reservation.day,
            unresolved.opportunity_key.reservation.phase,
        )
        if phase_changed:
            assert snapshot.phase is not None
            current_phase_key = (snapshot.phase.day, snapshot.phase.phase)
            phase_records = tuple(
                observation
                for observation in observations
                if isinstance(observation, PhaseTimingObservation)
                and (observation.day, observation.phase) == current_phase_key
            )
            return _ObservationResolution(
                VoteAbilityOutcomeStatus.UNKNOWN,
                DiscussionObservationStatus.RECOVERY_UNKNOWN,
                DiscussionTerminalReason.PHASE_CHANGED,
                phase_records[0] if len(phase_records) == 1 else None,
                (
                    max(item.connection_generation for item in phase_records)
                    if phase_records
                    else None
                ),
            )

        contiguous_barriers = tuple(
            observation
            for observation in observations
            if isinstance(observation, ResumeRecoveryBarrier)
            and observation.complete is True
            and observation.connection_generation
            > unresolved.send_connection_generation
            and observation.replay_contiguous is True
            and observation.replay_gap_or_floor is False
        )
        if contiguous_barriers:
            return _ObservationResolution(
                VoteAbilityOutcomeStatus.UNKNOWN,
                DiscussionObservationStatus.RECOVERY_UNKNOWN,
                DiscussionTerminalReason.AUTHORITATIVE_AMBIGUOUS,
                contiguous_barriers[0] if len(contiguous_barriers) == 1 else None,
                max(item.connection_generation for item in contiguous_barriers),
            )

        if insufficient:
            return _ObservationResolution(
                VoteAbilityOutcomeStatus.UNKNOWN,
                DiscussionObservationStatus.RECOVERY_UNKNOWN,
                DiscussionTerminalReason.AUTHORITATIVE_AMBIGUOUS,
                insufficient[0] if len(insufficient) == 1 else None,
                (
                    insufficient[0].observation_connection_generation
                    if len(insufficient) == 1
                    else None
                ),
            )
        return None

    async def _finalize_unresolved(
        self,
        resolution: _ObservationResolution,
    ) -> bool:
        if self._finalization_task is not None:
            return await self._join_finalization()
        unresolved = self._unresolved
        if unresolved is None:
            return False
        evidence: EvidenceRef | None = None
        correlation = unresolved.discussion
        if correlation is None:
            if self.discussion_context is not None:
                raise RuntimeError(
                    "contextful unresolved reservation omitted discussion correlation"
                )
            self._complete_unresolved(unresolved, resolution)
            return False

        context = self.discussion_context
        if context is None or correlation.context_sha256 != context.context_sha256:
            raise RuntimeError(
                "discussion correlation context does not match sealed context"
            )
        if resolution.evidence_record is not None:
            evidence = evidence_ref_for_record(
                resolution.evidence_record,
                context,
            )

        task = asyncio.create_task(
            self._finalize_unresolved_owned(unresolved, resolution, evidence),
            name="aiwolf-vote-ability-observation-finalization",
        )
        self._finalization_task = task
        return await self._join_finalization()

    async def _join_finalization(self) -> bool:
        task = self._finalization_task
        if task is None:
            return False
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
                self._clear_current_cancellation()
        if task.cancelled():
            raise RuntimeError("discussion observation finalizer was cancelled")
        task.result()
        if self._finalization_task is task:
            self._finalization_task = None
        return cancelled

    @staticmethod
    def _clear_current_cancellation() -> None:
        current = asyncio.current_task()
        if current is not None:
            current.uncancel()

    async def _finalize_unresolved_owned(
        self,
        unresolved: UnresolvedReservation,
        resolution: _ObservationResolution,
        evidence: EvidenceRef | None,
    ) -> None:
        if unresolved.discussion is not None:
            await self.invoker.finalize_discussion_observation(
                owner="vote_ability",
                correlation=unresolved.discussion,
                status=resolution.discussion_status,
                reason=resolution.reason,
                evidence=evidence,
            )
        self._complete_unresolved(unresolved, resolution)

    def _complete_unresolved(
        self,
        unresolved: UnresolvedReservation,
        resolution: _ObservationResolution,
    ) -> None:
        if self._unresolved is not unresolved:
            raise RuntimeError("unresolved reservation changed before completion")
        self._outcomes.append(
            VoteAbilityOutcome(
                opportunity_key=unresolved.opportunity_key,
                mapping_order=unresolved.mapping_order,
                action=unresolved.action,
                ability_id=unresolved.ability_id,
                vote_target_player_id=unresolved.vote_target_player_id,
                ability_target_player_ids=unresolved.ability_target_player_ids,
                request_event_id=unresolved.request_event_id,
                send_connection_generation=unresolved.send_connection_generation,
                observation_connection_generation=resolution.observation_generation,
                status=resolution.outcome_status,
                rejection_reason=resolution.rejection_reason,
                attempts=unresolved.attempt_ordinal,
                discussion=unresolved.discussion,
            )
        )
        key = unresolved.opportunity_key.reservation
        self._terminal.add(key)
        self._unresolved = None
        if resolution.outcome_status is VoteAbilityOutcomeStatus.ACCEPTED:
            self._accepted_count += 1
        elif resolution.outcome_status is VoteAbilityOutcomeStatus.REJECTED:
            self._rejected_count += 1
        elif resolution.outcome_status is VoteAbilityOutcomeStatus.UNKNOWN:
            self._unknown_count += 1

    def _record(
        self,
        candidate: _Candidate,
        status: VoteAbilityOutcomeStatus,
        *,
        terminal: bool,
        ability_id: str | None = None,
        vote_target_player_id: str | None = None,
        ability_target_player_ids: tuple[str, ...] = (),
        request_event_id: str | None = None,
        send_connection_generation: int | None = None,
    ) -> None:
        if (
            ability_id is None
            and candidate.action == "ability.use"
            and len(candidate.handles) == 1
        ):
            handle = candidate.handles[0]
            if isinstance(handle, AbilityAction):
                ability_id = handle.ability_id
        self._outcomes.append(
            VoteAbilityOutcome(
                opportunity_key=candidate.key,
                mapping_order=candidate.mapping_order,
                action=candidate.action,
                ability_id=ability_id,
                vote_target_player_id=vote_target_player_id,
                ability_target_player_ids=ability_target_player_ids,
                request_event_id=request_event_id,
                send_connection_generation=send_connection_generation,
                observation_connection_generation=None,
                status=status,
                rejection_reason=None,
                attempts=self._attempts.get(candidate.key.reservation, 0),
            )
        )
        if terminal:
            self._terminal.add(candidate.key.reservation)

    @staticmethod
    def _ability_id(
        handles: tuple[VoteAction | AbilityAction, ...], decision: AbilityDecision
    ) -> str:
        option_index = int(decision.option_id.partition(":")[2])
        handle = handles[option_index]
        if not isinstance(handle, AbilityAction):
            raise RuntimeError("ability decision selected a non-ability handle")
        return handle.ability_id

    def _now(self) -> float:
        value = self._clock()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError("clock must return a finite non-negative number")
        return float(value)
