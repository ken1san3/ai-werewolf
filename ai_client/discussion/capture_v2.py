"""Structural offline candidates for Phase 6 generation v2 capture.

These candidates exercise closed shapes and hash/CAS rules.  The authenticated
network, broker, durable-audit, and delivery hooks that may promote observations
to runtime authority are deliberately unconnected; this module fails closed at
that promotion boundary.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from enum import Enum
import hashlib
import json
import math
import re
from typing import Any, Literal, Mapping
from types import MappingProxyType


StageV2 = Literal["chat_plan", "message", "pre_vote", "co_opportunity", "ability"]
_STAGES = ("chat_plan", "message", "pre_vote", "co_opportunity", "ability")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_ID4 = re.compile(r"^[a-z][0-9]{3}$")


class CaptureV2Error(ValueError):
    pass


class FreshnessStatusV2(str, Enum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    LEASE_INVALID = "LEASE_INVALID"


_OBSERVATION_TOKEN = object()


def promote_runtime_authority_v2(candidate: object) -> None:
    """Fail closed until an authenticated runtime adapter owns opaque evidence."""
    raise CaptureV2Error("runtime authority hook is not connected")


def create_runtime_state_lease_store_v2() -> None:
    """Fail closed until broker/audit observations are supplied by runtime hooks."""
    raise CaptureV2Error("runtime lease hook is not connected")


@dataclass(frozen=True, init=False)
class StructuralBrokerObservationCandidateV2:
    operation: str; owner_invocation_id: str; broker_segment_id: str; status: str; record_sha256: str
    def __init__(self, token: object, operation: str, owner: str, segment: str, status: str, record_sha256: str) -> None:
        if token is not _OBSERVATION_TOKEN: raise CaptureV2Error("structural broker candidate is opaque")
        for name, value in (("operation", operation), ("owner", owner), ("segment", segment), ("status", status)): _text(name, value)
        _sha("record_sha256", record_sha256)
        object.__setattr__(self, "operation", operation); object.__setattr__(self, "owner_invocation_id", owner)
        object.__setattr__(self, "broker_segment_id", segment); object.__setattr__(self, "status", status)
        object.__setattr__(self, "record_sha256", record_sha256)


def structural_broker_observation_candidate(record: Mapping[str, object]) -> StructuralBrokerObservationCandidateV2:
    required = {"operation", "owner_invocation_id", "broker_segment_id", "status"}
    if not isinstance(record, Mapping) or set(record) != required:
        raise CaptureV2Error("broker observation record shape mismatch")
    return StructuralBrokerObservationCandidateV2(_OBSERVATION_TOKEN, record["operation"], record["owner_invocation_id"],
        record["broker_segment_id"], record["status"], canonical_sha256_v2(record))


@dataclass(frozen=True, init=False)
class StructuralAttemptAuditCandidateV2:
    reservation: "AttemptReservationV2"; status: str; audit_sequence: int; record_sha256: str
    def __init__(self, token: object, reservation: "AttemptReservationV2", status: str,
                 sequence: int, record_sha256: str) -> None:
        if token is not _OBSERVATION_TOKEN: raise CaptureV2Error("structural audit candidate is opaque")
        _validate_attempt(reservation); _text("status", status); _integer("audit_sequence", sequence, 1); _sha("record_sha256", record_sha256)
        object.__setattr__(self, "reservation", reservation); object.__setattr__(self, "status", status)
        object.__setattr__(self, "audit_sequence", sequence); object.__setattr__(self, "record_sha256", record_sha256)


def structural_attempt_audit_candidate(record: Mapping[str, object]) -> StructuralAttemptAuditCandidateV2:
    required = {"reservation", "status", "audit_sequence"}
    if not isinstance(record, Mapping) or set(record) != required or not isinstance(record["reservation"], AttemptReservationV2):
        raise CaptureV2Error("attempt audit record shape mismatch")
    return StructuralAttemptAuditCandidateV2(_OBSERVATION_TOKEN, record["reservation"], record["status"],
                                  record["audit_sequence"], canonical_sha256_v2(record))


@dataclass(frozen=True, init=False)
class StructuralDeliveryObservationCandidateV2:
    capture_id: str; status: str; record_sha256: str
    def __init__(self, token: object, capture_id: str, status: str, record_sha256: str) -> None:
        if token is not _OBSERVATION_TOKEN: raise CaptureV2Error("structural delivery candidate is opaque")
        _sha("capture_id", capture_id); _text("status", status); _sha("record_sha256", record_sha256)
        object.__setattr__(self, "capture_id", capture_id); object.__setattr__(self, "status", status)
        object.__setattr__(self, "record_sha256", record_sha256)


def structural_delivery_observation_candidate(record: Mapping[str, object]) -> StructuralDeliveryObservationCandidateV2:
    if not isinstance(record, Mapping) or set(record) != {"capture_id", "status"}:
        raise CaptureV2Error("delivery observation record shape mismatch")
    return StructuralDeliveryObservationCandidateV2(_OBSERVATION_TOKEN, record["capture_id"], record["status"], canonical_sha256_v2(record))


def _plain(value: object) -> object:
    if hasattr(value, "__dataclass_fields__"):
        return {item.name: _plain(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _plain(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(child) for child in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise CaptureV2Error("non-finite hash material")
    if value is None or type(value) in {str, int, float, bool}:
        return value
    raise CaptureV2Error("hash material is not closed JSON")


def _freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value): raise CaptureV2Error("JSON object keys must be strings")
        return MappingProxyType({key: _freeze_json(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(child) for child in value)
    _plain(value)
    return value


def canonical_bytes_v2(value: object) -> bytes:
    return json.dumps(_plain(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def canonical_sha256_v2(value: object) -> str:
    return hashlib.sha256(canonical_bytes_v2(value)).hexdigest()


def _text(name: str, value: object) -> str:
    if type(value) is not str or not value:
        raise CaptureV2Error(f"{name} must be a non-empty string")
    return value


def _integer(name: str, value: object, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise CaptureV2Error(f"{name} must be an integer >= {minimum}")
    return value


def _sha(name: str, value: object) -> str:
    if type(value) is not str or _SHA.fullmatch(value) is None:
        raise CaptureV2Error(f"{name} must be lowercase SHA-256")
    return value


def _stage(value: object) -> str:
    if type(value) is not str or value not in _STAGES:
        raise CaptureV2Error("invalid generation v2 stage")
    return value


def expires_at_monotonic_us(value: float) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise CaptureV2Error("monotonic deadline must be finite and non-negative")
    return math.floor(value * 1_000_000)


def deadline_is_current(clock_value: float, expiry_us: int) -> bool:
    if isinstance(clock_value, bool) or not isinstance(clock_value, (int, float)) or not math.isfinite(clock_value):
        raise CaptureV2Error("clock must be finite")
    _integer("expiry_us", expiry_us)
    return math.ceil(clock_value * 1_000_000) < expiry_us


@dataclass(frozen=True)
class StructuralInboundCandidateV2:
    schema_version: Literal["aiwolf.authenticated-inbound-ref.v2"]
    game_id: str
    player_id: str
    connection_generation: int
    server_event_id: str
    server_seq: int
    message_type: Literal["game.event", "game.state_sync", "player.action_state"]
    source_path: str
    source_sha256: str
    protocol_version: str

    def __post_init__(self) -> None:
        if self.schema_version != "aiwolf.authenticated-inbound-ref.v2":
            raise CaptureV2Error("invalid inbound ref schema")
        for name in ("game_id", "player_id", "server_event_id", "protocol_version"):
            _text(name, getattr(self, name))
        _integer("connection_generation", self.connection_generation)
        _integer("server_seq", self.server_seq)
        if self.message_type not in {"game.event", "game.state_sync", "player.action_state"}:
            raise CaptureV2Error("invalid authenticated message type")
        if type(self.source_path) is not str or not self.source_path.startswith("/"):
            raise CaptureV2Error("source_path must be an absolute JSON pointer")
        _sha("source_sha256", self.source_sha256)


@dataclass(frozen=True)
class PhaseAttributionV2:
    schema_version: Literal["aiwolf.phase-attribution.v2"]
    mode: Literal["LIVE_REDUCER_PHASE", "SYNC_REPLAY_PHASE"]
    day: int
    phase: str
    world_version_before: int | None
    last_applied_sequence_before: int | None
    state_sync_history_index: int | None
    preceding_phase_entry_index: int | None
    phase_source: StructuralInboundCandidateV2 | None
    reducer_phase_witness_sha256: str | None

    def __post_init__(self) -> None:
        if self.schema_version != "aiwolf.phase-attribution.v2":
            raise CaptureV2Error("invalid phase attribution schema")
        _integer("day", self.day)
        _text("phase", self.phase)
        if self.mode == "LIVE_REDUCER_PHASE":
            _integer("world_version_before", self.world_version_before)
            _integer("last_applied_sequence_before", self.last_applied_sequence_before)
            if any(v is not None for v in (self.state_sync_history_index, self.preceding_phase_entry_index, self.phase_source)):
                raise CaptureV2Error("live phase attribution contains replay fields")
            _sha("reducer_phase_witness_sha256", self.reducer_phase_witness_sha256)
        elif self.mode == "SYNC_REPLAY_PHASE":
            index = _integer("state_sync_history_index", self.state_sync_history_index)
            preceding = _integer("preceding_phase_entry_index", self.preceding_phase_entry_index)
            if preceding >= index or not isinstance(self.phase_source, StructuralInboundCandidateV2):
                raise CaptureV2Error("sync phase source must precede the record")
            if any(v is not None for v in (self.world_version_before, self.last_applied_sequence_before, self.reducer_phase_witness_sha256)):
                raise CaptureV2Error("sync phase attribution contains live fields")
            if self.phase_source.message_type != "game.state_sync" or self.phase_source.source_path != f"/payload/history/{preceding}":
                raise CaptureV2Error("sync phase source path is not exact")
        else:
            raise CaptureV2Error("invalid phase attribution mode")


def live_phase_attribution(source: StructuralInboundCandidateV2, day: int, phase: str,
                           world_version: int, last_sequence: int) -> PhaseAttributionV2:
    material = {"schema_version": "aiwolf.reducer-phase-witness.v2", "game_id": source.game_id,
                "connection_generation": source.connection_generation, "day": day, "phase": phase,
                "world_version_before": world_version, "last_applied_sequence_before": last_sequence}
    return PhaseAttributionV2("aiwolf.phase-attribution.v2", "LIVE_REDUCER_PHASE", day, phase,
                              world_version, last_sequence, None, None, None, canonical_sha256_v2(material))


@dataclass(frozen=True)
class ActionContextRefV2:
    schema_version: Literal["aiwolf.action-context-ref.v2"]
    parent_source: StructuralInboundCandidateV2
    day: int
    phase: str
    connection_generation: int
    action_generation: int

    def __post_init__(self) -> None:
        if self.schema_version != "aiwolf.action-context-ref.v2" or not isinstance(self.parent_source, StructuralInboundCandidateV2):
            raise CaptureV2Error("invalid action context")
        _integer("day", self.day); _text("phase", self.phase)
        _integer("connection_generation", self.connection_generation); _integer("action_generation", self.action_generation)
        if self.parent_source.connection_generation != self.connection_generation:
            raise CaptureV2Error("action context connection mismatch")


@dataclass(frozen=True)
class ProvenanceSidecarEntryV2:
    schema_version: Literal["aiwolf.provenance-sidecar-entry.v2"]
    subject_kind: Literal["HISTORY_RECORD", "ACTION_HANDLE"]
    record_identity: tuple[str, int] | None
    action_identity: tuple[int, int] | None
    typed_value_sha256: str
    source: StructuralInboundCandidateV2
    phase_attribution: PhaseAttributionV2 | None
    action_context: ActionContextRefV2 | None

    def __post_init__(self) -> None:
        if self.schema_version != "aiwolf.provenance-sidecar-entry.v2" or not isinstance(self.source, StructuralInboundCandidateV2):
            raise CaptureV2Error("invalid provenance sidecar")
        _sha("typed_value_sha256", self.typed_value_sha256)
        if self.subject_kind == "HISTORY_RECORD":
            if self.record_identity is None or len(self.record_identity) != 2 or self.action_identity is not None or self.action_context is not None or not isinstance(self.phase_attribution, PhaseAttributionV2):
                raise CaptureV2Error("history sidecar shape mismatch")
            _text("record_kind", self.record_identity[0]); _integer("local_order", self.record_identity[1])
            assert self.phase_attribution is not None
            if self.phase_attribution.mode == "LIVE_REDUCER_PHASE":
                material = {"schema_version": "aiwolf.reducer-phase-witness.v2", "game_id": self.source.game_id,
                    "connection_generation": self.source.connection_generation, "day": self.phase_attribution.day,
                    "phase": self.phase_attribution.phase, "world_version_before": self.phase_attribution.world_version_before,
                    "last_applied_sequence_before": self.phase_attribution.last_applied_sequence_before}
                if canonical_sha256_v2(material) != self.phase_attribution.reducer_phase_witness_sha256:
                    raise CaptureV2Error("live reducer phase witness mismatch")
            else:
                phase_source = self.phase_attribution.phase_source
                assert phase_source is not None
                if ((phase_source.game_id, phase_source.player_id, phase_source.connection_generation,
                     phase_source.server_event_id, phase_source.server_seq, phase_source.protocol_version,
                     phase_source.message_type) !=
                    (self.source.game_id, self.source.player_id, self.source.connection_generation,
                     self.source.server_event_id, self.source.server_seq, self.source.protocol_version,
                     self.source.message_type)):
                    raise CaptureV2Error("sync phase source is from another authenticated envelope")
        elif self.subject_kind == "ACTION_HANDLE":
            if self.action_identity is None or len(self.action_identity) != 2 or self.record_identity is not None or self.phase_attribution is not None or not isinstance(self.action_context, ActionContextRefV2):
                raise CaptureV2Error("action sidecar shape mismatch")
            generation = _integer("action_generation", self.action_identity[0]); index = _integer("received_index", self.action_identity[1])
            if (generation != self.action_context.action_generation or self.source.game_id != self.action_context.parent_source.game_id
                    or self.source.server_event_id != self.action_context.parent_source.server_event_id
                    or self.source.player_id != self.action_context.parent_source.player_id
                    or self.source.connection_generation != self.action_context.parent_source.connection_generation
                    or self.source.server_seq != self.action_context.parent_source.server_seq
                    or self.source.protocol_version != self.action_context.parent_source.protocol_version
                    or self.source.message_type != self.action_context.parent_source.message_type
                    or self.source.source_path not in {f"/payload/actions/{index}", f"/payload/action_state/actions/{index}"}):
                raise CaptureV2Error("action source is not bound to its parent context")
        else:
            raise CaptureV2Error("invalid sidecar subject kind")


class ProvenanceSidecarRegistryV2:
    def __init__(self) -> None:
        self._identities: dict[tuple[str, object], ProvenanceSidecarEntryV2] = {}
        self._sources: set[tuple[str, str, str]] = set()

    def add(self, entry: ProvenanceSidecarEntryV2) -> None:
        if not isinstance(entry, ProvenanceSidecarEntryV2):
            raise CaptureV2Error("entry must be a provenance sidecar")
        identity = (entry.subject_kind, entry.record_identity or entry.action_identity)
        source = (entry.source.server_event_id, entry.source.source_path, entry.source.source_sha256)
        if identity in self._identities or source in self._sources:
            raise CaptureV2Error("sidecar identity or primary source is already registered")
        self._identities[identity] = entry; self._sources.add(source)


@dataclass(frozen=True)
class StructuralPublicChannelCandidateV2:
    schema_version: Literal["aiwolf.public-channel-authority.v2"]
    option_id: str
    channel_id: str
    audience: Literal["PUBLIC"]
    recipient_scope: Literal["SERVER_FILTERED_PUBLIC"]
    context_sha256: str
    action_source_sha256: str
    connection_generation: int
    action_generation: int
    phase_identity: str
    authority_revision_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != "aiwolf.public-channel-authority.v2" or self.audience != "PUBLIC" or self.recipient_scope != "SERVER_FILTERED_PUBLIC":
            raise CaptureV2Error("invalid public channel authority")
        if not _ID4.fullmatch(self.option_id) or not self.option_id.startswith("o"):
            raise CaptureV2Error("invalid channel option ID")
        _text("channel_id", self.channel_id)
        for name in ("context_sha256", "action_source_sha256", "phase_identity", "authority_revision_sha256"):
            _sha(name, getattr(self, name))
        _integer("connection_generation", self.connection_generation); _integer("action_generation", self.action_generation)
        expected = canonical_sha256_v2({key: value for key, value in asdict(self).items() if key != "authority_revision_sha256"})
        if self.authority_revision_sha256 != expected:
            raise CaptureV2Error("channel authority revision mismatch")


def make_structural_public_channel_candidate(option_id: str, channel_id: str, context_sha256: str,
                                  sidecar: ProvenanceSidecarEntryV2, phase_identity: str, *,
                                  context_channel_id: str, context_is_public: bool,
                                  action_channel_id: str) -> StructuralPublicChannelCandidateV2:
    """Bind caller-supplied authenticated context/action facts; adapters remain unconnected."""
    if sidecar.subject_kind != "ACTION_HANDLE" or sidecar.action_context is None:
        raise CaptureV2Error("channel candidate requires an action sidecar")
    if (type(context_is_public) is not bool or not context_is_public
            or channel_id != context_channel_id or channel_id != action_channel_id):
        raise CaptureV2Error("channel is not proven public in both context and action")
    base = dict(schema_version="aiwolf.public-channel-authority.v2", option_id=option_id, channel_id=channel_id,
                audience="PUBLIC", recipient_scope="SERVER_FILTERED_PUBLIC", context_sha256=context_sha256,
                action_source_sha256=canonical_sha256_v2(sidecar),
                connection_generation=sidecar.action_context.connection_generation,
                action_generation=sidecar.action_context.action_generation, phase_identity=phase_identity)
    return StructuralPublicChannelCandidateV2(**base, authority_revision_sha256=canonical_sha256_v2(base))


def make_structural_action_option_candidate(
    event: object,
    handle: object,
    bound_context: object,
    *,
    authenticated_player_id: str,
    connection_generation: int,
    received_index: int,
    option_id: str,
    phase_identity: str,
) -> "StructuralActionOptionCandidateV2":
    """Pure adapter from existing validated network/context types.

    The network hook that proves sequence continuity and connection ownership is
    deliberately outside this module; its observed owner/generation are mandatory
    inputs and are checked against every typed value here.
    """
    from ai_client.discussion.context import BoundDiscussionContext
    from ai_client.network.types import (
        AbilityAction, ChatAction, CoDeclareAction, CoReportAction, ServerEvent, VoteAction,
    )
    if not isinstance(event, ServerEvent) or not isinstance(bound_context, BoundDiscussionContext):
        raise CaptureV2Error("action adapter requires validated ServerEvent and bound context")
    if event.game_id != bound_context.context.game_id or authenticated_player_id != bound_context.context.player_id:
        raise CaptureV2Error("action event/context owner mismatch")
    if event.type == "player.action_state":
        parent = event.payload; parent_path = "/payload"; primary_path = f"/payload/actions/{received_index}"
    elif event.type == "game.state_sync":
        parent = event.payload.get("action_state"); parent_path = "/payload/action_state"
        primary_path = f"/payload/action_state/actions/{received_index}"
    else:
        raise CaptureV2Error("action source is not an authenticated action-state event")
    if not isinstance(parent, Mapping) or not isinstance(parent.get("actions"), (list, tuple)):
        raise CaptureV2Error("action-state parent is malformed")
    actions = parent["actions"]
    if not 0 <= received_index < len(actions) or not isinstance(actions[received_index], Mapping):
        raise CaptureV2Error("received action index is absent")
    raw = actions[received_index]
    if (getattr(handle, "connection_generation", None) != connection_generation
            or parent.get("day") != getattr(handle, "day", None)
            or parent.get("phase") != getattr(handle, "phase", None)):
        raise CaptureV2Error("typed action context differs from source parent")
    expected: dict[str, object]
    if isinstance(handle, ChatAction): expected = {"type": "chat", "channel": handle.channel}
    elif isinstance(handle, VoteAction): expected = {"type": "vote", "valid_targets": list(handle.valid_targets), "target_count": handle.target_count, "allows_abstain": handle.allows_abstain}
    elif isinstance(handle, AbilityAction): expected = {"type": "ability", "ability_id": handle.ability_id, "description": handle.description, "valid_targets": list(handle.valid_targets), "target_count": handle.target_count, "uses_remaining": handle.uses_remaining}
    elif isinstance(handle, CoDeclareAction): expected = {"type": "co_declare", "claimed_role_ids": list(handle.claimed_role_ids)}
    elif isinstance(handle, CoReportAction): expected = {"type": "co_report"}
    else: raise CaptureV2Error("unsupported typed action handle")
    if _plain(raw) != expected:
        raise CaptureV2Error("typed action does not exactly match source subtree")
    parent_source = StructuralInboundCandidateV2("aiwolf.authenticated-inbound-ref.v2", event.game_id,
        authenticated_player_id, connection_generation, event.event_id, event.seq, event.type,
        parent_path, canonical_sha256_v2(parent), event.protocol_version)
    primary_source = replace(parent_source, source_path=primary_path, source_sha256=canonical_sha256_v2(raw))
    context = ActionContextRefV2("aiwolf.action-context-ref.v2", parent_source, handle.day,
                                 handle.phase, connection_generation, handle.action_generation)
    sidecar = ProvenanceSidecarEntryV2("aiwolf.provenance-sidecar-entry.v2", "ACTION_HANDLE", None,
        (handle.action_generation, received_index), canonical_sha256_v2(handle), primary_source, None, context)
    authority = None
    if isinstance(handle, ChatAction):
        channels = [channel for channel in bound_context.context.chat_channels if channel.channel_id == handle.channel]
        if len(channels) != 1:
            raise CaptureV2Error("chat channel is absent from bound context")
        if channels[0].is_public:
            authority = make_structural_public_channel_candidate(option_id, handle.channel, bound_context.context_sha256,
                sidecar, phase_identity, context_channel_id=channels[0].channel_id,
                context_is_public=channels[0].is_public, action_channel_id=str(raw["channel"]))
    kind = handle.type
    return StructuralActionOptionCandidateV2("aiwolf.action-option-binding.v2", option_id, kind,
        handle.action_generation, connection_generation, phase_identity, canonical_sha256_v2(handle), sidecar, authority)


def bind_ability_result_sidecar_from_validated_event(
    event: object,
    record: object,
    phase_attribution: PhaseAttributionV2,
    bound_context: object,
    *,
    connection_generation: int,
    history_index: int | None = None,
) -> ProvenanceSidecarEntryV2:
    """Bind an existing typed ability record to its exact live or sync subtree."""
    from ai_client.network.types import ServerEvent
    from ai_client.discussion.context import BoundDiscussionContext
    from ai_client.world.model import AbilityResultRecord
    if (not isinstance(event, ServerEvent) or not isinstance(record, AbilityResultRecord)
            or not isinstance(bound_context, BoundDiscussionContext)):
        raise CaptureV2Error("ability adapter requires validated ServerEvent and typed record")
    if event.game_id != bound_context.context.game_id:
        raise CaptureV2Error("ability event/context game mismatch")
    authenticated_player_id = bound_context.context.player_id
    if event.type == "game.event" and history_index is None:
        container = event.payload; source_path = "/payload"
    elif event.type == "game.state_sync" and type(history_index) is int:
        history = event.payload.get("history")
        if not isinstance(history, (list, tuple)) or not 0 <= history_index < len(history):
            raise CaptureV2Error("sync history index is absent")
        container = history[history_index]; source_path = f"/payload/history/{history_index}"
        if not isinstance(container, Mapping) or container.get("type") != "game.event":
            raise CaptureV2Error("sync history source is not game.event")
        container = container.get("payload")
    else: raise CaptureV2Error("ability source path does not match event type")
    if not isinstance(container, Mapping) or not isinstance(container.get("event_payload"), Mapping):
        raise CaptureV2Error("ability event container is malformed")
    if event.type == "game.state_sync":
        if (phase_attribution.mode != "SYNC_REPLAY_PHASE"
                or phase_attribution.state_sync_history_index != history_index
                or phase_attribution.phase_source is None):
            raise CaptureV2Error("sync phase attribution does not identify the result entry")
        preceding = phase_attribution.preceding_phase_entry_index
        assert isinstance(preceding, int)
        raw_phase = event.payload["history"][preceding]
        expected_phase_source = phase_attribution.phase_source
        if (not isinstance(raw_phase, Mapping) or raw_phase.get("type") != "game.event"
                or not isinstance(raw_phase.get("payload"), Mapping)
                or raw_phase["payload"].get("event_type") != "PHASE_STARTED"
                or not isinstance(raw_phase["payload"].get("event_payload"), Mapping)
                or raw_phase["payload"]["event_payload"].get("day") != phase_attribution.day
                or raw_phase["payload"]["event_payload"].get("phase") != phase_attribution.phase
                or expected_phase_source.source_sha256 != canonical_sha256_v2(raw_phase)):
            raise CaptureV2Error("sync PHASE_STARTED source does not match actual history")
    elif phase_attribution.mode != "LIVE_REDUCER_PHASE":
        raise CaptureV2Error("live ability event requires live reducer phase witness")
    event_type = container.get("event_type"); payload = container["event_payload"]
    expected = {"target_player_id": record.target_player_id, "result": record.result_id, "role_id": record.revealed_role_id}
    if (event_type != record.event_type or event_type not in {"INSPECT_RESULT", "MEDIUM_RESULT", "INSPECT_DEAD_ROLE_RESULT", "GUARD_SUCCEEDED"}
            or any(payload.get(key) != value for key, value in expected.items())
            or record.day != phase_attribution.day or record.phase != phase_attribution.phase):
        raise CaptureV2Error("typed ability result differs from authenticated source")
    inbound = StructuralInboundCandidateV2("aiwolf.authenticated-inbound-ref.v2", event.game_id,
        authenticated_player_id, connection_generation, event.event_id, event.seq, event.type,
        source_path, canonical_sha256_v2(event.payload if source_path == "/payload" else event.payload["history"][history_index]),
        event.protocol_version)
    return ProvenanceSidecarEntryV2("aiwolf.provenance-sidecar-entry.v2", "HISTORY_RECORD",
        ("ability_result", record.order), None, canonical_sha256_v2(record), inbound, phase_attribution, None)


@dataclass(frozen=True)
class StructuralActionOptionCandidateV2:
    schema_version: Literal["aiwolf.action-option-binding.v2"]
    option_id: str
    action_kind: Literal["chat", "vote", "co_declare", "ability"]
    action_generation: int
    connection_generation: int
    phase_identity: str
    action_sha256: str
    source: ProvenanceSidecarEntryV2
    channel_authority: StructuralPublicChannelCandidateV2 | None

    def __post_init__(self) -> None:
        if self.schema_version != "aiwolf.action-option-binding.v2" or not isinstance(self.source, ProvenanceSidecarEntryV2):
            raise CaptureV2Error("invalid action binding")
        if not _ID4.fullmatch(self.option_id) or not self.option_id.startswith("o"):
            raise CaptureV2Error("invalid action option ID")
        if self.action_kind not in {"chat", "vote", "co_declare", "ability"}:
            raise CaptureV2Error("invalid action kind")
        _sha("phase_identity", self.phase_identity); _sha("action_sha256", self.action_sha256)
        if self.source.action_context is None or (self.action_generation, self.connection_generation) != (self.source.action_context.action_generation, self.source.action_context.connection_generation):
            raise CaptureV2Error("action binding generation mismatch")
        if self.action_sha256 != self.source.typed_value_sha256:
            raise CaptureV2Error("action binding typed hash mismatch")
        if self.action_kind == "chat":
            if self.channel_authority is not None:
                authority = self.channel_authority
                if (authority.option_id != self.option_id or authority.phase_identity != self.phase_identity
                        or authority.action_generation != self.action_generation
                        or authority.connection_generation != self.connection_generation
                        or authority.action_source_sha256 != canonical_sha256_v2(self.source)):
                    raise CaptureV2Error("chat authority does not match action binding")
        elif self.channel_authority is not None:
            raise CaptureV2Error("non-chat action cannot carry channel authority")


@dataclass(frozen=True, init=False)
class StructuralActionCatalogCandidateV2:
    """All eligible actions in authenticated received order."""

    bindings: tuple[StructuralActionOptionCandidateV2, ...]

    def __init__(self, token: object, bindings: tuple[StructuralActionOptionCandidateV2, ...]) -> None:
        if token is not _OBSERVATION_TOKEN:
            raise CaptureV2Error("complete received action catalog is opaque")
        object.__setattr__(self, "bindings", bindings)
        self.__post_init__()

    def __post_init__(self) -> None:
        if not self.bindings:
            raise CaptureV2Error("action option catalog must not be empty")
        if any(binding.option_id != f"o{index:03d}" for index, binding in enumerate(self.bindings)):
            raise CaptureV2Error("action option IDs must follow received order")
        identities = [binding.source.action_identity for binding in self.bindings]
        if len(identities) != len(set(identities)):
            raise CaptureV2Error("action option source identity is duplicated")
        indexes = [identity[1] for identity in identities if identity is not None]
        if indexes != sorted(indexes):
            raise CaptureV2Error("action options are not in received order")

    def resolve(self, option_id: str) -> StructuralActionOptionCandidateV2:
        matches = [binding for binding in self.bindings if binding.option_id == option_id]
        if len(matches) != 1:
            raise CaptureV2Error("selected option ID is outside the catalog")
        return matches[0]


def make_structural_complete_action_catalog_candidate(
    event: object,
    handles: tuple[object, ...],
    bound_context: object,
    *, authenticated_player_id: str, connection_generation: int,
    phase_identity: str,
) -> StructuralActionCatalogCandidateV2:
    from ai_client.network.types import ServerEvent
    if not isinstance(event, ServerEvent) or type(handles) is not tuple:
        raise CaptureV2Error("complete typed action tuple is required")
    parent = event.payload if event.type == "player.action_state" else event.payload.get("action_state")
    if not isinstance(parent, Mapping) or not isinstance(parent.get("actions"), (list, tuple)) or len(parent["actions"]) != len(handles):
        raise CaptureV2Error("typed actions do not cover the complete received action set")
    bindings = tuple(make_structural_action_option_candidate(event, handle, bound_context,
        authenticated_player_id=authenticated_player_id, connection_generation=connection_generation,
        received_index=index, option_id=f"o{index:03d}", phase_identity=phase_identity)
        for index, handle in enumerate(handles))
    return StructuralActionCatalogCandidateV2(_OBSERVATION_TOKEN, bindings)


def require_single_public_chat_binding(
    catalog: StructuralActionCatalogCandidateV2,
) -> StructuralActionOptionCandidateV2:
    if not isinstance(catalog, StructuralActionCatalogCandidateV2):
        raise CaptureV2Error("complete received action catalog is required")
    bindings = tuple(item for item in catalog.bindings if item.action_kind == "chat")
    if len(bindings) != 1:
        raise CaptureV2Error("exactly one public chat action is required")
    binding = bindings[0]
    if binding.action_kind != "chat" or binding.channel_authority is None:
        raise CaptureV2Error("chat action lacks public authority")
    return binding


@dataclass(frozen=True)
class StructuralDisclosureCandidateV2:
    schema_version: Literal["aiwolf.disclosure-authority.v2"]
    authority_kind: Literal["SELF_ABILITY_REPORT"]
    source: ProvenanceSidecarEntryV2
    evidence_ref: tuple[Literal["ability_result"], int, Literal["AUTHORIZED_PRIVATE"]]
    actor_player_id: str
    event_type: Literal["INSPECT_RESULT", "MEDIUM_RESULT", "INSPECT_DEAD_ROLE_RESULT", "GUARD_SUCCEEDED"]
    target_player_id: str
    result_id: str | None
    revealed_role_id: str | None
    read_visibility: Literal["AUTHORIZED_PRIVATE"]
    disclosure_audience: Literal["PUBLIC"]
    channel_authority_sha256: str
    world_version: int
    fact_revision: int

    def __post_init__(self) -> None:
        if (self.schema_version != "aiwolf.disclosure-authority.v2"
                or self.authority_kind != "SELF_ABILITY_REPORT"
                or self.read_visibility != "AUTHORIZED_PRIVATE"
                or self.disclosure_audience != "PUBLIC"):
            raise CaptureV2Error("invalid disclosure authority")
        if self.source.subject_kind != "HISTORY_RECORD" or self.source.record_identity is None:
            raise CaptureV2Error("disclosure requires an authenticated history sidecar")
        if self.source.record_identity[0] != "ability_result":
            raise CaptureV2Error("disclosure source must be an ability result")
        if self.evidence_ref != ("ability_result", self.source.record_identity[1], "AUTHORIZED_PRIVATE"):
            raise CaptureV2Error("disclosure evidence identity mismatch")
        _text("actor_player_id", self.actor_player_id); _text("target_player_id", self.target_player_id)
        if self.source.source.player_id != self.actor_player_id:
            raise CaptureV2Error("disclosure actor is not the authenticated owner")
        if self.event_type not in {"INSPECT_RESULT", "MEDIUM_RESULT", "INSPECT_DEAD_ROLE_RESULT", "GUARD_SUCCEEDED"}:
            raise CaptureV2Error("unsupported disclosure event")
        if self.event_type in {"INSPECT_RESULT", "MEDIUM_RESULT"}:
            _text("result_id", self.result_id)
            if self.revealed_role_id is not None: raise CaptureV2Error("event cannot reveal a role")
        elif self.event_type == "INSPECT_DEAD_ROLE_RESULT":
            _text("revealed_role_id", self.revealed_role_id)
            if self.result_id is not None: raise CaptureV2Error("event cannot carry result_id")
        elif self.result_id is not None or self.revealed_role_id is not None:
            raise CaptureV2Error("guard result carries only its target")
        _sha("channel_authority_sha256", self.channel_authority_sha256)
        _integer("world_version", self.world_version); _integer("fact_revision", self.fact_revision)


def make_structural_disclosure_candidate(record: object, source: ProvenanceSidecarEntryV2,
                              channel_authority: StructuralPublicChannelCandidateV2, *,
                              actor_player_id: str, world_version: int,
                              fact_revision: int) -> StructuralDisclosureCandidateV2:
    from ai_client.world.model import AbilityResultRecord
    if not isinstance(record, AbilityResultRecord) or source.typed_value_sha256 != canonical_sha256_v2(record):
        raise CaptureV2Error("typed ability record does not match disclosure sidecar")
    if not isinstance(channel_authority, StructuralPublicChannelCandidateV2):
        raise CaptureV2Error("structural public channel candidate is required")
    return StructuralDisclosureCandidateV2("aiwolf.disclosure-authority.v2", "SELF_ABILITY_REPORT", source,
        ("ability_result", record.order, "AUTHORIZED_PRIVATE"), actor_player_id, record.event_type,
        record.target_player_id, record.result_id, record.revealed_role_id, "AUTHORIZED_PRIVATE", "PUBLIC",
        canonical_sha256_v2(channel_authority), world_version, fact_revision)


@dataclass(frozen=True)
class StageSchemaHashV2:
    stage: StageV2
    schema_sha256: str

    def __post_init__(self) -> None:
        _stage(self.stage); _sha("schema_sha256", self.schema_sha256)


@dataclass(frozen=True)
class StageProjectionBindingV2:
    schema_version: Literal["aiwolf.stage-projection-binding.v2"]
    stage: StageV2
    schema_sha256: str
    base_input_sha256: str
    accepted_plan_sha256: str | None
    canonical_input_sha256: str
    messages_sha256: str
    projection_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != "aiwolf.stage-projection-binding.v2": raise CaptureV2Error("invalid projection schema")
        _stage(self.stage)
        for name in ("schema_sha256", "base_input_sha256", "canonical_input_sha256", "messages_sha256", "projection_sha256"):
            _sha(name, getattr(self, name))
        if self.stage == "message": _sha("accepted_plan_sha256", self.accepted_plan_sha256)
        elif self.accepted_plan_sha256 is not None: raise CaptureV2Error("only message projection may bind an accepted plan")
        expected = canonical_sha256_v2({key: value for key, value in asdict(self).items() if key != "projection_sha256"})
        if expected != self.projection_sha256: raise CaptureV2Error("projection hash mismatch")


@dataclass(frozen=True)
class GenerationCaptureV2:
    schema_version: Literal["aiwolf.generation-capture.v2"]
    capture_id: str
    base_capture_id: str
    game_id: str
    player_id: str
    base_revision: int
    trigger: object
    world_version: int
    last_applied_sequence: int
    fact_revision: int
    phase_identity: str
    action_generation: int
    connection_generation: int
    channel_authorities: tuple[StructuralPublicChannelCandidateV2, ...]
    recipient_proof_sha256: str
    catalog_sha256: str
    option_catalog_sha256: str
    input_sha256: str
    profile_bundle_sha256: str
    stage_schema_sha256s: tuple[StageSchemaHashV2, ...]
    state_lease_id: str
    expires_at_monotonic_us: int

    def __post_init__(self) -> None:
        if self.schema_version != "aiwolf.generation-capture.v2": raise CaptureV2Error("invalid capture schema")
        for name in ("capture_id", "base_capture_id", "phase_identity", "recipient_proof_sha256", "catalog_sha256", "option_catalog_sha256", "input_sha256", "profile_bundle_sha256"):
            _sha(name, getattr(self, name))
        _text("game_id", self.game_id); _text("player_id", self.player_id); _text("state_lease_id", self.state_lease_id)
        from ai_client.discussion.model import DiscussionTrigger
        if not isinstance(self.trigger, DiscussionTrigger): raise CaptureV2Error("trigger must be DiscussionTrigger")
        for name in ("base_revision", "world_version", "last_applied_sequence", "fact_revision", "action_generation", "connection_generation", "expires_at_monotonic_us"):
            _integer(name, getattr(self, name))
        if not self.stage_schema_sha256s or tuple(_STAGES.index(item.stage) for item in self.stage_schema_sha256s) != tuple(sorted(_STAGES.index(item.stage) for item in self.stage_schema_sha256s)):
            raise CaptureV2Error("stage schema tuple must be a non-empty ordered subset")
        if len({item.stage for item in self.stage_schema_sha256s}) != len(self.stage_schema_sha256s): raise CaptureV2Error("duplicate stage schema")
        is_chat = self.trigger.kind in {"INITIAL_CHAT", "PEER_CHAT"}
        if (is_chat and len(self.channel_authorities) != 1) or (not is_chat and self.channel_authorities):
            raise CaptureV2Error("channel authority cardinality does not match trigger")
        for authority in self.channel_authorities:
            if (not isinstance(authority, StructuralPublicChannelCandidateV2)
                    or authority.phase_identity != self.phase_identity
                    or authority.action_generation != self.action_generation
                    or authority.connection_generation != self.connection_generation):
                raise CaptureV2Error("channel candidate does not match capture generation")
        if self.recipient_proof_sha256 != recipient_proof_sha256_v2(self.trigger.kind, self.channel_authorities):
            raise CaptureV2Error("recipient proof does not match exact channel candidates")
        expected_stages = {"INITIAL_CHAT": ("chat_plan", "message"), "PEER_CHAT": ("chat_plan", "message"),
                           "PRE_VOTE": ("pre_vote",), "CO_OPPORTUNITY": ("co_opportunity",),
                           "ABILITY": ("ability",)}.get(self.trigger.kind)
        if expected_stages is None or tuple(item.stage for item in self.stage_schema_sha256s) != expected_stages:
            raise CaptureV2Error("stage schemas do not match trigger kind")
        material = {item.name: getattr(self, item.name) for item in fields(self) if item.name != "capture_id"}
        if canonical_sha256_v2(material) != self.capture_id: raise CaptureV2Error("capture hash mismatch")


def make_capture_v2(**fields: object) -> GenerationCaptureV2:
    material = {"schema_version": "aiwolf.generation-capture.v2", **fields}
    return GenerationCaptureV2(capture_id=canonical_sha256_v2(material), **material)


def recipient_proof_sha256_v2(trigger_kind: str,
                              authorities: tuple[StructuralPublicChannelCandidateV2, ...]) -> str:
    _text("trigger_kind", trigger_kind)
    if type(authorities) is not tuple:
        raise CaptureV2Error("channel candidates must be a tuple")
    return canonical_sha256_v2({"schema_version": "aiwolf.recipient-proof-candidate.v2",
                                "trigger_kind": trigger_kind,
                                "channel_authorities": authorities})


@dataclass(frozen=True)
class FreshnessWitnessV2:
    base_revision: int; world_version: int; last_applied_sequence: int; fact_revision: int
    phase_identity: str; action_generation: int; connection_generation: int
    input_sha256: str; catalog_sha256: str; option_catalog_sha256: str
    recipient_proof_sha256: str; profile_bundle_sha256: str
    stage_schema_sha256s: tuple[StageSchemaHashV2, ...]
    lease_owner: str; lease_status: str; expires_at_monotonic_us: int

    def __post_init__(self) -> None:
        for name in ("base_revision", "world_version", "last_applied_sequence", "fact_revision", "action_generation", "connection_generation", "expires_at_monotonic_us"):
            _integer(name, getattr(self, name))
        for name in ("phase_identity", "input_sha256", "catalog_sha256", "option_catalog_sha256", "recipient_proof_sha256", "profile_bundle_sha256"):
            _sha(name, getattr(self, name))
        _text("lease_owner", self.lease_owner); _text("lease_status", self.lease_status)


def compare_freshness_v2(expected: FreshnessWitnessV2, actual: FreshnessWitnessV2, clock: float) -> FreshnessStatusV2:
    lease_fields = ("connection_generation", "recipient_proof_sha256", "lease_owner", "lease_status", "expires_at_monotonic_us")
    if not deadline_is_current(clock, actual.expires_at_monotonic_us) or any(getattr(expected, n) != getattr(actual, n) for n in lease_fields):
        return FreshnessStatusV2.LEASE_INVALID
    if expected != actual:
        return FreshnessStatusV2.STALE
    return FreshnessStatusV2.CURRENT


@dataclass(frozen=True)
class AttemptReservationV2:
    schema_version: Literal["aiwolf.attempt-reservation.v2"]
    stage: StageV2; attempt: int; request_id: str; derived_seed: int; broker_segment_id: str
    call_ordinal: int; projection_sha256: str; provider_request_sha256: str


@dataclass(frozen=True)
class AttemptLedgerEntryV2:
    schema_version: Literal["aiwolf.attempt-ledger-entry.v2"]
    stage: StageV2; attempt: int; request_id: str; derived_seed: int; broker_segment_id: str
    call_ordinal: int; projection_sha256: str; provider_request_sha256: str
    status: str; started_audit_sequence: int; started_record_sha256: str
    terminal_audit_sequence: int | None; terminal_record_sha256: str | None

    def __post_init__(self) -> None:
        _validate_attempt(self)
        _integer("started_audit_sequence", self.started_audit_sequence, 1); _sha("started_record_sha256", self.started_record_sha256)
        if self.status == "STARTED":
            if self.terminal_audit_sequence is not None or self.terminal_record_sha256 is not None: raise CaptureV2Error("STARTED cannot have terminal ack")
        elif self.status in {"ACCEPTED", "SCHEMA_INVALID", "MECHANICAL_INVALID", "BACKEND_FAILED", "TIMEOUT", "CANCELLED", "STALE", "EXHAUSTED"}:
            _integer("terminal_audit_sequence", self.terminal_audit_sequence, 1); _sha("terminal_record_sha256", self.terminal_record_sha256)
        else: raise CaptureV2Error("invalid attempt status")


def _validate_attempt(value: AttemptReservationV2) -> None:
    expected_schema = ("aiwolf.attempt-ledger-entry.v2" if isinstance(value, AttemptLedgerEntryV2)
                       else "aiwolf.attempt-reservation.v2")
    if value.schema_version != expected_schema: raise CaptureV2Error("invalid attempt schema version")
    _stage(value.stage)
    if value.attempt not in (1, 2) or value.call_ordinal not in (1, 2): raise CaptureV2Error("attempt and call ordinal must be 1 or 2")
    _text("request_id", value.request_id); _text("broker_segment_id", value.broker_segment_id)
    if type(value.derived_seed) is not int or not 0 <= value.derived_seed <= 2**32 - 1: raise CaptureV2Error("invalid derived seed")
    _sha("projection_sha256", value.projection_sha256); _sha("provider_request_sha256", value.provider_request_sha256)


def stage_seed_v2(capture_id: str, stage: StageV2, attempt: int) -> int:
    _sha("capture_id", capture_id); _stage(stage)
    if attempt not in (1, 2): raise CaptureV2Error("attempt must be 1 or 2")
    framed = capture_id.encode() + b"\0" + stage.encode() + b"\0" + attempt.to_bytes(4, "big")
    return int.from_bytes(hashlib.sha256(framed).digest()[:4], "big")


@dataclass(frozen=True)
class RecoveryProofV2:
    schema_version: Literal["aiwolf.state-lease-recovery-proof.v2"]
    mode: Literal["ALL_SEGMENT_TERMINAL_ACKS", "OWNED_BROKER_SHUTDOWN_CLEAN"]
    owner_invocation_id: str
    broker_lease_ids: tuple[str, ...]
    terminal_ack_record_sha256s: tuple[str, ...]
    broker_config_fingerprint: str
    broker_shutdown_clean: bool
    delivery_terminal_record_sha256: str | None
    recovery_audit_sequence: int
    recovery_audit_record_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != "aiwolf.state-lease-recovery-proof.v2": raise CaptureV2Error("invalid recovery proof schema")
        _text("owner_invocation_id", self.owner_invocation_id)
        if not self.broker_lease_ids or len(self.broker_lease_ids) > 2 or len(set(self.broker_lease_ids)) != len(self.broker_lease_ids):
            raise CaptureV2Error("invalid recovery segment tuple")
        for value in self.broker_lease_ids: _text("broker_lease_id", value)
        _sha("broker_config_fingerprint", self.broker_config_fingerprint)
        _integer("recovery_audit_sequence", self.recovery_audit_sequence, 1); _sha("recovery_audit_record_sha256", self.recovery_audit_record_sha256)
        if self.delivery_terminal_record_sha256 is not None: _sha("delivery_terminal_record_sha256", self.delivery_terminal_record_sha256)
        if self.mode == "ALL_SEGMENT_TERMINAL_ACKS":
            if self.broker_shutdown_clean or len(self.terminal_ack_record_sha256s) != len(self.broker_lease_ids):
                raise CaptureV2Error("terminal ack recovery proof is incomplete")
            for value in self.terminal_ack_record_sha256s: _sha("terminal_ack_record_sha256", value)
        elif self.mode == "OWNED_BROKER_SHUTDOWN_CLEAN":
            if not self.broker_shutdown_clean or self.terminal_ack_record_sha256s:
                raise CaptureV2Error("shutdown recovery proof is inconsistent")
        else: raise CaptureV2Error("invalid recovery proof mode")


@dataclass(frozen=True, init=False)
class StructuralRecoveryObservationCandidateV2:
    proof: RecoveryProofV2
    def __init__(self, token: object, proof: RecoveryProofV2) -> None:
        if token is not _OBSERVATION_TOKEN: raise CaptureV2Error("structural recovery candidate is opaque")
        object.__setattr__(self, "proof", proof)


def structural_recovery_observation_candidate(
    proof: RecoveryProofV2,
    broker_observations: tuple[StructuralBrokerObservationCandidateV2, ...],
) -> StructuralRecoveryObservationCandidateV2:
    if not isinstance(proof, RecoveryProofV2): raise CaptureV2Error("recovery proof is structural only")
    if proof.mode == "ALL_SEGMENT_TERMINAL_ACKS":
        if (tuple(item.broker_segment_id for item in broker_observations) != proof.broker_lease_ids
                or tuple(item.record_sha256 for item in broker_observations) != proof.terminal_ack_record_sha256s
                or any(item.owner_invocation_id != proof.owner_invocation_id for item in broker_observations)
                or any(item.operation not in {"release", "cancel", "cancel_successor", "ABANDON"}
                       or item.status not in {"RELEASED", "CANCELLED", "ABANDONED"}
                       for item in broker_observations)):
            raise CaptureV2Error("recovery broker observations do not match proof")
    else:
        if (len(broker_observations) != 1 or broker_observations[0].operation != "broker_shutdown"
                or broker_observations[0].status != "CLEAN"
                or broker_observations[0].owner_invocation_id != proof.owner_invocation_id):
            raise CaptureV2Error("owned clean broker shutdown observation is required")
    return StructuralRecoveryObservationCandidateV2(_OBSERVATION_TOKEN, proof)


@dataclass(frozen=True)
class StateGenerationLeaseV2:
    schema_version: Literal["aiwolf.state-generation-lease.v2"]
    state_lease_id: str; owner_invocation_id: str; lease_revision: int; capture_id: str | None; status: str
    base_revision: int; world_version: int; last_applied_sequence: int; fact_revision: int; phase_identity: str
    action_generation: int; connection_generation: int; input_sha256: str | None; catalog_sha256: str | None
    option_catalog_sha256: str | None; recipient_proof_sha256: str | None; profile_bundle_sha256: str | None
    stage_schema_sha256s: tuple[StageSchemaHashV2, ...]; expires_at_monotonic_us: int
    broker_lease_ids: tuple[str, ...]; accepted_plan_sha256: str | None
    stage_projections: tuple[StageProjectionBindingV2, ...]; reserved_attempt: AttemptReservationV2 | None
    attempt_ledger: tuple[AttemptLedgerEntryV2, ...]; recovery_proof_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != "aiwolf.state-generation-lease.v2": raise CaptureV2Error("invalid state lease schema")
        _text("state_lease_id", self.state_lease_id); _text("owner_invocation_id", self.owner_invocation_id)
        _integer("lease_revision", self.lease_revision)
        if not self.broker_lease_ids or len(self.broker_lease_ids) > 2 or len(set(self.broker_lease_ids)) != len(self.broker_lease_ids):
            raise CaptureV2Error("state lease broker segments are invalid")
        if self.broker_lease_ids[0] != self.state_lease_id or self.owner_invocation_id != self.state_lease_id:
            raise CaptureV2Error("initial broker, owner, and state lease IDs must match")
        if self.status == "PREPARING":
            if (self.capture_id is not None or any(value is not None for value in (
                    self.input_sha256, self.catalog_sha256, self.option_catalog_sha256,
                    self.recipient_proof_sha256, self.profile_bundle_sha256,
                    self.accepted_plan_sha256, self.reserved_attempt, self.recovery_proof_sha256))
                    or self.stage_schema_sha256s or self.stage_projections or self.attempt_ledger
                    or len(self.broker_lease_ids) != 1 or self.lease_revision != 0):
                raise CaptureV2Error("PREPARING lease shape is not exact")
        elif self.status not in {"RESERVED", "ACTIVE", "STAGED", "COMMITTED", "INVALIDATED", "RECOVERY_PENDING", "RELEASED"}:
            raise CaptureV2Error("invalid state lease status")
        elif not (self.status == "INVALIDATED" and self.capture_id is None):
            for name in ("capture_id", "input_sha256", "catalog_sha256", "option_catalog_sha256",
                         "recipient_proof_sha256", "profile_bundle_sha256"):
                _sha(name, getattr(self, name))
            if not self.stage_schema_sha256s: raise CaptureV2Error("finalized lease requires stage schemas")
        if len({projection.stage for projection in self.stage_projections}) != len(self.stage_projections):
            raise CaptureV2Error("duplicate stage projection")
        if self.reserved_attempt is not None: _validate_attempt(self.reserved_attempt)
        sequences: list[int] = []
        for entry in self.attempt_ledger:
            if not isinstance(entry, AttemptLedgerEntryV2): raise CaptureV2Error("invalid attempt ledger entry")
            sequences.append(entry.started_audit_sequence)
            if entry.terminal_audit_sequence is not None: sequences.append(entry.terminal_audit_sequence)
        if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
            raise CaptureV2Error("attempt audit sequences must strictly increase")
        if self.recovery_proof_sha256 is not None:
            _sha("recovery_proof_sha256", self.recovery_proof_sha256)
            if self.status != "RELEASED":
                raise CaptureV2Error("recovery proof is only valid on RELEASED")
        if self.status == "RESERVED" and (self.accepted_plan_sha256 is not None
                or self.stage_projections or self.reserved_attempt is not None or self.attempt_ledger
                or self.recovery_proof_sha256 is not None):
            raise CaptureV2Error("RESERVED lease shape is not exact")
        if self.status in {"STAGED", "COMMITTED"}:
            required = {item.stage for item in self.stage_schema_sha256s}
            accepted = {item.stage for item in self.attempt_ledger if item.status == "ACCEPTED"}
            if (self.reserved_attempt is not None or any(item.status == "STARTED" for item in self.attempt_ledger)
                    or required != accepted):
                raise CaptureV2Error("staged lease requires exactly accepted terminal stages")
        chat_accepted = any(item.stage == "chat_plan" and item.status == "ACCEPTED" for item in self.attempt_ledger)
        if (self.accepted_plan_sha256 is None) != (not chat_accepted):
            raise CaptureV2Error("accepted plan must correspond to accepted chat_plan")
        if self.accepted_plan_sha256 is not None:
            _sha("accepted_plan_sha256", self.accepted_plan_sha256)


class StructuralStateLeaseSimulatorV2:
    def __init__(self) -> None:
        self._lease: StateGenerationLeaseV2 | None = None
        self._recovery_origin: str | None = None
        self._active_segment: str | None = None
        self._completed_segments: set[str] = set()
    @property
    def lease(self) -> StateGenerationLeaseV2 | None: return self._lease

    def prepare(self, owner: str, witness: FreshnessWitnessV2) -> StateGenerationLeaseV2:
        if self._lease is not None and self._lease.status != "RELEASED": raise CaptureV2Error("an unreleased state lease already exists")
        _text("owner", owner)
        self._lease = StateGenerationLeaseV2("aiwolf.state-generation-lease.v2", owner, owner, 0, None,
            "PREPARING", witness.base_revision, witness.world_version, witness.last_applied_sequence,
            witness.fact_revision, witness.phase_identity, witness.action_generation,
            witness.connection_generation, None, None, None, None, None, (),
            witness.expires_at_monotonic_us, (owner,), None, (), None, ())
        return self._lease

    def finalize_capture(self, expected_revision: int, capture: GenerationCaptureV2, witness: FreshnessWitnessV2, clock: float) -> StateGenerationLeaseV2:
        lease = self._expect(expected_revision, "PREPARING")
        if compare_freshness_v2(_witness_for(lease, witness), witness, clock) is not FreshnessStatusV2.CURRENT:
            self._lease = replace(lease, lease_revision=lease.lease_revision + 1, status="INVALIDATED"); return self._lease
        if capture.state_lease_id != lease.state_lease_id or capture.base_revision != lease.base_revision:
            raise CaptureV2Error("capture does not belong to preparing lease")
        capture_freshness = (capture.world_version, capture.last_applied_sequence, capture.fact_revision,
                             capture.phase_identity, capture.action_generation, capture.connection_generation,
                             capture.expires_at_monotonic_us)
        lease_freshness = (lease.world_version, lease.last_applied_sequence, lease.fact_revision,
                           lease.phase_identity, lease.action_generation, lease.connection_generation,
                           lease.expires_at_monotonic_us)
        if capture_freshness != lease_freshness:
            raise CaptureV2Error("capture freshness differs from PREPARING lease")
        capture_hashes = (capture.input_sha256, capture.catalog_sha256, capture.option_catalog_sha256,
                          capture.recipient_proof_sha256, capture.profile_bundle_sha256,
                          capture.stage_schema_sha256s)
        witness_hashes = (witness.input_sha256, witness.catalog_sha256, witness.option_catalog_sha256,
                          witness.recipient_proof_sha256, witness.profile_bundle_sha256,
                          witness.stage_schema_sha256s)
        if capture_hashes != witness_hashes:
            raise CaptureV2Error("capture hash DAG differs from final freshness witness")
        self._lease = replace(lease, lease_revision=lease.lease_revision + 1, status="RESERVED", capture_id=capture.capture_id,
            input_sha256=capture.input_sha256, catalog_sha256=capture.catalog_sha256,
            option_catalog_sha256=capture.option_catalog_sha256, recipient_proof_sha256=capture.recipient_proof_sha256,
            profile_bundle_sha256=capture.profile_bundle_sha256, stage_schema_sha256s=capture.stage_schema_sha256s)
        return self._lease

    def transition(self, expected_revision: int, target: str) -> StateGenerationLeaseV2:
        lease = self._expect(expected_revision)
        allowed = {"PREPARING": {"INVALIDATED"}, "RESERVED": {"INVALIDATED"},
                   "ACTIVE": {"STAGED", "INVALIDATED"}, "STAGED": {"COMMITTED", "INVALIDATED"},
                   "COMMITTED": {"RECOVERY_PENDING"}, "INVALIDATED": {"RECOVERY_PENDING"}}
        if target not in allowed.get(lease.status, set()): raise CaptureV2Error("closed lease transition rejected")
        if lease.status == "ACTIVE" and target == "STAGED":
            terminal_by_stage = {entry.stage: entry.status for entry in lease.attempt_ledger}
            required_stages = {item.stage for item in lease.stage_schema_sha256s}
            if (self._active_segment is not None or lease.reserved_attempt is not None
                    or any(entry.status == "STARTED" for entry in lease.attempt_ledger)
                    or not required_stages or any(terminal_by_stage.get(stage) != "ACCEPTED" for stage in required_stages)):
                raise CaptureV2Error("all stages and broker activity must be terminal before staging")
        if target == "RECOVERY_PENDING": self._recovery_origin = lease.status
        self._lease = replace(lease, lease_revision=lease.lease_revision + 1, status=target); return self._lease

    def activate_with_observation(self, expected_revision: int,
                                  observation: StructuralBrokerObservationCandidateV2) -> StateGenerationLeaseV2:
        lease = self._expect(expected_revision, "RESERVED")
        if (not isinstance(observation, StructuralBrokerObservationCandidateV2)
                or observation.operation != "claim" or observation.status != "GRANTED"
                or observation.owner_invocation_id != lease.owner_invocation_id
                or observation.broker_segment_id != lease.broker_lease_ids[0]):
            raise CaptureV2Error("structural broker GRANTED candidate is required")
        self._active_segment = observation.broker_segment_id
        self._lease = replace(lease, lease_revision=lease.lease_revision + 1, status="ACTIVE")
        return self._lease

    def register_segment(self, expected_revision: int, observation: StructuralBrokerObservationCandidateV2) -> StateGenerationLeaseV2:
        lease = self._expect(expected_revision, "ACTIVE")
        if (not isinstance(observation, StructuralBrokerObservationCandidateV2)
                or observation.operation != "reserve_successor" or observation.status != "OFFERED"
                or observation.owner_invocation_id != lease.owner_invocation_id):
            raise CaptureV2Error("structural successor candidate is required")
        segment_id = observation.broker_segment_id
        if segment_id in lease.broker_lease_ids or len(lease.broker_lease_ids) >= 2: raise CaptureV2Error("broker segment limit or reuse")
        if self._active_segment is not None or any(entry.status == "STARTED" for entry in lease.attempt_ledger):
            raise CaptureV2Error("cannot register successor while a segment or attempt is active")
        prior_count = sum(entry.broker_segment_id == lease.broker_lease_ids[-1] for entry in lease.attempt_ledger)
        if prior_count != 2 or lease.broker_lease_ids[-1] not in self._completed_segments:
            raise CaptureV2Error("successor requires a terminal two-call prior segment")
        self._lease = replace(lease, lease_revision=lease.lease_revision + 1, broker_lease_ids=(*lease.broker_lease_ids, segment_id)); return self._lease

    def activate_segment(self, expected_revision: int, observation: StructuralBrokerObservationCandidateV2) -> StateGenerationLeaseV2:
        lease = self._expect(expected_revision, "ACTIVE")
        if (not isinstance(observation, StructuralBrokerObservationCandidateV2)
                or observation.operation != "claim" or observation.status != "GRANTED"
                or observation.owner_invocation_id != lease.owner_invocation_id):
            raise CaptureV2Error("structural successor GRANTED candidate is required")
        segment_id = observation.broker_segment_id
        if self._active_segment is not None or segment_id not in lease.broker_lease_ids or segment_id in self._completed_segments:
            raise CaptureV2Error("broker segment cannot be activated")
        self._active_segment = segment_id
        self._lease = replace(lease, lease_revision=lease.lease_revision + 1)
        return self._lease

    def complete_active_segment(self, expected_revision: int, segment_id: str) -> StateGenerationLeaseV2:
        lease = self._expect(expected_revision, "ACTIVE")
        if self._active_segment != segment_id or any(entry.status == "STARTED" for entry in lease.attempt_ledger):
            raise CaptureV2Error("active broker segment is not terminal")
        self._active_segment = None; self._completed_segments.add(segment_id)
        self._lease = replace(lease, lease_revision=lease.lease_revision + 1)
        return self._lease

    def register_projection(self, expected_revision: int, projection: StageProjectionBindingV2) -> StateGenerationLeaseV2:
        lease = self._expect(expected_revision, "ACTIVE")
        prior = next((p for p in lease.stage_projections if p.stage == projection.stage), None)
        if prior is not None:
            if prior != projection: raise CaptureV2Error("stage projection drift")
            return lease
        schema = next((s.schema_sha256 for s in lease.stage_schema_sha256s if s.stage == projection.stage), None)
        if schema != projection.schema_sha256 or projection.base_input_sha256 != lease.input_sha256: raise CaptureV2Error("projection is outside capture")
        if projection.stage == "message" and projection.accepted_plan_sha256 != lease.accepted_plan_sha256: raise CaptureV2Error("message projection plan drift")
        self._lease = replace(lease, lease_revision=lease.lease_revision + 1, stage_projections=(*lease.stage_projections, projection)); return self._lease

    def reserve_attempt(self, expected_revision: int, reservation: AttemptReservationV2) -> StateGenerationLeaseV2:
        lease = self._expect(expected_revision, "ACTIVE"); _validate_attempt(reservation)
        if lease.reserved_attempt is not None or any(e.status == "STARTED" for e in lease.attempt_ledger): raise CaptureV2Error("previous attempt lacks terminal ack")
        if reservation.broker_segment_id not in lease.broker_lease_ids: raise CaptureV2Error("unregistered broker segment")
        if reservation.broker_segment_id != self._active_segment:
            raise CaptureV2Error("broker segment is not claimed and active")
        if lease.attempt_ledger and lease.attempt_ledger[-1].status in {"BACKEND_FAILED", "TIMEOUT", "CANCELLED", "STALE", "EXHAUSTED"}:
            raise CaptureV2Error("terminal uncertainty or exhaustion forbids another attempt")
        segment_entries = [e for e in lease.attempt_ledger if e.broker_segment_id == reservation.broker_segment_id]
        if reservation.call_ordinal != len(segment_entries) + 1: raise CaptureV2Error("invalid segment call ordinal")
        if any((e.stage, e.attempt) == (reservation.stage, reservation.attempt) or e.request_id == reservation.request_id or e.derived_seed == reservation.derived_seed for e in lease.attempt_ledger):
            raise CaptureV2Error("attempt identity reuse")
        same_stage = [e for e in lease.attempt_ledger if e.stage == reservation.stage]
        if reservation.attempt != len(same_stage) + 1:
            raise CaptureV2Error("stage attempts must form the 1,2 prefix")
        if lease.attempt_ledger and _STAGES.index(reservation.stage) < _STAGES.index(lease.attempt_ledger[-1].stage):
            raise CaptureV2Error("attempt stages must follow canonical order")
        if lease.attempt_ledger:
            previous = lease.attempt_ledger[-1]
            if reservation.stage == previous.stage and previous.status == "ACCEPTED":
                raise CaptureV2Error("accepted stage cannot be retried")
            if _STAGES.index(reservation.stage) > _STAGES.index(previous.stage) and previous.status != "ACCEPTED":
                raise CaptureV2Error("next stage requires accepted prior stage")
        projection = next((p for p in lease.stage_projections if p.stage == reservation.stage), None)
        if projection is None or projection.projection_sha256 != reservation.projection_sha256: raise CaptureV2Error("attempt projection mismatch")
        expected_id = f"phase6-v2:{lease.capture_id}:{reservation.stage}:{reservation.attempt}"
        if reservation.request_id != expected_id: raise CaptureV2Error("request ID mismatch")
        if reservation.derived_seed != stage_seed_v2(lease.capture_id, reservation.stage, reservation.attempt):
            raise CaptureV2Error("derived seed mismatch")
        self._lease = replace(lease, lease_revision=lease.lease_revision + 1, reserved_attempt=reservation); return self._lease

    def start_attempt(self, expected_revision: int, observation: StructuralAttemptAuditCandidateV2) -> StateGenerationLeaseV2:
        lease = self._expect(expected_revision, "ACTIVE")
        if lease.reserved_attempt is None: raise CaptureV2Error("no reserved attempt")
        if (not isinstance(observation, StructuralAttemptAuditCandidateV2) or observation.status != "STARTED"
                or observation.reservation != lease.reserved_attempt):
            raise CaptureV2Error("STARTED audit does not match reservation")
        if lease.attempt_ledger:
            last_sequence = lease.attempt_ledger[-1].terminal_audit_sequence
            if last_sequence is None or observation.audit_sequence <= last_sequence:
                raise CaptureV2Error("STARTED audit sequence is not monotonic")
        fields = asdict(lease.reserved_attempt); fields["schema_version"] = "aiwolf.attempt-ledger-entry.v2"
        entry = AttemptLedgerEntryV2(**fields, status="STARTED", started_audit_sequence=observation.audit_sequence,
                                     started_record_sha256=observation.record_sha256, terminal_audit_sequence=None,
                                     terminal_record_sha256=None)
        self._lease = replace(lease, lease_revision=lease.lease_revision + 1, reserved_attempt=None, attempt_ledger=(*lease.attempt_ledger, entry)); return self._lease

    def finish_attempt(self, expected_revision: int, observation: StructuralAttemptAuditCandidateV2,
                       accepted_plan_sha256: str | None = None) -> StateGenerationLeaseV2:
        lease = self._expect(expected_revision, "ACTIVE")
        if not lease.attempt_ledger or lease.attempt_ledger[-1].status != "STARTED": raise CaptureV2Error("no started attempt")
        old = lease.attempt_ledger[-1]
        reservation = AttemptReservationV2("aiwolf.attempt-reservation.v2", old.stage, old.attempt, old.request_id,
            old.derived_seed, old.broker_segment_id, old.call_ordinal, old.projection_sha256, old.provider_request_sha256)
        if (not isinstance(observation, StructuralAttemptAuditCandidateV2) or observation.status == "STARTED"
                or observation.reservation != reservation or observation.audit_sequence <= old.started_audit_sequence):
            raise CaptureV2Error("terminal audit does not match STARTED attempt")
        status = observation.status
        entry = replace(old, status=status, terminal_audit_sequence=observation.audit_sequence,
                        terminal_record_sha256=observation.record_sha256)
        plan = lease.accepted_plan_sha256
        if old.stage == "chat_plan" and status == "ACCEPTED":
            _sha("accepted_plan_sha256", accepted_plan_sha256)
            if plan is not None: raise CaptureV2Error("accepted plan already fixed")
            plan = accepted_plan_sha256
        elif accepted_plan_sha256 is not None: raise CaptureV2Error("plan hash only accompanies accepted chat plan")
        self._lease = replace(lease, lease_revision=lease.lease_revision + 1,
                              attempt_ledger=(*lease.attempt_ledger[:-1], entry), accepted_plan_sha256=plan)
        return self._lease

    def recover(self, expected_revision: int, observation: StructuralRecoveryObservationCandidateV2,
                expected_broker_config_fingerprint: str) -> StateGenerationLeaseV2:
        lease = self._expect(expected_revision, "RECOVERY_PENDING")
        if not isinstance(observation, StructuralRecoveryObservationCandidateV2):
            raise CaptureV2Error("structural recovery candidate is required")
        proof = observation.proof
        if (proof.owner_invocation_id != lease.owner_invocation_id
                or proof.broker_lease_ids != lease.broker_lease_ids
                or proof.broker_config_fingerprint != expected_broker_config_fingerprint
                or (self._recovery_origin == "COMMITTED" and proof.delivery_terminal_record_sha256 is None)):
            raise CaptureV2Error("recovery proof does not match state lease")
        proof_sha256 = canonical_sha256_v2(proof)
        self._active_segment = None; self._completed_segments.update(lease.broker_lease_ids)
        self._lease = replace(lease, lease_revision=lease.lease_revision + 1, status="RELEASED", recovery_proof_sha256=proof_sha256); return self._lease

    def release_with_terminal_acks(self, expected_revision: int,
                                   terminal_observations: tuple[StructuralBrokerObservationCandidateV2, ...],
                                   delivery_observation: StructuralDeliveryObservationCandidateV2 | None) -> StateGenerationLeaseV2:
        lease = self._expect(expected_revision)
        if lease.status not in {"COMMITTED", "INVALIDATED"}:
            raise CaptureV2Error("only committed or invalidated lease can release")
        if len(terminal_observations) != len(lease.broker_lease_ids):
            raise CaptureV2Error("every broker segment requires a terminal ack")
        for segment_id, observation in zip(lease.broker_lease_ids, terminal_observations):
            if (not isinstance(observation, StructuralBrokerObservationCandidateV2)
                    or observation.owner_invocation_id != lease.owner_invocation_id
                    or observation.broker_segment_id != segment_id
                    or observation.operation not in {"release", "cancel", "cancel_successor", "ABANDON"}
                    or observation.status not in {"RELEASED", "CANCELLED", "ABANDONED"}):
                raise CaptureV2Error("broker terminal observation mismatch")
        if lease.status == "COMMITTED":
            if (not isinstance(delivery_observation, StructuralDeliveryObservationCandidateV2)
                    or delivery_observation.capture_id != lease.capture_id
                    or delivery_observation.status not in {"DELIVERED", "FAILED"}):
                raise CaptureV2Error("structural delivery terminal candidate is required")
        elif delivery_observation is not None: raise CaptureV2Error("invalidated lease has no delivery terminal")
        self._active_segment = None; self._completed_segments.update(lease.broker_lease_ids)
        self._lease = replace(lease, lease_revision=lease.lease_revision + 1, status="RELEASED")
        return self._lease

    def _expect(self, revision: int, status: str | None = None) -> StateGenerationLeaseV2:
        if self._lease is None or self._lease.lease_revision != revision: raise CaptureV2Error("lease revision CAS failed")
        if status is not None and self._lease.status != status: raise CaptureV2Error("lease status mismatch")
        return self._lease


def _witness_for(lease: StateGenerationLeaseV2, supplied: FreshnessWitnessV2) -> FreshnessWitnessV2:
    return replace(supplied, base_revision=lease.base_revision, world_version=lease.world_version,
                   last_applied_sequence=lease.last_applied_sequence, fact_revision=lease.fact_revision,
                   phase_identity=lease.phase_identity, action_generation=lease.action_generation,
                   connection_generation=lease.connection_generation, lease_owner=lease.owner_invocation_id,
                   lease_status=lease.status, expires_at_monotonic_us=lease.expires_at_monotonic_us)
