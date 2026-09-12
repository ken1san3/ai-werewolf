"""Authenticated client session and structured-backend proxy for admission IPC."""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
import json
import struct
from types import TracebackType
from typing import Mapping

from .admission_types import (
    GENERATION_IPC_PROTOCOL,
    AdmissionCredentials,
    AdmissionRequest,
    AdmissionResult,
    AdmissionStatus,
    GenerationBrokerConfig,
    request_to_wire,
)
from .types import (
    BackendIdentity,
    LLMBackendError,
    LLMBackendErrorCode,
    LLMUsage,
    ProviderQuiescence,
    ProviderTiming,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
)


class _ProtocolViolation(Exception):
    pass


def _reject_constant(_value: str) -> object:
    raise _ProtocolViolation("invalid JSON number")


def _object_no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, child in pairs:
        if key in value:
            raise _ProtocolViolation("duplicate JSON key")
        value[key] = child
    return value


def _exact(value: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise _ProtocolViolation("invalid frame shape")
    if value.get("protocol") != GENERATION_IPC_PROTOCOL:
        raise _ProtocolViolation("protocol mismatch")
    return value


async def _read_frame(
    reader: asyncio.StreamReader, max_frame_bytes: int
) -> dict[str, object]:
    header = await reader.readexactly(4)
    length = struct.unpack("!I", header)[0]
    if length == 0 or length > max_frame_bytes:
        raise _ProtocolViolation("invalid frame length")
    encoded = await reader.readexactly(length)
    try:
        text = encoded.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_object_no_duplicates,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise _ProtocolViolation("invalid JSON frame") from None
    if not isinstance(value, dict):
        raise _ProtocolViolation("frame must be an object")
    return value


def _encode_frame(max_frame_bytes: int, message_type: str, **fields: object) -> bytes:
    value = {
        "protocol": GENERATION_IPC_PROTOCOL,
        "type": message_type,
        **fields,
    }
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise _ProtocolViolation("frame is not finite JSON") from None
    if len(encoded) == 0 or len(encoded) > max_frame_bytes:
        raise _ProtocolViolation("frame exceeds configured bound")
    return struct.pack("!I", len(encoded)) + encoded


def _identity_from_wire(value: object) -> BackendIdentity:
    if not isinstance(value, dict) or set(value) != {
        "backend_type",
        "config_fingerprint",
        "endpoint_origin",
        "endpoint_path",
        "model",
    }:
        raise _ProtocolViolation("invalid backend identity")
    try:
        return BackendIdentity(
            backend_type=value["backend_type"],  # type: ignore[arg-type]
            endpoint_origin=value["endpoint_origin"],  # type: ignore[arg-type]
            endpoint_path=value["endpoint_path"],  # type: ignore[arg-type]
            model=value["model"],  # type: ignore[arg-type]
            config_fingerprint=value["config_fingerprint"],  # type: ignore[arg-type]
        )
    except (TypeError, ValueError):
        raise _ProtocolViolation("invalid backend identity") from None


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(child) for child in value]
    return value


def _structured_request_to_wire(request: StructuredGenerationRequest) -> object:
    return {
        "messages": [
            {"content": message.content, "role": message.role}
            for message in request.messages
        ],
        "output_schema": _plain_json(request.output_schema),
        "request_id": request.request_id,
    }


