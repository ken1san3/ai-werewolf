"""Public immutable types for the protocol-only Network Client."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Sequence


class ClientLifecycle(str, Enum):
    NEW = "NEW"
    JOINING = "JOINING"
    RESUMING = "RESUMING"
    SYNCHRONIZING = "SYNCHRONIZING"
    CONNECTED = "CONNECTED"
    RECONNECT_WAIT = "RECONNECT_WAIT"
    STOPPING = "STOPPING"
    ENDED = "ENDED"
    FAILED = "FAILED"


class ClientExitReason(str, Enum):
    GAME_ENDED = "GAME_ENDED"
    STOPPED = "STOPPED"
    RECONNECT_EXHAUSTED = "RECONNECT_EXHAUSTED"
    INCOMPATIBLE_PROTOCOL = "INCOMPATIBLE_PROTOCOL"
    INCOMPATIBLE_PROTOCOL_CHECKPOINT = "INCOMPATIBLE_PROTOCOL_CHECKPOINT"
    INVALID_SERVER_MESSAGE = "INVALID_SERVER_MESSAGE"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    JOIN_OUTCOME_UNKNOWN = "JOIN_OUTCOME_UNKNOWN"
    CREDENTIAL_INVALID = "CREDENTIAL_INVALID"
    CREDENTIAL_SAVE_FAILED = "CREDENTIAL_SAVE_FAILED"
    RESUME_BUFFER_OVERRUN = "RESUME_BUFFER_OVERRUN"
    CONSUMER_OVERRUN = "CONSUMER_OVERRUN"
    CONFIG_INVALID = "CONFIG_INVALID"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ClientError(RuntimeError):
    """Base class for local Network Client failures."""


class CredentialError(ClientError, ValueError):
    """Credential storage contains invalid data or cannot be accessed."""


class StaleActionError(ClientError):
    """A request used an action handle from an older snapshot or connection."""


class NotDeliveredError(ClientError):
    """A command was rejected locally before the WebSocket send boundary."""


class DeliveryUnknownError(ClientError):
    """The socket failed after a command crossed the send boundary."""

    def __init__(
        self,
        message: str,
        *,
        request_event_id: str | None = None,
        action: str | None = None,
        connection_generation: int | None = None,
    ) -> None:
        super().__init__(message)
        supplied = (
            request_event_id is not None,
            action is not None,
            connection_generation is not None,
        )
        if any(supplied) and not all(supplied):
            raise ValueError("delivery-unknown attempt identity must be complete")
        if request_event_id is not None and (
            not isinstance(request_event_id, str) or not request_event_id
        ):
            raise ValueError("request_event_id must be a non-empty string")
        if action is not None and (not isinstance(action, str) or not action):
            raise ValueError("action must be a non-empty string")
        if connection_generation is not None and (
            isinstance(connection_generation, bool)
            or not isinstance(connection_generation, int)
            or connection_generation < 0
        ):
            raise ValueError("connection_generation must be a non-negative integer")
        self.request_event_id = request_event_id
        self.action = action
        self.connection_generation = connection_generation


class ConsumerOverrunError(ClientError):
    """The bounded inbound event stream has no capacity left."""


class ProtocolError(ClientError):
    """A received or locally-created protocol message is invalid."""


class IncompatibleProtocolError(ProtocolError):
    """The peer speaks a different protocol major version."""


@dataclass(frozen=True)
class NetworkClientConfig:
    uri: str
    game_id: str
    entry_token: str | None
    protocol_version: str | None = None
    connect_timeout_seconds: float = 5.0
    sync_timeout_seconds: float = 5.0
    inbound_event_capacity: int = 1024
    outbound_command_capacity: int = 64
    resume_replay_capacity: int = 128
    shutdown_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if not isinstance(self.uri, str) or not self.uri:
            raise ValueError("uri must be a non-empty string")
        if not isinstance(self.game_id, str) or not self.game_id:
            raise ValueError("game_id must be a non-empty string")
        if self.entry_token is not None and (
            not isinstance(self.entry_token, str) or not self.entry_token
        ):
            raise ValueError("entry_token must be non-empty when supplied")
        if self.protocol_version is not None and not _valid_protocol_version(self.protocol_version):
            raise ValueError("protocol_version must be major.minor")
        for name, value in (
            ("connect_timeout_seconds", self.connect_timeout_seconds),
            ("sync_timeout_seconds", self.sync_timeout_seconds),
            ("shutdown_timeout_seconds", self.shutdown_timeout_seconds),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be a positive finite number")
        if (
            isinstance(self.resume_replay_capacity, bool)
            or not isinstance(self.resume_replay_capacity, int)
            or self.resume_replay_capacity < 1
        ):
            raise ValueError("resume_replay_capacity must be a positive integer")
        if self.inbound_event_capacity < 1 or self.outbound_command_capacity < 1:
            raise ValueError("event and command capacities must be positive")


@dataclass(frozen=True)
class ReconnectPolicy:
    initial_delay_seconds: float = 0.25
    multiplier: float = 2.0
    max_delay_seconds: float = 5.0
    jitter_ratio: float = 0.2
    max_disconnected_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.initial_delay_seconds < 0:
            raise ValueError("initial_delay_seconds must not be negative")
        if self.multiplier < 1:
            raise ValueError("multiplier must be at least 1")
        if self.max_delay_seconds < self.initial_delay_seconds:
            raise ValueError("max_delay_seconds must not be below initial delay")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between 0 and 1")
        if self.max_disconnected_seconds <= 0:
            raise ValueError("max_disconnected_seconds must be positive")


@dataclass(frozen=True)
class SessionCheckpoint:
    connection_token: str
    last_seq: int
    protocol_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.connection_token, str) or not self.connection_token:
            raise ValueError("connection_token must be a non-empty string")
        if isinstance(self.last_seq, bool) or not isinstance(self.last_seq, int) or self.last_seq < 0:
            raise ValueError("last_seq must be a non-negative integer")
        if not _valid_protocol_version(self.protocol_version):
            raise ValueError("protocol_version must be major.minor")


class CredentialStore(Protocol):
    async def load(self) -> SessionCheckpoint | None:
        """Load a checkpoint, returning None when no checkpoint exists."""

    async def save(self, checkpoint: SessionCheckpoint) -> None:
        """Atomically persist a checkpoint."""


@dataclass(frozen=True)
class ActionHandle:
    connection_generation: int
    action_generation: int
    phase: str
    day: int
    type: str

    @property
    def generation(self) -> int:
        """Compatibility alias for the action generation."""

        return self.action_generation


@dataclass(frozen=True)
class AbilityAction(ActionHandle):
    ability_id: str
    description: str | None
    valid_targets: tuple[str, ...]
    target_count: int
    uses_remaining: int | None


@dataclass(frozen=True)
class ChatAction(ActionHandle):
    channel: str

    @property
    def channel_id(self) -> str:
        return self.channel


@dataclass(frozen=True)
class VoteAction(ActionHandle):
    valid_targets: tuple[str, ...]
    target_count: int
    allows_abstain: bool


@dataclass(frozen=True)
class CoDeclareAction(ActionHandle):
    claimed_role_ids: tuple[str, ...]


@dataclass(frozen=True)
class CoReportAction(ActionHandle):
    pass


Action = AbilityAction | ChatAction | VoteAction | CoDeclareAction | CoReportAction


@dataclass(frozen=True)
class ClientSnapshot:
    lifecycle: ClientLifecycle = ClientLifecycle.NEW
    player_id: str | None = None
    last_seq: int = 0
    players: tuple[Mapping[str, Any], ...] = ()
    deaths: tuple[Mapping[str, Any], ...] = ()
    action_state: Mapping[str, Any] | None = None
    self_info: Mapping[str, Any] | None = None
    revealed_roles: tuple[Mapping[str, Any], ...] = ()
    history: tuple[Mapping[str, Any], ...] = ()
    actions: tuple[Action, ...] = ()
    generation: int = 0
    connection_generation: int = 0
    action_generation: int = 0
    state_sync: Mapping[str, Any] | None = None
    player_list: Mapping[str, Any] | None = None
    player_deaths: Mapping[str, Any] | None = None

    @property
    def current_actions(self) -> tuple[Action, ...]:
        return self.actions

    @property
    def action_handles(self) -> tuple[Action, ...]:
        return self.actions

    @property
    def self_state(self) -> Mapping[str, Any] | None:
        return self.self_info

    @property
    def snapshot_generation(self) -> int:
        return self.generation


@dataclass(frozen=True)
class ServerEvent:
    type: str
    protocol_version: str
    event_id: str
    game_id: str
    seq: int
    timestamp: int
    payload: Mapping[str, Any]

    def as_message(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "protocol_version": self.protocol_version,
            "event_id": self.event_id,
            "game_id": self.game_id,
            "seq": self.seq,
            "timestamp": self.timestamp,
            "payload": _thaw(self.payload),
        }


@dataclass(frozen=True)
class LifecycleChanged:
    previous: ClientLifecycle
    current: ClientLifecycle


@dataclass(frozen=True)
class SequenceGapDetected:
    expected_seq: int
    received_seq: int
    connection_generation: int

    @property
    def expected(self) -> int:
        return self.expected_seq

    @property
    def received(self) -> int:
        return self.received_seq


@dataclass(frozen=True)
class SequenceGapRecovered:
    previous_seq: int
    recovered_seq: int
    connection_generation: int


@dataclass(frozen=True)
class ActionAccepted:
    action: str
    request_event_id: str
    seq: int
    observation_connection_generation: int
    observed_at_monotonic: float

    def __post_init__(self) -> None:
        _require_non_empty_string("action", self.action)
        _require_non_empty_string("request_event_id", self.request_event_id)
        _require_non_negative_int("seq", self.seq)
        _require_non_negative_int(
            "observation_connection_generation",
            self.observation_connection_generation,
        )
        _require_finite_number("observed_at_monotonic", self.observed_at_monotonic)

    @property
    def connection_generation(self) -> int:
        """Compatibility-shaped access shared with existing transport notices."""

        return self.observation_connection_generation


@dataclass(frozen=True)
class ActionRejected:
    action: str
    reason: str
    seq: int
    connection_generation: int
    observed_at_monotonic: float
    request_event_id: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty_string("action", self.action)
        _require_non_empty_string("reason", self.reason)
        _require_non_negative_int("seq", self.seq)
        _require_non_negative_int("connection_generation", self.connection_generation)
        _require_finite_number("observed_at_monotonic", self.observed_at_monotonic)
        if self.request_event_id is not None:
            _require_non_empty_string("request_event_id", self.request_event_id)

    @property
    def observation_connection_generation(self) -> int:
        """Name the observation generation without breaking Phase 3.4 callers."""

        return self.connection_generation


@dataclass(frozen=True)
class ResumeRecoveryCompleted:
    """Network facts for one replay/resume/sync recovery barrier."""

    connection_generation: int
    requested_last_seq: int
    replay_first_seq: int | None
    replay_last_seq: int | None
    replay_contiguous: bool
    replay_gap_or_floor: bool
    resumed_seq: int
    state_sync_seq: int
    complete: bool = True

    def __post_init__(self) -> None:
        for name in (
            "connection_generation",
            "requested_last_seq",
            "resumed_seq",
            "state_sync_seq",
        ):
            _require_non_negative_int(name, getattr(self, name))
        for name in ("replay_first_seq", "replay_last_seq"):
            value = getattr(self, name)
            if value is not None:
                _require_non_negative_int(name, value)
        if (self.replay_first_seq is None) != (self.replay_last_seq is None):
            raise ValueError("replay range endpoints must both be present or both be absent")
        if (
            self.replay_first_seq is not None
            and self.replay_last_seq is not None
            and self.replay_first_seq > self.replay_last_seq
        ):
            raise ValueError("replay_first_seq must not exceed replay_last_seq")
        for name in ("replay_contiguous", "replay_gap_or_floor", "complete"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be bool")
        if self.replay_gap_or_floor and self.replay_contiguous:
            raise ValueError("a replay cannot be both contiguous and gap/floor recovery")
        if self.resumed_seq <= self.requested_last_seq:
            raise ValueError("resumed_seq must follow requested_last_seq")
        if self.state_sync_seq <= self.resumed_seq:
            raise ValueError("state_sync_seq must follow resumed_seq")
        if self.replay_contiguous:
            if self.replay_first_seq is None:
                if self.resumed_seq != self.requested_last_seq + 1:
                    raise ValueError("empty contiguous replay must be followed by its ACK")
            elif (
                self.replay_first_seq != self.requested_last_seq + 1
                or self.replay_last_seq != self.resumed_seq - 1
            ):
                raise ValueError("contiguous replay range must span checkpoint to ACK")
        elif not self.replay_gap_or_floor:
            raise ValueError("non-contiguous recovery must identify a replay gap/floor")
        if self.replay_gap_or_floor and self.replay_first_seq is not None:
            raise ValueError("gap/floor recovery must not publish a partial replay range")
        if not self.complete:
            raise ValueError("only completed recovery barriers are published")


@dataclass(frozen=True)
class PhaseTimingMapped:
    phase: str
    day: int
    source_seq: int
    connection_generation: int
    action_generation: int
    server_timestamp: int
    phase_ends_at: int | None
    mapped_at_monotonic: float
    local_deadline_monotonic: float | None

    def __post_init__(self) -> None:
        _require_non_empty_string("phase", self.phase)
        for name in (
            "day",
            "source_seq",
            "connection_generation",
            "action_generation",
            "server_timestamp",
        ):
            _require_non_negative_int(name, getattr(self, name))
        if self.phase_ends_at is not None:
            _require_non_negative_int("phase_ends_at", self.phase_ends_at)
        _require_finite_number("mapped_at_monotonic", self.mapped_at_monotonic)
        if self.local_deadline_monotonic is not None:
            _require_finite_number(
                "local_deadline_monotonic", self.local_deadline_monotonic
            )


@dataclass(frozen=True)
class PhaseDeadlineReached:
    phase: str
    day: int
    connection_generation: int
    action_generation: int
    local_deadline_monotonic: float
    reached_at_monotonic: float

    def __post_init__(self) -> None:
        _require_non_empty_string("phase", self.phase)
        for name in ("day", "connection_generation", "action_generation"):
            _require_non_negative_int(name, getattr(self, name))
        _require_finite_number(
            "local_deadline_monotonic", self.local_deadline_monotonic
        )
        _require_finite_number("reached_at_monotonic", self.reached_at_monotonic)


@dataclass(frozen=True)
class NotDelivered:
    action: str
    reason: str
    connection_generation: int


@dataclass(frozen=True)
class DeliveryUnknown:
    action: str
    reason: str
    connection_generation: int


@dataclass(frozen=True)
class GameEnded:
    event: ServerEvent


@dataclass(frozen=True)
class FatalTermination:
    reason: ClientExitReason
    detail: str | None = None


ClientEvent = (
    ServerEvent
    | LifecycleChanged
    | SequenceGapDetected
    | SequenceGapRecovered
    | ActionAccepted
    | ActionRejected
    | ResumeRecoveryCompleted
    | PhaseTimingMapped
    | PhaseDeadlineReached
    | NotDelivered
    | DeliveryUnknown
    | GameEnded
    | FatalTermination
)


@dataclass(frozen=True)
class SendReceipt:
    event_id: str
    connection_generation: int
    sent: bool = True


@dataclass(frozen=True)
class ClientExit:
    reason: ClientExitReason
    success: bool
    lifecycle: ClientLifecycle
    detail: str | None = None


def _valid_protocol_version(value: object) -> bool:
    if not isinstance(value, str):
        return False
    major, dot, minor = value.partition(".")
    return bool(dot and major.isdigit() and minor.isdigit())


def _require_non_empty_string(name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")


def _require_non_negative_int(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _require_finite_number(name: str, value: object) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{name} must be a finite non-negative number")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, frozenset):
        return [_thaw(item) for item in value]
    return value


def immutable_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({key: immutable_value(item) for key, item in value.items()})


def immutable_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return immutable_mapping(value)
    if isinstance(value, list):
        return tuple(immutable_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(immutable_value(item) for item in value)
    if isinstance(value, set):
        return frozenset(immutable_value(item) for item in value)
    return value
