"""Opaque, one-shot observations for committed inbound events."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from typing import Literal, Mapping

from .types import (
    AbilityAction, Action, ChatAction, CoDeclareAction, CoReportAction,
    LifecycleChanged, ServerEvent, VoteAction,
)


SequenceModeV2 = Literal["CONTIGUOUS", "RESUME_REPLAY", "SYNC_BARRIER"]
_ISSUER = object()


def _canonical(value: object) -> bytes:
    def plain(item: object) -> object:
        if isinstance(item, Mapping):
            return {str(key): plain(child) for key, child in item.items()}
        if isinstance(item, (tuple, list)): return [plain(child) for child in item]
        if item is None or type(item) in {str, int, float, bool}: return item
        raise TypeError("source material is not closed JSON")
    return json.dumps(plain(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


@dataclass(frozen=True, init=False)
class PrivateSourceSliceV2:
    event_object: ServerEvent
    player_id: str
    connection_generation: int
    source_path: str
    canonical_source_bytes: bytes
    source_sha256: str

    def __init__(self, token: object, event: ServerEvent, player_id: str,
                 generation: int, path: str, value: object) -> None:
        if token is not _ISSUER: raise TypeError("private source slice is opaque")
        data = _canonical(value)
        object.__setattr__(self, "event_object", event)
        object.__setattr__(self, "player_id", player_id)
        object.__setattr__(self, "connection_generation", generation)
        object.__setattr__(self, "source_path", path)
        object.__setattr__(self, "canonical_source_bytes", data)
        object.__setattr__(self, "source_sha256", hashlib.sha256(data).hexdigest())


@dataclass(frozen=True, init=False)
class NetworkCommittedInboundV2:
    schema_version: Literal["aiwolf.network-committed-inbound.v2"]
    event_object: ServerEvent
    game_id: str
    player_id: str
    protocol_version: str
    connection_generation: int
    sequence_mode: SequenceModeV2
    previous_committed_seq: int
    committed_seq: int
    credential_checkpoint_seq: int
    client_snapshot_generation_after: int
    action_generation_after: int
    typed_actions_after: tuple[Action, ...]
    prepared_source_slices: tuple[PrivateSourceSliceV2, ...]

    def __init__(self, token: object, **values: object) -> None:
        if token is not _ISSUER: raise TypeError("committed inbound observation is opaque")
        for name, value in values.items(): object.__setattr__(self, name, value)


@dataclass(frozen=True)
class SyncCommitIdentityV2:
    server_event_object: ServerEvent
    event_id: str
    seq: int
    connection_generation: int


@dataclass(frozen=True, init=False)
class NetworkLifecycleObservationV2:
    lifecycle_event_object: LifecycleChanged
    connection_generation: int
    previous: object
    current: object
    ready_after_sync_identity: SyncCommitIdentityV2 | None

    def __init__(self, token: object, event: LifecycleChanged, generation: int,
                 identity: SyncCommitIdentityV2 | None) -> None:
        if token is not _ISSUER: raise TypeError("lifecycle observation is opaque")
        object.__setattr__(self, "lifecycle_event_object", event)
        object.__setattr__(self, "connection_generation", generation)
        object.__setattr__(self, "previous", event.previous)
        object.__setattr__(self, "current", event.current)
        object.__setattr__(self, "ready_after_sync_identity", identity)


class InboundAuthorityClaimCapabilityV2:
    __slots__ = ("_client", "_authority_owner_registration_v2")
    def __init__(self, token: object, client: object) -> None:
        if token is not _ISSUER: raise TypeError("claim capability is opaque")
        self._client = client
        self._authority_owner_registration_v2 = None


def _issue_capability(client: object) -> InboundAuthorityClaimCapabilityV2:
    return InboundAuthorityClaimCapabilityV2(_ISSUER, client)


def _prepare_observation(event: ServerEvent, *, player_id: str, generation: int,
                         mode: SequenceModeV2, previous_seq: int,
                         snapshot_generation: int, action_generation: int,
                         actions: tuple[Action, ...]) -> NetworkCommittedInboundV2:
    if mode not in {"CONTIGUOUS", "RESUME_REPLAY", "SYNC_BARRIER"}: raise ValueError("invalid sequence mode")
    slices: list[PrivateSourceSliceV2] = []
    if event.type == "player.action_state":
        slices.append(PrivateSourceSliceV2(_ISSUER, event, player_id, generation, "/payload", event.payload))
        for index, raw in enumerate(event.payload["actions"]):
            slices.append(PrivateSourceSliceV2(_ISSUER, event, player_id, generation, f"/payload/actions/{index}", raw))
    elif event.type == "game.state_sync":
        action_state = event.payload["action_state"]
        slices.append(PrivateSourceSliceV2(_ISSUER, event, player_id, generation, "/payload/action_state", action_state))
        for index, raw in enumerate(action_state["actions"]):
            slices.append(PrivateSourceSliceV2(_ISSUER, event, player_id, generation, f"/payload/action_state/actions/{index}", raw))
        for index, raw in enumerate(event.payload["history"]):
            slices.append(PrivateSourceSliceV2(_ISSUER, event, player_id, generation, f"/payload/history/{index}", raw))
    elif event.type == "game.event":
        slices.append(PrivateSourceSliceV2(_ISSUER, event, player_id, generation, "/payload", event.payload))
    if event.type in {"player.action_state", "game.state_sync"}:
        parent = event.payload if event.type == "player.action_state" else event.payload["action_state"]
        raw_actions = parent["actions"]
        if len(raw_actions) != len(actions): raise ValueError("typed action cardinality mismatch")
        for raw, typed in zip(raw_actions, actions):
            expected: dict[str, object] = {"type": typed.type}
            if isinstance(typed, ChatAction): expected["channel"] = typed.channel
            elif isinstance(typed, VoteAction): expected.update(valid_targets=list(typed.valid_targets), target_count=typed.target_count, allows_abstain=typed.allows_abstain)
            elif isinstance(typed, AbilityAction): expected.update(ability_id=typed.ability_id, description=typed.description, valid_targets=list(typed.valid_targets), target_count=typed.target_count, uses_remaining=typed.uses_remaining)
            elif isinstance(typed, CoDeclareAction): expected["claimed_role_ids"] = list(typed.claimed_role_ids)
            elif not isinstance(typed, CoReportAction): raise ValueError("unknown typed action")
            if (_canonical(raw) != _canonical(expected)
                    or typed.connection_generation != generation
                    or typed.action_generation != action_generation
                    or typed.day != parent["day"] or typed.phase != parent["phase"]):
                raise ValueError("typed action differs from exact committed source")
    return NetworkCommittedInboundV2(_ISSUER, schema_version="aiwolf.network-committed-inbound.v2",
        event_object=event, game_id=event.game_id,
        player_id=player_id, protocol_version=event.protocol_version,
        connection_generation=generation, sequence_mode=mode,
        previous_committed_seq=previous_seq, committed_seq=event.seq,
        credential_checkpoint_seq=event.seq,
        client_snapshot_generation_after=snapshot_generation,
        action_generation_after=action_generation, typed_actions_after=actions,
        prepared_source_slices=tuple(slices))


def _issue_lifecycle(event: LifecycleChanged, generation: int,
                     identity: SyncCommitIdentityV2 | None) -> NetworkLifecycleObservationV2:
    return NetworkLifecycleObservationV2(_ISSUER, event, generation, identity)
