"""Private owner-bound bridge from inbound authority to UNLEASED capture material."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, is_dataclass
from hashlib import sha256
import json
from threading import get_ident
from types import MappingProxyType
from typing import Mapping

from ai_client.network.types import (
    AbilityAction, ChatAction, ClientLifecycle, CoDeclareAction, CoReportAction,
    VoteAction,
)
from ai_client.world.model import AbilityResultRecord, Freshness
from .context import canonical_sha256, channel_is_public, validate_bound_context_snapshot


_ISSUER = object()


class AuthorityOwnerRegistrationV2:
    __slots__ = ("exact_network_client", "exact_claim_capability", "exact_runtime_source",
                 "exact_authority_runtime", "exact_world", "exact_discussion_store",
                 "lifecycle", "issuer_capability", "registered_receipt",
                 "discussion_owner_stage", "authority_successor_publish_capability")
    def __init__(self, token: object, network: object, capability: object, source: object,
                 authority: object, world: object) -> None:
        if token is not _ISSUER: raise TypeError("authority owner registration is opaque")
        object.__setattr__(self, "exact_network_client", network)
        object.__setattr__(self, "exact_claim_capability", capability)
        object.__setattr__(self, "exact_runtime_source", source)
        object.__setattr__(self, "exact_authority_runtime", authority)
        object.__setattr__(self, "exact_world", world)
        object.__setattr__(self, "exact_discussion_store", None)
        object.__setattr__(self, "lifecycle", "ACTIVE")
        object.__setattr__(self, "issuer_capability", object())
        object.__setattr__(self, "registered_receipt", None)
        object.__setattr__(self, "discussion_owner_stage", "NO_RECEIPT")
        object.__setattr__(self, "authority_successor_publish_capability", object())
    def __repr__(self) -> str: return "AuthorityOwnerRegistrationV2(<opaque>)"


def _register_authority_owners_v2(network: object, capability: object, source: object,
                                  authority: object, world: object) -> AuthorityOwnerRegistrationV2:
    from ai_client.network.client import NetworkClient
    from ai_client.network.inbound_authority_v2 import InboundAuthorityClaimCapabilityV2
    from ai_client.runtime import _Phase6NetworkEventSource
    from ai_client.world.inbound_authority_v2 import InboundAuthorityRuntimeV2
    from ai_client.world.service import WorldState
    objects = (network, capability, source, authority, world)
    if (type(network) is not NetworkClient
            or type(capability) is not InboundAuthorityClaimCapabilityV2
            or type(source) is not _Phase6NetworkEventSource
            or type(authority) is not InboundAuthorityRuntimeV2
            or type(world) is not WorldState
            or capability._client is not network or network._authority_capability is not capability
            or source._network is not network or source._authority_capability is not capability
            or source._world is not world or world._source is not source
            or world._inbound_authority is not authority
            or authority._composition_capability is not capability
            or source._retired or network._authority_iteration_started
            or world._authority_owner_mode_v2 != "REGISTRATION_PENDING"
            or any(getattr(item, "_authority_owner_registration_v2", None) is not None
                   for item in objects)):
        raise AuthorityCaptureBridgeError("OWNER_REGISTRATION_MISMATCH")
    registration = AuthorityOwnerRegistrationV2(
        _ISSUER, network, capability, source, authority, world)
    initial_abort = world._prepare_initial_registered_abort_v2(registration)
    network._authority_owner_registration_v2 = registration
    capability._authority_owner_registration_v2 = registration
    source._authority_owner_registration_v2 = registration
    authority._authority_owner_registration_v2 = registration
    world._authority_owner_registration_v2 = registration
    world._authority_successor_publish_capability_v2 = registration.authority_successor_publish_capability
    world._abort_bundle = initial_abort
    world._authority_owner_mode_v2 = "REGISTERED_OWNER"
    return registration


def _retire_authority_registration_v2(source: object) -> None:
    registration = getattr(source, "_authority_owner_registration_v2", None)
    if type(registration) is AuthorityOwnerRegistrationV2:
        object.__setattr__(registration, "lifecycle", "RETIRED")


def _validate_owner_registration_structure_v2(
    registration: object, receipt: object = None, *, require_store: bool = False,
    allow_retired: bool = False,
) -> None:
    """Static identity and monotone stages, independent of network freshness."""
    from ai_client.network.client import NetworkClient
    from ai_client.network.inbound_authority_v2 import InboundAuthorityClaimCapabilityV2
    from ai_client.runtime import _Phase6NetworkEventSource
    from ai_client.world.inbound_authority_v2 import InboundAuthorityRuntimeV2
    from ai_client.world.service import WorldState
    from .state import DiscussionStateStore
    if type(registration) is not AuthorityOwnerRegistrationV2:
        raise AuthorityCaptureBridgeError("OWNER_MISMATCH")
    network = registration.exact_network_client
    capability = registration.exact_claim_capability
    source = registration.exact_runtime_source
    authority = registration.exact_authority_runtime
    world = registration.exact_world
    if (registration.lifecycle not in (("ACTIVE", "RETIRED") if allow_retired else ("ACTIVE",))
            or type(network) is not NetworkClient
            or type(capability) is not InboundAuthorityClaimCapabilityV2
            or type(source) is not _Phase6NetworkEventSource
            or type(authority) is not InboundAuthorityRuntimeV2
            or type(world) is not WorldState
            or capability._client is not network
            or network._authority_capability is not capability
            or source._network is not network or source._authority_capability is not capability
            or source._world is not world or world._source is not source
            or world._inbound_authority is not authority
            or world._authority_owner_mode_v2 != "REGISTERED_OWNER"
            or world._authority_successor_publish_capability_v2
                is not registration.authority_successor_publish_capability
            or authority._composition_capability is not capability
            or any(item._authority_owner_registration_v2 is not registration
                   for item in (network, capability, source, authority, world))):
        raise AuthorityCaptureBridgeError("OWNER_MISMATCH")
    registered = registration.registered_receipt
    stage = registration.discussion_owner_stage
    if (stage not in {"NO_RECEIPT", "RECEIPT_ONLY", "STORE_ATTACHED"}
            or (stage == "NO_RECEIPT" and
                (registered is not None or source._context_owner_receipt_v2 is not None
                 or registration.exact_discussion_store is not None))
            or (stage != "NO_RECEIPT" and registered is None)
            or (stage == "RECEIPT_ONLY" and registration.exact_discussion_store is not None)
            or (stage == "STORE_ATTACHED" and registration.exact_discussion_store is None)):
        raise AuthorityCaptureBridgeError("OWNER_MISMATCH")
    if receipt is not None and receipt is not registered:
        raise AuthorityCaptureBridgeError("OWNER_MISMATCH")
    if registered is not None:
        if (type(registered) is not ValidatedContextOwnerReceiptV2
                or source._context_owner_receipt_v2 is not registered
                or registered.owner_registration_identity is not registration
                or registered.exact_runtime_source_object is not source
                or registered.exact_world_object is not world
                or registered.exact_bound_context_object is not source._bound
                or registered.exact_context_object is not source._bound.context
                or registered._capability is not registered.port_issuer_capability
                or registered.context_sha256 != source._bound.context_sha256
                or registered.manifest_sha256 != source._bound.manifest_sha256):
            raise AuthorityCaptureBridgeError("OWNER_MISMATCH")
    store = registration.exact_discussion_store
    if store is not None:
        if (type(store) is not DiscussionStateStore or registered is None
                or store._authority_owner_registration_v2 is not registration
                or store._bound is not registered.exact_bound_context_object):
            raise AuthorityCaptureBridgeError("OWNER_MISMATCH")
    elif require_store:
        raise AuthorityCaptureBridgeError("OWNER_MISMATCH")


def _validate_owner_registration_v2(registration: object, receipt: object = None,
                                    *, require_store: bool = False) -> None:
    """Positive issuer/read guard; terminal transfer uses the static guard only."""
    _validate_owner_registration_structure_v2(registration, receipt, require_store=require_store)
    source = registration.exact_runtime_source
    if source._retired:
        raise AuthorityCaptureBridgeError("OWNER_MISMATCH")
    registered = registration.registered_receipt
    if registered is None:
        if source._bound is not None:
            raise AuthorityCaptureBridgeError("OWNER_MISMATCH")
        return
    network = registration.exact_network_client
    current = network.snapshot()
    inbound = registration.exact_authority_runtime.snapshot()
    if (current.player_id != registered.authenticated_player_id
            or inbound.player_id != current.player_id
            or inbound.game_id != registered.network_game_id
            or network.config.game_id != registered.network_game_id
            or current.connection_generation != registered.connection_generation
            or inbound.connection_generation != current.connection_generation):
        raise AuthorityCaptureBridgeError("STALE_OWNER_READ")


def _attach_discussion_store_v2(registration: object, receipt: object, store: object) -> None:
    from .state import DiscussionStateStore
    if (type(registration) is not AuthorityOwnerRegistrationV2
            or type(receipt) is not ValidatedContextOwnerReceiptV2
            or type(store) is not DiscussionStateStore
            or registration.lifecycle != "ACTIVE"
            or registration.registered_receipt is not receipt
            or receipt.owner_registration_identity is not registration
            or registration.exact_runtime_source._context_owner_receipt_v2 is not receipt
            or store._bound is not receipt.exact_bound_context_object
            or store._bound.context is not receipt.exact_context_object
            or store._bound.context_sha256 != receipt.context_sha256
            or registration.exact_discussion_store is not None
            or store._authority_owner_registration_v2 is not None):
        raise AuthorityCaptureBridgeError("STORE_REGISTRATION_MISMATCH")
    _validate_owner_registration_v2(registration, receipt)
    store._check_owner()
    registration.exact_discussion_store = store
    store._authority_owner_registration_v2 = registration
    registration.discussion_owner_stage = "STORE_ATTACHED"


class AuthorityCaptureBridgeError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _plain(value: object) -> object:
    if is_dataclass(value):
        return {key: _plain(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if value is None or type(value) in {str, int, float, bool}:
        return value
    if hasattr(value, "value") and type(value.value) is str:
        return value.value
    raise TypeError("bridge material is not closed JSON")


def _bytes(value: object) -> bytes:
    return json.dumps(_plain(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: object) -> str:
    return sha256(_bytes(value)).hexdigest()


def _loop_token() -> tuple[int, int | None]:
    try:
        loop = id(asyncio.get_running_loop())
    except RuntimeError:
        loop = None
    return get_ident(), loop


class ValidatedContextOwnerReceiptV2:
    __slots__ = (
        "exact_runtime_source_object", "exact_world_object", "exact_bound_context_object",
        "exact_context_object", "network_game_id", "authenticated_player_id",
        "manifest_sha256", "context_sha256", "connection_generation",
        "issued_world_snapshot_object", "issued_authority_revision",
        "issued_readiness_status", "owner_registration_identity", "proof_identity",
        "port_issuer_capability", "_capability",
    )

    def __init__(self, token: object, **values: object) -> None:
        if token is not _ISSUER:
            raise TypeError("context owner receipt is opaque")
        for key, value in values.items():
            object.__setattr__(self, key, value)

    def __repr__(self) -> str:
        return "ValidatedContextOwnerReceiptV2(<opaque>)"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise TypeError("context owner receipt is immutable")


def _issue_context_owner_receipt_v2(source: object, world: object, bound: object) -> ValidatedContextOwnerReceiptV2:
    raise AuthorityCaptureBridgeError("PENDING_PROOF_REQUIRED")


def _consume_authority_pending_proof_v2(proof: object, source: object, world: object):
    from ai_client.discussion.context import (
        AuthorityPendingProofV2, BoundDiscussionContext, _retire_pending_proof_v2,
        _EMPTY_AUTHORITY_PROOF_REFS,
    )
    from ai_client.runtime import _Phase6NetworkEventSource
    from ai_client.world.service import WorldState
    if (type(proof) is not AuthorityPendingProofV2
            or type(source) is not _Phase6NetworkEventSource or type(world) is not WorldState):
        raise AuthorityCaptureBridgeError("OWNER_ISSUER_MISMATCH")
    refs = proof._secret_refs
    pending = refs.get("pending"); registration = refs.get("registration")
    try:
        _validate_owner_registration_v2(registration)
        if (proof._state != "ACTIVE" or pending is None
                or pending._active_authority_proof_v2 is not proof
                or type(registration) is not AuthorityOwnerRegistrationV2
                or registration.lifecycle != "ACTIVE"
                or registration.exact_runtime_source is not source
                or registration.exact_world is not world
                or source._authority_owner_registration_v2 is not registration
                or world._authority_owner_registration_v2 is not registration
                or source._world is not world or world._source is not source
                or source._pending is not pending
                or source._bound is not None or source._retired):
            raise AuthorityCaptureBridgeError("PENDING_PROOF_MISMATCH")
        bootstrap_receipt = refs["bootstrap_receipt"]
        if (pending._bootstrap_validation_receipt is not bootstrap_receipt
                or pending._manifest_material is not refs["manifest_material"]
                or pending._manifest_bytes is not refs["manifest_bytes"]
                or pending.manifest_sha256 != refs["manifest_sha256"]
                or pending.context is not refs["context"]
                or pending.context_sha256 != refs["context_sha256"]
                or canonical_sha256(pending.context) != refs["context_sha256"]):
            raise AuthorityCaptureBridgeError("PENDING_PROOF_STALE")
        network = registration.exact_network_client.snapshot()
        authority_runtime = world._inbound_authority
        authority = world.inbound_authority_snapshot()
        snapshot = world.snapshot()
        if (snapshot is not refs["world_snapshot"]
                or authority is not refs["authority_snapshot"]
                or authority_runtime._authority_owner_registration_v2 is not registration
                or authority_runtime._composition_capability is not registration.exact_claim_capability
                or network.player_id != refs["authenticated_player_id"]
                or network.connection_generation != refs["connection_generation"]
                or authority.connection_generation != network.connection_generation):
            raise AuthorityCaptureBridgeError("PENDING_PROOF_STALE")
        bound = BoundDiscussionContext(
            manifest_sha256=refs["manifest_sha256"],
            context_sha256=refs["context_sha256"], context=refs["context"])
        port_capability = object()
        receipt = ValidatedContextOwnerReceiptV2(
            _ISSUER, exact_runtime_source_object=source, exact_world_object=world,
            exact_bound_context_object=bound, exact_context_object=bound.context,
            network_game_id=registration.exact_network_client.config.game_id,
            authenticated_player_id=network.player_id,
            manifest_sha256=bound.manifest_sha256, context_sha256=bound.context_sha256,
            connection_generation=network.connection_generation,
            issued_world_snapshot_object=snapshot,
            issued_authority_revision=authority.authority_revision,
            issued_readiness_status=authority.readiness_status,
            owner_registration_identity=registration,
            proof_identity=proof._proof_identity,
            port_issuer_capability=port_capability, _capability=port_capability)
        result = (bound, receipt)
    except BaseException:
        if type(proof) is AuthorityPendingProofV2 and proof._state == "ACTIVE":
            if pending is not None and pending._active_authority_proof_v2 is proof:
                pending._active_authority_proof_v2 = None
            _retire_pending_proof_v2(proof)
        raise
    # All objects (including the return tuple and empty-ref sentinel) already exist.
    proof._state = "CONSUMED"; proof._secret_refs = _EMPTY_AUTHORITY_PROOF_REFS
    pending._active_authority_proof_v2 = None
    source._bound = bound; source._context_owner_receipt_v2 = receipt
    registration.registered_receipt = receipt
    registration.discussion_owner_stage = "RECEIPT_ONLY"
    object.__setattr__(bootstrap_receipt, "_manifest_material", None)
    object.__setattr__(bootstrap_receipt, "_manifest_bytes", None)
    pending._manifest_material = None
    pending._manifest_bytes = None
    return result


def _network_fingerprint(snapshot: object) -> tuple[object, ...]:
    return (
        snapshot.lifecycle, snapshot.player_id, snapshot.last_seq, snapshot.generation,
        snapshot.connection_generation, snapshot.action_generation, _sha(snapshot.actions),
    )


def _pointer(root: object, path: str) -> object:
    if not path.startswith("/"):
        raise AuthorityCaptureBridgeError("SOURCE_PATH_INVALID")
    current = root
    for raw in path[1:].split("/"):
        index = 0
        while index < len(raw):
            if raw[index] == "~":
                if index + 1 >= len(raw) or raw[index + 1] not in "01":
                    raise AuthorityCaptureBridgeError("SOURCE_PATH_INVALID")
                index += 2
            else:
                index += 1
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if part not in current:
                raise AuthorityCaptureBridgeError("SOURCE_PATH_INVALID")
            current = current[part]
        elif (isinstance(current, (tuple, list)) and part.isdigit()
              and (part == "0" or not part.startswith("0"))):
            index = int(part)
            if index >= len(current):
                raise AuthorityCaptureBridgeError("SOURCE_PATH_INVALID")
            current = current[index]
        else:
            raise AuthorityCaptureBridgeError("SOURCE_PATH_INVALID")
    return current


class OwnerBoundInboundReadV2:
    __slots__ = (
        "world_snapshot", "authority_store", "authority_snapshot", "network_fingerprint",
        "current_actions_view", "ability_records", "history_retention", "action_sidecars",
        "ability_sidecars", "resolved_sources", "source_retention_fingerprint", "_capability",
    )

    def __init__(self, token: object, **values: object) -> None:
        if token is not _ISSUER:
            raise TypeError("owner-bound inbound read is opaque")
        for key, value in values.items():
            object.__setattr__(self, key, value)

    def __repr__(self) -> str:
        return "OwnerBoundInboundReadV2(<opaque>)"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise TypeError("owner-bound inbound read is immutable")


class WorldAuthorityReadPortV2:
    __slots__ = ("_world", "_source", "_receipt", "_owner", "_registration",
                 "_receipt_capability", "_origin_capability", "_bridge")

    def __init__(self, token: object, world: object, source: object,
                 receipt: ValidatedContextOwnerReceiptV2) -> None:
        if token is not _ISSUER:
            raise TypeError("world authority read port is opaque")
        self._world = world; self._source = source; self._receipt = receipt
        self._owner = _loop_token()
        self._registration = receipt.owner_registration_identity
        self._receipt_capability = receipt.port_issuer_capability
        self._origin_capability = object(); self._bridge = None

    def read_current(self) -> OwnerBoundInboundReadV2:
        if _loop_token() != self._owner:
            raise AuthorityCaptureBridgeError("OWNER_LOOP_MISMATCH")
        registration = self._registration
        _validate_owner_registration_v2(registration, self._receipt)
        if (self._receipt.exact_world_object is not self._world
                or self._receipt.exact_runtime_source_object is not self._source
                or registration.lifecycle != "ACTIVE"
                or registration.registered_receipt is not self._receipt
                or self._source._context_owner_receipt_v2 is not self._receipt
                or self._world._authority_read_port_v2 is not self
                or self._receipt_capability is not self._receipt.port_issuer_capability
                or registration.exact_network_client._authority_owner_registration_v2 is not registration
                or registration.exact_claim_capability._authority_owner_registration_v2 is not registration
                or registration.exact_runtime_source is not self._source
                or self._source._network is not registration.exact_network_client
                or self._source._authority_capability is not registration.exact_claim_capability):
            raise AuthorityCaptureBridgeError("OWNER_MISMATCH")
        if (not self._world._run_started or self._world._run_task is None
                or self._source._retired):
            raise AuthorityCaptureBridgeError("OWNER_NOT_RUNNING")
        world1 = self._world.snapshot(); authority_store = self._world._inbound_authority
        authority1 = authority_store.snapshot(); network1 = self._source.snapshot()
        if (world1.freshness is not Freshness.CURRENT or authority1.readiness_status != "READY"
                or authority_store._authority_owner_registration_v2 is not registration
                or authority_store._composition_capability is not registration.exact_claim_capability
                or authority1.world_version != world1.version
                or authority1.last_committed_server_seq != world1.last_applied_seq
                or authority1.game_id != self._receipt.network_game_id
                or authority1.player_id != self._receipt.authenticated_player_id
                or authority1.connection_generation != self._receipt.connection_generation
                or network1.lifecycle is not ClientLifecycle.CONNECTED
                or network1.player_id != authority1.player_id
                or network1.connection_generation != authority1.connection_generation
                or network1.action_generation != authority1.action_generation):
            raise AuthorityCaptureBridgeError("STALE_OWNER_READ")
        view = self._world._current_actions_for_snapshot(network1)
        if (view.world_version != world1.version or view.world_last_applied_seq != world1.last_applied_seq
                or view.network_last_seq != network1.last_seq or not view.is_caught_up):
            raise AuthorityCaptureBridgeError("STALE_OWNER_READ")
        records = tuple(self._world.ability_results().records)
        retention = self._world.ability_results().retention
        resolved: dict[tuple[str, str], object] = {}
        for sidecar in (*authority1.action_sidecars, *authority1.ability_sidecars):
            if sidecar.subject_kind == "ACTION_HANDLE":
                index = sidecar.identity[1]
                expected_parent = ("/payload" if sidecar.source.source_path.startswith("/payload/actions/")
                                   else "/payload/action_state")
                if (sidecar.parent_source is None
                        or sidecar.source.event_id != sidecar.parent_source.event_id
                        or not sidecar.source.source_path.endswith(f"/actions/{index}")
                        or sidecar.parent_source.source_path != expected_parent):
                    raise AuthorityCaptureBridgeError("ACTION_SOURCE_MISMATCH")
            elif sidecar.subject_kind == "ABILITY_RESULT" and sidecar.phase_source is not None:
                if sidecar.source.event_id != sidecar.phase_source.event_id:
                    raise AuthorityCaptureBridgeError("ABILITY_SOURCE_MISMATCH")
                if (sidecar.state_sync_history_index is None
                        or sidecar.preceding_phase_entry_index is None
                        or sidecar.preceding_phase_entry_index >= sidecar.state_sync_history_index):
                    raise AuthorityCaptureBridgeError("ABILITY_PHASE_SOURCE_MISMATCH")
            for ref in (sidecar.source, sidecar.parent_source, sidecar.phase_source):
                if ref is None:
                    continue
                key = (ref.event_id, ref.source_path)
                source = authority_store._private_sources.get(key)
                if source is None:
                    raise AuthorityCaptureBridgeError("SOURCE_NOT_RETAINED")
                event = source.event_object
                if (event.game_id != ref.game_id or event.protocol_version != ref.protocol_version
                        or event.event_id != ref.event_id or event.seq != ref.seq
                        or event.type != ref.message_type or source.player_id != ref.player_id
                        or source.connection_generation != ref.connection_generation
                        or source.source_path != ref.source_path):
                    raise AuthorityCaptureBridgeError("SOURCE_METADATA_MISMATCH")
                actual = _pointer({"payload": event.payload}, ref.source_path)
                actual_bytes = _bytes(actual)
                if (actual_bytes != source.canonical_source_bytes
                        or sha256(actual_bytes).hexdigest() != source.source_sha256
                        or source.source_sha256 != ref.source_sha256):
                    raise AuthorityCaptureBridgeError("SOURCE_HASH_MISMATCH")
                resolved[key] = source
            if sidecar.subject_kind == "ACTION_HANDLE":
                primary = resolved[(sidecar.source.event_id, sidecar.source.source_path)]
                parent = resolved[(sidecar.parent_source.event_id, sidecar.parent_source.source_path)]
                if primary.event_object is not parent.event_object:
                    raise AuthorityCaptureBridgeError("ACTION_SOURCE_MISMATCH")
            elif sidecar.phase_source is not None:
                primary = resolved[(sidecar.source.event_id, sidecar.source.source_path)]
                phase_source = resolved[(sidecar.phase_source.event_id, sidecar.phase_source.source_path)]
                if primary.event_object is not phase_source.event_object:
                    raise AuthorityCaptureBridgeError("ABILITY_SOURCE_MISMATCH")
        world2 = self._world.snapshot(); authority2 = self._world._inbound_authority.snapshot()
        network2 = self._source.snapshot(); retention2 = self._world.ability_results().retention
        if (world2 is not world1 or self._world._inbound_authority is not authority_store
                or authority2 is not authority1 or _network_fingerprint(network2) != _network_fingerprint(network1)
                or retention2 != retention):
            raise AuthorityCaptureBridgeError("STALE_OWNER_READ")
        source_fp = _sha(tuple(sorted((key[0], key[1], value.source_sha256)
                                      for key, value in resolved.items())))
        return OwnerBoundInboundReadV2(
            _ISSUER, world_snapshot=world1, authority_store=authority_store,
            authority_snapshot=authority1, network_fingerprint=_network_fingerprint(network1),
            current_actions_view=view, ability_records=records, history_retention=retention,
            action_sidecars=authority1.action_sidecars, ability_sidecars=authority1.ability_sidecars,
            resolved_sources=MappingProxyType(resolved), source_retention_fingerprint=source_fp,
            _capability=object())


class OwnedDiscussionCaptureReadV2:
    __slots__ = ("bound", "capture", "state", "fingerprint", "_tuple", "_capability")

    def __init__(self, token: object, **values: object) -> None:
        if token is not _ISSUER:
            raise TypeError("owned capture read is opaque")
        for key, value in values.items(): object.__setattr__(self, key, value)

    def __repr__(self) -> str: return "OwnedDiscussionCaptureReadV2(<opaque>)"
    def __setattr__(self, _name: str, _value: object) -> None:
        raise TypeError("owned capture read is immutable")


def _capture_tuple(store: object) -> tuple[object, ...]:
    capture = store._current_capture
    return (store._bound, capture, None if capture is None else capture.capture_id,
            store._state, canonical_sha256(store._state), store._epoch, store._revision,
            store._fact_revision, store._capture_ordinal, store._staged, store._committed,
            store._dispatch, store._delivery, store._observation, store._last_abort, store._closed)


def _same_capture_tuple(left: tuple[object, ...], right: tuple[object, ...]) -> bool:
    identity_indexes = {0, 1, 3, 9, 10, 11, 12, 13, 14}
    return all((a is b) if index in identity_indexes else (a == b)
               for index, (a, b) in enumerate(zip(left, right)))


class DiscussionCaptureReadPortV2:
    __slots__ = ("_store", "_bound", "_owner", "_receipt", "_registration",
                 "_receipt_capability", "_origin_capability", "_bridge")
    def __init__(self, token: object, store: object, receipt: object) -> None:
        if token is not _ISSUER: raise TypeError("capture read port is opaque")
        if store._owner_token is None:
            raise AuthorityCaptureBridgeError("CAPTURE_OWNER_UNINITIALIZED")
        self._store = store; self._bound = store._bound; self._owner = store._owner_token
        self._receipt = receipt; self._registration = receipt.owner_registration_identity
        self._receipt_capability = receipt.port_issuer_capability
        self._origin_capability = object(); self._bridge = None

    def read_current(self) -> OwnedDiscussionCaptureReadV2:
        _validate_owner_registration_v2(self._registration, self._receipt, require_store=True)
        if _loop_token() != self._owner:
            raise AuthorityCaptureBridgeError("CAPTURE_OWNER_MISMATCH")
        before = _capture_tuple(self._store)
        bound, capture, _capture_id, state, state_hash, *_rest = before
        outstanding = before[9:15]
        if (bound is not self._bound or capture is None or before[-1]
                or any(item is not None for item in outstanding)
                or capture.state is not state or capture.state_sha256 != state_hash
                or capture.base_revision != self._store._revision
                or capture.fact_revision != self._store._fact_revision
                or capture.capture_ordinal != self._store._capture_ordinal
                or capture.epoch != self._store._epoch
                or capture.state.epoch != self._store._epoch):
            raise AuthorityCaptureBridgeError("CAPTURE_NOT_CURRENT")
        after = _capture_tuple(self._store)
        if not _same_capture_tuple(before, after):
            raise AuthorityCaptureBridgeError("STALE_CAPTURE_READ")
        return OwnedDiscussionCaptureReadV2(
            _ISSUER, bound=bound, capture=capture, state=state,
            fingerprint=_sha((capture.capture_id, state_hash, *before[5:9])),
            _tuple=before, _capability=object())


class _PrivateMaterial:
    __slots__ = ("_values", "_hidden_owner_receipt", "_origin_ref", "__weakref__")
    def __init__(self, token: object, values: Mapping[str, object], hidden: object) -> None:
        if token is not _ISSUER: raise TypeError("prepared material is opaque")
        object.__setattr__(self, "_values", _freeze(dict(values)))
        object.__setattr__(self, "_hidden_owner_receipt", hidden)
        import weakref
        object.__setattr__(self, "_origin_ref", weakref.ref(self))
    def __getattr__(self, name: str) -> object:
        try: return self._values[name]
        except KeyError: raise AttributeError(name) from None
    def __setattr__(self, _name: str, _value: object) -> None: raise TypeError("prepared material is immutable")
    def __repr__(self) -> str: return "PreparedAuthorityCaptureMaterialV2(<redacted>)"
    def __reduce__(self): raise TypeError("prepared material cannot be serialized")


PreparedAuthorityCaptureMaterialV2 = _PrivateMaterial


class OwnedFinalCaptureSourceV2:
    """Private, immutable second read used by the RESERVED CAS."""
    __slots__ = ("exact_bridge", "exact_prepared_material", "exact_capture_read",
                 "exact_inbound_read", "stable_fingerprint", "_issuer", "_origin_ref", "__weakref__")
    def __init__(self, token: object, bridge: object, material: object,
                 capture_read: object, inbound_read: object, fingerprint: object) -> None:
        if token is not _ISSUER:
            raise TypeError("final capture source is opaque")
        object.__setattr__(self, "exact_bridge", bridge)
        object.__setattr__(self, "exact_prepared_material", material)
        object.__setattr__(self, "exact_capture_read", capture_read)
        object.__setattr__(self, "exact_inbound_read", inbound_read)
        object.__setattr__(self, "stable_fingerprint", fingerprint)
        object.__setattr__(self, "_issuer", _ISSUER)
        import weakref
        object.__setattr__(self, "_origin_ref", weakref.ref(self))
    def __setattr__(self, _name: str, _value: object) -> None:
        raise TypeError("final capture source is immutable")
    def __repr__(self) -> str:
        return "OwnedFinalCaptureSourceV2(<redacted>)"
    def __reduce_ex__(self, _protocol: int):
        raise TypeError("final capture source cannot be serialized")


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


def require_leased_authority_capture_v2(value: object) -> None:
    if isinstance(value, _PrivateMaterial):
        raise AuthorityCaptureBridgeError("LEASE_REQUIRED")
    raise AuthorityCaptureBridgeError("AUTHORITY_MATERIAL_REQUIRED")


def _source_ref(ref: object) -> Mapping[str, object]:
    return {"event_id": ref.event_id, "seq": ref.seq, "message_type": ref.message_type,
            "connection_generation": ref.connection_generation, "source_path": ref.source_path,
            "source_sha256": ref.source_sha256}


def _subject_binding(sidecar: object) -> Mapping[str, object]:
    return {"typed_value_sha256": sidecar.typed_value_sha256,
            "source_ref": _source_ref(sidecar.source),
            "parent_source_ref_or_null": None if sidecar.parent_source is None else _source_ref(sidecar.parent_source),
            "phase_source_ref_or_null": None if sidecar.phase_source is None else _source_ref(sidecar.phase_source)}


def _phase_identity(game_id: str, day: int, phase: str) -> str:
    return _sha({"schema_version": "aiwolf.phase-identity.v2", "game_id": game_id,
                 "day": day, "phase": phase})


def _sidecar_projection(sidecar: object) -> Mapping[str, object]:
    source = sidecar.source
    source_ref = {"schema_version": "aiwolf.authenticated-inbound-ref.v2",
        "game_id": source.game_id, "player_id": source.player_id,
        "connection_generation": source.connection_generation,
        "server_event_id": source.event_id, "server_seq": source.seq,
        "message_type": source.message_type, "source_path": source.source_path,
        "source_sha256": source.source_sha256, "protocol_version": source.protocol_version}
    if sidecar.subject_kind == "ACTION_HANDLE":
        parent = sidecar.parent_source
        parent_ref = {"schema_version": "aiwolf.authenticated-inbound-ref.v2",
            "game_id": parent.game_id, "player_id": parent.player_id,
            "connection_generation": parent.connection_generation,
            "server_event_id": parent.event_id, "server_seq": parent.seq,
            "message_type": parent.message_type, "source_path": parent.source_path,
            "source_sha256": parent.source_sha256, "protocol_version": parent.protocol_version}
        action_context = {"schema_version": "aiwolf.action-context-ref.v2",
            "parent_source": parent_ref, "day": sidecar.day, "phase": sidecar.phase,
            "connection_generation": source.connection_generation,
            "action_generation": sidecar.action_generation}
        return {"schema_version": "aiwolf.provenance-sidecar-entry.v2",
            "subject_kind": "ACTION_HANDLE", "record_identity": None,
            "action_identity": {"action_generation": sidecar.identity[0],
                                "received_index": sidecar.identity[1]},
            "typed_value_sha256": sidecar.typed_value_sha256, "source": source_ref,
            "phase_attribution": None, "action_context": action_context}
    phase_source = None
    if sidecar.phase_source is not None:
        item = sidecar.phase_source
        phase_source = {"schema_version": "aiwolf.authenticated-inbound-ref.v2",
            "game_id": item.game_id, "player_id": item.player_id,
            "connection_generation": item.connection_generation,
            "server_event_id": item.event_id, "server_seq": item.seq,
            "message_type": item.message_type, "source_path": item.source_path,
            "source_sha256": item.source_sha256, "protocol_version": item.protocol_version}
    attribution = {"schema_version": "aiwolf.phase-attribution.v2",
        "mode": sidecar.phase_attribution_mode, "day": sidecar.day, "phase": sidecar.phase,
        "world_version_before": sidecar.world_version_before,
        "last_applied_sequence_before": sidecar.last_applied_sequence_before,
        "state_sync_history_index": sidecar.state_sync_history_index,
        "preceding_phase_entry_index": sidecar.preceding_phase_entry_index,
        "phase_source": phase_source,
        "reducer_phase_witness_sha256": sidecar.reducer_phase_witness_sha256}
    return {"schema_version": "aiwolf.provenance-sidecar-entry.v2",
        "subject_kind": "HISTORY_RECORD",
        "record_identity": {"record_kind": sidecar.identity[0], "local_order": sidecar.identity[1]},
        "action_identity": None, "typed_value_sha256": sidecar.typed_value_sha256,
        "source": source_ref, "phase_attribution": attribution, "action_context": None}


class _ClosedAuthority:
    __slots__ = ()
    def __setattr__(self, _name: str, _value: object) -> None:
        raise TypeError("authority object is immutable")
    def __repr__(self) -> str: return f"{type(self).__name__}(<opaque>)"
    def _projection(self) -> Mapping[str, object]:
        return {name: getattr(self, name) for name in self.__slots__}


class PublicChannelAuthorityV2(_ClosedAuthority):
    __slots__ = ("schema_version", "option_id", "channel_id", "audience", "recipient_scope",
                 "context_sha256", "action_source_sha256", "connection_generation",
                 "action_generation", "phase_identity", "authority_revision_sha256")
    def __init__(self, token: object, **values: object) -> None:
        if token is not _ISSUER: raise TypeError("public channel authority is opaque")
        for key, value in values.items(): object.__setattr__(self, key, value)


class ActionOptionBindingV2(_ClosedAuthority):
    __slots__ = ("schema_version", "option_id", "action_kind", "action_generation",
                 "connection_generation", "phase_identity", "action_sha256", "source",
                 "channel_authority")
    def __init__(self, token: object, **values: object) -> None:
        if token is not _ISSUER: raise TypeError("action option binding is opaque")
        for key, value in values.items(): object.__setattr__(self, key, value)
    def _projection(self) -> Mapping[str, object]:
        result = super()._projection()
        return {**result, "channel_authority": (
            None if self.channel_authority is None else self.channel_authority._projection())}


class DisclosureAuthorityV2(_ClosedAuthority):
    __slots__ = ("schema_version", "authority_kind", "source", "evidence_ref",
                 "actor_player_id", "event_type", "target_player_id", "result_id",
                 "revealed_role_id", "read_visibility", "disclosure_audience",
                 "channel_authority_sha256", "world_version", "fact_revision")
    def __init__(self, token: object, **values: object) -> None:
        if token is not _ISSUER: raise TypeError("disclosure authority is opaque")
        for key, value in values.items(): object.__setattr__(self, key, value)


class DisclosureCandidateV2(_ClosedAuthority):
    __slots__ = ("candidate_id", "authority", "authority_sha256")
    def __init__(self, token: object, candidate_id: str, authority: DisclosureAuthorityV2,
                 authority_sha256: str) -> None:
        if token is not _ISSUER: raise TypeError("disclosure candidate is opaque")
        object.__setattr__(self, "candidate_id", candidate_id)
        object.__setattr__(self, "authority", authority)
        object.__setattr__(self, "authority_sha256", authority_sha256)
    def _projection(self) -> Mapping[str, object]:
        return {"candidate_id": self.candidate_id,
                "authority": self.authority._projection(),
                "authority_sha256": self.authority_sha256}


def _validate_ability_sidecar(record: AbilityResultRecord, sidecar: object,
                              inbound: OwnerBoundInboundReadV2) -> None:
    source_slice = inbound.resolved_sources[(sidecar.source.event_id, sidecar.source.source_path)]
    raw = _pointer({"payload": source_slice.event_object.payload}, sidecar.source.source_path)
    if sidecar.source.message_type == "game.event":
        if sidecar.source.source_path != "/payload" or not isinstance(raw, Mapping):
            raise AuthorityCaptureBridgeError("ABILITY_SOURCE_MISMATCH")
        event = raw
        if (sidecar.phase_attribution_mode != "LIVE_REDUCER_PHASE"
                or sidecar.state_sync_history_index is not None
                or sidecar.preceding_phase_entry_index is not None
                or sidecar.phase_source is not None
                or sidecar.world_version_before is None
                or sidecar.last_applied_sequence_before is None):
            raise AuthorityCaptureBridgeError("ABILITY_PHASE_ATTRIBUTION_MISMATCH")
        witness = {"schema_version": "aiwolf.reducer-phase-witness.v2",
            "game_id": sidecar.source.game_id,
            "connection_generation": sidecar.source.connection_generation,
            "day": sidecar.day, "phase": sidecar.phase,
            "world_version_before": sidecar.world_version_before,
            "last_applied_sequence_before": sidecar.last_applied_sequence_before}
        if sidecar.reducer_phase_witness_sha256 != _sha(witness):
            raise AuthorityCaptureBridgeError("ABILITY_PHASE_WITNESS_MISMATCH")
    elif sidecar.source.message_type == "game.state_sync":
        if (sidecar.phase_attribution_mode != "SYNC_REPLAY_PHASE"
                or sidecar.world_version_before is not None
                or sidecar.last_applied_sequence_before is not None
                or sidecar.reducer_phase_witness_sha256 is not None
                or sidecar.state_sync_history_index is None
                or sidecar.preceding_phase_entry_index is None
                or sidecar.phase_source is None
                or sidecar.source.source_path != f"/payload/history/{sidecar.state_sync_history_index}"
                or sidecar.phase_source.source_path != f"/payload/history/{sidecar.preceding_phase_entry_index}"
                or sidecar.preceding_phase_entry_index >= sidecar.state_sync_history_index):
            raise AuthorityCaptureBridgeError("ABILITY_PHASE_ATTRIBUTION_MISMATCH")
        if not isinstance(raw, Mapping) or set(raw) != {"type", "payload"} or raw["type"] != "game.event":
            raise AuthorityCaptureBridgeError("ABILITY_SOURCE_MISMATCH")
        event = raw["payload"]
        phase_slice = inbound.resolved_sources[(sidecar.phase_source.event_id,
                                                sidecar.phase_source.source_path)]
        phase_raw = _pointer({"payload": phase_slice.event_object.payload},
                             sidecar.phase_source.source_path)
        if (not isinstance(phase_raw, Mapping) or set(phase_raw) != {"type", "payload"}
                or phase_raw["type"] != "game.event"):
            raise AuthorityCaptureBridgeError("ABILITY_PHASE_SOURCE_MISMATCH")
        phase_event = phase_raw["payload"]
        if (not isinstance(phase_event, Mapping)
                or phase_event.get("event_type") != "PHASE_STARTED"):
            raise AuthorityCaptureBridgeError("ABILITY_PHASE_SOURCE_MISMATCH")
        phase_payload = phase_event.get("event_payload")
        if (not isinstance(phase_payload, Mapping) or phase_payload.get("day") != record.day
                or phase_payload.get("phase") != record.phase):
            raise AuthorityCaptureBridgeError("ABILITY_PHASE_SOURCE_MISMATCH")
    else:
        raise AuthorityCaptureBridgeError("ABILITY_SOURCE_MISMATCH")
    if not isinstance(event, Mapping) or set(event) != {"event_type", "event_payload"}:
        raise AuthorityCaptureBridgeError("ABILITY_SOURCE_MISMATCH")
    event_type = event["event_type"]; payload = event["event_payload"]
    matrix = {
        "INSPECT_RESULT": {"target_player_id", "result"},
        "MEDIUM_RESULT": {"target_player_id", "result"},
        "INSPECT_DEAD_ROLE_RESULT": {"target_player_id", "role_id"},
        "GUARD_SUCCEEDED": {"target_player_id"},
    }
    if event_type not in matrix or not isinstance(payload, Mapping) or set(payload) != matrix[event_type]:
        raise AuthorityCaptureBridgeError("ABILITY_EVENT_SHAPE_MISMATCH")
    result = payload.get("result"); role = payload.get("role_id")
    if (not isinstance(payload.get("target_player_id"), str)
            or (event_type in {"INSPECT_RESULT", "MEDIUM_RESULT"} and not isinstance(result, str))
            or (event_type == "INSPECT_DEAD_ROLE_RESULT" and not isinstance(role, str))
            or (event_type == "GUARD_SUCCEEDED" and (result is not None or role is not None))
            or event_type != record.event_type
            or payload["target_player_id"] != record.target_player_id
            or result != record.result_id or role != record.revealed_role_id
            or sidecar.day != record.day or sidecar.phase != record.phase):
        raise AuthorityCaptureBridgeError("ABILITY_RECORD_MISMATCH")


class AuthorityCaptureBridgeV2:
    __slots__ = ("_world_port", "_capture_port", "_receipt", "_token",
                 "_registration", "_receipt_capability")
    def __init__(self, token: object, world_port: WorldAuthorityReadPortV2,
                 capture_port: DiscussionCaptureReadPortV2,
                 receipt: ValidatedContextOwnerReceiptV2) -> None:
        if token is not _ISSUER: raise TypeError("authority bridge is opaque")
        if (type(world_port) is not WorldAuthorityReadPortV2
                or type(capture_port) is not DiscussionCaptureReadPortV2
                or type(receipt) is not ValidatedContextOwnerReceiptV2
                or world_port._receipt is not receipt or capture_port._receipt is not receipt
                or capture_port._bound is not receipt.exact_bound_context_object
                or world_port._registration is not receipt.owner_registration_identity
                or capture_port._registration is not receipt.owner_registration_identity
                or world_port._receipt_capability is not receipt.port_issuer_capability
                or capture_port._receipt_capability is not receipt.port_issuer_capability
                or world_port._world._authority_read_port_v2 is not world_port
                or capture_port._store._capture_read_port_v2 is not capture_port
                or world_port._source is not receipt.exact_runtime_source_object
                or world_port._world is not receipt.exact_world_object
                or capture_port._store is not receipt.owner_registration_identity.exact_discussion_store
                or world_port._bridge is not None or capture_port._bridge is not None):
            raise AuthorityCaptureBridgeError("BRIDGE_OWNER_MISMATCH")
        self._world_port = world_port; self._capture_port = capture_port
        self._receipt = receipt; self._token = object()
        self._registration = receipt.owner_registration_identity
        self._receipt_capability = receipt.port_issuer_capability
        _validate_owner_registration_v2(self._registration, receipt, require_store=True)
        world_port._bridge = self; capture_port._bridge = self

    def prepare_current(self) -> PreparedAuthorityCaptureMaterialV2:
        registration = self._registration
        _validate_owner_registration_v2(registration, self._receipt, require_store=True)
        if (registration.lifecycle != "ACTIVE"
                or registration.registered_receipt is not self._receipt
                or registration.exact_runtime_source._context_owner_receipt_v2 is not self._receipt
                or registration.exact_discussion_store is not self._capture_port._store
                or self._capture_port._store._authority_owner_registration_v2 is not registration):
            raise AuthorityCaptureBridgeError("BRIDGE_OWNER_MISMATCH")
        capture_read = self._capture_port.read_current()
        inbound = self._world_port.read_current()
        capture = capture_read.capture; world = inbound.world_snapshot
        validate_bound_context_snapshot(capture_read.bound, world)
        if (capture.context is not self._receipt.exact_context_object
                or capture.game_id != self._receipt.network_game_id
                or capture.player_id != self._receipt.authenticated_player_id
                or capture.context_sha256 != self._receipt.context_sha256
                or capture.world_version != world.version
                or capture.last_applied_seq != world.last_applied_seq
                or capture.trigger.day != world.phase.day or capture.trigger.phase != world.phase.phase):
            raise AuthorityCaptureBridgeError("CAPTURE_WORLD_MISMATCH")
        actions = tuple(inbound.current_actions_view.actions)
        sidecars = tuple(inbound.action_sidecars)
        if len(actions) != len(sidecars): raise AuthorityCaptureBridgeError("ACTION_CARDINALITY_MISMATCH")
        authority = inbound.authority_snapshot
        phase_identity = _phase_identity(capture.game_id, world.phase.day, world.phase.phase)
        network_connection_generation = inbound.network_fingerprint[4]
        network_action_generation = inbound.network_fingerprint[5]
        bindings = []
        public = []
        action_identities: set[tuple[object, ...]] = set()
        primary_sources: set[tuple[str, str]] = set()
        for index, (action, sidecar) in enumerate(zip(actions, sidecars)):
            if sidecar.subject_kind != "ACTION_HANDLE" or sidecar.identity != (action.action_generation, index):
                raise AuthorityCaptureBridgeError("ACTION_IDENTITY_MISMATCH")
            if sidecar.identity in action_identities:
                raise AuthorityCaptureBridgeError("ACTION_IDENTITY_DUPLICATE")
            action_identities.add(sidecar.identity)
            source_identity = (sidecar.source.event_id, sidecar.source.source_path)
            if source_identity in primary_sources:
                raise AuthorityCaptureBridgeError("ACTION_SOURCE_DUPLICATE")
            primary_sources.add(source_identity)
            if sidecar.typed_value_sha256 != _sha(action):
                raise AuthorityCaptureBridgeError("ACTION_TYPED_HASH_MISMATCH")
            if (sidecar.day != action.day or sidecar.phase != action.phase
                    or sidecar.action_generation != action.action_generation
                    or sidecar.source.connection_generation != action.connection_generation
                    or action.day != world.phase.day or action.phase != world.phase.phase
                    or action.day != capture.trigger.day or action.phase != capture.trigger.phase
                    or action.connection_generation != network_connection_generation
                    or action.connection_generation != capture.trigger.connection_generation
                    or action.connection_generation != authority.connection_generation
                    or action.action_generation != network_action_generation
                    or action.action_generation != capture.trigger.action_generation
                    or action.action_generation != authority.action_generation):
                raise AuthorityCaptureBridgeError("ACTION_IDENTITY_MISMATCH")
            if isinstance(action, CoReportAction): raise AuthorityCaptureBridgeError("UNSUPPORTED_ACTION_KIND")
            if not isinstance(action, (ChatAction, VoteAction, CoDeclareAction, AbilityAction)):
                raise AuthorityCaptureBridgeError("UNSUPPORTED_ACTION_KIND")
            channel_hash = None
            channel_authority = None
            option_id = f"o{index:03d}"
            sidecar_projection = _sidecar_projection(sidecar)
            parent_slice = inbound.resolved_sources[
                (sidecar.parent_source.event_id, sidecar.parent_source.source_path)]
            primary_slice = inbound.resolved_sources[
                (sidecar.source.event_id, sidecar.source.source_path)]
            parent = _pointer({"payload": parent_slice.event_object.payload},
                              sidecar.parent_source.source_path)
            expected_primary_path = (
                f"/payload/actions/{index}" if sidecar.parent_source.source_path == "/payload"
                else f"/payload/action_state/actions/{index}")
            expected_message = ("player.action_state" if sidecar.parent_source.source_path == "/payload"
                                else "game.state_sync")
            if (sidecar.parent_source.source_path not in {"/payload", "/payload/action_state"}
                    or sidecar.source.source_path != expected_primary_path
                    or sidecar.source.message_type != expected_message
                    or sidecar.parent_source.message_type != expected_message
                    or not isinstance(parent, Mapping)
                    or set(parent) != {"phase", "day", "phase_ends_at", "actions"}
                    or parent["day"] != action.day or parent["phase"] != action.phase
                    or not isinstance(parent["actions"], (tuple, list))
                    or index >= len(parent["actions"])
                    or _bytes(parent["actions"][index]) != _bytes(
                        _pointer({"payload": primary_slice.event_object.payload}, expected_primary_path))):
                raise AuthorityCaptureBridgeError("ACTION_PARENT_MISMATCH")
            # ClientState adds exactly these four provenance fields when decoding
            # an action. All remaining concrete dataclass fields are wire fields.
            wire_action = {key: value for key, value in _plain(action).items()
                           if key not in {"connection_generation", "action_generation", "phase", "day"}}
            if _bytes(parent["actions"][index]) != _bytes(wire_action):
                raise AuthorityCaptureBridgeError("ACTION_RAW_TYPED_MISMATCH")
            if isinstance(action, ChatAction) and capture.trigger.kind in {"INITIAL_CHAT", "PEER_CHAT"}:
                if not channel_is_public(capture.context, action.channel):
                    raise AuthorityCaptureBridgeError("CHAT_CHANNEL_NOT_PUBLIC")
                preceding = {"schema_version": "aiwolf.public-channel-authority.v2",
                    "option_id": option_id, "channel_id": action.channel, "audience": "PUBLIC",
                    "recipient_scope": "SERVER_FILTERED_PUBLIC",
                    "context_sha256": capture.context_sha256,
                    "action_source_sha256": _sha(sidecar_projection),
                    "connection_generation": action.connection_generation,
                    "action_generation": action.action_generation,
                    "phase_identity": phase_identity}
                channel_hash = _sha(preceding)
                channel_authority = PublicChannelAuthorityV2(
                    _ISSUER, **preceding, authority_revision_sha256=channel_hash)
                public.append(channel_authority)
            binding = ActionOptionBindingV2(
                _ISSUER, schema_version="aiwolf.action-option-binding.v2",
                option_id=option_id, action_kind=action.type,
                action_generation=action.action_generation,
                connection_generation=action.connection_generation,
                phase_identity=phase_identity, action_sha256=_sha(action),
                source=_freeze(sidecar_projection),
                channel_authority=channel_authority)
            bindings.append(binding)
        if capture.trigger.kind in {"INITIAL_CHAT", "PEER_CHAT"}:
            if sum(isinstance(action, ChatAction) for action in actions) != 1 or len(public) != 1:
                raise AuthorityCaptureBridgeError("CHAT_ACTION_CARDINALITY")
        elif public:
            raise AuthorityCaptureBridgeError("UNEXPECTED_PUBLIC_AUTHORITY")
        records = {record.order: record for record in inbound.ability_records}
        if len(records) != len(inbound.ability_records):
            raise AuthorityCaptureBridgeError("ABILITY_RECORD_DUPLICATE")
        disclosures = []
        proven: set[int] = set()
        sidecar_identities: set[tuple[object, ...]] = set()
        for sidecar in inbound.ability_sidecars:
            if sidecar.subject_kind != "ABILITY_RESULT" or sidecar.identity[0] != "ability_result":
                raise AuthorityCaptureBridgeError("ABILITY_IDENTITY_MISMATCH")
            order = sidecar.identity[1]
            if sidecar.identity in sidecar_identities:
                raise AuthorityCaptureBridgeError("ABILITY_SIDECAR_DUPLICATE")
            sidecar_identities.add(sidecar.identity)
            record = records.get(order)
            if record is None or sidecar.typed_value_sha256 != _sha(record):
                raise AuthorityCaptureBridgeError("ABILITY_RECORD_MISMATCH")
            if (sidecar.day != record.day or sidecar.phase != record.phase
                    or sidecar.source.connection_generation != self._receipt.connection_generation):
                raise AuthorityCaptureBridgeError("ABILITY_RECORD_MISMATCH")
            _validate_ability_sidecar(record, sidecar, inbound)
            if sidecar.source.player_id != capture.player_id:
                raise AuthorityCaptureBridgeError("ABILITY_RECORD_MISMATCH")
            proven.add(order)
            if not public or sidecar.source.seq > capture.last_applied_seq:
                continue
            channel_authority = public[0]
            disclosure_authority = DisclosureAuthorityV2(
                _ISSUER, schema_version="aiwolf.disclosure-authority.v2",
                authority_kind="SELF_ABILITY_REPORT",
                source=_freeze(_sidecar_projection(sidecar)),
                evidence_ref=_freeze({"record_kind": "ability_result", "order": order,
                                      "visibility": "AUTHORIZED_PRIVATE"}),
                actor_player_id=capture.player_id, event_type=record.event_type,
                target_player_id=record.target_player_id, result_id=record.result_id,
                revealed_role_id=record.revealed_role_id,
                read_visibility="AUTHORIZED_PRIVATE", disclosure_audience="PUBLIC",
                channel_authority_sha256=_sha(channel_authority._projection()),
                world_version=world.version, fact_revision=capture.fact_revision)
            authority_hash = _sha(disclosure_authority._projection())
            disclosures.append((authority_hash, sidecar.source.event_id, sidecar.source.source_path,
                                sidecar.typed_value_sha256, disclosure_authority))
        disclosures.sort(key=lambda item: item[:4])
        if len({item[:4] for item in disclosures}) != len(disclosures):
            raise AuthorityCaptureBridgeError("DISCLOSURE_DUPLICATE")
        disclosure_values = tuple(DisclosureCandidateV2(
            _ISSUER, f"d{index:03d}", item[4], item[0])
            for index, item in enumerate(disclosures))
        unproven = tuple(("ability_result", order) for order in sorted(set(records) - proven))
        action_values = tuple(bindings); public_values = tuple(public)
        action_projection = tuple(item._projection() for item in action_values)
        public_projection = tuple(item._projection() for item in public_values)
        disclosure_projection = tuple(item._projection() for item in disclosure_values)
        stable = {
            "schema_version": "aiwolf.prepared-authority-capture-material.v2", "status": "UNLEASED",
            "base_capture_id": capture.capture_id, "game_id": capture.game_id,
            "player_id": capture.player_id, "context_sha256": capture.context_sha256,
            "base_revision": capture.base_revision, "fact_revision": capture.fact_revision,
            "trigger": _plain(capture.trigger), "world_version": world.version,
            "last_applied_sequence": world.last_applied_seq,
            "authority_revision": authority.authority_revision,
            "authority_snapshot_sha256": authority.snapshot_sha256,
            "phase_identity": phase_identity,
            "connection_generation": authority.connection_generation,
            "action_generation": authority.action_generation,
            "world_owner_fingerprint": _sha(inbound.network_fingerprint),
            "capture_read_fingerprint": capture_read.fingerprint,
            "source_retention_fingerprint": inbound.source_retention_fingerprint,
            "action_bindings": action_values, "public_channel_authorities": public_values,
            "disclosure_candidates": disclosure_values,
            "unproven_ability_record_identities": unproven,
            "action_catalog_sha256": _sha(action_projection),
            "disclosure_catalog_sha256": _sha((disclosure_projection, unproven)),
        }
        hash_material = dict(stable)
        hash_material["action_bindings"] = action_projection
        hash_material["public_channel_authorities"] = public_projection
        hash_material["disclosure_candidates"] = disclosure_projection
        stable["prepared_material_sha256"] = _sha(hash_material)
        if not _same_capture_tuple(_capture_tuple(self._capture_port._store), capture_read._tuple):
            raise AuthorityCaptureBridgeError("STALE_CAPTURE_READ")
        check = self._world_port.read_current()
        if (check.world_snapshot is not inbound.world_snapshot
                or check.authority_snapshot is not inbound.authority_snapshot
                or check.network_fingerprint != inbound.network_fingerprint
                or check.source_retention_fingerprint != inbound.source_retention_fingerprint):
            raise AuthorityCaptureBridgeError("STALE_OWNER_READ")
        return _PrivateMaterial(_ISSUER, stable, (self._token, inbound, capture_read))

    def _read_final_capture_source_v2(
        self, prepared_material: object, hidden_owner_receipt: object,
    ) -> OwnedFinalCaptureSourceV2:
        """Re-read the exact registered owners without consuming authority."""
        if (type(prepared_material) is not _PrivateMaterial
                or prepared_material._origin_ref() is not prepared_material
                or prepared_material._hidden_owner_receipt is not hidden_owner_receipt
                or not isinstance(hidden_owner_receipt, tuple)
                or len(hidden_owner_receipt) != 3
                or hidden_owner_receipt[0] is not self._token):
            raise AuthorityCaptureBridgeError("FINAL_SOURCE_OWNER_MISMATCH")
        current = self.prepare_current()
        capture_read = current._hidden_owner_receipt[2]
        inbound_read = current._hidden_owner_receipt[1]
        stable_names = tuple(prepared_material._values)
        if (stable_names != tuple(current._values)
                or prepared_material.prepared_material_sha256 != current.prepared_material_sha256
                or hidden_owner_receipt[1].world_snapshot is not inbound_read.world_snapshot
                or hidden_owner_receipt[1].authority_snapshot is not inbound_read.authority_snapshot
                or hidden_owner_receipt[1].current_actions_view.actions is not inbound_read.current_actions_view.actions
                or hidden_owner_receipt[2].capture is not capture_read.capture
                or hidden_owner_receipt[2].state is not capture_read.state
                or hidden_owner_receipt[2].bound is not capture_read.bound
                or not _same_capture_tuple(hidden_owner_receipt[2]._tuple, capture_read._tuple)):
            raise AuthorityCaptureBridgeError("FINAL_SOURCE_STALE")
        for name in stable_names:
            old, new = prepared_material._values[name], current._values[name]
            if name in {"action_bindings", "public_channel_authorities", "disclosure_candidates"}:
                old = tuple(item._projection() for item in old)
                new = tuple(item._projection() for item in new)
            if _sha(old) != _sha(new):
                raise AuthorityCaptureBridgeError("FINAL_SOURCE_STALE")
        fingerprint = _sha((current.prepared_material_sha256,
                            capture_read.fingerprint,
                            inbound_read.network_fingerprint,
                            inbound_read.source_retention_fingerprint))
        return OwnedFinalCaptureSourceV2(
            _ISSUER, self, prepared_material, capture_read, inbound_read, fingerprint)


def _create_world_authority_read_port_v2(world: object,
                                          receipt: ValidatedContextOwnerReceiptV2) -> WorldAuthorityReadPortV2:
    from ai_client.world.service import WorldState
    if type(world) is not WorldState or type(receipt) is not ValidatedContextOwnerReceiptV2:
        raise AuthorityCaptureBridgeError("OWNER_ISSUER_MISMATCH")
    registration = world._authority_owner_registration_v2
    source = None if type(registration) is not AuthorityOwnerRegistrationV2 else registration.exact_runtime_source
    if (registration is None or registration.lifecycle != "ACTIVE"
            or registration.exact_world is not world or source is None
            or receipt.owner_registration_identity is not registration
            or registration.registered_receipt is not receipt
            or source._context_owner_receipt_v2 is not receipt
            or world._authority_read_port_v2 is not None):
        raise AuthorityCaptureBridgeError("OWNER_ISSUER_MISMATCH")
    _validate_owner_registration_v2(registration, receipt)
    port = WorldAuthorityReadPortV2(_ISSUER, world, source, receipt)
    world._authority_read_port_v2 = port
    return port


def _create_capture_read_port_v2(store: object, receipt: object) -> DiscussionCaptureReadPortV2:
    from .state import DiscussionStateStore
    if type(store) is not DiscussionStateStore or type(receipt) is not ValidatedContextOwnerReceiptV2:
        raise AuthorityCaptureBridgeError("OWNER_ISSUER_MISMATCH")
    registration = store._authority_owner_registration_v2
    if (type(registration) is not AuthorityOwnerRegistrationV2
            or registration.lifecycle != "ACTIVE"
            or registration.exact_discussion_store is not store
            or registration.registered_receipt is not receipt
            or receipt.owner_registration_identity is not registration
            or store._bound is not receipt.exact_bound_context_object
            or store._owner_token != _loop_token()
            or store._capture_read_port_v2 is not None):
        raise AuthorityCaptureBridgeError("OWNER_ISSUER_MISMATCH")
    _validate_owner_registration_v2(registration, receipt, require_store=True)
    port = DiscussionCaptureReadPortV2(_ISSUER, store, receipt)
    store._capture_read_port_v2 = port
    return port


def _create_authority_capture_bridge_v2(world_port: WorldAuthorityReadPortV2,
                                         capture_port: DiscussionCaptureReadPortV2,
                                         receipt: ValidatedContextOwnerReceiptV2) -> AuthorityCaptureBridgeV2:
    return AuthorityCaptureBridgeV2(_ISSUER, world_port, capture_port, receipt)
