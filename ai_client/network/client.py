"""Async, protocol-only Network Client.

The receiver is the sole owner of client state.  The sender is a separate task
with a bounded FIFO queue, so slow callers cannot stop event ingestion.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
import json
import random
import time
from contextlib import suppress
from collections.abc import AsyncIterator, Sequence
from typing import Any, Awaitable, Callable, Mapping

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from .protocol import (
    ProtocolMessageValidator,
    ProtocolValidationError,
    make_client_request,
    same_major_version,
)
from .state import ClientState
from .types import (
    AbilityAction,
    Action,
    ActionRejected,
    ChatAction,
    ClientEvent,
    ClientExit,
    ClientExitReason,
    ClientLifecycle,
    ClientSnapshot,
    CoDeclareAction,
    CoReportAction,
    ConsumerOverrunError,
    CredentialError,
    CredentialStore,
    DeliveryUnknown,
    DeliveryUnknownError,
    FatalTermination,
    GameEnded,
    IncompatibleProtocolError,
    LifecycleChanged,
    NetworkClientConfig,
    NotDelivered,
    NotDeliveredError,
    PhaseDeadlineReached,
    ReconnectPolicy,
    SequenceGapDetected,
    SequenceGapRecovered,
    SendReceipt,
    ServerEvent,
    SessionCheckpoint,
    StaleActionError,
    VoteAction,
    immutable_mapping,
)


Socket = Any
Connector = Callable[[str], Any]
Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]
TimestampFactory = Callable[[], int]


class _TransientFailure(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _FatalFailure(Exception):
    def __init__(self, reason: ClientExitReason, detail: str | None = None) -> None:
        super().__init__(detail or reason.value)
        self.reason = reason
        self.detail = detail


class _GameEndedSignal(Exception):
    pass


@dataclass
class _Command:
    message: dict[str, Any]
    action: str
    generation: int
    future: asyncio.Future[SendReceipt]
    started: bool = False


@dataclass(frozen=True)
class _GenerationOutcome:
    connected: bool
    terminal_reason: ClientExitReason | None = None


_EVENT_STREAM_END = object()


class NetworkClient:
    """Maintain one authenticated seat over a finite-retry WebSocket session."""

    def __init__(
        self,
        config: NetworkClientConfig,
        credential_store: CredentialStore,
        *,
        reconnect_policy: ReconnectPolicy = ReconnectPolicy(),
        validator: ProtocolMessageValidator | None = None,
        connector: Connector = connect,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        random_source: random.Random | None = None,
        timestamp_factory: TimestampFactory | None = None,
    ) -> None:
        self.config = config
        self.credential_store = credential_store
        self.reconnect_policy = reconnect_policy
        self.validator = validator or ProtocolMessageValidator()
        self._connector = connector
        self._clock = clock
        self._sleep = sleep
        self._random = random_source or random.Random()
        self._timestamp = timestamp_factory or (lambda: int(time.time()))

        self._state = ClientState()
        self._events: asyncio.Queue[object] = asyncio.Queue(
            maxsize=config.inbound_event_capacity
        )
        self._event_stream_closed = False
        self._stop_requested = False
        self._run_started = False
        self._checkpoint: SessionCheckpoint | None = None
        self._connection_generation = 0
        self._socket: Socket | None = None
        self._outbound: asyncio.Queue[_Command] | None = None
        self._sender_task: asyncio.Task[None] | None = None
        self._deadline_task: asyncio.Task[None] | None = None
        self._awaiting_sync = True
        self._authenticated = False
        self._connected_once_in_generation = False
        self._join_request_sent = False
        self._resume_request_sent = False
        self._join_retry_after_ambiguous = False
        self._background_failure: _FatalFailure | _TransientFailure | None = None

    @property
    def lifecycle(self) -> ClientLifecycle:
        return self._state.snapshot().lifecycle

    @property
    def connection_generation(self) -> int:
        return self._connection_generation

    @property
    def action_generation(self) -> int:
        return self._state.action_generation

    @property
    def last_seq(self) -> int:
        return self._state.last_seq

    def snapshot(self) -> ClientSnapshot:
        return self._state.snapshot()

    async def events(self) -> AsyncIterator[ClientEvent]:
        """Yield client events until the client reaches a terminal state."""

        while True:
            item = await self._events.get()
            if item is _EVENT_STREAM_END:
                return
            yield item

    async def run(self) -> ClientExit:
        """Run until game end, explicit stop, or a fatal/reconnect failure."""

        if self._run_started:
            raise RuntimeError("NetworkClient.run() may only be called once")
        self._run_started = True
        self._stop_requested = False
        disconnected_since: float | None = None
        retry_number = 0

        try:
            try:
                checkpoint = await self.credential_store.load()
            except CredentialError:
                raise _FatalFailure(ClientExitReason.CREDENTIAL_INVALID)
            except (OSError, TypeError, ValueError) as error:
                raise _FatalFailure(ClientExitReason.CREDENTIAL_INVALID, str(error)) from error
            if checkpoint is not None and not isinstance(checkpoint, SessionCheckpoint):
                raise _FatalFailure(ClientExitReason.CREDENTIAL_INVALID)
            self._checkpoint = checkpoint
            self._state.set_last_seq(checkpoint.last_seq if checkpoint is not None else 0)
            if self.config.protocol_version is not None and not same_major_version(
                self.config.protocol_version, self.validator.protocol_version
            ):
                raise _FatalFailure(ClientExitReason.INCOMPATIBLE_PROTOCOL)
            if checkpoint is None and self.config.entry_token is None:
                raise _FatalFailure(ClientExitReason.CONFIG_INVALID, "entry_token is required for Join")

            while not self._stop_requested:
                try:
                    outcome = await self._run_generation()
                except _GameEndedSignal:
                    return await self._finish(ClientExitReason.GAME_ENDED, success=True)
                except _TransientFailure:
                    outcome = _GenerationOutcome(
                        connected=self._connected_once_in_generation
                    )
                except _FatalFailure:
                    raise

                if self._stop_requested:
                    break
                if outcome.terminal_reason is not None:
                    return await self._finish(outcome.terminal_reason, success=True)

                now = self._clock()
                if outcome.connected:
                    # A completed state sync starts a new disconnect budget.
                    disconnected_since = now
                    retry_number = 0
                elif disconnected_since is None:
                    disconnected_since = now
                elapsed = max(0.0, now - disconnected_since)
                if elapsed >= self.reconnect_policy.max_disconnected_seconds:
                    raise _FatalFailure(ClientExitReason.RECONNECT_EXHAUSTED)

                await self._set_lifecycle(ClientLifecycle.RECONNECT_WAIT)
                delay = self._retry_delay(retry_number)
                remaining = self.reconnect_policy.max_disconnected_seconds - elapsed
                await self._sleep(min(delay, max(0.0, remaining)))
                retry_number += 1
        except _FatalFailure as failure:
            return await self._finish(failure.reason, success=False, detail=failure.detail)
        except asyncio.CancelledError:
            self._stop_requested = True
            await self._cancel_deadline()
            await self._close_socket()
            await self._stop_sender()
            self._close_event_stream()
            raise
        except Exception as error:
            return await self._finish(ClientExitReason.INTERNAL_ERROR, success=False, detail=str(error))
        return await self._finish(ClientExitReason.STOPPED, success=True)

    async def stop(self) -> None:
        """Request a graceful stop and close the active transport."""

        self._stop_requested = True
        if self._run_started and not self._event_stream_closed:
            await self._set_lifecycle(ClientLifecycle.STOPPING)
        await self._close_socket()

    async def send_chat(self, action: ChatAction, message: str) -> SendReceipt:
        self._validate_action(action, "chat")
        if not isinstance(message, str) or not message:
            raise ValueError("message must be a non-empty string")
        return await self._queue_request(
            "chat.send", {"channel_id": action.channel, "message": message}
        )

    async def send_vote(self, action: VoteAction, target_player_id: str | None) -> SendReceipt:
        self._validate_action(action, "vote")
        if target_player_id is None:
            if not action.allows_abstain:
                raise ValueError("this vote action does not allow abstention")
        elif target_player_id not in action.valid_targets:
            raise ValueError("vote target is not in the received valid_targets")
        return await self._queue_request(
            "vote.cast", {"target_player_id": target_player_id}
        )

    async def send_ability(
        self, action: AbilityAction, target_player_ids: Sequence[str]
    ) -> SendReceipt:
        self._validate_action(action, "ability")
        if isinstance(target_player_ids, (str, bytes)) or not isinstance(
            target_player_ids, Sequence
        ):
            raise ValueError("target_player_ids must be a sequence")
        targets = tuple(target_player_ids)
        if len(targets) != action.target_count:
            raise ValueError("ability target count does not match the received action")
        if len(set(targets)) != len(targets) or any(
            target not in action.valid_targets for target in targets
        ):
            raise ValueError("ability target is not in the received valid_targets")
        return await self._queue_request(
            "ability.use",
            {"ability_id": action.ability_id, "target_player_ids": list(targets)},
        )

    async def send_co_declare(
        self, action: CoDeclareAction, claimed_role_id: str, comment: str
    ) -> SendReceipt:
        self._validate_action(action, "co_declare")
        if claimed_role_id not in action.claimed_role_ids:
            raise ValueError("claimed role is not in the received claimed_role_ids")
        if not isinstance(comment, str) or not comment:
            raise ValueError("comment must be a non-empty string")
        return await self._queue_request(
            "co.declare",
            {"claimed_role_id": claimed_role_id, "comment": comment},
        )

    async def send_co_report(
        self,
        action: CoReportAction,
        kind: str,
        target_player_id: str,
        claimed_result: str,
    ) -> SendReceipt:
        self._validate_action(action, "co_report")
        for name, value in (
            ("kind", kind),
            ("target_player_id", target_player_id),
            ("claimed_result", claimed_result),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        return await self._queue_request(
            "co.report",
            {
                "kind": kind,
                "target_player_id": target_player_id,
                "claimed_result": claimed_result,
            },
        )

    async def _run_generation(self) -> _GenerationOutcome:
        self._connection_generation += 1
        generation = self._connection_generation
        self._state.begin_connection(generation)
        self._awaiting_sync = True
        self._authenticated = False
        self._connected_once_in_generation = False
        self._join_request_sent = False
        self._resume_request_sent = False
        self._background_failure = None
        await self._set_lifecycle(
            ClientLifecycle.RESUMING if self._checkpoint is not None else ClientLifecycle.JOINING
        )

        socket: Socket | None = None
        try:
            try:
                socket, _owner = await asyncio.wait_for(
                    self._open_socket(), self.config.connect_timeout_seconds
                )
            except asyncio.TimeoutError as error:
                raise _TransientFailure("connect_timeout") from error
            except (OSError, ConnectionError) as error:
                raise _TransientFailure("connect_failed") from error
            self._socket = socket
            self._outbound = asyncio.Queue(maxsize=self.config.outbound_command_capacity)
            self._sender_task = asyncio.create_task(self._sender_loop(socket, generation))

            if self._checkpoint is None:
                assert self.config.entry_token is not None
                self._join_request_sent = True
                await self._send_request(
                    "session.join", {"entry_token": self.config.entry_token}, generation
                )
            else:
                self._resume_request_sent = True
                await self._send_request(
                    "session.resume",
                    {
                        "connection_token": self._checkpoint.connection_token,
                        "last_seq": self._checkpoint.last_seq,
                    },
                    generation,
                )
            await self._set_lifecycle(ClientLifecycle.SYNCHRONIZING)
            await self._send_request("session.ready", {}, generation)

            while True:
                if self._background_failure is not None:
                    raise self._background_failure
                try:
                    raw_message = await self._receive(socket)
                except asyncio.TimeoutError as error:
                    raise _TransientFailure("sync_timeout") from error
                if raw_message is None:
                    raise _TransientFailure("connection_closed")
                await self._accept_server_message(raw_message)
        except _GameEndedSignal:
            raise
        except _FatalFailure:
            raise
        except _TransientFailure:
            if self._join_request_sent and not self._authenticated and self._checkpoint is None:
                self._join_retry_after_ambiguous = True
            raise
        except ConnectionClosed as error:
            self._raise_for_close(error)
            raise AssertionError("_raise_for_close must raise")
        except ProtocolValidationError as error:
            raise _FatalFailure(ClientExitReason.INVALID_SERVER_MESSAGE, str(error)) from error
        except (OSError, ConnectionError) as error:
            raise _TransientFailure("transport_error") from error
        except (NotDeliveredError, DeliveryUnknownError) as error:
            raise _TransientFailure(str(error)) from error
        finally:
            await self._cancel_deadline()
            await self._close_socket()
            await self._stop_sender()
            self._state.invalidate_actions()
            self._socket = None
            self._outbound = None

        return _GenerationOutcome(connected=self._connected_once_in_generation)

    async def _open_socket(self) -> tuple[Socket, Any | None]:
        candidate = self._connector(self.config.uri)
        if inspect.isawaitable(candidate):
            return await candidate, None
        enter = getattr(candidate, "__aenter__", None)
        if enter is not None:
            return await enter(), candidate
        return candidate, None

    async def _receive(self, socket: Socket) -> Any:
        receiver = getattr(socket, "recv", None)
        if receiver is None:
            raise TypeError("WebSocket connection has no recv()")
        if self._awaiting_sync:
            return await asyncio.wait_for(receiver(), self.config.sync_timeout_seconds)
        return await receiver()

    async def _accept_server_message(self, raw_message: str | bytes) -> None:
        try:
            message = self.validator.decode_server(raw_message)
        except ProtocolValidationError:
            raise
        if not same_major_version(message["protocol_version"], self.validator.protocol_version):
            raise _FatalFailure(ClientExitReason.INCOMPATIBLE_PROTOCOL)
        if message["game_id"] != self.config.game_id:
            raise _FatalFailure(ClientExitReason.INVALID_SERVER_MESSAGE, "game_id mismatch")

        sequence = message["seq"]
        previous_seq = self._state.last_seq
        message_type = message["type"]
        is_sync_barrier = message_type == "game.state_sync" and self._awaiting_sync
        if sequence <= previous_seq:
            return
        if not is_sync_barrier and sequence != previous_seq + 1:
            await self._publish(
                SequenceGapDetected(
                    expected_seq=previous_seq + 1,
                    received_seq=sequence,
                    connection_generation=self._connection_generation,
                )
            )
            raise _TransientFailure("sequence_gap")

        self._state.apply_server_event(message)
        token = self._checkpoint.connection_token if self._checkpoint is not None else None
        if message_type == "session.joined":
            token_value = message["payload"]["connection_token"]
            try:
                SessionCheckpoint(token_value, sequence)
            except (TypeError, ValueError) as error:
                raise _FatalFailure(ClientExitReason.INVALID_SERVER_MESSAGE, str(error)) from error
            token = token_value
            self._authenticated = True
        elif message_type == "session.resumed":
            self._authenticated = True
        if token is None:
            raise _FatalFailure(ClientExitReason.CREDENTIAL_SAVE_FAILED, "no connection token")
        try:
            checkpoint = SessionCheckpoint(token, sequence)
            await self.credential_store.save(checkpoint)
        except CredentialError as error:
            raise _FatalFailure(ClientExitReason.CREDENTIAL_SAVE_FAILED) from error
        except (OSError, TypeError, ValueError) as error:
            raise _FatalFailure(ClientExitReason.CREDENTIAL_SAVE_FAILED, str(error)) from error
        self._checkpoint = checkpoint
        self._state.set_last_seq(sequence)

        event = ServerEvent(
            type=message["type"],
            protocol_version=message["protocol_version"],
            event_id=message["event_id"],
            game_id=message["game_id"],
            seq=sequence,
            timestamp=message["timestamp"],
            payload=immutable_mapping(message["payload"]),
        )
        await self._publish(event)
        if message_type == "action.rejected":
            payload = message["payload"]
            await self._publish(
                ActionRejected(payload["action"], payload["reason"], sequence)
            )
        if message_type in {"player.action_state", "game.state_sync"}:
            self._schedule_deadline(
                message["payload"]["action_state"]
                if message_type == "game.state_sync"
                else message["payload"],
                message["timestamp"],
            )

        if is_sync_barrier:
            if sequence > previous_seq + 1 and previous_seq > 0:
                await self._publish(
                    SequenceGapRecovered(
                        previous_seq=previous_seq,
                        recovered_seq=sequence,
                        connection_generation=self._connection_generation,
                    )
                )
            self._awaiting_sync = False
            self._connected_once_in_generation = True
            await self._set_lifecycle(ClientLifecycle.CONNECTED)

        if (
            message_type == "game.event"
            and message["payload"]["event_type"] == "GAME_ENDED"
        ):
            await self._publish(GameEnded(event))
            raise _GameEndedSignal()

    async def _send_request(
        self, message_type: str, payload: Mapping[str, Any], generation: int
    ) -> SendReceipt:
        if generation != self._connection_generation or self._outbound is None:
            raise NotDeliveredError("connection generation is no longer active")
        message = make_client_request(
            message_type,
            self.config.game_id,
            payload,
            protocol_version=self.config.protocol_version or self.validator.protocol_version,
            timestamp=self._timestamp(),
            validator=self.validator,
        )
        loop = asyncio.get_running_loop()
        command = _Command(
            message=message,
            action=message_type,
            generation=generation,
            future=loop.create_future(),
        )
        try:
            self._outbound.put_nowait(command)
        except asyncio.QueueFull as error:
            await self._publish(NotDelivered(message_type, "outbound_queue_full", generation))
            raise NotDeliveredError("outbound command queue is full") from error
        try:
            return await command.future
        except asyncio.CancelledError:
            # A queued command is intentionally not removed when its caller
            # cancels; the sender retains ownership after queue acceptance.
            raise

    async def _queue_request(self, message_type: str, payload: Mapping[str, Any]) -> SendReceipt:
        if self.lifecycle is not ClientLifecycle.CONNECTED:
            await self._publish(
                NotDelivered(message_type, "client_not_connected", self._connection_generation)
            )
            raise NotDeliveredError("client is not connected")
        return await self._send_request(message_type, payload, self._connection_generation)

    async def _sender_loop(self, socket: Socket, generation: int) -> None:
        assert self._outbound is not None
        queue = self._outbound
        while True:
            command = await queue.get()
            if command.generation != generation:
                await self._complete_not_delivered(command, "stale_generation")
                continue
            command.started = True
            try:
                await socket.send(
                    json.dumps(command.message, ensure_ascii=False, separators=(",", ":"))
                )
            except asyncio.CancelledError:
                if command.started:
                    await self._complete_delivery_unknown(command, "sender_cancelled")
                raise
            except Exception:
                await self._complete_delivery_unknown(command, "send_failed")
                self._background_failure = _TransientFailure("send_failed")
                await self._close_socket()
                return
            if not command.future.done():
                command.future.set_result(
                    SendReceipt(
                        event_id=command.message["event_id"],
                        connection_generation=generation,
                    )
                )

    async def _complete_not_delivered(self, command: _Command, reason: str) -> None:
        if not command.future.done():
            command.future.set_exception(NotDeliveredError(reason))
        # Client notices are best-effort during shutdown; server events are
        # never suppressed because they are the authoritative event stream.
        with suppress(_FatalFailure):
            await self._publish(NotDelivered(command.action, reason, command.generation))

    async def _complete_delivery_unknown(self, command: _Command, reason: str) -> None:
        if not command.future.done():
            command.future.set_exception(DeliveryUnknownError(reason))
        # Client notices are best-effort during shutdown; server events are
        # never suppressed because they are the authoritative event stream.
        with suppress(_FatalFailure):
            await self._publish(DeliveryUnknown(command.action, reason, command.generation))

    def _validate_action(self, action: Action, expected_type: str) -> None:
        if not isinstance(action, (AbilityAction, ChatAction, VoteAction, CoDeclareAction, CoReportAction)):
            raise TypeError("action must be a typed action handle")
        if action.type != expected_type:
            raise TypeError(f"expected {expected_type} action handle")
        snapshot = self._state.snapshot()
        if snapshot.lifecycle is not ClientLifecycle.CONNECTED:
            raise StaleActionError("client is not connected")
        if (
            action.connection_generation != self._connection_generation
            or action.action_generation != self._state.action_generation
            or action not in snapshot.actions
        ):
            raise StaleActionError("action handle is stale")

    def _schedule_deadline(self, payload: Mapping[str, Any], server_timestamp: int) -> None:
        previous = self._deadline_task
        if previous is not None:
            previous.cancel()
        deadline = payload.get("phase_ends_at")
        if deadline is None:
            self._deadline_task = None
            return
        phase = payload["phase"]
        day = payload["day"]
        action_generation = self._state.action_generation
        self._deadline_task = asyncio.create_task(
            self._deadline_worker(phase, day, deadline, server_timestamp, action_generation)
        )

    async def _deadline_worker(
        self,
        phase: str,
        day: int,
        deadline: int,
        server_timestamp: int,
        action_generation: int,
    ) -> None:
        try:
            await self._sleep(max(0.0, float(deadline - server_timestamp)))
            if (
                self.lifecycle is ClientLifecycle.CONNECTED
                and self._state.action_generation == action_generation
            ):
                await self._publish(PhaseDeadlineReached(phase, day, action_generation))
        except asyncio.CancelledError:
            raise
        except _FatalFailure as error:
            self._background_failure = error
            await self._close_socket()

    async def _cancel_deadline(self) -> None:
        task = self._deadline_task
        self._deadline_task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _stop_sender(self) -> None:
        queue = self._outbound
        if queue is not None:
            while not queue.empty():
                await self._complete_not_delivered(queue.get_nowait(), "connection_closed")
        task = self._sender_task
        self._sender_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _close_socket(self) -> None:
        socket = self._socket
        if socket is None:
            return
        close = getattr(socket, "close", None)
        if close is not None:
            result = close()
            if inspect.isawaitable(result):
                with suppress(Exception):
                    await result
        wait_closed = getattr(socket, "wait_closed", None)
        if wait_closed is not None:
            result = wait_closed()
            if inspect.isawaitable(result):
                with suppress(Exception):
                    await result

    def _raise_for_close(self, error: ConnectionClosed) -> None:
        code = getattr(error, "code", None)
        if code == 4001:
            raise _FatalFailure(ClientExitReason.AUTHENTICATION_FAILED, "session replaced") from error
        if code in {1002, 1003, 1007}:
            raise _FatalFailure(ClientExitReason.INVALID_SERVER_MESSAGE, f"close code {code}") from error
        if code == 1008:
            reason = (
                ClientExitReason.JOIN_OUTCOME_UNKNOWN
                if self._join_retry_after_ambiguous and self._join_request_sent
                else ClientExitReason.AUTHENTICATION_FAILED
            )
            raise _FatalFailure(reason, f"close code {code}") from error
        raise _TransientFailure(f"connection_closed_{code}") from error

    def _retry_delay(self, retry_number: int) -> float:
        policy = self.reconnect_policy
        base = min(
            policy.max_delay_seconds,
            policy.initial_delay_seconds * (policy.multiplier**retry_number),
        )
        if policy.jitter_ratio == 0:
            return base
        factor = 1.0 + self._random.uniform(-policy.jitter_ratio, policy.jitter_ratio)
        return max(0.0, base * factor)

    async def _set_lifecycle(self, lifecycle: ClientLifecycle) -> None:
        previous = self.lifecycle
        if previous is lifecycle:
            return
        self._state.set_lifecycle(lifecycle)
        await self._publish(LifecycleChanged(previous, lifecycle))

    async def _publish(self, event: ClientEvent) -> None:
        if self._events.full():
            raise _FatalFailure(ClientExitReason.CONSUMER_OVERRUN)
        self._events.put_nowait(event)

    async def _finish(
        self, reason: ClientExitReason, *, success: bool, detail: str | None = None
    ) -> ClientExit:
        await self._cancel_deadline()
        await self._close_socket()
        if reason in {ClientExitReason.GAME_ENDED, ClientExitReason.STOPPED}:
            if self.lifecycle is not ClientLifecycle.ENDED:
                with suppress(_FatalFailure):
                    await self._set_lifecycle(ClientLifecycle.ENDED)
        else:
            if self.lifecycle is not ClientLifecycle.FAILED:
                with suppress(_FatalFailure):
                    await self._set_lifecycle(ClientLifecycle.FAILED)
            with suppress(_FatalFailure):
                await self._publish(FatalTermination(reason, detail))
        await self._stop_sender()
        self._close_event_stream()
        return ClientExit(reason, success, self.lifecycle, detail)

    def _close_event_stream(self) -> None:
        if self._event_stream_closed:
            return
        self._event_stream_closed = True
        try:
            self._events.put_nowait(_EVENT_STREAM_END)
        except asyncio.QueueFull:
            # The queue is already in terminal overrun territory.  Preserve
            # wake-up semantics for consumers by dropping the oldest item.
            with suppress(asyncio.QueueEmpty):
                self._events.get_nowait()
            with suppress(asyncio.QueueFull):
                self._events.put_nowait(_EVENT_STREAM_END)


__all__ = ["NetworkClient"]
