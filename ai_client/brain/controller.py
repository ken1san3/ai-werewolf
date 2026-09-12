"""One Brain invocation: capture, run, validate, stale-check, and dispatch."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import math
import time
from typing import Any, Callable

from ai_client.network import (
    AbilityAction,
    ActionHandle,
    ChatAction,
    CoDeclareAction,
    CoReportAction,
    DeliveryUnknownError,
    NetworkClient,
    NotDeliveredError,
    SendReceipt,
    StaleActionError,
    VoteAction,
)
from ai_client.world import Freshness, WorldSnapshot, WorldState

from .interface import Brain
from .model import (
    AbilityDecision,
    BrainActionContext,
    BrainActionOption,
    BrainInput,
    BrainRunConfig,
    BrainDecision,
    ChatDecision,
    CoDeclareDecision,
    CoReportDecision,
    DecisionOutcome,
    DecisionStatus,
    DispatchDeadline,
    NoDecision,
    VoteDecision,
)


_DECISION_TYPES = (
    NoDecision,
    ChatDecision,
    VoteDecision,
    AbilityDecision,
    CoDeclareDecision,
    CoReportDecision,
)


class _Invocation:
    def __init__(self, request: BrainInput) -> None:
        self.request = request
        self.brain_task: asyncio.Task[BrainDecision] | None = None
        self.world_task: asyncio.Task[WorldSnapshot] | None = None
        self.cancel_status: DecisionStatus | None = None
        self.terminal = False


class BrainController:
    """Orchestrate one immutable Brain request without owning World/Network loops."""

    def __init__(
        self,
        *,
        world: WorldState,
        sender: NetworkClient,
        brain: Brain,
        config: BrainRunConfig = BrainRunConfig(),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(config, BrainRunConfig):
            raise TypeError("config must be BrainRunConfig")
        if not hasattr(brain, "decide") or not callable(brain.decide):
            raise TypeError("brain must provide an async decide(request) method")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self.world = world
        self.sender = sender
        self.brain = brain
        self.config = config
        self._clock = clock
        self._active: _Invocation | None = None
        self._stopping = False
        self._unresponsive = False
        self._last_dispatched_decision: tuple[SendReceipt, BrainDecision] | None = None

    @property
    def active(self) -> bool:
        return self._active is not None

    @property
    def unresponsive(self) -> bool:
        return self._unresponsive

    def capture_input(
        self,
        *,
        allowed_handles: tuple[ActionHandle, ...] | None = None,
        dispatch_deadline: DispatchDeadline | None = None,
    ) -> BrainInput | None:
        """Synchronously capture a consistent, request-local World view."""

        if dispatch_deadline is not None and not isinstance(
            dispatch_deadline, DispatchDeadline
        ):
            raise TypeError("dispatch_deadline must be DispatchDeadline when supplied")
        snapshot = self.world.snapshot()
        if not self._snapshot_is_current(snapshot):
            return None
        actions = self.world.current_actions()
        if not (
            actions.is_caught_up
            and actions.world_version == snapshot.version
            and actions.world_last_applied_seq == snapshot.last_applied_seq
            and actions.network_last_seq == snapshot.last_applied_seq
        ):
            return None
        if any(not isinstance(action, ActionHandle) for action in actions.actions):
            return None
        captured_actions = tuple(actions.actions)
        if allowed_handles is not None:
            if (
                not isinstance(allowed_handles, tuple)
                or not allowed_handles
                or any(not isinstance(handle, ActionHandle) for handle in allowed_handles)
                or len(set(allowed_handles)) != len(allowed_handles)
                or any(handle not in captured_actions for handle in allowed_handles)
            ):
                return None
            allowed = frozenset(allowed_handles)
            captured_actions = tuple(
                handle for handle in captured_actions if handle in allowed
            )
        phase = snapshot.phase
        assert phase is not None
        if dispatch_deadline is not None and (
            (phase.day, phase.phase)
            != (dispatch_deadline.day, dispatch_deadline.phase)
            or not self._deadline_allows_dispatch(dispatch_deadline)
        ):
            return None
        history = self.world.history()
        co = self.world.co_for_day(phase.day)
        ability_results = self.world.ability_results()
        options = tuple(
            BrainActionOption(f"action:{index}", action)
            for index, action in enumerate(captured_actions)
        )
        return BrainInput(
            snapshot=snapshot,
            action_context=BrainActionContext(
                world_version=actions.world_version,
                world_last_applied_seq=actions.world_last_applied_seq,
                network_last_seq=actions.network_last_seq,
                is_caught_up=actions.is_caught_up,
                options=options,
            ),
            history=history,
            co=co,
            ability_results=ability_results,
        )

    def dispatch_context_is_current(
        self,
        *,
        allowed_handles: tuple[ActionHandle, ...],
        dispatch_deadline: DispatchDeadline,
    ) -> bool:
        """Check the received-handle and deadline context without creating input."""

        if not isinstance(dispatch_deadline, DispatchDeadline):
            raise TypeError("dispatch_deadline must be DispatchDeadline")
        if (
            not isinstance(allowed_handles, tuple)
            or not allowed_handles
            or any(not isinstance(handle, ActionHandle) for handle in allowed_handles)
            or len(set(allowed_handles)) != len(allowed_handles)
        ):
            return False
        snapshot = self.world.snapshot()
        if not self._snapshot_is_current(snapshot):
            return False
        phase = snapshot.phase
        assert phase is not None
        if (phase.day, phase.phase) != (
            dispatch_deadline.day,
            dispatch_deadline.phase,
        ):
            return False
        actions = self.world.current_actions()
        if not (
            actions.is_caught_up
            and actions.world_version == snapshot.version
            and actions.world_last_applied_seq == snapshot.last_applied_seq
            and actions.network_last_seq == snapshot.last_applied_seq
            and all(handle in actions.actions for handle in allowed_handles)
        ):
            return False
        return self._deadline_allows_dispatch(dispatch_deadline)

    async def decide_and_send(
        self,
        request: BrainInput,
        *,
        timeout_seconds: float | None = None,
        dispatch_deadline: DispatchDeadline | None = None,
    ) -> DecisionOutcome:
        if not isinstance(request, BrainInput):
            raise TypeError("request must be BrainInput")
        timeout = self._validate_timeout(timeout_seconds)
        if dispatch_deadline is not None and not isinstance(
            dispatch_deadline, DispatchDeadline
        ):
            raise TypeError("dispatch_deadline must be DispatchDeadline when supplied")
        if self._stopping:
            return self._outcome(request, DecisionStatus.CANCELLED)
        if self._unresponsive:
            return self._outcome(
                request,
                DecisionStatus.BRAIN_FAILED,
                error_type="UnresponsiveBrain",
                started=True,
            )
        if self._active is not None:
            raise RuntimeError("BrainController supports only one active invocation")
        if not self._request_is_current(request):
            return self._outcome(request, DecisionStatus.STALE)

        invocation = _Invocation(request)
        self._active = invocation
        invocation.brain_task = asyncio.create_task(self._run_brain(request))
        deadline = asyncio.get_running_loop().time() + timeout
        invocation.world_task = asyncio.create_task(
            self.world.wait_for_update(request.snapshot.version)
        )
        try:
            decision = await self._wait_for_decision(
                invocation,
                deadline,
                dispatch_deadline=dispatch_deadline,
            )
            if isinstance(decision, DecisionOutcome):
                return decision
            if invocation.cancel_status is not None:
                return self._outcome(request, invocation.cancel_status, started=True)
            outcome = self._validate_and_dispatch(request, decision)
            if outcome is not None:
                return outcome
            if dispatch_deadline is not None and not self._deadline_allows_dispatch(
                dispatch_deadline
            ):
                return self._outcome(
                    request,
                    DecisionStatus.DEADLINE_SUPPRESSED,
                    option_id=getattr(decision, "option_id", None),
                    started=True,
                )
            stale = self._stale_before_send(request, decision.option_id)
            if stale:
                return self._outcome(
                    request,
                    DecisionStatus.STALE,
                    option_id=getattr(decision, "option_id", None),
                    started=True,
                )
            return await self._dispatch(request, decision)
        except asyncio.CancelledError:
            invocation.cancel_status = DecisionStatus.CANCELLED
            invocation.terminal = True
            await self._cancel_invocation_tasks(invocation)
            return self._outcome(request, DecisionStatus.CANCELLED, started=True)
        finally:
            invocation.terminal = True
            await self._cleanup_invocation(invocation)

    async def stop(self) -> None:
        """Permanently close new invocations and cancel the active Brain task."""

        self._stopping = True
        invocation = self._active
        if invocation is None:
            return
        invocation.cancel_status = DecisionStatus.CANCELLED
        invocation.terminal = True
        await self._cancel_invocation_tasks(invocation)

    async def _run_brain(self, request: BrainInput) -> BrainDecision:
        return await self.brain.decide(request)

    async def _wait_for_decision(
        self,
        invocation: _Invocation,
        deadline: float,
        *,
        dispatch_deadline: DispatchDeadline | None,
    ) -> BrainDecision | DecisionOutcome:
        assert invocation.brain_task is not None
        assert invocation.world_task is not None
        brain_task = invocation.brain_task
        world_task = invocation.world_task
        while True:
            if dispatch_deadline is not None and not self._deadline_allows_dispatch(
                dispatch_deadline
            ):
                return await self._cancel_for_deadline(invocation, brain_task)
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                invocation.cancel_status = DecisionStatus.TIMED_OUT
                invocation.terminal = True
                await self._cancel_brain_task(brain_task)
                return self._outcome(
                    invocation.request, DecisionStatus.TIMED_OUT, started=True
                )
            cutoff_remaining = (
                None
                if dispatch_deadline is None
                else dispatch_deadline.not_after_monotonic - self._clock()
            )
            wait_timeout = (
                remaining
                if cutoff_remaining is None
                else min(remaining, max(0.0, cutoff_remaining))
            )
            done, _pending = await asyncio.wait(
                {brain_task, world_task},
                timeout=wait_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if dispatch_deadline is not None and (
                (
                    not done
                    and cutoff_remaining is not None
                    and cutoff_remaining <= remaining
                )
                or not self._deadline_allows_dispatch(dispatch_deadline)
            ):
                return await self._cancel_for_deadline(invocation, brain_task)
            if not done:
                invocation.cancel_status = DecisionStatus.TIMED_OUT
                invocation.terminal = True
                await self._cancel_brain_task(brain_task)
                return self._outcome(
                    invocation.request, DecisionStatus.TIMED_OUT, started=True
                )
            if world_task in done and brain_task in done:
                try:
                    world_snapshot = world_task.result()
                except Exception:
                    world_snapshot = None
                if world_snapshot is None or not self._snapshot_allows_invocation(
                    invocation.request.snapshot, world_snapshot
                ):
                    invocation.cancel_status = DecisionStatus.STALE
                    invocation.terminal = True
                    await self._cancel_brain_task(brain_task)
                    return self._outcome(
                        invocation.request, DecisionStatus.STALE, started=True
                    )
            if brain_task in done:
                try:
                    return brain_task.result()
                except asyncio.CancelledError:
                    status = invocation.cancel_status or DecisionStatus.CANCELLED
                    return self._outcome(
                        invocation.request, status, started=True
                    )
                except Exception as error:
                    return self._outcome(
                        invocation.request,
                        DecisionStatus.BRAIN_FAILED,
                        error_type=type(error).__name__,
                        started=True,
                    )
            try:
                world_snapshot = world_task.result()
            except Exception:
                invocation.cancel_status = DecisionStatus.STALE
                invocation.terminal = True
                await self._cancel_brain_task(brain_task)
                return self._outcome(
                    invocation.request, DecisionStatus.STALE, started=True
                )
            if not self._snapshot_allows_invocation(
                invocation.request.snapshot, world_snapshot
            ):
                invocation.cancel_status = DecisionStatus.STALE
                invocation.terminal = True
                await self._cancel_brain_task(brain_task)
                return self._outcome(
                    invocation.request, DecisionStatus.STALE, started=True
                )
            world_task = asyncio.create_task(
                self.world.wait_for_update(world_snapshot.version)
            )
            invocation.world_task = world_task

    async def _cancel_for_deadline(
        self,
        invocation: _Invocation,
        brain_task: asyncio.Task[BrainDecision],
    ) -> DecisionOutcome:
        invocation.cancel_status = DecisionStatus.DEADLINE_SUPPRESSED
        invocation.terminal = True
        await self._cancel_brain_task(brain_task)
        return self._outcome(
            invocation.request,
            DecisionStatus.DEADLINE_SUPPRESSED,
            started=True,
        )

    def _validate_and_dispatch(
        self, request: BrainInput, decision: object
    ) -> DecisionOutcome | None:
        if not isinstance(decision, _DECISION_TYPES):
            return self._outcome(
                request, DecisionStatus.INVALID_DECISION, started=True
            )
        if isinstance(decision, NoDecision):
            return self._outcome(request, DecisionStatus.NO_DECISION, started=True)
        options = request.action_context.options
        option_map: dict[str, BrainActionOption] = {}
        for option in options:
            if (
                not isinstance(option, BrainActionOption)
                or option.option_id in option_map
                or not isinstance(option.handle, ActionHandle)
            ):
                return self._outcome(
                    request, DecisionStatus.INVALID_DECISION, started=True
                )
            option_map[option.option_id] = option
        option_id = getattr(decision, "option_id", None)
        option = option_map.get(option_id) if isinstance(option_id, str) else None
        if option is None:
            return self._outcome(
                request,
                DecisionStatus.INVALID_DECISION,
                option_id=option_id if isinstance(option_id, str) else None,
                started=True,
            )
        handle = option.handle
        valid = self._decision_matches_handle(decision, handle)
        if not valid:
            return self._outcome(
                request,
                DecisionStatus.INVALID_DECISION,
                option_id=option_id,
                started=True,
            )
        return None

    def _decision_matches_handle(
        self, decision: BrainDecision, handle: ActionHandle
    ) -> bool:
        if isinstance(decision, ChatDecision):
            return (
                isinstance(handle, ChatAction)
                and handle.type == "chat"
                and self._non_empty_string(decision.message)
            )
        if isinstance(decision, VoteDecision):
            if not isinstance(handle, VoteAction) or handle.type != "vote":
                return False
            target = decision.target_player_id
            if target is None:
                return handle.allows_abstain
            return isinstance(target, str) and target in handle.valid_targets
        if isinstance(decision, AbilityDecision):
            if not isinstance(handle, AbilityAction) or handle.type != "ability":
                return False
            targets = decision.target_player_ids
            return (
                isinstance(targets, tuple)
                and all(isinstance(target, str) for target in targets)
                and len(targets) == handle.target_count
                and len(set(targets)) == len(targets)
                and all(target in handle.valid_targets for target in targets)
            )
        if isinstance(decision, CoDeclareDecision):
            return (
                isinstance(handle, CoDeclareAction)
                and handle.type == "co_declare"
                and isinstance(decision.claimed_role_id, str)
                and self._non_empty_string(decision.claimed_role_id)
                and decision.claimed_role_id in handle.claimed_role_ids
                and self._non_empty_string(decision.comment)
            )
        if isinstance(decision, CoReportDecision):
            return (
                isinstance(handle, CoReportAction)
                and handle.type == "co_report"
                and self._non_empty_string(decision.kind)
                and self._non_empty_string(decision.target_player_id)
                and self._non_empty_string(decision.claimed_result)
            )
        return False

    async def _dispatch(self, request: BrainInput, decision: BrainDecision) -> DecisionOutcome:
        if self._stopping:
            return self._outcome(
                request,
                DecisionStatus.CANCELLED,
                option_id=getattr(decision, "option_id", None),
                started=True,
            )
        option = next(
            option
            for option in request.action_context.options
            if option.option_id == getattr(decision, "option_id", None)
        )
        handle = option.handle
        try:
            if isinstance(decision, ChatDecision) and isinstance(handle, ChatAction):
                receipt = await self.sender.send_chat(handle, decision.message)
            elif isinstance(decision, VoteDecision) and isinstance(handle, VoteAction):
                receipt = await self.sender.send_vote(handle, decision.target_player_id)
            elif isinstance(decision, AbilityDecision) and isinstance(handle, AbilityAction):
                receipt = await self.sender.send_ability(handle, decision.target_player_ids)
            elif isinstance(decision, CoDeclareDecision) and isinstance(handle, CoDeclareAction):
                receipt = await self.sender.send_co_declare(
                    handle, decision.claimed_role_id, decision.comment
                )
            elif isinstance(decision, CoReportDecision) and isinstance(handle, CoReportAction):
                receipt = await self.sender.send_co_report(
                    handle,
                    decision.kind,
                    decision.target_player_id,
                    decision.claimed_result,
                )
            else:
                return self._outcome(
                    request,
                    DecisionStatus.INVALID_DECISION,
                    option_id=getattr(decision, "option_id", None),
                    started=True,
                )
        except StaleActionError as error:
            return self._outcome(
                request,
                DecisionStatus.STALE,
                option_id=getattr(decision, "option_id", None),
                error_type=type(error).__name__,
                started=True,
            )
        except NotDeliveredError as error:
            return self._outcome(
                request,
                DecisionStatus.SEND_NOT_DELIVERED,
                option_id=getattr(decision, "option_id", None),
                error_type=type(error).__name__,
                started=True,
            )
        except DeliveryUnknownError as error:
            return self._delivery_unknown_outcome(
                request,
                decision,
                handle,
                error_type=type(error).__name__,
                request_event_id=error.request_event_id,
                attempt_action=error.action,
                send_connection_generation=error.connection_generation,
            )
        except Exception as error:
            return self._delivery_unknown_outcome(
                request,
                decision,
                handle,
                error_type=type(error).__name__,
            )
        if not isinstance(receipt, SendReceipt):
            return self._delivery_unknown_outcome(
                request,
                decision,
                handle,
                error_type="InvalidSendReceipt",
            )
        self._last_dispatched_decision = (receipt, decision)
        return self._outcome(
            request,
            DecisionStatus.SENT,
            option_id=getattr(decision, "option_id", None),
            receipt=receipt,
            started=True,
        )

    def _delivery_unknown_outcome(
        self,
        request: BrainInput,
        decision: BrainDecision,
        handle: ActionHandle,
        *,
        error_type: str,
        request_event_id: str | None = None,
        attempt_action: str | None = None,
        send_connection_generation: int | None = None,
    ) -> DecisionOutcome:
        action_by_handle = {
            ChatAction: "chat.send",
            VoteAction: "vote.cast",
            AbilityAction: "ability.use",
            CoDeclareAction: "co.declare",
            CoReportAction: "co.report",
        }
        derived_action = next(
            action
            for handle_type, action in action_by_handle.items()
            if isinstance(handle, handle_type)
        )
        if attempt_action is not None and attempt_action != derived_action:
            raise RuntimeError("delivery-unknown action identity does not match the decision")
        return self._outcome(
            request,
            DecisionStatus.SEND_DELIVERY_UNKNOWN,
            option_id=getattr(decision, "option_id", None),
            error_type=error_type,
            started=True,
            request_event_id=request_event_id,
            attempt_action=attempt_action or derived_action,
            send_connection_generation=send_connection_generation,
            vote_target_player_id=(
                decision.target_player_id if isinstance(decision, VoteDecision) else None
            ),
            ability_id=handle.ability_id if isinstance(handle, AbilityAction) else None,
            ability_target_player_ids=(
                decision.target_player_ids
                if isinstance(decision, AbilityDecision)
                else ()
            ),
        )

    def take_dispatched_decision(
        self, receipt: SendReceipt | None
    ) -> BrainDecision | None:
        """Return the matching successful dispatch decision exactly once."""

        dispatched = self._last_dispatched_decision
        if receipt is None or dispatched is None or dispatched[0] != receipt:
            return None
        self._last_dispatched_decision = None
        return dispatched[1]

    def _snapshot_is_current(self, snapshot: WorldSnapshot) -> bool:
        return (
            snapshot.freshness is Freshness.CURRENT
            and snapshot.is_caught_up
            and snapshot.phase is not None
            and snapshot.complete
        )

    def _snapshot_allows_invocation(
        self, initial: WorldSnapshot, current: WorldSnapshot
    ) -> bool:
        return (
            self._snapshot_is_current(current)
            and initial.phase is not None
            and current.phase is not None
            and (initial.phase.day, initial.phase.phase)
            == (current.phase.day, current.phase.phase)
        )

    def _request_is_current(self, request: BrainInput) -> bool:
        if not self._request_matches_capture_contract(request):
            return False
        snapshot = self.world.snapshot()
        if not self._snapshot_allows_invocation(request.snapshot, snapshot):
            return False
        actions = self.world.current_actions()
        return (
            actions.is_caught_up
            and actions.world_version == snapshot.version
            and actions.world_last_applied_seq == snapshot.last_applied_seq
            and actions.network_last_seq == snapshot.last_applied_seq
        )

    def _request_matches_capture_contract(self, request: BrainInput) -> bool:
        snapshot = request.snapshot
        context = request.action_context
        if not self._snapshot_is_current(snapshot):
            return False
        if not (
            context.is_caught_up
            and context.world_version == snapshot.version
            and context.world_last_applied_seq == snapshot.last_applied_seq
            and context.network_last_seq == snapshot.last_applied_seq
        ):
            return False
        option_ids: set[str] = set()
        for index, option in enumerate(context.options):
            if not isinstance(option, BrainActionOption):
                return False
            if option.option_id in option_ids:
                return False
            if option.option_id != f"action:{index}":
                return False
            option_ids.add(option.option_id)
        return True

    def _stale_before_send(self, request: BrainInput, option_id: str) -> bool:
        if self._stopping:
            return True
        snapshot = self.world.snapshot()
        if not self._snapshot_allows_invocation(request.snapshot, snapshot):
            return True
        actions = self.world.current_actions()
        if not (
            actions.is_caught_up
            and actions.world_version == snapshot.version
            and actions.world_last_applied_seq == snapshot.last_applied_seq
            and actions.network_last_seq == snapshot.last_applied_seq
        ):
            return True
        selected = next(
            option
            for option in request.action_context.options
            if option.option_id == option_id
        )
        return selected.handle not in actions.actions

    def _deadline_allows_dispatch(self, deadline: DispatchDeadline) -> bool:
        view = self.world.transport_observations()
        current = view.current_deadline
        return (
            current is not None
            and current.mapping_order == deadline.mapping_order
            and current.phase == deadline.phase
            and current.day == deadline.day
            and current.connection_generation == deadline.connection_generation
            and current.action_generation == deadline.action_generation
            and current.local_deadline_monotonic is not None
            and self._clock() < deadline.not_after_monotonic
        )

    def _validate_timeout(self, timeout_seconds: float | None) -> float:
        timeout = (
            self.config.max_decision_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
            or timeout > self.config.max_decision_seconds
        ):
            raise ValueError(
                "timeout_seconds must be positive, finite, and no greater than max_decision_seconds"
            )
        return float(timeout)

    def _outcome(
        self,
        request: BrainInput,
        status: DecisionStatus,
        *,
        option_id: str | None = None,
        receipt: SendReceipt | None = None,
        error_type: str | None = None,
        started: bool = False,
        request_event_id: str | None = None,
        attempt_action: str | None = None,
        send_connection_generation: int | None = None,
        vote_target_player_id: str | None = None,
        ability_id: str | None = None,
        ability_target_player_ids: tuple[str, ...] = (),
    ) -> DecisionOutcome:
        phase = request.snapshot.phase
        return DecisionOutcome(
            status=status,
            request_version=request.snapshot.version,
            phase=phase.phase if phase is not None else None,
            day=phase.day if phase is not None else None,
            option_id=option_id,
            receipt=receipt,
            error_type=error_type,
            invocation_started=started,
            request_event_id=request_event_id,
            attempt_action=attempt_action,
            send_connection_generation=send_connection_generation,
            vote_target_player_id=vote_target_player_id,
            ability_id=ability_id,
            ability_target_player_ids=ability_target_player_ids,
        )

    async def _cancel_invocation_tasks(self, invocation: _Invocation) -> None:
        brain_task = invocation.brain_task
        if brain_task is not None:
            await self._cancel_brain_task(brain_task)
        world_task = invocation.world_task
        if world_task is not None:
            await self._cancel_task_with_grace(world_task)

    async def _cancel_brain_task(self, task: asyncio.Task[Any]) -> None:
        completed = await self._cancel_task_with_grace(task)
        if not completed:
            self._unresponsive = True

    async def _cancel_task_with_grace(self, task: asyncio.Task[Any]) -> bool:
        if task.done():
            self._consume_task(task)
            return True
        task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.shield(task), self.config.cancellation_grace_seconds
            )
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception:
            self._consume_task(task)
        if task.done():
            self._consume_task(task)
        else:
            task.add_done_callback(self._consume_task)
        return task.done()

    async def _cleanup_invocation(self, invocation: _Invocation) -> None:
        if self._active is invocation:
            self._active = None
        world_task = invocation.world_task
        if world_task is not None and not world_task.done():
            world_task.cancel()
        if world_task is not None:
            self._consume_task(world_task)

    @staticmethod
    def _consume_task(task: asyncio.Future[Any]) -> None:
        with suppress(asyncio.CancelledError, Exception):
            task.result()

    @staticmethod
    def _non_empty_string(value: object) -> bool:
        return isinstance(value, str) and bool(value)
