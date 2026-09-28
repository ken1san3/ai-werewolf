"""Runtime inbound authority state owned by one WorldState composition."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import copy
import hashlib
import json
from typing import Literal, Mapping

from ai_client.network.inbound_authority_v2 import (
    NetworkCommittedInboundV2, NetworkLifecycleObservationV2, PrivateSourceSliceV2,
)
from ai_client.network.types import (
    AbilityAction, ChatAction, CoDeclareAction, CoReportAction, VoteAction,
)
from .model import AbilityResultRecord, PhaseTransitionRecord


_TOKEN = object()


def _bytes(value: object) -> bytes:
    def plain(item: object) -> object:
        if hasattr(item, "__dataclass_fields__"):
            return {key: plain(child) for key, child in asdict(item).items()}
        if isinstance(item, Mapping): return {str(k): plain(v) for k, v in item.items()}
        if isinstance(item, (tuple, list)): return [plain(v) for v in item]
        if item is None or type(item) in {str, int, float, bool}: return item
        raise TypeError("authority material is not closed JSON")
    return json.dumps(plain(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


def _sha(value: object) -> str: return hashlib.sha256(_bytes(value)).hexdigest()


@dataclass(frozen=True, init=False)
class VerifiedInboundRefV2:
    schema_version: Literal["aiwolf.verified-inbound-ref.v2"]
    game_id: str; protocol_version: str
    event_id: str; seq: int; message_type: str; player_id: str
    connection_generation: int; source_path: str; source_sha256: str
    def __init__(self, token: object, source: PrivateSourceSliceV2) -> None:
        if token is not _TOKEN: raise TypeError("verified inbound ref is runtime opaque")
        event = source.event_object
        for name in self.__dataclass_fields__:
            value = {"schema_version": "aiwolf.verified-inbound-ref.v2",
                     "game_id": event.game_id, "protocol_version": event.protocol_version,
                     "event_id": event.event_id, "seq": event.seq, "message_type": event.type,
                     "player_id": source.player_id, "connection_generation": source.connection_generation,
                     "source_path": source.source_path, "source_sha256": source.source_sha256}[name]
            object.__setattr__(self, name, value)


@dataclass(frozen=True, init=False)
class VerifiedProvenanceSidecarEntryV2:
    schema_version: Literal["aiwolf.verified-provenance-sidecar-entry.v2"]
    subject_kind: Literal["ACTION_HANDLE", "ABILITY_RESULT"]
    identity: tuple[object, ...]
    typed_value_sha256: str
    source: VerifiedInboundRefV2
    parent_source: VerifiedInboundRefV2 | None
    phase_source: VerifiedInboundRefV2 | None
    day: int; phase: str
    action_generation: int | None
    phase_attribution_mode: Literal["LIVE_REDUCER_PHASE", "SYNC_REPLAY_PHASE"] | None
    reducer_phase_witness_sha256: str | None
    state_sync_history_index: int | None
    preceding_phase_entry_index: int | None
    world_version_before: int | None
    last_applied_sequence_before: int | None
    def __init__(self, token: object, **values: object) -> None:
        if token is not _TOKEN: raise TypeError("verified sidecar is runtime opaque")
        for name, value in values.items(): object.__setattr__(self, name, value)


@dataclass(frozen=True)
class InboundAuthoritySnapshotV2:
    schema_version: str
    game_id: str | None
    player_id: str | None
    protocol_version: str | None
    connection_generation: int | None
    last_committed_server_seq: int
    world_version: int
    authority_revision: int
    readiness_status: Literal["EMPTY", "PENDING_SYNC", "READY", "INVALID"]
    readiness_connection_generation: int | None
    readiness_sync_identity: tuple[str, int, int] | None
    action_generation: int | None
    action_sidecars: tuple[VerifiedProvenanceSidecarEntryV2, ...]
    ability_sidecars: tuple[VerifiedProvenanceSidecarEntryV2, ...]
    snapshot_sha256: str


class InboundAuthorityRuntimeV2:
    def __init__(self, token: object, capability: object) -> None:
        if token is not _TOKEN: raise TypeError("runtime authority store is composition-only")
        self._composition_capability = capability
        self._revision = 0; self._readiness = "EMPTY"; self._sync = None
        self._pending: tuple[tuple[VerifiedProvenanceSidecarEntryV2, ...], tuple[VerifiedProvenanceSidecarEntryV2, ...]] | None = None
        self._actions = (); self._abilities = (); self._observation = None
        self._private_sources: dict[tuple[str, str], PrivateSourceSliceV2] = {}
        self._snapshot = self._make_snapshot(0, 0)

    def begin(self, observation: NetworkCommittedInboundV2, event: object,
              world_version: int, last_seq: int, phase: object) -> None:
        if not isinstance(observation, NetworkCommittedInboundV2) or observation.event_object is not event:
            raise ValueError("exact committed inbound observation required")
        if (observation.sequence_mode != "RESUME_REPLAY"
                and observation.previous_committed_seq != last_seq):
            raise ValueError("pre-reducer sequence witness mismatch")
        self._observation = (observation, world_version, last_seq, phase)
        self._emitted_records = []
        self._history_index = None
        self._accepted_sync_phases = {}

    def before_sync_reset(self) -> None: self._emitted_records = []
    def before_sync_history_entry(self, index: int, entry: object) -> None: self._history_index = index
    def after_sync_history_entry(self, index: int) -> None: pass
    def record_appended(self, record: object, event: object, history_index: int | None) -> None:
        self._emitted_records.append((event, history_index, record))
        if isinstance(record, PhaseTransitionRecord) and history_index is not None:
            self._accepted_sync_phases[history_index] = record
    def after_apply(self, result: object, retention: object) -> None: pass

    def finish(self, reducer: object, result: object) -> tuple[str, object] | None:
        if self._observation is None: return None
        observation, version, last_seq, phase = self._observation
        self._observation = None
        actions = (self._action_sidecars(observation)
                   if observation.event_object.type in {"player.action_state", "game.state_sync"}
                   else ())
        abilities = self._ability_sidecars(observation, tuple(self._emitted_records), version, last_seq, phase)
        return (observation.event_object.type, (observation, actions, abilities, result))

    def prepare_commit(self, world_version: int, last_seq: int, delta: tuple[str, object] | None = None,
               lifecycle: NetworkLifecycleObservationV2 | None = None,
               invalidate: bool = False,
               retained_ability_orders: frozenset[int] | None = None) -> "InboundAuthorityRuntimeV2":
        prepared = copy.copy(self)
        prepared._private_sources = dict(self._private_sources)
        prepared._apply_commit(world_version, last_seq, delta, lifecycle, invalidate,
                               retained_ability_orders)
        return prepared

    def prepare_invalid_empty(self, world_version: int, last_seq: int) -> "InboundAuthorityRuntimeV2":
        prepared = copy.copy(self)
        prepared._private_sources = {}
        prepared._invalidate(world_version, last_seq)
        return prepared

    def _apply_commit(self, world_version: int, last_seq: int, delta: tuple[str, object] | None = None,
               lifecycle: NetworkLifecycleObservationV2 | None = None,
               invalidate: bool = False,
               retained_ability_orders: frozenset[int] | None = None) -> None:
        if invalidate:
            self._invalidate(world_version, last_seq); return
        if delta is not None:
            kind, data = delta; observation, actions, abilities, result = data
            generation = observation.connection_generation
            if kind == "game.state_sync" and getattr(result, "state_sync", False):
                identity = (observation.event_object.event_id, observation.committed_seq, generation)
                if observation.sequence_mode == "SYNC_BARRIER":
                    self._readiness = "PENDING_SYNC"; self._sync = identity
                    self._pending = (actions, abilities); self._actions = (); self._abilities = ()
                elif observation.sequence_mode == "CONTIGUOUS" and self._readiness == "READY" and self.connection_generation == generation:
                    self._sync = identity; self._actions = actions; self._abilities = abilities
                else: self._invalidate(world_version, last_seq); return
            elif kind == "player.action_state":
                if observation.sequence_mode != "CONTIGUOUS" or self._readiness != "READY" or self.connection_generation != generation:
                    self._invalidate(world_version, last_seq); return
                self._actions = actions
            elif kind == "game.event":
                if observation.sequence_mode != "CONTIGUOUS" or self._readiness != "READY" or self.connection_generation != generation:
                    self._invalidate(world_version, last_seq); return
                self._abilities = (*self._abilities, *abilities)
            self._base = observation
            for source in observation.prepared_source_slices:
                self._private_sources[(source.event_object.event_id, source.source_path)] = source
        if lifecycle is not None:
            identity = lifecycle.ready_after_sync_identity
            expected = None if identity is None else (identity.event_id, identity.seq, identity.connection_generation)
            if (str(lifecycle.current).endswith("CONNECTED") and self._readiness == "PENDING_SYNC"
                    and expected == self._sync and self._pending is not None):
                self._readiness = "READY"; self._actions, self._abilities = self._pending; self._pending = None
            else: self._invalidate(world_version, last_seq); return
        if retained_ability_orders is not None:
            self._abilities = tuple(item for item in self._abilities
                                    if item.identity[1] in retained_ability_orders)
        self._prune_private_sources()
        self._revision += 1; self._snapshot = self._make_snapshot(world_version, last_seq)

    @property
    def connection_generation(self) -> int | None:
        base = getattr(self, "_base", None)
        return None if base is None else base.connection_generation

    def snapshot(self) -> InboundAuthoritySnapshotV2: return self._snapshot

    def _invalidate(self, version: int, seq: int) -> None:
        self._readiness = "INVALID"; self._sync = None; self._pending = None
        self._actions = (); self._abilities = (); self._revision += 1
        self._private_sources.clear()
        if hasattr(self, "_base"): del self._base
        self._snapshot = self._make_snapshot(version, seq)

    def _make_snapshot(self, version: int, seq: int) -> InboundAuthoritySnapshotV2:
        base = getattr(self, "_base", None)
        values = dict(schema_version="aiwolf.inbound-authority-snapshot.v2",
            game_id=None if base is None else base.game_id, player_id=None if base is None else base.player_id,
            protocol_version=None if base is None else base.protocol_version,
            connection_generation=None if base is None else base.connection_generation,
            last_committed_server_seq=seq, world_version=version, authority_revision=self._revision,
            readiness_status=self._readiness, readiness_sync_identity=self._sync,
            readiness_connection_generation=(self.connection_generation
                                             if self._readiness in {"PENDING_SYNC", "READY"}
                                             else None),
            action_generation=None if base is None else base.action_generation_after,
            action_sidecars=self._actions if self._readiness == "READY" else (),
            ability_sidecars=self._abilities if self._readiness == "READY" else ())
        return InboundAuthoritySnapshotV2(**values, snapshot_sha256=_sha(values))

    def _action_sidecars(self, observation: NetworkCommittedInboundV2):
        parent = observation.event_object.payload if observation.event_object.type == "player.action_state" else observation.event_object.payload["action_state"]
        raw = parent["actions"]
        if len(raw) != len(observation.typed_actions_after): raise ValueError("action cardinality mismatch")
        prefix = "/payload/actions/" if observation.event_object.type == "player.action_state" else "/payload/action_state/actions/"
        sources = {item.source_path: item for item in observation.prepared_source_slices}
        parent_path = "/payload" if observation.event_object.type == "player.action_state" else "/payload/action_state"
        result = []
        for index, (item, typed) in enumerate(zip(raw, observation.typed_actions_after)):
            expected = {"type": typed.type}
            if isinstance(typed, ChatAction): expected["channel"] = typed.channel
            elif isinstance(typed, VoteAction): expected.update(valid_targets=list(typed.valid_targets), target_count=typed.target_count, allows_abstain=typed.allows_abstain)
            elif isinstance(typed, AbilityAction): expected.update(ability_id=typed.ability_id, description=typed.description, valid_targets=list(typed.valid_targets), target_count=typed.target_count, uses_remaining=typed.uses_remaining)
            elif isinstance(typed, CoDeclareAction): expected["claimed_role_ids"] = list(typed.claimed_role_ids)
            elif not isinstance(typed, CoReportAction): raise ValueError("unknown action handle")
            if _bytes(item) != _bytes(expected) or typed.connection_generation != observation.connection_generation or typed.action_generation != observation.action_generation_after:
                raise ValueError("typed action mismatch")
            source = sources[prefix + str(index)]
            result.append(VerifiedProvenanceSidecarEntryV2(_TOKEN,
                schema_version="aiwolf.verified-provenance-sidecar-entry.v2", subject_kind="ACTION_HANDLE",
                identity=(typed.action_generation, index), typed_value_sha256=_sha(typed),
                source=VerifiedInboundRefV2(_TOKEN, source),
                parent_source=VerifiedInboundRefV2(_TOKEN, sources[parent_path]), phase_source=None,
                day=typed.day, phase=typed.phase, action_generation=typed.action_generation,
                phase_attribution_mode=None, reducer_phase_witness_sha256=None,
                state_sync_history_index=None, preceding_phase_entry_index=None,
                world_version_before=None, last_applied_sequence_before=None))
        return tuple(result)

    def _ability_sidecars(self, observation, emitted, version, last_seq, phase):
        records = tuple((event, index, record) for event, index, record in emitted
                        if isinstance(record, AbilityResultRecord)
                        and event is observation.event_object)
        if not records:
            return ()
        if observation.event_object.type == "game.event":
            candidates = records
        elif observation.event_object.type == "game.state_sync":
            candidates = records
        else: return ()
        sources = {s.source_path: s for s in observation.prepared_source_slices}
        result = []
        for (_event, actual_index, record) in candidates:
            if observation.event_object.type == "game.event":
                path = "/payload"; container = observation.event_object.payload; phase_index = None
            else:
                if actual_index is None: continue
                path = f"/payload/history/{actual_index}"
                container = observation.event_object.payload["history"][actual_index].get("payload")
                phase_indexes = [index for index in self._accepted_sync_phases if index < actual_index]
                phase_index = max(phase_indexes) if phase_indexes else None
            phase_path = None if phase_index is None else f"/payload/history/{phase_index}"
            source = sources.get(path)
            if source is None: continue
            event_type = container.get("event_type")
            payload = container.get("event_payload")
            if not isinstance(payload, Mapping): continue
            legal_keys = ({"target_player_id", "result"} if event_type in {"INSPECT_RESULT", "MEDIUM_RESULT"}
                          else {"target_player_id", "role_id"} if event_type == "INSPECT_DEAD_ROLE_RESULT"
                          else {"target_player_id"} if event_type == "GUARD_SUCCEEDED" else set())
            if set(payload) != legal_keys: continue
            if (event_type != record.event_type or payload["target_player_id"] != record.target_player_id
                    or payload.get("result") != record.result_id or payload.get("role_id") != record.revealed_role_id):
                continue
            if event_type in {"INSPECT_RESULT", "MEDIUM_RESULT"} and not isinstance(payload["result"], str):
                continue
            if event_type == "INSPECT_DEAD_ROLE_RESULT" and not isinstance(payload["role_id"], str):
                continue
            phase_ref = None if phase_path is None else VerifiedInboundRefV2(_TOKEN, sources[phase_path])
            if phase_path is not None:
                accepted_phase = self._accepted_sync_phases[int(phase_path.rsplit("/", 1)[1])]
                if accepted_phase.day != record.day or accepted_phase.phase != record.phase:
                    continue
            if observation.event_object.type == "game.state_sync" and phase_ref is None:
                continue
            if observation.event_object.type == "game.event" and phase is None:
                continue
            if observation.event_object.type == "game.event" and (
                    getattr(phase, "day", None) != record.day
                    or getattr(phase, "phase", None) != record.phase):
                continue
            witness = (_sha({"game_id": observation.game_id,
                             "schema_version": "aiwolf.reducer-phase-witness.v2",
                             "connection_generation": observation.connection_generation,
                             "day": record.day, "phase": record.phase,
                             "world_version_before": version,
                             "last_applied_sequence_before": last_seq})
                       if observation.event_object.type == "game.event" else None)
            result.append(VerifiedProvenanceSidecarEntryV2(_TOKEN,
                schema_version="aiwolf.verified-provenance-sidecar-entry.v2", subject_kind="ABILITY_RESULT",
                identity=("ability_result", record.order), typed_value_sha256=_sha(record),
                source=VerifiedInboundRefV2(_TOKEN, source), parent_source=None, phase_source=phase_ref,
                day=record.day, phase=record.phase, action_generation=None,
                phase_attribution_mode=("LIVE_REDUCER_PHASE" if observation.event_object.type == "game.event" else "SYNC_REPLAY_PHASE"),
                reducer_phase_witness_sha256=witness,
                state_sync_history_index=actual_index,
                preceding_phase_entry_index=phase_index,
                world_version_before=version, last_applied_sequence_before=last_seq))
        return tuple(result)

    def _prune_private_sources(self) -> None:
        sidecars = (*self._actions, *self._abilities)
        if self._pending is not None: sidecars = (*sidecars, *self._pending[0], *self._pending[1])
        keep = {(ref.event_id, ref.source_path) for item in sidecars
                for ref in (item.source, item.parent_source, item.phase_source) if ref is not None}
        self._private_sources = {key: value for key, value in self._private_sources.items() if key in keep}


def _create_runtime(capability: object) -> InboundAuthorityRuntimeV2:
    if capability is None: raise TypeError("exact network claim capability is required")
    return InboundAuthorityRuntimeV2(_TOKEN, capability)
