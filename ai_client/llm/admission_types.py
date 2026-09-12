"""Model-neutral contracts for the Phase 5 generation admission boundary.

This module is deliberately free of I/O.  Admission metadata never contains a
prompt, model/role name, action payload, credential, or generated result.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from enum import Enum, IntEnum
import hashlib
import json
import math
import re
from typing import Literal, Mapping, Protocol, runtime_checkable

from .backend import StructuredLLMBackend
from .types import BackendIdentity, StructuredGenerationRequest, StructuredGenerationResponse


GENERATION_IPC_PROTOCOL = "aiwolf.generation-ipc.v1"
_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _bounded_int(name: str, value: object, lower: int, upper: int) -> None:
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f"{name} must be an int in [{lower}, {upper}]")


def _non_negative_int(name: str, value: object) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative int")


def _positive_finite(name: str, value: object) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{name} must be a finite positive number")


def _non_empty_string(name: str, value: object, *, maximum: int = 128) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(
            f"{name} must be a non-empty string of at most {maximum} characters"
        )


class GenerationPriority(IntEnum):
    RESERVATION = 0
    REACTION = 1


class AdmissionStatus(str, Enum):
    OFFERED = "OFFERED"
    GRANTED = "GRANTED"
    REPLACED = "REPLACED"
    EXPIRED = "EXPIRED"
    OVERLOADED = "OVERLOADED"
    UNAVAILABLE = "UNAVAILABLE"
    CANCELLED = "CANCELLED"
    POISONED = "POISONED"


@dataclass(frozen=True)
class AdmissionRequest:
    invocation_id: str
    priority: GenerationPriority
    phase: str
    day: int
    action_generation: int
    mapping_order: int
    not_after_monotonic: float

    def __post_init__(self) -> None:
        _non_empty_string("invocation_id", self.invocation_id)
        if not isinstance(self.priority, GenerationPriority):
            raise TypeError("priority must be GenerationPriority")
        _non_empty_string("phase", self.phase)
        for name in ("day", "action_generation", "mapping_order"):
            _non_negative_int(name, getattr(self, name))
        _positive_finite("not_after_monotonic", self.not_after_monotonic)


@runtime_checkable
class GenerationLease(Protocol):
    @property
    def invocation_id(self) -> str: ...

    async def claim(self) -> AdmissionStatus: ...

    def activate(self) -> AbstractContextManager[None]: ...

    async def release(self) -> None: ...


@dataclass(frozen=True)
class AdmissionResult:
    status: AdmissionStatus
    lease: GenerationLease | None
    queue_wait_microseconds: int

    def __post_init__(self) -> None:
        if not isinstance(self.status, AdmissionStatus):
            raise TypeError("status must be AdmissionStatus")
        _non_negative_int("queue_wait_microseconds", self.queue_wait_microseconds)
        if (self.status is AdmissionStatus.OFFERED) != (self.lease is not None):
            raise ValueError("only OFFERED results carry a lease")


@runtime_checkable
class SuccessorReservation(Protocol):
    @property
    def invocation_id(self) -> str: ...

    async def wait_offer(self) -> AdmissionResult: ...

    async def cancel(self) -> AdmissionStatus: ...


@runtime_checkable
class GenerationAdmission(Protocol):
    async def acquire(self, request: AdmissionRequest) -> AdmissionResult: ...

    async def cancel(self, invocation_id: str) -> AdmissionStatus: ...

    async def replace_waiting(
        self, old_invocation_id: str, replacement: AdmissionRequest
    ) -> AdmissionResult: ...

    async def reserve_successor(
        self, active_invocation_id: str, successor: AdmissionRequest
    ) -> SuccessorReservation: ...

    async def cancel_successor(
        self, successor_invocation_id: str
    ) -> AdmissionStatus: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class BrokeredBackend(StructuredLLMBackend, Protocol):
    async def generate(
        self, request: StructuredGenerationRequest
    ) -> StructuredGenerationResponse: ...


@dataclass(frozen=True)
class GenerationBrokerConfig:
    max_clients: int = 9
    max_pending_total: int = 9
    max_pending_per_client: int = 1
    max_active: Literal[1] = 1
    max_calls_per_lease: Literal[2] = 2
    max_frame_bytes: int = 131072
    authentication_timeout_seconds: float = 5.0
    cancellation_grace_seconds: float = 0.25
    provider_drain_grace_seconds: float = 5.0
    shutdown_grace_seconds: float = 6.0
    metrics_queue_capacity: int = 64

    def __post_init__(self) -> None:
        _bounded_int("max_clients", self.max_clients, 1, 9)
        _bounded_int("max_pending_total", self.max_pending_total, 1, 9)
        _bounded_int("max_pending_per_client", self.max_pending_per_client, 1, 1)
        _bounded_int("max_active", self.max_active, 1, 1)
        _bounded_int("max_calls_per_lease", self.max_calls_per_lease, 2, 2)
        _bounded_int("max_frame_bytes", self.max_frame_bytes, 1, 131072)
        _bounded_int("metrics_queue_capacity", self.metrics_queue_capacity, 1, 64)
        for name in (
            "authentication_timeout_seconds",
            "cancellation_grace_seconds",
            "provider_drain_grace_seconds",
            "shutdown_grace_seconds",
        ):
            _positive_finite(name, getattr(self, name))
        if self.shutdown_grace_seconds < (
            self.provider_drain_grace_seconds + self.cancellation_grace_seconds
        ):
            raise ValueError(
                "shutdown_grace_seconds must cover provider drain and cancellation"
            )

    @property
    def config_fingerprint(self) -> str:
        encoded = json.dumps(
            asdict(self),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, repr=False)
class AdmissionCredentials:
    client_id: str
    token: str

    def __post_init__(self) -> None:
        _non_empty_string("client_id", self.client_id)
        if not isinstance(self.token, str) or _TOKEN_RE.fullmatch(self.token) is None:
            raise ValueError("token must be a 256-bit hexadecimal secret")

    def __repr__(self) -> str:
        return f"AdmissionCredentials(client_id={self.client_id!r}, token=<redacted>)"


@dataclass(frozen=True)
class BrokerReady:
    host: str
    port: int
    protocol: str
    backend_identity: BackendIdentity
    config_fingerprint: str

    def __post_init__(self) -> None:
        if self.host not in {"127.0.0.1", "::1"}:
            raise ValueError("host must be a numeric loopback address")
        _bounded_int("port", self.port, 1, 65535)
        if self.protocol != GENERATION_IPC_PROTOCOL:
            raise ValueError("protocol mismatch")
        if not isinstance(self.backend_identity, BackendIdentity):
            raise TypeError("backend_identity must be BackendIdentity")
        if not re.fullmatch(r"[0-9a-f]{64}", self.config_fingerprint):
            raise ValueError("config_fingerprint must be lowercase SHA-256")


@dataclass(frozen=True)
class AdmissionBrokerSnapshot:
    offered: str | None
    claimed: str | None
    provider_call_active: bool
    draining: bool
    poisoned: bool
    poison_reason: str | None
    pending_total: int
    connected_clients: int

    def __post_init__(self) -> None:
        for name in ("provider_call_active", "draining", "poisoned"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be bool")
        _non_negative_int("pending_total", self.pending_total)
        _non_negative_int("connected_clients", self.connected_clients)


def request_to_wire(request: AdmissionRequest) -> Mapping[str, object]:
    """Return the exact scheduling-only representation used on private IPC."""

    return {
        "action_generation": request.action_generation,
        "day": request.day,
        "invocation_id": request.invocation_id,
        "mapping_order": request.mapping_order,
        "not_after_monotonic": request.not_after_monotonic,
        "phase": request.phase,
        "priority": int(request.priority),
    }


def request_from_wire(value: object) -> AdmissionRequest:
    if not isinstance(value, dict) or set(value) != {
        "action_generation",
        "day",
        "invocation_id",
        "mapping_order",
        "not_after_monotonic",
        "phase",
        "priority",
    }:
        raise ValueError("invalid admission request")
    if type(value["priority"]) is not int:
        raise ValueError("invalid admission request")
    try:
        priority = GenerationPriority(value["priority"])
    except (TypeError, ValueError):
        raise ValueError("invalid admission request") from None
    try:
        return AdmissionRequest(
            invocation_id=value["invocation_id"],  # type: ignore[arg-type]
            priority=priority,
            phase=value["phase"],  # type: ignore[arg-type]
            day=value["day"],  # type: ignore[arg-type]
            action_generation=value["action_generation"],  # type: ignore[arg-type]
            mapping_order=value["mapping_order"],  # type: ignore[arg-type]
            not_after_monotonic=value["not_after_monotonic"],  # type: ignore[arg-type]
        )
    except (TypeError, ValueError):
        raise ValueError("invalid admission request") from None