def _optional_count(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise _ProtocolViolation("invalid usage count")
    return value


def _response_from_wire(value: object) -> StructuredGenerationResponse:
    if not isinstance(value, dict) or set(value) != {
        "finish_reason",
        "provider_model",
        "provider_timing",
        "request_id",
        "text",
        "usage",
    }:
        raise _ProtocolViolation("invalid structured response")
    usage = value["usage"]
    if not isinstance(usage, dict) or set(usage) != {
        "completion_tokens",
        "prompt_tokens",
    }:
        raise _ProtocolViolation("invalid structured response usage")
    timing_value = value["provider_timing"]
    if timing_value is None:
        timing = None
    elif isinstance(timing_value, dict) and set(timing_value) == {
        "completion_microseconds",
        "completion_tokens",
        "prompt_microseconds",
        "prompt_tokens",
    }:
        try:
            timing = ProviderTiming(
                prompt_tokens=timing_value["prompt_tokens"],  # type: ignore[arg-type]
                prompt_microseconds=timing_value["prompt_microseconds"],  # type: ignore[arg-type]
                completion_tokens=timing_value["completion_tokens"],  # type: ignore[arg-type]
                completion_microseconds=timing_value["completion_microseconds"],  # type: ignore[arg-type]
            )
        except (TypeError, ValueError):
            raise _ProtocolViolation("invalid structured response timing") from None
    else:
        raise _ProtocolViolation("invalid structured response timing")
    try:
        return StructuredGenerationResponse(
            request_id=value["request_id"],  # type: ignore[arg-type]
            text=value["text"],  # type: ignore[arg-type]
            provider_model=value["provider_model"],  # type: ignore[arg-type]
            finish_reason=value["finish_reason"],  # type: ignore[arg-type]
            usage=LLMUsage(
                prompt_tokens=_optional_count(usage["prompt_tokens"]),
                completion_tokens=_optional_count(usage["completion_tokens"]),
            ),
            provider_timing=timing,
        )
    except (TypeError, ValueError):
        raise _ProtocolViolation("invalid structured response") from None


def _admission_backend_error(code: LLMBackendErrorCode) -> LLMBackendError:
    return LLMBackendError(code, provider_quiescence=ProviderQuiescence.NOT_STARTED)


def _consume_future_error(future: asyncio.Future[object]) -> None:
    if not future.cancelled():
        future.exception()


@dataclass
class _ControlLane:
    invocation_id: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    disposition: str | None = None
    callers: int = 0
    lease: _ClientGenerationLease | None = None
    abandon_disposition: str | None = None


class BrokerAdmissionSession:
    """One authenticated, single-owner admission connection."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        credentials: AdmissionCredentials,
        identity: BackendIdentity,
        config: GenerationBrokerConfig,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._credentials = credentials
        self._identity = identity
        self._config = config
        self._write_lock = asyncio.Lock()
        self._results: dict[str, asyncio.Future[AdmissionResult]] = {}
        self._acks: dict[str, asyncio.Future[str]] = {}
        self._control_lanes: dict[str, _ControlLane] = {}
        self._terminal_controls: dict[str, AdmissionStatus] = {}
        self._attachments: dict[str, asyncio.Future[str]] = {}
        self._generations: dict[
            tuple[str, int], asyncio.Future[StructuredGenerationResponse]
        ] = {}
        self._claimed_invocation: str | None = None
        self._activated_invocation: str | None = None
        self._call_ordinals: dict[str, int] = {}
        self._request_ids: dict[str, set[str]] = {}
        self._closed = False
        self._transport_closed = False
        self._reader_task = asyncio.create_task(
            self._reader_loop(), name=f"aiwolf-admission-client-{credentials.client_id}"
        )

    @classmethod
    async def connect(
        cls,
        host: str,
        port: int,
        credentials: AdmissionCredentials,
        *,
        config: GenerationBrokerConfig = GenerationBrokerConfig(),
    ) -> BrokerAdmissionSession:
        if host not in {"127.0.0.1", "::1"}:
            raise ValueError("admission host must be numeric loopback")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("port must be an int in [1, 65535]")
        if not isinstance(credentials, AdmissionCredentials):
            raise TypeError("credentials must be AdmissionCredentials")
        if not isinstance(config, GenerationBrokerConfig):
            raise TypeError("config must be GenerationBrokerConfig")
        try:
            async with asyncio.timeout(config.authentication_timeout_seconds):
                reader, writer = await asyncio.open_connection(host, port)
                writer.write(
                    _encode_frame(
                        config.max_frame_bytes,
                        "HELLO",
                        client_id=credentials.client_id,
                        token=credentials.token,
                    )
                )
                await writer.drain()
                ready = _exact(
                    await _read_frame(reader, config.max_frame_bytes),
                    {
                        "backend_identity",
                        "config_fingerprint",
                        "protocol",
                        "type",
                    },
                )
                if ready["type"] != "READY":
                    raise _ProtocolViolation("authentication rejected")
                fingerprint = ready["config_fingerprint"]
                if (
                    not isinstance(fingerprint, str)
                    or fingerprint != config.config_fingerprint
                ):
                    raise _ProtocolViolation("configuration mismatch")
                identity = _identity_from_wire(ready["backend_identity"])
        except (OSError, TimeoutError, asyncio.IncompleteReadError, _ProtocolViolation):
            if "writer" in locals():
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
            raise _admission_backend_error(
                LLMBackendErrorCode.ADMISSION_UNAVAILABLE
            ) from None
        return cls(
            reader,
            writer,
            credentials=credentials,
            identity=identity,
            config=config,
        )

    @property
    def identity(self) -> BackendIdentity:
        return self._identity

    async def acquire(self, request: AdmissionRequest) -> AdmissionResult:
        self._require_open()
        if not isinstance(request, AdmissionRequest):
            raise TypeError("request must be AdmissionRequest")
        lane = self._register_control_lane(request.invocation_id)
        future = self._register_result(request.invocation_id)
        try:
            await self._send("ENQUEUE", request=request_to_wire(request))
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            await self._ordinary_control(lane, "CANCEL")
            raise
        finally:
            if future.done():
                self._results.pop(request.invocation_id, None)
                self._cleanup_control_lane(lane)

    async def replace_waiting(
        self, old_invocation_id: str, replacement: AdmissionRequest
    ) -> AdmissionResult:
        self._require_open()
        if not isinstance(old_invocation_id, str) or not old_invocation_id:
            raise ValueError("old_invocation_id must be non-empty")
        if not isinstance(replacement, AdmissionRequest):
            raise TypeError("replacement must be AdmissionRequest")
        lane = self._register_control_lane(replacement.invocation_id)
        future = self._register_result(replacement.invocation_id)
        try:
            await self._send(
                "REPLACE",
                old_invocation_id=old_invocation_id,
                replacement=request_to_wire(replacement),
            )
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            await self._ordinary_control(lane, "CANCEL")
            raise
        finally:
            if future.done():
                self._results.pop(replacement.invocation_id, None)
                self._cleanup_control_lane(lane)

    async def reserve_successor(
        self, active_invocation_id: str, successor: AdmissionRequest
    ) -> _ClientSuccessorReservation:
        self._require_open()
        if not isinstance(active_invocation_id, str) or not active_invocation_id:
            raise ValueError("active_invocation_id must be non-empty")
        if not isinstance(successor, AdmissionRequest):
            raise TypeError("successor must be AdmissionRequest")
        result = self._register_result(successor.invocation_id)
        result.add_done_callback(_consume_future_error)
        result.add_done_callback(
            lambda completed, invocation_id=successor.invocation_id: self._forget_result(
                invocation_id, completed
            )
        )
        loop = asyncio.get_running_loop()
        attached: asyncio.Future[str] = loop.create_future()
        self._attachments[successor.invocation_id] = attached
        try:
            await self._send(
                "RESERVE_SUCCESSOR",
                active_invocation_id=active_invocation_id,
                successor=request_to_wire(successor),
            )
            status = await asyncio.shield(attached)
        except asyncio.CancelledError:
            await self._cancel_successor_bounded(successor.invocation_id)
            raise
        finally:
            self._attachments.pop(successor.invocation_id, None)
        if status != "SUCCESSOR_ATTACHED" and not result.done():
            try:
                terminal = AdmissionStatus(status)
            except ValueError:
                raise _admission_backend_error(
                    LLMBackendErrorCode.ADMISSION_PROTOCOL
                ) from None
            result.set_result(AdmissionResult(terminal, None, 0))
        return _ClientSuccessorReservation(self, successor.invocation_id, result)

    async def cancel_successor(
        self, successor_invocation_id: str
    ) -> AdmissionStatus:
        self._require_open()
        status = await self._control(
            "CANCEL_SUCCESSOR", successor_invocation_id, successor_invocation_id=successor_invocation_id
        )
        try:
            return AdmissionStatus(status)
        except ValueError:
            raise _admission_backend_error(LLMBackendErrorCode.ADMISSION_PROTOCOL) from None

    async def cancel(self, invocation_id: str) -> AdmissionStatus:
        self._require_open()
        if not isinstance(invocation_id, str) or not invocation_id or len(invocation_id) > 128:
            raise ValueError("invocation_id must be a non-empty string of at most 128 characters")
        lane = self._control_lanes.get(invocation_id)
        if lane is None:
            terminal = self._terminal_controls.get(invocation_id)
            if terminal is not None:
                return terminal
            raise _admission_backend_error(LLMBackendErrorCode.ADMISSION_PROTOCOL)
        status = await self._ordinary_control(lane, "CANCEL")
        assert isinstance(status, AdmissionStatus)
        return status

    async def aclose(self) -> None:
        if self._transport_closed:
            return
        if not self._closed and self._claimed_invocation is not None:
            try:
                lane = self._control_lanes.get(self._claimed_invocation)
                if lane is not None:
                    await self._ordinary_control(lane, "ABANDON")
            except (LLMBackendError, TimeoutError):
                pass
        self._closed = True
        self._transport_closed = True
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except OSError:
            pass
        if not self._reader_task.done():
            self._reader_task.cancel()
        await asyncio.gather(self._reader_task, return_exceptions=True)
        self._resolve_unavailable()
        self._control_lanes.clear()
        self._terminal_controls.clear()

    async def _claim(
        self, invocation_id: str, lane: _ControlLane
    ) -> AdmissionStatus:
        status = await self._ordinary_control(lane, "CLAIM")
        assert isinstance(status, AdmissionStatus)
        return status

    async def _release(self, lane: _ControlLane) -> None:
        await self._ordinary_control(lane, "RELEASE")

    def _clear_claim(self, invocation_id: str) -> None:
        if self._claimed_invocation == invocation_id:
            self._claimed_invocation = None
        if self._activated_invocation == invocation_id:
            self._activated_invocation = None
        self._call_ordinals.pop(invocation_id, None)
        self._request_ids.pop(invocation_id, None)

    async def _generate(
        self, invocation_id: str, request: StructuredGenerationRequest
    ) -> StructuredGenerationResponse:
        self._require_open()
        if self._activated_invocation != invocation_id:
            raise _admission_backend_error(LLMBackendErrorCode.ADMISSION_PROTOCOL)
        ordinal = self._call_ordinals.get(invocation_id, 0) + 1
        request_ids = self._request_ids.get(invocation_id)
        generation_in_flight = any(
            not future.done() for future in self._generations.values()
        )
        if (
            generation_in_flight
            or
            ordinal > self._config.max_calls_per_lease
            or request_ids is None
            or request.request_id in request_ids
        ):
            raise _admission_backend_error(LLMBackendErrorCode.ADMISSION_PROTOCOL)
        request_ids.add(request.request_id)
        self._call_ordinals[invocation_id] = ordinal
        loop = asyncio.get_running_loop()
        future: asyncio.Future[StructuredGenerationResponse] = loop.create_future()
        future.add_done_callback(_consume_future_error)
        key = (invocation_id, ordinal)
        self._generations[key] = future
        try:
            await self._send(
                "GENERATE",
                call_ordinal=ordinal,
                invocation_id=invocation_id,
                structured_request=_structured_request_to_wire(request),
            )
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            await self._abandon_bounded(invocation_id)
            raise
        finally:
            self._generations.pop(key, None)

    async def _reader_loop(self) -> None:
        try:
            while True:
                frame = await _read_frame(self._reader, self._config.max_frame_bytes)
                self._route_frame(frame)
        except asyncio.CancelledError:
            raise
        except _ProtocolViolation:
            self._resolve_generation_protocol_error()
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            if not self._closed:
                self._closed = True
                self._resolve_unavailable()
            if not self._transport_closed:
                self._transport_closed = True
                self._writer.close()
                try:
                    await self._writer.wait_closed()
                except OSError:
                    pass

    def _route_frame(self, raw: dict[str, object]) -> None:
        message_type = raw.get("type")
        if message_type == "OFFER":
            frame = _exact(
                raw,
                {"invocation_id", "protocol", "queue_wait_microseconds", "type"},
            )
            invocation_id = frame["invocation_id"]
            wait = frame["queue_wait_microseconds"]
            if (
                not isinstance(invocation_id, str)
                or type(wait) is not int
                or wait < 0
            ):
                raise _ProtocolViolation("invalid offer")
            future = self._results.get(invocation_id)
            if future is None or future.done():
                raise _ProtocolViolation("unowned offer")
            lane = self._control_lanes.get(invocation_id)
            if lane is None:
                lane = self._register_control_lane(invocation_id)
            if lane.lease is not None or lane.disposition is not None:
                raise _ProtocolViolation("duplicate or terminal offer")
            lease = _ClientGenerationLease(self, invocation_id, lane)
            lane.lease = lease
            self._terminal_controls.clear()
            future.set_result(AdmissionResult(AdmissionStatus.OFFERED, lease, wait))
            return
        if message_type == "ACK":
            frame = _exact(raw, {"invocation_id", "protocol", "status", "type"})
            invocation_id = frame["invocation_id"]
            status = frame["status"]
            if not isinstance(invocation_id, str) or not isinstance(status, str):
                raise _ProtocolViolation("invalid acknowledgement")
            acknowledgement = self._acks.get(invocation_id)
            acknowledgement_owned = (
                acknowledgement is not None and not acknowledgement.done()
            )
            if acknowledgement_owned:
                acknowledgement.set_result(status)
            result = self._results.get(invocation_id)
            result_owned = result is not None and not result.done()
            if result_owned:
                try:
                    terminal = AdmissionStatus(status)
                except ValueError:
                    terminal = None
                if terminal not in {None, AdmissionStatus.GRANTED, AdmissionStatus.OFFERED}:
                    result.set_result(AdmissionResult(terminal, None, 0))
            attached = self._attachments.get(invocation_id)
            attached_owned = attached is not None and not attached.done()
            if attached_owned:
                attached.set_result(status)
            lane = self._control_lanes.get(invocation_id)
            lane_owned = lane is not None and (
                acknowledgement_owned
                or result_owned
                or (
                    lane.disposition in {None, AdmissionStatus.GRANTED.value}
                    and status
                    in {
                        AdmissionStatus.CANCELLED.value,
                        AdmissionStatus.EXPIRED.value,
                        AdmissionStatus.POISONED.value,
                        AdmissionStatus.REPLACED.value,
                        AdmissionStatus.UNAVAILABLE.value,
                    }
                )
            )
            tombstone_owned = (
                self._terminal_controls.get(invocation_id)
                is AdmissionStatus.CANCELLED
                and status == AdmissionStatus.POISONED.value
            )
            if not (
                acknowledgement_owned
                or result_owned
                or attached_owned
                or lane_owned
                or tombstone_owned
            ):
                raise _ProtocolViolation("unowned acknowledgement")
            if lane is not None:
                lane.disposition = status
            if tombstone_owned:
                self._terminal_controls[invocation_id] = AdmissionStatus.POISONED
            return
        if message_type == "SUCCESSOR_ATTACHED":
            frame = _exact(
                raw,
                {
                    "active_invocation_id",
                    "protocol",
                    "successor_invocation_id",
                    "type",
                },
            )
            successor_id = frame["successor_invocation_id"]
            if not isinstance(successor_id, str):
                raise _ProtocolViolation("invalid successor acknowledgement")
            attached = self._attachments.get(successor_id)
            if attached is None or attached.done():
                raise _ProtocolViolation("unowned successor acknowledgement")
            attached.set_result("SUCCESSOR_ATTACHED")
            return
        if message_type == "RESULT":
            frame = _exact(
                raw,
                {
                    "call_ordinal",
                    "invocation_id",
                    "protocol",
                    "structured_response",
                    "type",
                },
            )
            key = self._generation_key(frame)
            future = self._generations.get(key)
            if future is None or future.done():
                raise _ProtocolViolation("unowned result")
            future.set_result(_response_from_wire(frame["structured_response"]))
            return
        if message_type == "ERROR":
            base_keys = {
                "call_ordinal",
                "code",
                "invocation_id",
                "protocol",
                "provider_quiescence",
                "retryable",
                "type",
            }
            expected_keys = (
                base_keys | {"http_status"}
                if raw.get("code") == LLMBackendErrorCode.HTTP_STATUS.value
                else base_keys
            )
            frame = _exact(
                raw,
                expected_keys,
            )
            key = self._generation_key(frame)
            future = self._generations.get(key)
            if future is None or future.done():
                raise _ProtocolViolation("unowned error")
            try:
                code = LLMBackendErrorCode(frame["code"])
                quiescence = ProviderQuiescence(frame["provider_quiescence"])
            except (TypeError, ValueError):
                raise _ProtocolViolation("invalid error evidence") from None
            retryable = frame["retryable"]
            if type(retryable) is not bool:
                raise _ProtocolViolation("invalid error retryability")
            http_status = frame.get("http_status")
            if code is LLMBackendErrorCode.HTTP_STATUS:
                if type(http_status) is not int or not 100 <= http_status <= 599:
                    raise _ProtocolViolation("invalid HTTP status evidence")
            elif "http_status" in frame:
                raise _ProtocolViolation("unexpected HTTP status evidence")
            error = LLMBackendError(
                code,
                http_status=http_status,  # type: ignore[arg-type]
                retryable=retryable,
                provider_quiescence=quiescence,
            )
            future.set_exception(error)
            return
        raise _ProtocolViolation("unknown message type")

    @staticmethod
    def _generation_key(frame: Mapping[str, object]) -> tuple[str, int]:
        invocation_id = frame["invocation_id"]
        ordinal = frame["call_ordinal"]
        if not isinstance(invocation_id, str) or type(ordinal) is not int:
            raise _ProtocolViolation("invalid generation owner")
        return invocation_id, ordinal

    def _register_result(self, invocation_id: str) -> asyncio.Future[AdmissionResult]:
        if invocation_id in self._results:
            raise _admission_backend_error(LLMBackendErrorCode.ADMISSION_PROTOCOL)
        future = asyncio.get_running_loop().create_future()
        self._results[invocation_id] = future
        return future

    def _register_control_lane(self, invocation_id: str) -> _ControlLane:
        if invocation_id in self._control_lanes or invocation_id in self._terminal_controls:
            raise _admission_backend_error(LLMBackendErrorCode.ADMISSION_PROTOCOL)
        # Retain only a prior acknowledged abandonment while its provider may
        # still be draining.  A later OFFER proves that drain quiesced and
        # clears this single tombstone in _route_frame().
        self._terminal_controls = {
            key: status
            for key, status in self._terminal_controls.items()
            if status is AdmissionStatus.CANCELLED
        }
        lane = _ControlLane(invocation_id)
        self._control_lanes[invocation_id] = lane
        return lane

    def _cleanup_control_lane(self, lane: _ControlLane) -> None:
        if lane.callers != 0 or lane.disposition in {None, AdmissionStatus.GRANTED.value}:
            return
        if self._control_lanes.get(lane.invocation_id) is lane:
            self._control_lanes.pop(lane.invocation_id, None)
        try:
            terminal = AdmissionStatus(lane.disposition)
        except ValueError:
            return
        self._terminal_controls = {
            key: status
            for key, status in self._terminal_controls.items()
            if status is AdmissionStatus.CANCELLED
        }
        self._terminal_controls[lane.invocation_id] = terminal

    def _forget_result(
        self, invocation_id: str, future: asyncio.Future[AdmissionResult]
    ) -> None:
        if self._results.get(invocation_id) is future:
            self._results.pop(invocation_id, None)

    async def _control(self, operation: str, key: str, **fields: object) -> str:
        if key in self._acks and not self._acks[key].done():
            raise _admission_backend_error(LLMBackendErrorCode.ADMISSION_PROTOCOL)
        acknowledgement: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._acks[key] = acknowledgement
        try:
            await self._send(operation, **fields)
            async with asyncio.timeout(self._config.cancellation_grace_seconds):
                return await asyncio.shield(acknowledgement)
        except TimeoutError:
            await self._close_failed_transport(protocol_error=False)
            raise _admission_backend_error(
                LLMBackendErrorCode.ADMISSION_UNAVAILABLE
            ) from None
        finally:
            self._acks.pop(key, None)

    async def _ordinary_control(
        self, lane: _ControlLane, operation: str
    ) -> AdmissionStatus | str:
        lane.callers += 1
        try:
            async with lane.lock:
                cached = self._cached_control_result(lane, operation)
                if cached is not None:
                    if cached == "RETIRED":
                        return cached
                    return await self._apply_control_result(
                        lane,
                        operation,
                        (
                            cached.value
                            if isinstance(cached, AdmissionStatus)
                            else cached
                        ),
                    )
                control_task = asyncio.create_task(
                    self._control(
                        operation,
                        lane.invocation_id,
                        invocation_id=lane.invocation_id,
                    ),
                    name=(
                        f"aiwolf-admission-{operation.lower()}-"
                        f"{lane.invocation_id}"
                    ),
                )
                try:
                    status_text = await asyncio.shield(control_task)
                except asyncio.CancelledError:
                    try:
                        status_text = await self._finish_cancelled_control(
                            control_task
                        )
                        result = await self._apply_control_result(
                            lane, operation, status_text
                        )
                        if operation == "CLAIM" and result is AdmissionStatus.GRANTED:
                            abandon_task = asyncio.create_task(
                                self._control(
                                    "ABANDON",
                                    lane.invocation_id,
                                    invocation_id=lane.invocation_id,
                                ),
                                name=(
                                    "aiwolf-admission-abandon-cancelled-claim-"
                                    f"{lane.invocation_id}"
                                ),
                            )
                            abandon_text = await self._finish_cancelled_control(
                                abandon_task
                            )
                            await self._apply_control_result(
                                lane, "ABANDON", abandon_text
                            )
                    except (LLMBackendError, asyncio.CancelledError):
                        pass
                    raise
                return await self._apply_control_result(
                    lane, operation, status_text
                )
        finally:
            lane.callers -= 1
            self._cleanup_control_lane(lane)

    @staticmethod
    async def _finish_cancelled_control(
        control_task: asyncio.Task[str],
    ) -> str:
        while not control_task.done():
            try:
                await asyncio.shield(control_task)
            except asyncio.CancelledError:
                continue
        return control_task.result()

    @staticmethod
    def _cached_control_result(
        lane: _ControlLane, operation: str
    ) -> AdmissionStatus | str | None:
        disposition = lane.disposition
        if operation in {"CLAIM", "CANCEL"} and disposition is not None:
            try:
                return AdmissionStatus(disposition)
            except ValueError:
                return None
        if operation == "ABANDON" and lane.abandon_disposition is not None:
            return AdmissionStatus(lane.disposition or AdmissionStatus.UNAVAILABLE.value)
        if operation == "RELEASE" and lane.abandon_disposition is not None:
            return "RETIRED"
        if operation in {"ABANDON", "RELEASE"} and disposition in {
            AdmissionStatus.POISONED.value,
            AdmissionStatus.UNAVAILABLE.value,
        }:
            return AdmissionStatus(disposition)
        return None

    async def _apply_control_result(
        self,
        lane: _ControlLane,
        operation: str,
        status_text: str,
    ) -> AdmissionStatus | str:
        lease = lane.lease
        if operation in {"CLAIM", "CANCEL"}:
            try:
                status = AdmissionStatus(status_text)
            except ValueError:
                await self._close_failed_transport(protocol_error=True)
                raise _admission_backend_error(
                    LLMBackendErrorCode.ADMISSION_PROTOCOL
                ) from None
            if status is AdmissionStatus.OFFERED:
                await self._close_failed_transport(protocol_error=True)
                raise _admission_backend_error(
                    LLMBackendErrorCode.ADMISSION_PROTOCOL
                )
            lane.disposition = status.value
            if status is AdmissionStatus.GRANTED:
                if lease is None:
                    await self._close_failed_transport(protocol_error=True)
                    raise _admission_backend_error(
                        LLMBackendErrorCode.ADMISSION_PROTOCOL
                    )
                lease._claimed = True
                self._claimed_invocation = lane.invocation_id
                self._call_ordinals.setdefault(lane.invocation_id, 0)
                self._request_ids.setdefault(lane.invocation_id, set())
            elif lease is not None:
                lease._retired = True
                lane.lease = None
            return status
        if operation == "ABANDON":
            try:
                status = AdmissionStatus(status_text)
            except ValueError:
                await self._close_failed_transport(protocol_error=True)
                raise _admission_backend_error(
                    LLMBackendErrorCode.ADMISSION_PROTOCOL
                ) from None
            if status not in {
                AdmissionStatus.CANCELLED,
                AdmissionStatus.POISONED,
                AdmissionStatus.UNAVAILABLE,
            }:
                await self._close_failed_transport(protocol_error=True)
                raise _admission_backend_error(
                    LLMBackendErrorCode.ADMISSION_PROTOCOL
                )
            lane.disposition = status.value
            lane.abandon_disposition = (
                "ABANDON_SESSION_LOST"
                if status is AdmissionStatus.UNAVAILABLE
                else "ABANDON_ACKNOWLEDGED"
            )
            if lease is not None:
                lease._claimed = False
                lease._abandon_disposition = lane.abandon_disposition
                lane.lease = None
            self._clear_claim(lane.invocation_id)
            return status
        if operation == "RELEASE":
            if status_text not in {
                "RELEASED",
                AdmissionStatus.POISONED.value,
                AdmissionStatus.UNAVAILABLE.value,
            }:
                await self._close_failed_transport(protocol_error=True)
                raise _admission_backend_error(
                    LLMBackendErrorCode.ADMISSION_PROTOCOL
                )
            lane.disposition = status_text
            if lease is not None:
                lease._claimed = False
                lease._released = True
                lane.lease = None
            self._clear_claim(lane.invocation_id)
            return status_text
        raise AssertionError(f"unknown ordinary control operation {operation}")

    async def _close_failed_transport(self, *, protocol_error: bool) -> None:
        if protocol_error:
            self._resolve_generation_protocol_error()
        self._closed = True
        self._transport_closed = True
        self._resolve_unavailable()
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except OSError:
            pass
        if self._reader_task is not asyncio.current_task() and not self._reader_task.done():
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)

    async def _cancel_successor_bounded(self, invocation_id: str) -> None:
        try:
            await self._control(
                "CANCEL_SUCCESSOR",
                invocation_id,
                successor_invocation_id=invocation_id,
            )
        except (LLMBackendError, asyncio.CancelledError):
            pass

    async def _abandon_bounded(self, invocation_id: str) -> None:
        lane = self._control_lanes.get(invocation_id)
        if lane is None:
            return
        try:
            await self._ordinary_control(lane, "ABANDON")
        except (LLMBackendError, asyncio.CancelledError):
            pass

    async def _send(self, message_type: str, **fields: object) -> None:
        self._require_open()
        try:
            encoded = _encode_frame(self._config.max_frame_bytes, message_type, **fields)
            async with self._write_lock:
                self._writer.write(encoded)
                await self._writer.drain()
        except (ConnectionError, OSError, _ProtocolViolation):
            raise _admission_backend_error(
                LLMBackendErrorCode.ADMISSION_UNAVAILABLE
            ) from None

    def _require_open(self) -> None:
        if self._closed:
            raise _admission_backend_error(LLMBackendErrorCode.ADMISSION_UNAVAILABLE)

    def _resolve_unavailable(self) -> None:
        unavailable = AdmissionResult(AdmissionStatus.UNAVAILABLE, None, 0)
        for future in self._results.values():
            if not future.done():
                future.set_result(unavailable)
        for future in self._attachments.values():
            if not future.done():
                future.set_result(AdmissionStatus.UNAVAILABLE.value)
        for future in self._acks.values():
            if not future.done():
                future.set_result(AdmissionStatus.UNAVAILABLE.value)
        for future in self._generations.values():
            if not future.done():
                future.set_exception(
                    _admission_backend_error(
                        LLMBackendErrorCode.ADMISSION_UNAVAILABLE
                    )
                )
        for lane in self._control_lanes.values():
            lane.disposition = AdmissionStatus.UNAVAILABLE.value
            lease = lane.lease
            if lease is not None:
                if lease._claimed or self._claimed_invocation == lane.invocation_id:
                    lane.abandon_disposition = "ABANDON_SESSION_LOST"
                    lease._abandon_disposition = "ABANDON_SESSION_LOST"
                lease._claimed = False
                lease._retired = True
        if self._claimed_invocation is not None:
            self._clear_claim(self._claimed_invocation)
        self._control_lanes.clear()
        self._terminal_controls.clear()

    def _resolve_generation_protocol_error(self) -> None:
        for future in self._generations.values():
            if not future.done():
                future.set_exception(
                    LLMBackendError(
                        LLMBackendErrorCode.ADMISSION_PROTOCOL,
                        http_status=None,
                        retryable=False,
                        provider_quiescence=ProviderQuiescence.UNKNOWN,
                    )
                )


class _LeaseActivation(AbstractContextManager[None]):
    def __init__(self, session: BrokerAdmissionSession, invocation_id: str) -> None:
        self._session = session
        self._invocation_id = invocation_id
        self._entered = False

    def __enter__(self) -> None:
        if (
            self._entered
            or self._session._claimed_invocation != self._invocation_id
            or self._session._activated_invocation is not None
        ):
            raise _admission_backend_error(LLMBackendErrorCode.ADMISSION_PROTOCOL)
        self._entered = True
        self._session._activated_invocation = self._invocation_id
        return None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._entered and self._session._activated_invocation == self._invocation_id:
            self._session._activated_invocation = None
        self._entered = False
        return None


class _ClientGenerationLease:
    def __init__(
        self,
        session: BrokerAdmissionSession,
        invocation_id: str,
        lane: _ControlLane,
    ) -> None:
        self._session = session
        self._invocation_id = invocation_id
        self._lane = lane
        self._claimed = False
        self._released = False
        self._retired = False
        self._abandon_disposition: str | None = None

    @property
    def invocation_id(self) -> str:
        return self._invocation_id

    async def claim(self) -> AdmissionStatus:
        if self._claimed or self._released:
            raise _admission_backend_error(LLMBackendErrorCode.ADMISSION_PROTOCOL)
        status = await self._session._claim(self._invocation_id, self._lane)
        self._retired = status is not AdmissionStatus.GRANTED
        return status

    def activate(self) -> AbstractContextManager[None]:
        if (
            not self._claimed
            or self._released
            or self._retired
            or self._abandon_disposition is not None
        ):
            raise _admission_backend_error(LLMBackendErrorCode.ADMISSION_PROTOCOL)
        return _LeaseActivation(self._session, self._invocation_id)

    async def release(self) -> None:
        if self._abandon_disposition is not None:
            self._released = True
            self._retired = True
            return
        if not self._claimed or self._released:
            raise _admission_backend_error(LLMBackendErrorCode.ADMISSION_PROTOCOL)
        await self._session._release(self._lane)


class _ClientSuccessorReservation:
    def __init__(
        self,
        session: BrokerAdmissionSession,
        invocation_id: str,
        future: asyncio.Future[AdmissionResult],
    ) -> None:
        self._session = session
        self._invocation_id = invocation_id
        self._future = future
        self._wait_started = False

    @property
    def invocation_id(self) -> str:
        return self._invocation_id

    async def wait_offer(self) -> AdmissionResult:
        if self._wait_started:
            raise _admission_backend_error(LLMBackendErrorCode.ADMISSION_PROTOCOL)
        self._wait_started = True
        try:
            return await asyncio.shield(self._future)
        finally:
            if self._future.done():
                self._session._forget_result(self._invocation_id, self._future)

    async def cancel(self) -> AdmissionStatus:
        return await self._session.cancel_successor(self._invocation_id)


class BrokeredStructuredLLMBackend:
    """Structured backend proxy scoped to one authenticated admission session."""

    def __init__(self, session: BrokerAdmissionSession) -> None:
        if not isinstance(session, BrokerAdmissionSession):
            raise TypeError("session must be BrokerAdmissionSession")
        self._session = session

    @property
    def identity(self) -> BackendIdentity:
        return self._session.identity

    async def generate(
        self, request: StructuredGenerationRequest
    ) -> StructuredGenerationResponse:
        if not isinstance(request, StructuredGenerationRequest):
            raise TypeError("request must be StructuredGenerationRequest")
        invocation_id = self._session._activated_invocation
        if invocation_id is None:
            raise _admission_backend_error(LLMBackendErrorCode.ADMISSION_PROTOCOL)
        return await self._session._generate(invocation_id, request)

    async def aclose(self) -> None:
        await self._session.aclose()
