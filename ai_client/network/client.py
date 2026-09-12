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
    ProtocolVersionMismatchError,
    make_client_request,
)
from .state import ClientState
from .types import (
    AbilityAction,
    Action,
    ActionAccepted,
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
    PhaseTimingMapped,
    ReconnectPolicy,
    ResumeRecoveryCompleted,
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
        self._stop_event = asyncio.Event()
        self._run_started = False
        self._checkpoint: SessionCheckpoint | None = None
        self._connection_generation = 0
        self._socket: Socket | None = None
        self._outbound: asyncio.Queue[_Command] | None = None
        self._sender_task: asyncio.Task[None] | None = None
        self._deadline_task: asyncio.Task[None] | None = None
        self._deadline_source_seq: int | None = None
        self._awaiting_sync = True
        self._authenticated = False
        self._connected_once_in_generation = False
        self._join_request_sent = False
        self._resume_request_sent = False
        self._resume_last_seq_requested: int | None = None
        self._join_retry_after_ambiguous = False
        self._resume_sync_required = False
        self._resume_replay_buffer: list[tuple[Mapping[str, Any], float | None]] = []
        self._resume_replay_first_seq: int | None = None
        self._resume_replay_last_seq: int | None = None
        self._resume_replay_contiguous = False
        self._resume_replay_gap_or_floor = False
        self._resume_resumed_seq: int | None = None
        self._terminal_candidate_seen = False
        self._sync_deadline: float | None = None
        self._sequence_gap_previous_seq: int | None = None
        self._background_failure: _FatalFailure | _TransientFailure | None = None
        self._cleanup_result: bool | None = True

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
            if self._event_stream_closed and self._events.empty():
                return
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
        self._stop_event.clear()
        self._sequence_gap_previous_seq = None
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
            if (
                checkpoint is not None
                and checkpoint.protocol_version != self.validator.protocol_version
            ):
                raise _FatalFailure(
                    ClientExitReason.INCOMPATIBLE_PROTOCOL_CHECKPOINT,
                    (
                        f"checkpoint protocol {checkpoint.protocol_version} cannot resume "
                        f"active protocol {self.validator.protocol_version}"
                    ),
                )
            self._checkpoint = checkpoint
            self._state.set_last_seq(checkpoint.last_seq if checkpoint is not None else 0)
            if (
                self.config.protocol_version is not None
                and self.config.protocol_version != self.validator.protocol_version
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
                await self._sleep_or_stop(min(delay, max(0.0, remaining)))
                retry_number += 1
        except _FatalFailure as failure:
            return await self._finish(failure.reason, success=False, detail=failure.detail)
        except asyncio.CancelledError:
            self._stop_requested = True
            self._stop_event.set()
            with suppress(_FatalFailure):
                if self.lifecycle is not ClientLifecycle.ENDED:
                    await self._set_lifecycle(ClientLifecycle.STOPPING)
            await self._cleanup_generation()
            with suppress(_FatalFailure):
                if self.lifecycle is not ClientLifecycle.ENDED:
                    await self._set_lifecycle(ClientLifecycle.ENDED)
            self._close_event_stream()
            raise
        except Exception as error:
            return await self._finish(ClientExitReason.INTERNAL_ERROR, success=False, detail=str(error))
        return await self._finish(ClientExitReason.STOPPED, success=True)

    async def stop(self) -> None:
        """Request a graceful stop and close the active transport."""

        self._stop_requested = True
        self._stop_event.set()
        if self._run_started and not self._event_stream_closed:
            await self._set_lifecycle(ClientLifecycle.STOPPING)
        if self._run_started:
            await self._cleanup_generation()
        else:
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
        self._resume_last_seq_requested = (
            self._checkpoint.last_seq if self._checkpoint is not None else None
        )
        self._resume_sync_required = False
        self._resume_replay_buffer.clear()
        self._resume_replay_first_seq = None
        self._resume_replay_last_seq = None
        self._resume_replay_contiguous = False
        self._resume_replay_gap_or_floor = False
        self._resume_resumed_seq = None
        self._sync_deadline = None
        self._background_failure = None
        self._cleanup_result = None
        await self._set_lifecycle(
            ClientLifecycle.RESUMING if self._checkpoint is not None else ClientLifecycle.JOINING
        )

        socket: Socket | None = None
        try:
            try:
                socket, _owner = await self._open_socket_or_stop()
            except asyncio.TimeoutError as error:
                raise _TransientFailure("connect_timeout") from error
            except (OSError, ConnectionError) as error:
                raise _TransientFailure("connect_failed") from error
            self._socket = socket
            if self._stop_requested:
                raise _TransientFailure("stopped")
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
            self._sync_deadline = (
                asyncio.get_running_loop().time() + self.config.sync_timeout_seconds
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
            cleanup_ok = await self._cleanup_generation()
            self._state.invalidate_actions()
            self._sync_deadline = None
            self._socket = None
            self._outbound = None
            self._resume_replay_buffer.clear()
            if not cleanup_ok:
                raise _FatalFailure(
                    ClientExitReason.INTERNAL_ERROR,
                    "shutdown timeout",
                )

        return _GenerationOutcome(connected=self._connected_once_in_generation)

    async def _open_socket_or_stop(self) -> tuple[Socket, Any | None]:
        connect_task = asyncio.create_task(self._open_socket())
        stop_task = asyncio.create_task(self._stop_event.wait())
        try:
            done, _pending = await asyncio.wait(
                {connect_task, stop_task},
                timeout=self.config.connect_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if connect_task in done:
                return await connect_task
            if stop_task in done or self._stop_requested:
                await self._cancel_and_close_socket_attempt(connect_task)
                raise _TransientFailure("stopped")
            await self._cancel_and_close_socket_attempt(connect_task)
            raise asyncio.TimeoutError()
        finally:
            connect_task.cancel()
            stop_task.cancel()
            await asyncio.gather(connect_task, stop_task, return_exceptions=True)

    async def _sleep_or_stop(self, delay: float) -> None:
        sleep_task = asyncio.create_task(self._sleep(delay))
        stop_task = asyncio.create_task(self._stop_event.wait())
        try:
            done, _pending = await asyncio.wait(
                {sleep_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stop_task in done or self._stop_requested:
                sleep_task.cancel()
                with suppress(asyncio.CancelledError):
                    await sleep_task
                return
            await sleep_task
        finally:
            sleep_task.cancel()
            stop_task.cancel()
            await asyncio.gather(sleep_task, stop_task, return_exceptions=True)

    async def _open_socket(self) -> tuple[Socket, Any | None]:
        candidate = self._connector(self.config.uri)
        if inspect.isawaitable(candidate):
            return await candidate, None
        enter = getattr(candidate, "__aenter__", None)
        if enter is not None:
            return await enter(), candidate
        return candidate, None

    async def _cancel_and_close_socket_attempt(
        self, connect_task: asyncio.Task[tuple[Socket, Any | None]]
    ) -> None:
        """Cancel a connector and close a socket it resolved during the race."""

        connect_task.cancel()
        try:
            opened = await connect_task
        except asyncio.CancelledError:
            return
        except Exception:
            return
        socket, owner = opened
        await self._close_socket_resource(socket)
        if owner is None:
            return
        exit_method = getattr(owner, "__aexit__", None)
        if exit_method is None:
            return
        try:
            result = exit_method(None, None, None)
        except Exception:
            return
        if inspect.isawaitable(result):
            with suppress(Exception):
                await result

    async def _receive(self, socket: Socket) -> Any:
        receiver = getattr(socket, "recv", None)
        if receiver is None:
            raise TypeError("WebSocket connection has no recv()")
        if self._awaiting_sync:
            if self._sync_deadline is None:
                self._sync_deadline = (
                    asyncio.get_running_loop().time() + self.config.sync_timeout_seconds
                )
            remaining = self._sync_deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError()
            return await asyncio.wait_for(receiver(), remaining)
        return await receiver()

    async def _accept_server_message(self, raw_message: str | bytes) -> None:
        try:
            message = self.validator.decode_server(raw_message)
        except ProtocolVersionMismatchError as error:
            raise _FatalFailure(
                ClientExitReason.INCOMPATIBLE_PROTOCOL,
                str(error),
            ) from error
        if message["protocol_version"] != self.validator.protocol_version:
            raise _FatalFailure(ClientExitReason.INCOMPATIBLE_PROTOCOL)
        if message["game_id"] != self.config.game_id:
            raise _FatalFailure(ClientExitReason.INVALID_SERVER_MESSAGE, "game_id mismatch")

        sequence = message["seq"]
        previous_seq = self._state.last_seq
        message_type = message["type"]
        observed_at_monotonic = (
            self._clock()
            if message_type in {"action.accepted", "action.rejected"}
            or self._timing_payload(message) is not None
            else None
        )
        is_sync_barrier = message_type == "game.state_sync" and self._awaiting_sync

        expected_ack = "session.resumed" if self._checkpoint is not None else "session.joined"
        if message_type in {"session.joined", "session.resumed"}:
            if message_type != expected_ack or self._authenticated:
                raise _FatalFailure(
                    ClientExitReason.INVALID_SERVER_MESSAGE,
                    f"unexpected authentication response: {message_type}",
                )
            if message_type == "session.resumed":
                if (
                    not self._resume_request_sent
                    or message["payload"]["last_seq"] != self._resume_last_seq_requested
                ):
                    raise _FatalFailure(
                        ClientExitReason.INVALID_SERVER_MESSAGE,
                        "session.resumed does not match the requested checkpoint",
                    )
            elif not self._join_request_sent:
                raise _FatalFailure(
                    ClientExitReason.INVALID_SERVER_MESSAGE,
                    "session.joined was not requested",
                )

        if not self._authenticated and message_type not in {"session.joined", "session.resumed"}:
            if self._resume_request_sent:
                self._buffer_resume_replay(message, observed_at_monotonic)
                return
            raise _FatalFailure(
                ClientExitReason.INVALID_SERVER_MESSAGE,
                f"server event arrived before session authentication: {message_type}",
            )

        if message_type == "session.resumed":
            requested_seq = self._resume_last_seq_requested
            if requested_seq is None or sequence <= requested_seq:
                raise _FatalFailure(
                    ClientExitReason.INVALID_SERVER_MESSAGE,
                    "session.resumed sequence is not newer than the requested checkpoint",
                )
            resumed_player_id = message["payload"]["player_id"]
            known_player_id = self._state.snapshot().player_id
            if known_player_id is not None and resumed_player_id != known_player_id:
                raise _FatalFailure(
                    ClientExitReason.INVALID_SERVER_MESSAGE,
                    "session.resumed does not match the known player",
                )
            if any(
                replay_message["type"] == "session.ready"
                and replay_message["payload"]["player_id"] != resumed_player_id
                for replay_message, _observed_at in self._resume_replay_buffer
            ):
                raise _FatalFailure(
                    ClientExitReason.INVALID_SERVER_MESSAGE,
                    "retained session.ready does not match the resumed player",
                )
            if self._resume_replay_buffer:
                self._resume_replay_first_seq = self._resume_replay_buffer[0][0]["seq"]
                self._resume_replay_last_seq = self._resume_replay_buffer[-1][0]["seq"]
                self._resume_replay_contiguous = True
                expected_sequence = requested_seq + len(self._resume_replay_buffer) + 1
                if sequence != expected_sequence:
                    raise _FatalFailure(
                        ClientExitReason.INVALID_SERVER_MESSAGE,
                        "session.resumed does not follow the retained replay",
                    )
                self._authenticated = True
                replay = tuple(self._resume_replay_buffer)
                self._resume_replay_buffer.clear()
                for replay_message, replay_observed_at in replay:
                    await self._commit_server_message(
                        replay_message,
                        observed_at_monotonic=replay_observed_at,
                    )
                previous_seq = self._state.last_seq
            else:
                self._authenticated = True
                if sequence > previous_seq + 1:
                    self._resume_replay_gap_or_floor = True
                    await self._publish(
                        SequenceGapDetected(
                            expected_seq=previous_seq + 1,
                            received_seq=sequence,
                            connection_generation=self._connection_generation,
                        )
                    )
                    self._resume_sync_required = True
                    if self._sequence_gap_previous_seq is None:
                        self._sequence_gap_previous_seq = previous_seq
                    self._state.apply_server_event(message)
                    await self._publish(self._server_event(message))
                    self._resume_resumed_seq = sequence
                    return
                self._resume_replay_contiguous = True
            self._resume_resumed_seq = sequence

        if self._resume_sync_required and not is_sync_barrier:
            # Replay is outside the server's retention window. Do not apply
            # an incremental event until the authoritative sync barrier arrives.
            return

        previous_seq = self._state.last_seq
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
            if self._sequence_gap_previous_seq is None:
                self._sequence_gap_previous_seq = previous_seq
            raise _TransientFailure("sequence_gap")

        await self._commit_server_message(
            message,
            is_sync_barrier=is_sync_barrier,
            observed_at_monotonic=observed_at_monotonic,
        )

    def _buffer_resume_replay(
        self,
        message: Mapping[str, Any],
        observed_at_monotonic: float | None,
    ) -> None:
        message_type = message["type"]
        if message_type == "game.state_sync" or (
            message_type.startswith("session.") and message_type != "session.ready"
        ):
            raise _FatalFailure(
                ClientExitReason.INVALID_SERVER_MESSAGE,
                f"invalid pre-authentication resume message: {message_type}",
            )
        requested_seq = self._resume_last_seq_requested
        if requested_seq is None:
            raise _FatalFailure(
                ClientExitReason.INVALID_SERVER_MESSAGE,
                "resume replay arrived without a requested checkpoint",
            )
        sequence = message["seq"]
        if sequence <= requested_seq:
            return
        if len(self._resume_replay_buffer) >= self.config.resume_replay_capacity:
            raise _FatalFailure(ClientExitReason.RESUME_BUFFER_OVERRUN)
        expected_sequence = requested_seq + len(self._resume_replay_buffer) + 1
        if sequence != expected_sequence:
            raise _FatalFailure(
                ClientExitReason.INVALID_SERVER_MESSAGE,
                "resume replay is not contiguous",
            )
        self._resume_replay_buffer.append((message, observed_at_monotonic))

    async def _commit_server_message(
        self,
        message: Mapping[str, Any],
        *,
        is_sync_barrier: bool = False,
        observed_at_monotonic: float | None = None,
    ) -> None:
        message_type = message["type"]
        sequence = message["seq"]
        self._state.apply_server_event(message)
        timing_payload = self._timing_payload(message)
        terminal_sync = is_sync_barrier and (
            self._terminal_candidate_seen
            or self._sync_history_has_game_ended(message["payload"])
        )
        if timing_payload is not None and not terminal_sync:
            # Invalidate the previous timer before credential persistence or raw
            # event publication can yield.  Otherwise a same-generation timing
            # replacement can expose its ServerEvent while the old timer still
            # considers itself current.
            self._deadline_source_seq = sequence
            if self._deadline_task is not None:
                self._deadline_task.cancel()
        token = self._checkpoint.connection_token if self._checkpoint is not None else None
        if message_type == "session.joined":
            token_value = message["payload"]["connection_token"]
            try:
                SessionCheckpoint(
                    token_value,
                    sequence,
                    self.validator.protocol_version,
                )
            except (TypeError, ValueError) as error:
                raise _FatalFailure(ClientExitReason.INVALID_SERVER_MESSAGE, str(error)) from error
            token = token_value
            self._authenticated = True
        if token is None:
            raise _FatalFailure(ClientExitReason.CREDENTIAL_SAVE_FAILED, "no connection token")
        try:
            checkpoint = SessionCheckpoint(
                token,
                sequence,
                self.validator.protocol_version,
            )
            await self.credential_store.save(checkpoint)
        except CredentialError as error:
            raise _FatalFailure(ClientExitReason.CREDENTIAL_SAVE_FAILED) from error
        except (OSError, TypeError, ValueError) as error:
            raise _FatalFailure(ClientExitReason.CREDENTIAL_SAVE_FAILED, str(error)) from error
        self._checkpoint = checkpoint
        self._state.set_last_seq(sequence)

        event = self._server_event(message)
        terminal_event = (
            message_type == "game.event"
            and message["payload"]["event_type"] == "GAME_ENDED"
            and not self._awaiting_sync
            and not self._resume_sync_required
        )
        if terminal_sync or terminal_event:
            self._ensure_event_capacity(
                1 + (1 if self._sequence_gap_previous_seq is not None else 0) + 2
            )
        await self._publish(event)
        if message_type == "action.accepted":
            payload = message["payload"]
            assert observed_at_monotonic is not None
            await self._publish(
                ActionAccepted(
                    action=payload["action"],
                    request_event_id=payload["request_event_id"],
                    seq=sequence,
                    observation_connection_generation=self._connection_generation,
                    observed_at_monotonic=observed_at_monotonic,
                )
            )
        if message_type == "action.rejected":
            payload = message["payload"]
            assert observed_at_monotonic is not None
            await self._publish(
                ActionRejected(
                    action=payload["action"],
                    reason=payload["reason"],
                    seq=sequence,
                    connection_generation=self._connection_generation,
                    observed_at_monotonic=observed_at_monotonic,
                    request_event_id=payload["request_event_id"],
                )
            )
        if timing_payload is not None and not terminal_sync:
            assert observed_at_monotonic is not None
            await self._replace_deadline_mapping(
                timing_payload,
                source_seq=sequence,
                server_timestamp=message["timestamp"],
                mapped_at_monotonic=observed_at_monotonic,
            )

        if message_type == "game.event" and message["payload"]["event_type"] == "GAME_ENDED":
            self._terminal_candidate_seen = True

        if is_sync_barrier:
            if self._sequence_gap_previous_seq is not None:
                await self._publish(
                    SequenceGapRecovered(
                        previous_seq=self._sequence_gap_previous_seq,
                        recovered_seq=sequence,
                        connection_generation=self._connection_generation,
                    )
                )
                self._sequence_gap_previous_seq = None
            if self._resume_last_seq_requested is not None:
                resumed_seq = self._resume_resumed_seq
                if resumed_seq is None:
                    raise _FatalFailure(
                        ClientExitReason.INVALID_SERVER_MESSAGE,
                        "resume sync arrived without a matching resume acknowledgement",
                    )
                await self._publish(
                    ResumeRecoveryCompleted(
                        connection_generation=self._connection_generation,
                        requested_last_seq=self._resume_last_seq_requested,
                        replay_first_seq=self._resume_replay_first_seq,
                        replay_last_seq=self._resume_replay_last_seq,
                        replay_contiguous=self._resume_replay_contiguous,
                        replay_gap_or_floor=self._resume_replay_gap_or_floor,
                        resumed_seq=resumed_seq,
                        state_sync_seq=sequence,
                    )
                )
            self._awaiting_sync = False
            self._resume_sync_required = False
            self._connected_once_in_generation = True
            if terminal_sync:
                await self._publish(GameEnded(event))
                await self._set_lifecycle(ClientLifecycle.ENDED)
                raise _GameEndedSignal()
            await self._set_lifecycle(ClientLifecycle.CONNECTED)

        if terminal_event:
            await self._publish(GameEnded(event))
            await self._set_lifecycle(ClientLifecycle.ENDED)
            raise _GameEndedSignal()

    @staticmethod
    def _sync_history_has_game_ended(payload: Mapping[str, Any]) -> bool:
        history = payload.get("history", ())
        if not isinstance(history, Sequence) or isinstance(history, (str, bytes)):
            return False
        for entry in history:
            if not isinstance(entry, Mapping) or entry.get("type") != "game.event":
                continue
            entry_payload = entry.get("payload")
            if (
                isinstance(entry_payload, Mapping)
                and entry_payload.get("event_type") == "GAME_ENDED"
            ):
                return True
        return False

    def _ensure_event_capacity(self, count: int) -> None:
        if self._events.maxsize - self._events.qsize() < count:
            raise _FatalFailure(ClientExitReason.CONSUMER_OVERRUN)

    def _server_event(self, message: Mapping[str, Any]) -> ServerEvent:
        return ServerEvent(
            type=message["type"],
            protocol_version=message["protocol_version"],
            event_id=message["event_id"],
            game_id=message["game_id"],
            seq=message["seq"],
            timestamp=message["timestamp"],
            payload=immutable_mapping(message["payload"]),
        )

    async def _send_request(
        self, message_type: str, payload: Mapping[str, Any], generation: int
    ) -> SendReceipt:
        if (
            self._stop_requested
            or generation != self._connection_generation
            or self._outbound is None
        ):
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
            if self._stop_requested:
                await self._complete_not_delivered(command, "client_stopping")
                return
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
            command.future.set_exception(
                DeliveryUnknownError(
                    reason,
                    request_event_id=command.message["event_id"],
                    action=command.action,
                    connection_generation=command.generation,
                )
            )
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

    @staticmethod
    def _timing_payload(message: Mapping[str, Any]) -> Mapping[str, Any] | None:
        message_type = message["type"]
        if message_type == "player.action_state":
            return message["payload"]
        if message_type == "game.state_sync":
            return message["payload"]["action_state"]
        if message_type == "game.event":
            payload = message["payload"]
            if payload["event_type"] in {"DAY_EXTENDED", "DAY_SHORTENED"}:
                return payload["event_payload"]
        return None

    async def _replace_deadline_mapping(
        self,
        payload: Mapping[str, Any],
        *,
        source_seq: int,
        server_timestamp: int,
        mapped_at_monotonic: float,
    ) -> None:
        previous = self._deadline_task
        if previous is not None:
            previous.cancel()
            with suppress(asyncio.CancelledError):
                await previous
        deadline = payload.get("phase_ends_at")
        phase = payload["phase"]
        day = payload["day"]
        action_generation = self._state.action_generation
        local_deadline = (
            None
            if deadline is None
            else mapped_at_monotonic + max(0, deadline - server_timestamp)
        )
        mapping = PhaseTimingMapped(
            phase=phase,
            day=day,
            source_seq=source_seq,
            connection_generation=self._connection_generation,
            action_generation=action_generation,
            server_timestamp=server_timestamp,
            phase_ends_at=deadline,
            mapped_at_monotonic=mapped_at_monotonic,
            local_deadline_monotonic=local_deadline,
        )
        self._deadline_source_seq = source_seq
        await self._publish(mapping)
        if local_deadline is None:
            self._deadline_task = None
            return
        self._deadline_task = asyncio.create_task(
            self._deadline_worker(
                phase,
                day,
                source_seq,
                self._connection_generation,
                action_generation,
                local_deadline,
                max(0.0, local_deadline - self._clock()),
            )
        )

    async def _deadline_worker(
        self,
        phase: str,
        day: int,
        source_seq: int,
        connection_generation: int,
        action_generation: int,
        local_deadline_monotonic: float,
        delay_seconds: float,
    ) -> None:
        try:
            await self._sleep(delay_seconds)
            if (
                self.lifecycle is ClientLifecycle.CONNECTED
                and self._connection_generation == connection_generation
                and self._state.action_generation == action_generation
                and self._deadline_source_seq == source_seq
            ):
                await self._publish(
                    PhaseDeadlineReached(
                        phase=phase,
                        day=day,
                        connection_generation=connection_generation,
                        action_generation=action_generation,
                        local_deadline_monotonic=local_deadline_monotonic,
                        reached_at_monotonic=self._clock(),
                    )
                )
        except asyncio.CancelledError:
            raise
        except _FatalFailure as error:
            self._background_failure = error
            await self._close_socket()

    async def _cancel_deadline(self) -> None:
        task = self._deadline_task
        self._deadline_task = None
        self._deadline_source_seq = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _cleanup_generation(self) -> bool:
        """Bound all transport/task cleanup to one shutdown deadline."""

        if self._cleanup_result is not None:
            if not self._cleanup_result:
                return False
            if (
                self._socket is None
                and self._sender_task is None
                and self._deadline_task is None
                and self._outbound is None
            ):
                return True
            # A stop can finish while the connector is still resolving.  If
            # that connector subsequently hands us a socket, the generation
            # finally must still close the newly-owned resources.
            self._cleanup_result = None

        if (
            self._socket is None
            and self._sender_task is None
            and self._deadline_task is None
            and self._outbound is None
        ):
            self._cleanup_result = True
            return True

        deadline = asyncio.get_running_loop().time() + self.config.shutdown_timeout_seconds
        cleanup_ok = True
        # Cancel client-owned tasks before waiting on the transport.  This
        # keeps a blocked sender from surviving a close/wait_closed timeout.
        for cleanup in (self._cancel_deadline, self._stop_sender, self._close_socket):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                cleanup_ok = False
                break
            try:
                await asyncio.wait_for(cleanup(), timeout=remaining)
            except asyncio.TimeoutError:
                cleanup_ok = False
                break
        if not cleanup_ok:
            self._abort_socket()
        self._cleanup_result = cleanup_ok
        return cleanup_ok

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
        await self._close_socket_resource(socket)

    async def _close_socket_resource(self, socket: Socket) -> None:
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

    def _abort_socket(self) -> None:
        socket = self._socket
        if socket is None:
            return
        transport = getattr(socket, "transport", None)
        abort = getattr(transport, "abort", None)
        if callable(abort):
            with suppress(Exception):
                abort()

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
        if not await self._cleanup_generation():
            reason = ClientExitReason.INTERNAL_ERROR
            success = False
            detail = "shutdown timeout"
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
        self._close_event_stream()
        return ClientExit(reason, success, self.lifecycle, detail)

    def _close_event_stream(self) -> None:
        if self._event_stream_closed:
            return
        self._event_stream_closed = True
        if not self._events.full():
            self._events.put_nowait(_EVENT_STREAM_END)


__all__ = ["NetworkClient"]
