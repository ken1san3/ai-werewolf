"""Per-game bounded generation-admission broker.

The broker owns one direct structured backend and one provider-call task at a
time.  It does not start, stop, inspect, or otherwise own the external model
provider.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum, auto
import hashlib
import hmac
import json
import math
import time
from typing import Callable, Mapping

from .admission_client import (
    _ProtocolViolation,
    _encode_frame,
    _exact,
    _read_frame,
)
from .admission_metrics import AdmissionMetric, AdmissionMetrics
from .admission_types import (
    GENERATION_IPC_PROTOCOL,
    AdmissionBrokerSnapshot,
    AdmissionCredentials,
    AdmissionRequest,
    AdmissionStatus,
    BrokerReady,
    GenerationBrokerConfig,
    GenerationPriority,
    request_from_wire,
)
from .backend import StructuredLLMBackend
from .types import (
    BackendIdentity,
    LLMBackendError,
    LLMBackendErrorCode,
    LLMMessage,
    LLMUsage,
    ProviderQuiescence,
    ProviderTiming,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
)


class _SlotState(Enum):
    ENQUEUED = auto()
    OFFERED = auto()
    CLAIMED = auto()
    ACTIVE = auto()
    DRAINING = auto()


@dataclass
class _AttachedSuccessor:
    request: AdmissionRequest
    attached_at: float


@dataclass
class _Slot:
    client_id: str
    request: AdmissionRequest
    state: _SlotState
    enqueued_at: float
    call_count: int = 0
    request_ids: set[str] | None = None
    attached: _AttachedSuccessor | None = None
    consumer_current: bool = True
    provider_task: asyncio.Task[None] | None = None
    drain_watch: asyncio.Task[None] | None = None
    provider_started_at: float | None = None
    request_bytes: int | None = None

    def __post_init__(self) -> None:
        if self.request_ids is None:
            self.request_ids = set()


class _Connection:
    def __init__(
        self,
        client_id: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        max_frame_bytes: int,
    ) -> None:
        self.client_id = client_id
        self.reader = reader
        self.writer = writer
        self.max_frame_bytes = max_frame_bytes
        self.outgoing: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=16)
        self.writer_task: asyncio.Task[None] | None = None
        self.closed = False

    def emit(self, message_type: str, **fields: object) -> bool:
        if self.closed:
            return False
        encoded = _encode_frame(self.max_frame_bytes, message_type, **fields)
        try:
            self.outgoing.put_nowait(encoded)
        except asyncio.QueueFull:
            return False
        return True

    async def writer_loop(self) -> None:
        try:
            while True:
                encoded = await self.outgoing.get()
                if encoded is None:
                    self.outgoing.task_done()
                    break
                self.writer.write(encoded)
                await self.writer.drain()
                self.outgoing.task_done()
        except (ConnectionError, OSError):
            pass

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.outgoing.put_nowait(None)
        except asyncio.QueueFull:
            pass
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except OSError:
            pass
        if self.writer_task is not None and not self.writer_task.done():
            self.writer_task.cancel()
        if self.writer_task is not None:
            await asyncio.gather(self.writer_task, return_exceptions=True)


def _identity_to_wire(identity: BackendIdentity) -> object:
    return {
        "backend_type": identity.backend_type,
        "config_fingerprint": identity.config_fingerprint,
        "endpoint_origin": identity.endpoint_origin,
        "endpoint_path": identity.endpoint_path,
        "model": identity.model,
    }


def _structured_request_from_wire(value: object) -> StructuredGenerationRequest:
    if not isinstance(value, dict) or set(value) != {
        "messages",
        "output_schema",
        "request_id",
    }:
        raise _ProtocolViolation("invalid structured request")
    raw_messages = value["messages"]
    if not isinstance(raw_messages, list) or not raw_messages:
        raise _ProtocolViolation("invalid structured request messages")
    messages: list[LLMMessage] = []
    try:
        for raw in raw_messages:
            if not isinstance(raw, dict) or set(raw) != {"content", "role"}:
                raise _ProtocolViolation("invalid structured request message")
            messages.append(
                LLMMessage(
                    role=raw["role"],  # type: ignore[arg-type]
                    content=raw["content"],  # type: ignore[arg-type]
                )
            )
        return StructuredGenerationRequest(
            request_id=value["request_id"],  # type: ignore[arg-type]
            messages=tuple(messages),
            output_schema=value["output_schema"],  # type: ignore[arg-type]
        )
    except (TypeError, ValueError):
        raise _ProtocolViolation("invalid structured request") from None


def _structured_response_to_wire(response: StructuredGenerationResponse) -> object:
    timing = response.provider_timing
    return {
        "finish_reason": response.finish_reason,
        "provider_model": response.provider_model,
        "provider_timing": (
            None
            if timing is None
            else {
                "completion_microseconds": timing.completion_microseconds,
                "completion_tokens": timing.completion_tokens,
                "prompt_microseconds": timing.prompt_microseconds,
                "prompt_tokens": timing.prompt_tokens,
            }
        ),
        "request_id": response.request_id,
        "text": response.text,
        "usage": {
            "completion_tokens": response.usage.completion_tokens,
            "prompt_tokens": response.usage.prompt_tokens,
        },
    }


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(child) for child in value]
    return value


def _structured_request_size(request: StructuredGenerationRequest) -> int:
    value = {
        "messages": [
            {"content": message.content, "role": message.role}
            for message in request.messages
        ],
        "output_schema": _plain_json(request.output_schema),
        "request_id": request.request_id,
    }
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )


def _structured_response_size(response: StructuredGenerationResponse) -> int:
    return len(
        json.dumps(
            _structured_response_to_wire(response),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )


class GenerationAdmissionBroker:
    """One authenticated loopback broker for a bounded set of opaque clients."""

    owns_external_provider = False

    def __init__(
        self,
        registry: Mapping[str, str],
        backend: StructuredLLMBackend,
        *,
        fairness_seed: str,
        config: GenerationBrokerConfig = GenerationBrokerConfig(),
        metrics: AdmissionMetrics | None = None,
        clock: Callable[[], float] = time.monotonic,
        backend_request_timeout_seconds: float | None = None,
    ) -> None:
        if not isinstance(registry, Mapping) or not registry:
            raise ValueError("registry must contain at least one client")
        if not isinstance(config, GenerationBrokerConfig):
            raise TypeError("config must be GenerationBrokerConfig")
        if len(registry) > config.max_clients:
            raise ValueError("registry exceeds max_clients")
        copied_registry: dict[str, str] = {}
        for client_id, token in registry.items():
            credentials = AdmissionCredentials(client_id=client_id, token=token)
            copied_registry[credentials.client_id] = credentials.token
        if len(copied_registry) != len(registry):
            raise ValueError("registry contains duplicate client IDs")
        if not isinstance(backend, StructuredLLMBackend):
            raise TypeError("backend must implement StructuredLLMBackend")
        if not isinstance(fairness_seed, str) or not fairness_seed:
            raise ValueError("fairness_seed must be a non-empty string")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if backend_request_timeout_seconds is None:
            backend_config = getattr(backend, "_config", None)
            backend_request_timeout_seconds = getattr(
                backend_config, "request_timeout_seconds", None
            )
        if backend_request_timeout_seconds is None:
            backend_request_timeout_seconds = config.provider_drain_grace_seconds
        if (
            isinstance(backend_request_timeout_seconds, bool)
            or not isinstance(backend_request_timeout_seconds, (int, float))
            or not math.isfinite(backend_request_timeout_seconds)
            or backend_request_timeout_seconds <= 0
        ):
            raise ValueError("backend request timeout must be finite and positive")
        if config.provider_drain_grace_seconds < backend_request_timeout_seconds:
            raise ValueError(
                "provider_drain_grace_seconds must cover backend request timeout"
            )
        self._registry = copied_registry
        self._backend = backend
        self._fairness_seed = fairness_seed
        self._config = config
        self._clock = clock
        from ai_client.game_time import GameTime
        self._game_time = GameTime.from_env()
        self._backend_request_timeout_seconds = float(
            backend_request_timeout_seconds
        )
        self._metrics = metrics or AdmissionMetrics(
            config.metrics_queue_capacity
        )
        if not isinstance(self._metrics, AdmissionMetrics):
            raise TypeError("metrics must be AdmissionMetrics")
        self._lock = asyncio.Lock()
        self._server: asyncio.AbstractServer | None = None
        self._connections: dict[str, _Connection] = {}
        self._used_clients: set[str] = set()
        self._slots: dict[str, _Slot] = {}
        self._current_client: str | None = None
        self._seen_invocations: set[str] = set()
        self._invocation_owner: dict[str, str] = {}
        self._terminal_status: dict[str, str] = {}
        self._ordinary_terminal_notified: set[str] = set()
        self._deadline_tasks: dict[str, asyncio.Task[None]] = {}
        self._control_tasks: set[asyncio.Task[object]] = set()
        self._closing = False
        self._closed = False
        self._poisoned = False
        self._poison_reason: str | None = None
        self._cleanup_incomplete = False
        self._started_at: float | None = None
        self._order = self._fairness_order()
        self._cursor = {
            priority: self._initial_cursor(priority)
            for priority in GenerationPriority
        }

    @property
    def ready(self) -> BrokerReady:
        server = self._server
        if server is None or not server.sockets:
            raise RuntimeError("broker is not started")
        address = server.sockets[0].getsockname()
        return BrokerReady(
            host=str(address[0]),
            port=int(address[1]),
            protocol=GENERATION_IPC_PROTOCOL,
            backend_identity=self._backend.identity,
            config_fingerprint=self._config.config_fingerprint,
        )

    @property
    def metrics(self) -> AdmissionMetrics:
        return self._metrics

    @property
    def snapshot(self) -> AdmissionBrokerSnapshot:
        current = (
            self._slots.get(self._current_client)
            if self._current_client is not None
            else None
        )
        return AdmissionBrokerSnapshot(
            offered=(
                current.request.invocation_id
                if current is not None and current.state is _SlotState.OFFERED
                else None
            ),
            claimed=(
                current.request.invocation_id
                if current is not None
                and current.state
                in {
                    _SlotState.CLAIMED,
                    _SlotState.ACTIVE,
                    _SlotState.DRAINING,
                }
                else None
            ),
            provider_call_active=(
                current is not None
                and current.provider_task is not None
                and not current.provider_task.done()
            ),
            draining=current is not None and current.state is _SlotState.DRAINING,
            poisoned=self._poisoned,
            poison_reason=self._poison_reason,
            pending_total=len(self._slots),
            connected_clients=len(self._connections),
        )

    @property
    def shutdown_clean(self) -> bool:
        return self._closed and not self._poisoned and not self._cleanup_incomplete

    async def start(self, host: str = "127.0.0.1") -> BrokerReady:
        if host not in {"127.0.0.1", "::1"}:
            raise ValueError("broker host must be numeric loopback")
        if self._server is not None or self._closed:
            raise RuntimeError("broker is already started or closed")
        self._started_at = self._clock()
        await self._metrics.start()
        try:
            self._server = await asyncio.start_server(
                self._accept_client, host=host, port=0, start_serving=True
            )
        except BaseException:
            await self._metrics.aclose()
            raise
        return self.ready

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closing = True
        server = self._server
        if server is not None:
            server.close()
        try:
            async with asyncio.timeout(self._config.shutdown_grace_seconds):
                provider_tasks: list[asyncio.Task[None]] = []
                async with self._lock:
                    for slot in tuple(self._slots.values()):
                        if slot.state in {_SlotState.ACTIVE, _SlotState.DRAINING}:
                            slot.consumer_current = False
                            slot.state = _SlotState.DRAINING
                            if slot.provider_task is not None:
                                provider_tasks.append(slot.provider_task)
                                self._ensure_drain_watch_locked(slot)
                            if slot.attached is not None:
                                self._terminal_attached_locked(
                                    slot, AdmissionStatus.UNAVAILABLE
                                )
                        else:
                            self._terminal_slot_locked(
                                slot, AdmissionStatus.UNAVAILABLE, notify=True
                            )
                if provider_tasks:
                    await asyncio.gather(
                        *(asyncio.shield(task) for task in provider_tasks),
                        return_exceptions=True,
                    )
                metrics_error: Exception | None = None
                try:
                    await self._metrics.flush()
                except Exception as error:
                    metrics_error = error
                    self._cleanup_incomplete = True
                connections = tuple(self._connections.values())
                await asyncio.gather(
                    *(connection.close() for connection in connections),
                    return_exceptions=True,
                )
                if server is not None:
                    await server.wait_closed()
                try:
                    await self._metrics.aclose()
                except Exception as error:
                    if metrics_error is None:
                        metrics_error = error
                    self._cleanup_incomplete = True
                await self._backend.aclose()
                if metrics_error is not None:
                    raise metrics_error
        except TimeoutError:
            self._cleanup_incomplete = True
            async with self._lock:
                self._poison_locked("ADMISSION_POISONED")
            for slot in tuple(self._slots.values()):
                if slot.provider_task is not None and not slot.provider_task.done():
                    slot.provider_task.cancel()
            await asyncio.gather(
                *(connection.close() for connection in self._connections.values()),
                return_exceptions=True,
            )
            try:
                async with asyncio.timeout(self._config.cancellation_grace_seconds):
                    await self._backend.aclose()
            except (TimeoutError, Exception):
                self._cleanup_incomplete = True
        finally:
            for task in tuple(self._control_tasks):
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._control_tasks, return_exceptions=True)
            self._connections.clear()
            self._closed = True

    async def _accept_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        connection: _Connection | None = None
        try:
            async with asyncio.timeout(
                self._config.authentication_timeout_seconds
            ):
                hello = _exact(
                    await _read_frame(reader, self._config.max_frame_bytes),
                    {"client_id", "protocol", "token", "type"},
                )
                if hello["type"] != "HELLO":
                    raise _ProtocolViolation("authentication rejected")
                client_id = hello["client_id"]
                token = hello["token"]
                if not isinstance(client_id, str) or not isinstance(token, str):
                    raise _ProtocolViolation("authentication rejected")
                async with self._lock:
                    expected = self._registry.get(client_id)
                    authenticated = (
                        expected is not None
                        and hmac.compare_digest(expected, token)
                        and client_id not in self._used_clients
                        and client_id not in self._connections
                        and not self._closing
                    )
                    if not authenticated:
                        raise _ProtocolViolation("authentication rejected")
                    self._used_clients.add(client_id)
                    connection = _Connection(
                        client_id,
                        reader,
                        writer,
                        self._config.max_frame_bytes,
                    )
                    self._connections[client_id] = connection
                writer.write(
                    _encode_frame(
                        self._config.max_frame_bytes,
                        "READY",
                        backend_identity=_identity_to_wire(self._backend.identity),
                        config_fingerprint=self._config.config_fingerprint,
                    )
                )
                await writer.drain()
            connection.writer_task = asyncio.create_task(
                connection.writer_loop(),
                name=f"aiwolf-admission-broker-writer-{connection.client_id}",
            )
            while not self._closing:
                frame = await _read_frame(reader, self._config.max_frame_bytes)
                await self._dispatch(connection, frame)
        except (
            asyncio.IncompleteReadError,
            asyncio.CancelledError,
            ConnectionError,
            OSError,
            TimeoutError,
            _ProtocolViolation,
        ):
            pass
        finally:
            if connection is not None:
                await self._disconnect(connection)
                await connection.close()
            else:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass

    async def _dispatch(
        self, connection: _Connection, raw: dict[str, object]
    ) -> None:
        message_type = raw.get("type")
        if message_type == "ENQUEUE":
            frame = _exact(raw, {"protocol", "request", "type"})
            try:
                request = request_from_wire(frame["request"])
            except ValueError:
                raise _ProtocolViolation("invalid admission request") from None
            async with self._lock:
                self._enqueue_locked(connection, request)
            return
        if message_type == "CLAIM":
            frame = _exact(raw, {"invocation_id", "protocol", "type"})
            invocation_id = self._owned_id(frame["invocation_id"])
            async with self._lock:
                self._claim_locked(connection, invocation_id)
            return
        if message_type == "REPLACE":
            frame = _exact(
                raw,
                {"old_invocation_id", "protocol", "replacement", "type"},
            )
            old_id = self._owned_id(frame["old_invocation_id"])
            try:
                replacement = request_from_wire(frame["replacement"])
            except ValueError:
                raise _ProtocolViolation("invalid replacement") from None
            async with self._lock:
                self._replace_locked(connection, old_id, replacement)
            return
        if message_type == "RESERVE_SUCCESSOR":
            frame = _exact(
                raw,
                {
                    "active_invocation_id",
                    "protocol",
                    "successor",
                    "type",
                },
            )
            active_id = self._owned_id(frame["active_invocation_id"])
            try:
                successor = request_from_wire(frame["successor"])
            except ValueError:
                raise _ProtocolViolation("invalid successor") from None
            async with self._lock:
                self._reserve_successor_locked(connection, active_id, successor)
            return
        if message_type == "CANCEL_SUCCESSOR":
            frame = _exact(
                raw,
                {"protocol", "successor_invocation_id", "type"},
            )
            successor_id = self._owned_id(frame["successor_invocation_id"])
            async with self._lock:
                self._cancel_successor_locked(connection, successor_id)
            return
        if message_type == "CANCEL":
            frame = _exact(raw, {"invocation_id", "protocol", "type"})
            invocation_id = self._owned_id(frame["invocation_id"])
            async with self._lock:
                self._cancel_locked(connection, invocation_id)
            return
        if message_type == "ABANDON":
            frame = _exact(raw, {"invocation_id", "protocol", "type"})
            invocation_id = self._owned_id(frame["invocation_id"])
            async with self._lock:
                self._abandon_locked(connection, invocation_id)
            return
        if message_type == "RELEASE":
            frame = _exact(raw, {"invocation_id", "protocol", "type"})
            invocation_id = self._owned_id(frame["invocation_id"])
            async with self._lock:
                self._release_locked(connection, invocation_id)
            return
        if message_type == "GENERATE":
            frame = _exact(
                raw,
                {
                    "call_ordinal",
                    "invocation_id",
                    "protocol",
                    "structured_request",
                    "type",
                },
            )
            invocation_id = self._owned_id(frame["invocation_id"])
            ordinal = frame["call_ordinal"]
            if type(ordinal) is not int:
                raise _ProtocolViolation("invalid call ordinal")
            request = _structured_request_from_wire(frame["structured_request"])
            async with self._lock:
                self._generate_locked(connection, invocation_id, ordinal, request)
            return
        raise _ProtocolViolation("unknown message type")

    @staticmethod
    def _owned_id(value: object) -> str:
        if not isinstance(value, str) or not value or len(value) > 128:
            raise _ProtocolViolation("invalid invocation owner")
        return value

    def _enqueue_locked(
        self, connection: _Connection, request: AdmissionRequest
    ) -> None:
        if request.invocation_id in self._seen_invocations:
            raise _ProtocolViolation("invocation ID reuse")
        self._seen_invocations.add(request.invocation_id)
        self._invocation_owner[request.invocation_id] = connection.client_id
        if self._poisoned:
            self._remember_terminal_locked(
                connection.client_id, request, AdmissionStatus.POISONED
            )
            self._emit_ordinary_terminal_ack_once(
                connection.client_id, request.invocation_id, AdmissionStatus.POISONED
            )
            return
        if self._closing:
            self._remember_terminal_locked(
                connection.client_id, request, AdmissionStatus.UNAVAILABLE
            )
            self._emit_ordinary_terminal_ack_once(
                connection.client_id, request.invocation_id, AdmissionStatus.UNAVAILABLE
            )
            return
        if self._clock() >= request.not_after_monotonic:
            self._remember_terminal_locked(
                connection.client_id, request, AdmissionStatus.EXPIRED
            )
            self._emit_ordinary_terminal_ack_once(
                connection.client_id, request.invocation_id, AdmissionStatus.EXPIRED
            )
            return
        if (
            connection.client_id in self._slots
            or len(self._slots) >= self._config.max_pending_total
        ):
            self._remember_terminal_locked(
                connection.client_id, request, AdmissionStatus.OVERLOADED
            )
            self._emit_ordinary_terminal_ack_once(
                connection.client_id, request.invocation_id, AdmissionStatus.OVERLOADED
            )
            return
        now = self._clock()
        slot = _Slot(
            client_id=connection.client_id,
            request=request,
            state=_SlotState.ENQUEUED,
            enqueued_at=now,
        )
        self._slots[connection.client_id] = slot
        self._record(slot, "ENQUEUED")
        self._schedule_deadline_locked(slot.client_id, request)
        self._offer_next_locked()

    def _claim_locked(self, connection: _Connection, invocation_id: str) -> None:
        terminal = self._terminal_status.get(invocation_id)
        if terminal is not None:
            self._require_invocation_owner(connection, invocation_id)
            self._emit_ordinary_terminal_ack_once(
                connection.client_id, invocation_id, terminal
            )
            return
        slot = self._require_slot_owner(connection, invocation_id)
        if slot.state is not _SlotState.OFFERED or self._current_client != slot.client_id:
            raise _ProtocolViolation("claim without current offer")
        if self._clock() >= slot.request.not_after_monotonic:
            self._terminal_slot_locked(slot, AdmissionStatus.EXPIRED, notify=True)
            self._offer_next_locked()
            return
        slot.state = _SlotState.CLAIMED
        order_index = self._order.index(slot.client_id)
        self._cursor[slot.request.priority] = (order_index + 1) % len(self._order)
        self._record(slot, "CLAIMED")
        self._emit_ack(slot.client_id, invocation_id, AdmissionStatus.GRANTED)

    def _replace_locked(
        self,
        connection: _Connection,
        old_invocation_id: str,
        replacement: AdmissionRequest,
    ) -> None:
        if replacement.invocation_id in self._seen_invocations:
            raise _ProtocolViolation("invocation ID reuse")
        if replacement.priority is not GenerationPriority.RESERVATION:
            raise _ProtocolViolation("replacement must be a reservation")
        old_terminal = self._terminal_status.get(old_invocation_id)
        if old_terminal is not None:
            self._require_invocation_owner(connection, old_invocation_id)
            if old_terminal == AdmissionStatus.CANCELLED.value:
                self._enqueue_locked(connection, replacement)
                return
            self._seen_invocations.add(replacement.invocation_id)
            self._invocation_owner[replacement.invocation_id] = connection.client_id
            self._remember_terminal_locked(
                connection.client_id, replacement, AdmissionStatus.UNAVAILABLE
            )
            self._emit_ordinary_terminal_ack_once(
                connection.client_id,
                replacement.invocation_id,
                AdmissionStatus.UNAVAILABLE,
            )
            return
        slot = self._require_slot_owner(connection, old_invocation_id)
        if (
            slot.state not in {_SlotState.ENQUEUED, _SlotState.OFFERED}
            or slot.request.priority is not GenerationPriority.REACTION
            or slot.call_count != 0
        ):
            self._seen_invocations.add(replacement.invocation_id)
            self._invocation_owner[replacement.invocation_id] = connection.client_id
            self._remember_terminal_locked(
                connection.client_id, replacement, AdmissionStatus.UNAVAILABLE
            )
            self._emit_ordinary_terminal_ack_once(
                connection.client_id,
                replacement.invocation_id,
                AdmissionStatus.UNAVAILABLE,
            )
            return
        self._seen_invocations.add(replacement.invocation_id)
        self._invocation_owner[replacement.invocation_id] = connection.client_id
        self._remember_terminal_locked(
            slot.client_id, slot.request, AdmissionStatus.REPLACED
        )
        self._emit_ordinary_terminal_ack_once(
            slot.client_id, old_invocation_id, AdmissionStatus.REPLACED
        )
        if self._current_client == slot.client_id:
            self._current_client = None
        slot.request = replacement
        slot.state = _SlotState.ENQUEUED
        slot.enqueued_at = self._clock()
        slot.call_count = 0
        assert slot.request_ids is not None
        slot.request_ids.clear()
        self._record(slot, "ENQUEUED")
        if self._clock() >= replacement.not_after_monotonic:
            self._terminal_slot_locked(slot, AdmissionStatus.EXPIRED, notify=True)
        else:
            self._schedule_deadline_locked(slot.client_id, replacement)
        self._offer_next_locked()

    def _reserve_successor_locked(
        self,
        connection: _Connection,
        active_invocation_id: str,
        successor: AdmissionRequest,
    ) -> None:
        if successor.invocation_id in self._seen_invocations:
            raise _ProtocolViolation("invocation ID reuse")
        if successor.priority is not GenerationPriority.RESERVATION:
            raise _ProtocolViolation("successor must be a reservation")
        self._require_invocation_owner(connection, active_invocation_id)
        self._seen_invocations.add(successor.invocation_id)
        self._invocation_owner[successor.invocation_id] = connection.client_id
        terminal_status: AdmissionStatus | None = None
        slot = self._slots.get(connection.client_id)
        if self._poisoned:
            terminal_status = AdmissionStatus.POISONED
        elif self._closing:
            terminal_status = AdmissionStatus.UNAVAILABLE
        elif self._clock() >= successor.not_after_monotonic:
            terminal_status = AdmissionStatus.EXPIRED
        elif (
            slot is None
            or slot.request.invocation_id != active_invocation_id
            or slot.request.priority is not GenerationPriority.REACTION
            or slot.state not in {_SlotState.CLAIMED, _SlotState.ACTIVE}
            or not slot.consumer_current
        ):
            terminal_status = AdmissionStatus.UNAVAILABLE
        elif slot.attached is not None:
            raise _ProtocolViolation("second attached successor")
        if terminal_status is not None:
            self._remember_terminal_locked(
                connection.client_id, successor, terminal_status
            )
            self._emit_ack(
                connection.client_id, successor.invocation_id, terminal_status
            )
            return
        assert slot is not None
        slot.attached = _AttachedSuccessor(successor, self._clock())
        self._record(
            slot,
            "SUCCESSOR_ATTACHED",
            invocation_id=successor.invocation_id,
            priority=successor.priority,
        )
        self._emit(
            slot.client_id,
            "SUCCESSOR_ATTACHED",
            active_invocation_id=active_invocation_id,
            successor_invocation_id=successor.invocation_id,
        )
        self._schedule_deadline_locked(slot.client_id, successor)

    def _cancel_successor_locked(
        self, connection: _Connection, successor_id: str
    ) -> None:
        terminal = self._terminal_status.get(successor_id)
        if terminal is not None:
            self._require_invocation_owner(connection, successor_id)
            self._emit_ack(connection.client_id, successor_id, terminal)
            return
        slot = self._slots.get(connection.client_id)
        if slot is None:
            raise _ProtocolViolation("unowned successor")
        if slot.attached is not None and slot.attached.request.invocation_id == successor_id:
            self._terminal_attached_locked(slot, AdmissionStatus.CANCELLED)
            return
        if slot.request.invocation_id != successor_id:
            raise _ProtocolViolation("unowned successor")
        if slot.state in {_SlotState.ENQUEUED, _SlotState.OFFERED}:
            self._terminal_slot_locked(slot, AdmissionStatus.CANCELLED, notify=True)
            self._offer_next_locked()
            return
        self._emit_ack(slot.client_id, successor_id, AdmissionStatus.GRANTED)

    def _cancel_locked(self, connection: _Connection, invocation_id: str) -> None:
        terminal = self._terminal_status.get(invocation_id)
        if terminal is not None:
            self._require_invocation_owner(connection, invocation_id)
            self._emit_ordinary_terminal_ack_once(
                connection.client_id, invocation_id, terminal
            )
            return
        slot = self._require_slot_owner(connection, invocation_id)
        if slot.state in {_SlotState.ENQUEUED, _SlotState.OFFERED}:
            self._terminal_slot_locked(slot, AdmissionStatus.CANCELLED, notify=True)
            self._offer_next_locked()
            return
        self._emit_ack(slot.client_id, invocation_id, AdmissionStatus.GRANTED)

    def _abandon_locked(self, connection: _Connection, invocation_id: str) -> None:
        terminal = self._terminal_status.get(invocation_id)
        if terminal is not None:
            self._require_invocation_owner(connection, invocation_id)
            self._emit_ordinary_terminal_ack_once(
                connection.client_id, invocation_id, terminal
            )
            return
        slot = self._require_slot_owner(connection, invocation_id)
        if slot.state in {_SlotState.ENQUEUED, _SlotState.OFFERED}:
            self._terminal_slot_locked(slot, AdmissionStatus.CANCELLED, notify=True)
            self._offer_next_locked()
            return
        if slot.state is _SlotState.CLAIMED:
            slot.consumer_current = False
            self._emit_ack(slot.client_id, invocation_id, AdmissionStatus.CANCELLED)
            self._finish_active_slot_locked(slot, "ABANDONED_DRAINED")
            return
        if slot.state is _SlotState.ACTIVE:
            self._begin_draining_locked(slot)
            self._emit_ack(slot.client_id, invocation_id, AdmissionStatus.CANCELLED)
            return
        if slot.state is _SlotState.DRAINING:
            self._emit_ack(slot.client_id, invocation_id, AdmissionStatus.CANCELLED)
            return
        raise _ProtocolViolation("invalid abandonment")

    def _release_locked(self, connection: _Connection, invocation_id: str) -> None:
        self._require_invocation_owner(connection, invocation_id)
        if self._poisoned:
            self._emit_ordinary_terminal_ack_once(
                connection.client_id, invocation_id, AdmissionStatus.POISONED
            )
            return
        terminal = self._terminal_status.get(invocation_id)
        if terminal is not None:
            self._emit_ordinary_terminal_ack_once(
                connection.client_id, invocation_id, terminal
            )
            return
        slot = self._require_slot_owner(connection, invocation_id)
        if slot.state is not _SlotState.CLAIMED or slot.provider_task is not None:
            raise _ProtocolViolation("release before provider quiescence")
        self._emit_ack(slot.client_id, invocation_id, "RELEASED")
        self._finish_active_slot_locked(slot, "RELEASED")

    def _generate_locked(
        self,
        connection: _Connection,
        invocation_id: str,
        ordinal: int,
        request: StructuredGenerationRequest,
    ) -> None:
        self._require_invocation_owner(connection, invocation_id)
        terminal = self._terminal_status.get(invocation_id)
        if terminal == AdmissionStatus.EXPIRED.value:
            return
        if self._poisoned:
            self._emit_backend_error(
                connection.client_id,
                invocation_id,
                ordinal,
                LLMBackendError(
                    LLMBackendErrorCode.ADMISSION_POISONED,
                    retryable=False,
                    provider_quiescence=ProviderQuiescence.NOT_STARTED,
                ),
            )
            return
        slot = self._require_slot_owner(connection, invocation_id)
        assert slot.request_ids is not None
        if (
            slot.state is not _SlotState.CLAIMED
            or self._current_client != slot.client_id
            or ordinal != slot.call_count + 1
            or not 1 <= ordinal <= self._config.max_calls_per_lease
            or request.request_id in slot.request_ids
        ):
            raise _ProtocolViolation("invalid generation ordinal or owner")
        slot.call_count = ordinal
        slot.request_ids.add(request.request_id)
        slot.request_bytes = _structured_request_size(request)
        if self._clock() >= slot.request.not_after_monotonic:
            self._record_call(
                slot,
                ordinal,
                LLMBackendErrorCode.ADMISSION_EXPIRED.value,
                None,
                False,
                ProviderQuiescence.NOT_STARTED,
                response=None,
                poison_transition=False,
            )
            self._terminal_slot_locked(slot, AdmissionStatus.EXPIRED, notify=True)
            self._offer_next_locked()
            return
        slot.state = _SlotState.ACTIVE
        slot.provider_started_at = self._clock()
        task = asyncio.create_task(
            self._run_backend_call(
                slot.client_id,
                invocation_id,
                ordinal,
                request,
                self._game_time.real_budget(self._backend_request_timeout_seconds),
            ),
            name=f"aiwolf-admission-provider-{invocation_id}-{ordinal}",
        )
        slot.provider_task = task

    async def _run_backend_call(
        self,
        client_id: str,
        invocation_id: str,
        ordinal: int,
        request: StructuredGenerationRequest,
        timeout_seconds: float,
    ) -> None:
        response: StructuredGenerationResponse | None = None
        error: LLMBackendError | None = None
        unclassified = False
        task = asyncio.current_task()
        assert task is not None
        async with self._lock:
            slot = self._slots.get(client_id)
            if (
                slot is None
                or slot.request.invocation_id != invocation_id
                or slot.call_count != ordinal
                or slot.provider_task is not task
            ):
                return
            if self._clock() >= slot.request.not_after_monotonic:
                self._record_call(
                    slot,
                    ordinal,
                    LLMBackendErrorCode.ADMISSION_EXPIRED.value,
                    None,
                    False,
                    ProviderQuiescence.NOT_STARTED,
                    response=None,
                    poison_transition=False,
                )
                self._terminal_slot_locked(
                    slot, AdmissionStatus.EXPIRED, notify=True
                )
                self._offer_next_locked()
                return
        try:
            async with asyncio.timeout(timeout_seconds):
                response = await self._backend.generate(request)
        except asyncio.CancelledError:
            unclassified = True
        except LLMBackendError as failure:
            error = failure
        except Exception:
            unclassified = True
        if response is None and error is None and not unclassified:
            unclassified = True
        await self._backend_terminal(
            client_id,
            invocation_id,
            ordinal,
            response=response,
            error=error,
            unclassified=unclassified,
        )

    async def _backend_terminal(
        self,
        client_id: str,
        invocation_id: str,
        ordinal: int,
        *,
        response: StructuredGenerationResponse | None,
        error: LLMBackendError | None,
        unclassified: bool,
    ) -> None:
        async with self._lock:
            slot = self._slots.get(client_id)
            if (
                slot is None
                or slot.request.invocation_id != invocation_id
                or slot.call_count != ordinal
            ):
                return
            slot.provider_task = None
            if response is not None and not isinstance(
                response, StructuredGenerationResponse
            ):
                response = None
                unclassified = True
            if error is not None and not self._source_error_is_valid(error):
                error = None
                unclassified = True
            cutoff_reached = self._clock() >= slot.request.not_after_monotonic
            if response is not None:
                quiescence = ProviderQuiescence.PROVEN_TERMINAL
                backend_code = None
                http_status = None
                retryable = None
                safe = True
            elif error is not None:
                quiescence = error.provider_quiescence
                backend_code = error.code.value
                http_status = error.http_status
                retryable = error.retryable
                safe = quiescence in {
                    ProviderQuiescence.PROVEN_TERMINAL,
                    ProviderQuiescence.NOT_STARTED,
                }
            else:
                quiescence = ProviderQuiescence.UNKNOWN
                backend_code = LLMBackendErrorCode.ADMISSION_POISONED.value
                http_status = None
                retryable = False
                safe = False
            self._record_call(
                slot,
                ordinal,
                backend_code,
                http_status,
                retryable,
                quiescence,
                response=response,
                poison_transition=not safe and not self._poisoned,
                backend_error_detail=(error.backend_error_detail if error is not None else None),
            )
            if cutoff_reached and slot.consumer_current:
                slot.consumer_current = False
                slot.state = _SlotState.DRAINING
                self._record(slot, "DRAINING", consumer_state="abandoned")
            if not safe:
                original_error = error
                consumer_current = slot.consumer_current
                self._poison_locked("PROVIDER_QUIESCENCE_UNKNOWN")
                if consumer_current:
                    self._emit_backend_error(
                        client_id,
                        invocation_id,
                        ordinal,
                        (
                            original_error
                            if original_error is not None
                            else LLMBackendError(
                                LLMBackendErrorCode.ADMISSION_POISONED,
                                retryable=False,
                                provider_quiescence=ProviderQuiescence.UNKNOWN,
                            )
                        ),
                    )
                return
            if slot.drain_watch is not None:
                slot.drain_watch.cancel()
                slot.drain_watch = None
            if slot.consumer_current:
                slot.state = _SlotState.CLAIMED
                if response is not None:
                    self._emit(
                        client_id,
                        "RESULT",
                        call_ordinal=ordinal,
                        invocation_id=invocation_id,
                        structured_response=_structured_response_to_wire(response),
                    )
                else:
                    assert error is not None
                    self._emit_backend_error(
                        client_id,
                        invocation_id,
                        ordinal,
                        error,
                    )
            else:
                self._finish_active_slot_locked(slot, "ABANDONED_DRAINED")

    def _begin_draining_locked(self, slot: _Slot) -> None:
        if not slot.consumer_current:
            return
        slot.consumer_current = False
        slot.state = _SlotState.DRAINING
        self._record(slot, "DRAINING", consumer_state="abandoned")
        self._ensure_drain_watch_locked(slot)

    def _ensure_drain_watch_locked(self, slot: _Slot) -> None:
        if slot.drain_watch is not None or slot.provider_task is None:
            return
        task = asyncio.create_task(
            self._drain_timeout(
                slot.client_id,
                slot.request.invocation_id,
                slot.provider_task,
            ),
            name=f"aiwolf-admission-drain-{slot.request.invocation_id}",
        )
        slot.drain_watch = task
        self._track_control_task(task)

    async def _drain_timeout(
        self,
        client_id: str,
        invocation_id: str,
        provider_task: asyncio.Task[None],
    ) -> None:
        try:
            await asyncio.sleep(self._config.provider_drain_grace_seconds)
            async with self._lock:
                slot = self._slots.get(client_id)
                if (
                    slot is None
                    or slot.request.invocation_id != invocation_id
                    or slot.state is not _SlotState.DRAINING
                    or provider_task.done()
                ):
                    return
                self._poison_locked("PROVIDER_QUIESCENCE_UNKNOWN")
                provider_task.cancel()
            try:
                async with asyncio.timeout(
                    self._config.cancellation_grace_seconds
                ):
                    await asyncio.shield(provider_task)
            except (TimeoutError, asyncio.CancelledError):
                self._cleanup_incomplete = not provider_task.done()
        except asyncio.CancelledError:
            return

    async def _deadline_timeout(
        self, client_id: str, invocation_id: str, cutoff: float
    ) -> None:
        try:
            remaining = cutoff - self._clock()
            if remaining > 0:
                await asyncio.sleep(remaining)
            async with self._lock:
                slot = self._slots.get(client_id)
                if slot is None:
                    return
                if (
                    slot.attached is not None
                    and slot.attached.request.invocation_id == invocation_id
                    and self._clock() >= cutoff
                ):
                    self._terminal_attached_locked(slot, AdmissionStatus.EXPIRED)
                    return
                if (
                    slot.request.invocation_id == invocation_id
                    and self._clock() >= cutoff
                ):
                    if slot.state in {_SlotState.ENQUEUED, _SlotState.OFFERED}:
                        self._terminal_slot_locked(
                            slot, AdmissionStatus.EXPIRED, notify=True
                        )
                        self._offer_next_locked()
                    elif slot.state is _SlotState.CLAIMED:
                        slot.consumer_current = False
                        self._terminal_slot_locked(
                            slot, AdmissionStatus.EXPIRED, notify=True
                        )
                        self._offer_next_locked()
                    elif slot.state is _SlotState.ACTIVE:
                        self._begin_draining_locked(slot)
        except asyncio.CancelledError:
            return

    async def _disconnect(self, connection: _Connection) -> None:
        async with self._lock:
            if self._connections.get(connection.client_id) is not connection:
                return
            self._connections.pop(connection.client_id, None)
            slot = self._slots.get(connection.client_id)
            if slot is None:
                return
            if slot.attached is not None:
                self._terminal_attached_locked(
                    slot, AdmissionStatus.UNAVAILABLE, notify=False
                )
            if slot.state in {_SlotState.ACTIVE, _SlotState.DRAINING}:
                self._begin_draining_locked(slot)
            else:
                self._terminal_slot_locked(
                    slot, AdmissionStatus.UNAVAILABLE, notify=False
                )
                self._offer_next_locked()

    def _offer_next_locked(self) -> None:
        if self._closing or self._poisoned or self._current_client is not None:
            return
        now = self._clock()
        for slot in tuple(self._slots.values()):
            if (
                slot.state is _SlotState.ENQUEUED
                and now >= slot.request.not_after_monotonic
            ):
                self._terminal_slot_locked(slot, AdmissionStatus.EXPIRED, notify=True)
        for priority in GenerationPriority:
            eligible = {
                slot.client_id
                for slot in self._slots.values()
                if slot.state is _SlotState.ENQUEUED
                and slot.request.priority is priority
            }
            if not eligible:
                continue
            start = self._cursor[priority]
            for offset in range(len(self._order)):
                client_id = self._order[(start + offset) % len(self._order)]
                if client_id not in eligible:
                    continue
                slot = self._slots[client_id]
                slot.state = _SlotState.OFFERED
                self._current_client = client_id
                wait = max(0, int((now - slot.enqueued_at) * 1_000_000))
                self._record(slot, "OFFERED", queue_wait_microseconds=wait)
                self._emit(
                    client_id,
                    "OFFER",
                    invocation_id=slot.request.invocation_id,
                    queue_wait_microseconds=wait,
                )
                return

    def _finish_active_slot_locked(self, slot: _Slot, terminal_status: str) -> None:
        old_request = slot.request
        self._remember_terminal_locked(slot.client_id, old_request, terminal_status)
        self._current_client = None
        attached = slot.attached
        slot.attached = None
        if attached is None:
            self._slots.pop(slot.client_id, None)
            self._offer_next_locked()
            return
        successor = attached.request
        if self._clock() >= successor.not_after_monotonic:
            self._remember_terminal_locked(
                slot.client_id, successor, AdmissionStatus.EXPIRED
            )
            self._emit_ack(
                slot.client_id, successor.invocation_id, AdmissionStatus.EXPIRED
            )
            self._slots.pop(slot.client_id, None)
        else:
            slot.request = successor
            slot.state = _SlotState.ENQUEUED
            slot.enqueued_at = attached.attached_at
            slot.call_count = 0
            assert slot.request_ids is not None
            slot.request_ids.clear()
            slot.consumer_current = True
            slot.provider_task = None
            slot.drain_watch = None
            self._record(slot, "ENQUEUED")
        self._offer_next_locked()

    def _terminal_slot_locked(
        self,
        slot: _Slot,
        status: AdmissionStatus,
        *,
        notify: bool,
    ) -> None:
        if self._current_client == slot.client_id:
            self._current_client = None
        self._remember_terminal_locked(slot.client_id, slot.request, status)
        if notify:
            self._emit_ordinary_terminal_ack_once(
                slot.client_id, slot.request.invocation_id, status
            )
        if slot.attached is not None:
            self._terminal_attached_locked(slot, status, notify=notify)
        self._slots.pop(slot.client_id, None)

    def _terminal_attached_locked(
        self,
        slot: _Slot,
        status: AdmissionStatus,
        *,
        notify: bool = True,
    ) -> None:
        attached = slot.attached
        if attached is None:
            return
        slot.attached = None
        self._remember_terminal_locked(slot.client_id, attached.request, status)
        if notify:
            self._emit_ack(slot.client_id, attached.request.invocation_id, status)

    def _poison_locked(self, reason: str) -> None:
        if self._poisoned:
            return
        self._poisoned = True
        self._poison_reason = reason
        self._metrics.record_nowait(
            AdmissionMetric(
                event="ADMISSION_POISONED",
                backend_code="ADMISSION_POISONED",
                retryable=False,
                provider_quiescence=ProviderQuiescence.UNKNOWN,
                poison_transition=True,
                monotonic_microseconds=self._now_microseconds(),
                terminal_status="PROVIDER_QUIESCENCE_UNKNOWN",
            ),
            critical=True,
        )
        for slot in tuple(self._slots.values()):
            self._terminal_slot_locked(slot, AdmissionStatus.POISONED, notify=True)
        self._current_client = None

    def _remember_terminal_locked(
        self,
        client_id: str,
        request: AdmissionRequest,
        status: AdmissionStatus | str,
    ) -> None:
        value = status.value if isinstance(status, AdmissionStatus) else status
        if request.invocation_id in self._terminal_status:
            return
        self._terminal_status[request.invocation_id] = value
        deadline_task = self._deadline_tasks.pop(request.invocation_id, None)
        if deadline_task is not None and deadline_task is not asyncio.current_task():
            deadline_task.cancel()
        self._metrics.record_nowait(
            AdmissionMetric(
                event="TERMINAL",
                invocation_id=request.invocation_id,
                client_id=client_id,
                priority=request.priority,
                monotonic_microseconds=self._now_microseconds(),
                terminal_status=value,
            )
        )

    def _record(
        self,
        slot: _Slot,
        event: str,
        *,
        invocation_id: str | None = None,
        priority: GenerationPriority | None = None,
        queue_wait_microseconds: int | None = None,
        consumer_state: str | None = None,
    ) -> None:
        self._metrics.record_nowait(
            AdmissionMetric(
                event=event,
                invocation_id=invocation_id or slot.request.invocation_id,
                client_id=slot.client_id,
                priority=(priority if priority is not None else slot.request.priority),
                queue_wait_microseconds=queue_wait_microseconds,
                consumer_state=consumer_state,
                monotonic_microseconds=self._now_microseconds(),
            )
        )

    def _record_call(
        self,
        slot: _Slot,
        ordinal: int,
        backend_code: str | None,
        http_status: int | None,
        retryable: bool | None,
        quiescence: ProviderQuiescence,
        *,
        response: StructuredGenerationResponse | None,
        poison_transition: bool,
        backend_error_detail: str | None = None,
    ) -> None:
        started_at = slot.provider_started_at
        generation_latency = (
            0
            if started_at is None
            else max(0, int((self._clock() - started_at) * 1_000_000))
        )
        timing = response.provider_timing if response is not None else None
        diagnostic = (
            response.provider_timing_diagnostic if response is not None else None
        )
        self._metrics.record_nowait(
            AdmissionMetric(
                event="PROVIDER_CALL_TERMINAL",
                invocation_id=slot.request.invocation_id,
                client_id=slot.client_id,
                priority=slot.request.priority,
                call_ordinal=ordinal,
                backend_code=backend_code,
                backend_error_detail=backend_error_detail,
                http_status=http_status,
                retryable=retryable,
                provider_quiescence=quiescence,
                consumer_state=("current" if slot.consumer_current else "abandoned"),
                generation_latency_microseconds=generation_latency,
                request_bytes=slot.request_bytes,
                response_bytes=(
                    _structured_response_size(response)
                    if response is not None
                    else 0
                ),
                prompt_tokens=(
                    response.usage.prompt_tokens if response is not None else None
                ),
                completion_tokens=(
                    response.usage.completion_tokens if response is not None else None
                ),
                provider_prompt_microseconds=(
                    timing.prompt_microseconds if timing is not None else None
                ),
                provider_completion_microseconds=(
                    timing.completion_microseconds if timing is not None else None
                ),
                provider_timing_diagnostic_status=(
                    diagnostic.status if diagnostic is not None else None
                ),
                provider_timing_prompt_n_state=(
                    diagnostic.prompt_n if diagnostic is not None else None
                ),
                provider_timing_prompt_ms_state=(
                    diagnostic.prompt_ms if diagnostic is not None else None
                ),
                provider_timing_predicted_n_state=(
                    diagnostic.predicted_n if diagnostic is not None else None
                ),
                provider_timing_predicted_ms_state=(
                    diagnostic.predicted_ms if diagnostic is not None else None
                ),
                provider_timing_extra_key_count=(
                    diagnostic.extra_key_count if diagnostic is not None else None
                ),
                poison_transition=poison_transition,
                monotonic_microseconds=self._now_microseconds(),
            )
        )

    def _require_slot_owner(
        self, connection: _Connection, invocation_id: str
    ) -> _Slot:
        slot = self._slots.get(connection.client_id)
        if slot is None or slot.request.invocation_id != invocation_id:
            raise _ProtocolViolation("unowned invocation")
        return slot

    def _require_invocation_owner(
        self, connection: _Connection, invocation_id: str
    ) -> None:
        if self._invocation_owner.get(invocation_id) != connection.client_id:
            raise _ProtocolViolation("unowned invocation")

    def _emit_ack(
        self,
        client_id: str,
        invocation_id: str,
        status: AdmissionStatus | str,
    ) -> None:
        self._emit(
            client_id,
            "ACK",
            invocation_id=invocation_id,
            status=(status.value if isinstance(status, AdmissionStatus) else status),
        )

    def _emit_ordinary_terminal_ack_once(
        self,
        client_id: str,
        invocation_id: str,
        status: AdmissionStatus | str,
    ) -> None:
        if invocation_id in self._ordinary_terminal_notified:
            return
        self._ordinary_terminal_notified.add(invocation_id)
        self._emit_ack(client_id, invocation_id, status)

    @staticmethod
    def _source_error_is_valid(error: LLMBackendError) -> bool:
        return (
            isinstance(error, LLMBackendError)
            and isinstance(error.code, LLMBackendErrorCode)
            and type(error.retryable) is bool
            and isinstance(error.provider_quiescence, ProviderQuiescence)
            and (
                (
                    error.code is LLMBackendErrorCode.HTTP_STATUS
                    and type(error.http_status) is int
                    and 100 <= error.http_status <= 599
                )
                or (
                    error.code is not LLMBackendErrorCode.HTTP_STATUS
                    and error.http_status is None
                )
            )
            and (
                error.backend_error_detail is None
                or (
                    error.code is LLMBackendErrorCode.HTTP_STATUS
                    and type(error.backend_error_detail) is str
                    and 1 <= len(error.backend_error_detail) <= 256
                    and all(character.isprintable() for character in error.backend_error_detail)
                )
            )
        )

    def _emit_backend_error(
        self,
        client_id: str,
        invocation_id: str,
        ordinal: int,
        error: LLMBackendError,
    ) -> bool:
        if not self._source_error_is_valid(error):
            self._poison_locked("ADMISSION_POISONED")
            return False
        fields: dict[str, object] = {
            "invocation_id": invocation_id,
            "call_ordinal": ordinal,
            "code": error.code.value,
            "retryable": error.retryable,
            "provider_quiescence": error.provider_quiescence.value,
        }
        if error.code is LLMBackendErrorCode.HTTP_STATUS:
            fields["http_status"] = error.http_status
        return self._emit(
            client_id,
            "ERROR",
            poison_on_encode=True,
            **fields,
        )

    def _emit(
        self,
        client_id: str,
        message_type: str,
        *,
        poison_on_encode: bool = False,
        **fields: object,
    ) -> bool:
        connection = self._connections.get(client_id)
        if connection is None:
            return False
        try:
            delivered = connection.emit(message_type, **fields)
        except _ProtocolViolation:
            if poison_on_encode:
                self._poison_locked("ADMISSION_POISONED")
            delivered = False
        if delivered:
            return True
        task = asyncio.create_task(self._disconnect(connection))
        self._track_control_task(task)
        return False

    def _schedule_deadline_locked(
        self, client_id: str, request: AdmissionRequest
    ) -> None:
        task = asyncio.create_task(
            self._deadline_timeout(
                client_id, request.invocation_id, request.not_after_monotonic
            ),
            name=f"aiwolf-admission-deadline-{request.invocation_id}",
        )
        self._deadline_tasks[request.invocation_id] = task
        task.add_done_callback(
            lambda completed, invocation_id=request.invocation_id: (
                self._deadline_tasks.pop(invocation_id, None)
                if self._deadline_tasks.get(invocation_id) is completed
                else None
            )
        )
        self._track_control_task(task)

    def _track_control_task(self, task: asyncio.Task[object]) -> None:
        self._control_tasks.add(task)
        task.add_done_callback(self._control_tasks.discard)

    def _fairness_order(self) -> tuple[str, ...]:
        def digest(client_id: str) -> bytes:
            framed = (
                b"aiwolf.phase5.fairness.v1\0"
                + self._fairness_seed.encode("utf-8")
                + b"\0"
                + client_id.encode("utf-8")
            )
            return hashlib.sha256(framed).digest()

        return tuple(
            sorted(
                self._registry,
                key=lambda client_id: (digest(client_id), client_id),
            )
        )

    def _initial_cursor(self, priority: GenerationPriority) -> int:
        framed = (
            b"aiwolf.phase5.fairness.v1.start\0"
            + self._fairness_seed.encode("utf-8")
            + b"\0"
            + str(int(priority)).encode("ascii")
        )
        return int.from_bytes(hashlib.sha256(framed).digest()[:8], "big") % len(
            self._order
        )

    def _now_microseconds(self) -> int:
        started_at = self._started_at
        if started_at is None:
            return 0
        return max(0, int((self._clock() - started_at) * 1_000_000))
