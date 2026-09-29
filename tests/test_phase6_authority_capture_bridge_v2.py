from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import pickle
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ai_client.discussion.authority_capture_bridge_v2 import (
    AuthorityCaptureBridgeError, PreparedAuthorityCaptureMaterialV2,
    require_leased_authority_capture_v2,
)
from ai_client.discussion.context import PendingDiscussionContext, validate_discussion_bootstrap
from ai_client.discussion.model import DiscussionTrigger
from ai_client.discussion.state import DiscussionStateStore, DiscussionViews
from ai_client.network.client import NetworkClient
from ai_client.network.protocol import PROTOCOL_VERSION
from ai_client.network.types import ClientExitReason, NetworkClientConfig
from ai_client.runtime import (
    _create_phase6_v2_authority_capture_bridge, _create_phase6_v2_inbound_world,
)
from tests.test_phase6_discussion_context import _envelope


@pytest.fixture
def anyio_backend(): return "asyncio"


class MemoryStore:
    def __init__(self): self.saved = []; self.checkpoint = None
    async def load(self): return self.checkpoint
    async def save(self, checkpoint): self.saved.append(checkpoint); self.checkpoint = checkpoint


class FakeSocket:
    def __init__(self, values):
        self.incoming = asyncio.Queue(); self.sent = []; self.closed = False
        for value in values: self.incoming.put_nowait(value)
    async def send(self, raw): self.sent.append(json.loads(raw))
    async def recv(self): return await self.incoming.get()
    async def close(self): self.closed = True
    async def wait_closed(self): pass


def wire(kind, seq, payload):
    return json.dumps({"type": kind, "protocol_version": PROTOCOL_VERSION,
        "event_id": str(uuid4()), "game_id": "opaque-game", "seq": seq,
        "timestamp": 1, "payload": payload})


def sync_payload(actions=(), history=()):
    return {"players": [{"player_id": "opaque-player", "display_name": "Self"}],
        "deaths": [], "action_state": {"phase": "day", "day": 1,
        "phase_ends_at": None, "actions": list(actions)},
        "self": {"player_id": "opaque-player", "role_id": "opaque-role",
        "modifier_ids": []}, "revealed_roles": [], "history": list(history)}


async def composed_bridge(*, history=(), live_event=None, actions=None):
    pending = validate_discussion_bootstrap(
        _envelope(), network_game_id="opaque-game", player_id="opaque-player")
    incoming = [
        wire("session.joined", 1, {"player_id": "opaque-player", "connection_token": "token"}),
        wire("session.ready", 2, {"player_id": "opaque-player", "ready": True}),
        wire("game.state_sync", 3, sync_payload(
            (({"type": "chat", "channel": "opaque-public"},)
             if actions is None else actions), history)),
    ]
    if live_event is not None:
        incoming.append(wire("game.event", 4, {"event_type": live_event[0],
                                               "event_payload": live_event[1]}))
    socket = FakeSocket(incoming)
    network = NetworkClient(NetworkClientConfig(
        "ws://offline", "opaque-game", "entry"), MemoryStore(), connector=lambda _uri: socket)
    source, world = _create_phase6_v2_inbound_world(network, pending)
    network_task = asyncio.create_task(network.run()); world_task = asyncio.create_task(world.run())
    try:
        bound = await asyncio.wait_for(source.bound_outcome, timeout=1)
    except Exception as error:
        network_result = network_task.result() if network_task.done() else None
        world_result = world_task.result() if world_task.done() else None
        pytest.fail(f"bind failed: {error!r}; network={network_result!r}; world={world_result!r}")
    assert source.context_owner_receipt_v2.issued_readiness_status == "PENDING_SYNC"
    source.release_composition()
    expected_seq = 4 if live_event is not None else 3
    for _ in range(100):
        if (world.inbound_authority_snapshot().readiness_status == "READY"
                and world.snapshot().last_applied_seq == expected_seq): break
        await asyncio.sleep(0.01)
    else: pytest.fail("authority did not become READY")
    store = DiscussionStateStore(bound)
    views = DiscussionViews(world.snapshot(), world.history(), world.co_for_day(1),
                            world.ability_results(), world.transport_observations())
    trigger = DiscussionTrigger("reaction_chat", "INITIAL_CHAT", 1, "day",
                                1, world.inbound_authority_snapshot().action_generation, 0, None)
    capture = store.capture(views, trigger)
    bridge = _create_phase6_v2_authority_capture_bridge(source, world, store)
    return socket, network_task, world_task, source, world, store, capture, bridge


async def close_composed(socket, network_task, world_task, source):
    socket.incoming.put_nowait(wire("game.event", source.snapshot().last_seq + 1,
        {"event_type": "GAME_ENDED", "event_payload": {}}))
    assert (await asyncio.wait_for(network_task, 1)).reason is ClientExitReason.GAME_ENDED
    await asyncio.wait_for(world_task, 1)


@pytest.mark.anyio
async def test_t518_positive_actual_startup_owner_read_and_unleased_material():
    values = await composed_bridge()
    socket, network_task, world_task, source, world, store, capture, bridge = values
    before = (world.snapshot(), world.inbound_authority_snapshot(), store.snapshot,
              store._capture_ordinal, store._staged, store._committed)
    material = bridge.prepare_current()
    assert isinstance(material, PreparedAuthorityCaptureMaterialV2)
    assert material.status == "UNLEASED" and material.base_capture_id == capture.capture_id
    assert material.world_version == world.snapshot().version
    assert material.last_applied_sequence == world.snapshot().last_applied_seq
    assert material.connection_generation == 1
    assert [item.option_id for item in material.action_bindings] == ["o000"]
    assert len(material.public_channel_authorities) == 1
    assert material.public_channel_authorities[0].recipient_scope == "SERVER_FILTERED_PUBLIC"
    assert len(material.prepared_material_sha256) == 64
    assert before == (world.snapshot(), world.inbound_authority_snapshot(), store.snapshot,
                      store._capture_ordinal, store._staged, store._committed)
    assert repr(material) == "PreparedAuthorityCaptureMaterialV2(<redacted>)"
    with pytest.raises(TypeError): asdict(material)
    with pytest.raises(TypeError): pickle.dumps(material)
    with pytest.raises(TypeError): json.dumps(material)
    with pytest.raises(TypeError): material.action_bindings[0].option_id = "changed"
    with pytest.raises(AuthorityCaptureBridgeError, match="LEASE_REQUIRED"):
        require_leased_authority_capture_v2(material)
    await close_composed(socket, network_task, world_task, source)


@pytest.mark.anyio
async def test_t518_sync_ability_disclosure_uses_actual_record_and_private_source():
    history = (
        {"type": "game.event", "payload": {"event_type": "PHASE_STARTED",
         "event_payload": {"day": 1, "phase": "day", "phase_ends_at": 10}}},
        {"type": "game.event", "payload": {"event_type": "GUARD_SUCCEEDED",
         "event_payload": {"target_player_id": "opaque-player"}}},
    )
    values = await composed_bridge(history=history)
    socket, network_task, world_task, _source, _world, _store, _capture, bridge = values
    material = bridge.prepare_current()
    assert len(material.disclosure_candidates) == 1
    disclosure = material.disclosure_candidates[0]
    assert disclosure.candidate_id == "d000"
    assert disclosure.authority.authority_kind == "SELF_ABILITY_REPORT"
    assert disclosure.authority.event_type == "GUARD_SUCCEEDED"
    assert disclosure.authority.source["phase_attribution"]["phase_source"] is not None
    await close_composed(socket, network_task, world_task, _source)


@pytest.mark.anyio
async def test_t518_live_ability_disclosure_uses_reducer_phase_witness():
    values = await composed_bridge(live_event=(
        "GUARD_SUCCEEDED", {"target_player_id": "opaque-player"}))
    socket, network_task, world_task, source, _world, _store, _capture, bridge = values
    material = bridge.prepare_current()
    assert len(material.disclosure_candidates) == 1
    binding = material.disclosure_candidates[0].authority.source
    assert binding["phase_attribution"]["phase_source"] is None
    assert binding["source"]["server_seq"] == 4
    await close_composed(socket, network_task, world_task, source)


@pytest.mark.anyio
async def test_t518_private_source_subtree_mutation_rejects_whole_prepare():
    values = await composed_bridge()
    socket, network_task, world_task, _source, world, _store, _capture, bridge = values
    source_slice = next(iter(world._inbound_authority._private_sources.values()))
    object.__setattr__(source_slice, "canonical_source_bytes", b"{}")
    with pytest.raises(AuthorityCaptureBridgeError, match="SOURCE_HASH_MISMATCH"):
        bridge.prepare_current()
    await close_composed(socket, network_task, world_task, _source)


@pytest.mark.anyio
@pytest.mark.parametrize(("actions", "code"), [
    (({"type": "chat", "channel": "opaque-private"},), "CHAT_CHANNEL_NOT_PUBLIC"),
    (({"type": "chat", "channel": "opaque-public"},
      {"type": "chat", "channel": "opaque-public"}), "CHAT_ACTION_CARDINALITY"),
    ((), "CHAT_ACTION_CARDINALITY"),
])
async def test_t518_chat_catalog_is_atomic_and_fail_closed(actions, code):
    values = await composed_bridge(actions=actions)
    socket, network_task, world_task, source, _world, _store, _capture, bridge = values
    with pytest.raises(AuthorityCaptureBridgeError, match=code):
        bridge.prepare_current()
    await close_composed(socket, network_task, world_task, source)


@pytest.mark.anyio
async def test_t518_next_world_commit_makes_old_capture_stale():
    values = await composed_bridge()
    socket, network_task, world_task, source, world, _store, _capture, bridge = values
    socket.incoming.put_nowait(wire("player.action_state", 4,
        {"phase": "day", "day": 1, "phase_ends_at": None,
         "actions": [{"type": "chat", "channel": "opaque-public"}]}))
    for _ in range(100):
        if world.snapshot().last_applied_seq == 4: break
        await asyncio.sleep(0.01)
    else: pytest.fail("next World commit was not observed")
    with pytest.raises(AuthorityCaptureBridgeError, match="CAPTURE_WORLD_MISMATCH"):
        bridge.prepare_current()
    await close_composed(socket, network_task, world_task, source)


@pytest.mark.anyio
async def test_t518_pending_direct_pending_and_stale_capture_fail_closed():
    validated = validate_discussion_bootstrap(
        _envelope(), network_game_id="opaque-game", player_id="opaque-player")
    direct = PendingDiscussionContext(
        manifest_material={}, manifest_bytes=b"{}", manifest_sha256="0" * 64,
        context_sha256="0" * 64,
        context=validated.context,
        _bootstrap_validation_receipt=validated._bootstrap_validation_receipt)
    assert direct._bootstrap_validation_receipt is validated._bootstrap_validation_receipt
    from ai_client.discussion.context import _revalidate_authority_pending_v2
    with pytest.raises(Exception, match="route is not proven"):
        _revalidate_authority_pending_v2(
            direct, validated.context, object())

    values = await composed_bridge()
    socket, network_task, world_task, _source, _world, store, _capture, bridge = values
    store._capture_ordinal += 1
    with pytest.raises(AuthorityCaptureBridgeError, match="CAPTURE_NOT_CURRENT"):
        bridge.prepare_current()
    await close_composed(socket, network_task, world_task, _source)


@pytest.mark.anyio
async def test_t518_public_snapshot_or_dict_cannot_construct_opaque_read_or_material():
    values = await composed_bridge()
    socket, network_task, world_task, _source, world, _store, _capture, _bridge = values
    from ai_client.discussion.authority_capture_bridge_v2 import (
        OwnerBoundInboundReadV2, ValidatedContextOwnerReceiptV2,
    )
    with pytest.raises(TypeError): ValidatedContextOwnerReceiptV2(object())
    with pytest.raises(TypeError): OwnerBoundInboundReadV2(object())
    with pytest.raises(TypeError): PreparedAuthorityCaptureMaterialV2(
        object(), {"status": "UNLEASED"}, object())
    with pytest.raises(AuthorityCaptureBridgeError, match="AUTHORITY_MATERIAL_REQUIRED"):
        require_leased_authority_capture_v2({"status": "UNLEASED",
                                             "authority": asdict(world.inbound_authority_snapshot())})
    await close_composed(socket, network_task, world_task, _source)


def test_t518_structural_fake_owners_and_invalid_json_pointers_are_rejected():
    from ai_client.discussion.authority_capture_bridge_v2 import (
        _create_capture_read_port_v2, _create_world_authority_read_port_v2,
        _issue_context_owner_receipt_v2, _pointer,
    )
    with pytest.raises(AuthorityCaptureBridgeError, match="PENDING_PROOF_REQUIRED"):
        _issue_context_owner_receipt_v2(SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    with pytest.raises(AuthorityCaptureBridgeError, match="OWNER_ISSUER_MISMATCH"):
        _create_world_authority_read_port_v2(SimpleNamespace(), SimpleNamespace())
    with pytest.raises(AuthorityCaptureBridgeError, match="OWNER_ISSUER_MISMATCH"):
        _create_capture_read_port_v2(SimpleNamespace(), SimpleNamespace())
    for path in ("/payload/~", "/payload/~2", "/payload/00"):
        with pytest.raises(AuthorityCaptureBridgeError, match="SOURCE_PATH_INVALID"):
            _pointer({"payload": ["value"]}, path)


def test_t518_bootstrap_receipt_rejects_wholesale_valid_material_substitution():
    from ai_client.discussion.context import _revalidate_authority_pending_v2
    first = validate_discussion_bootstrap(
        _envelope(), network_game_id="opaque-game", player_id="opaque-player")
    second = validate_discussion_bootstrap(
        _envelope(), network_game_id="opaque-game", player_id="opaque-player")
    first._manifest_material = second._manifest_material
    first._manifest_bytes = second._manifest_bytes
    first.manifest_sha256 = second.manifest_sha256
    first.context = second.context
    first.context_sha256 = second.context_sha256
    with pytest.raises(Exception, match="route is not proven"):
        _revalidate_authority_pending_v2(
            first, object(), object())


@pytest.mark.anyio
async def test_t518_action_phase_and_generation_must_match_current_world_and_trigger():
    from ai_client.discussion.authority_capture_bridge_v2 import _sha
    values = await composed_bridge()
    socket, network_task, world_task, source, world, _store, _capture, bridge = values
    action = source.snapshot().actions[0]
    sidecar = world._inbound_authority._actions[0]
    object.__setattr__(action, "day", action.day + 1)
    object.__setattr__(sidecar, "day", action.day)
    object.__setattr__(sidecar, "typed_value_sha256", _sha(action))
    with pytest.raises(AuthorityCaptureBridgeError, match="ACTION_IDENTITY_MISMATCH"):
        bridge.prepare_current()
    await close_composed(socket, network_task, world_task, source)


@pytest.mark.anyio
async def test_t518_ability_mode_witness_and_capture_epoch_mutations_are_rejected():
    values = await composed_bridge(live_event=(
        "GUARD_SUCCEEDED", {"target_player_id": "opaque-player"}))
    socket, network_task, world_task, source, world, store, _capture, bridge = values
    sidecar = world._inbound_authority._abilities[0]
    object.__setattr__(sidecar, "phase_attribution_mode", "SYNC_REPLAY_PHASE")
    object.__setattr__(sidecar, "reducer_phase_witness_sha256", "0" * 64)
    with pytest.raises(AuthorityCaptureBridgeError, match="ABILITY_PHASE_ATTRIBUTION_MISMATCH"):
        bridge.prepare_current()
    object.__setattr__(sidecar, "phase_attribution_mode", "LIVE_REDUCER_PHASE")
    store._epoch += 1
    with pytest.raises(AuthorityCaptureBridgeError, match="CAPTURE_NOT_CURRENT"):
        bridge.prepare_current()
    await close_composed(socket, network_task, world_task, source)


@pytest.mark.anyio
@pytest.mark.parametrize(("event_type", "payload"), [
    ("INSPECT_RESULT", {"target_player_id": "opaque-player", "result": "wolf"}),
    ("MEDIUM_RESULT", {"target_player_id": "opaque-player", "result": "human"}),
    ("INSPECT_DEAD_ROLE_RESULT", {"target_player_id": "opaque-player", "role_id": "role"}),
    ("GUARD_SUCCEEDED", {"target_player_id": "opaque-player"}),
])
async def test_t518_all_four_live_ability_shapes_are_closed(event_type, payload):
    values = await composed_bridge(live_event=(event_type, payload))
    socket, network_task, world_task, source, _world, _store, _capture, bridge = values
    authority = bridge.prepare_current().disclosure_candidates[0].authority
    assert authority.event_type == event_type
    assert authority.result_id == payload.get("result")
    assert authority.revealed_role_id == payload.get("role_id")
    await close_composed(socket, network_task, world_task, source)


@pytest.mark.anyio
@pytest.mark.parametrize(("event_type", "payload"), [
    ("INSPECT_RESULT", {"target_player_id": "opaque-player", "result": "wolf"}),
    ("MEDIUM_RESULT", {"target_player_id": "opaque-player", "result": "human"}),
    ("INSPECT_DEAD_ROLE_RESULT", {"target_player_id": "opaque-player", "role_id": "role"}),
    ("GUARD_SUCCEEDED", {"target_player_id": "opaque-player"}),
])
async def test_t518_all_four_sync_ability_shapes_are_closed(event_type, payload):
    history = (
        {"type": "game.event", "payload": {"event_type": "PHASE_STARTED",
         "event_payload": {"day": 1, "phase": "day", "phase_ends_at": 10}}},
        {"type": "game.event", "payload": {"event_type": event_type,
                                             "event_payload": payload}},
    )
    values = await composed_bridge(history=history)
    socket, network_task, world_task, source, _world, _store, _capture, bridge = values
    authority = bridge.prepare_current().disclosure_candidates[0].authority
    assert authority.event_type == event_type
    assert authority.result_id == payload.get("result")
    assert authority.revealed_role_id == payload.get("role_id")
    await close_composed(socket, network_task, world_task, source)


@pytest.mark.anyio
async def test_t518_parent_authority_shapes_and_phase_identity_are_exact():
    values = await composed_bridge()
    socket, network_task, world_task, source, _world, _store, _capture, bridge = values
    material = bridge.prepare_current()
    binding = material.action_bindings[0]
    public = material.public_channel_authorities[0]
    assert set(binding.__slots__) == {"schema_version", "option_id", "action_kind",
        "action_generation", "connection_generation", "phase_identity", "action_sha256",
        "source", "channel_authority"}
    assert set(public.__slots__) == {"schema_version", "option_id", "channel_id", "audience",
        "recipient_scope", "context_sha256", "action_source_sha256", "connection_generation",
        "action_generation", "phase_identity", "authority_revision_sha256"}
    assert binding.phase_identity == public.phase_identity == material.phase_identity
    assert len(material.phase_identity) == 64
    await close_composed(socket, network_task, world_task, source)


@pytest.mark.anyio
async def test_t518_disclosure_authority_shape_is_exact():
    values = await composed_bridge(live_event=(
        "INSPECT_RESULT", {"target_player_id": "opaque-player", "result": "wolf"}))
    socket, network_task, world_task, source, _world, _store, _capture, bridge = values
    authority = bridge.prepare_current().disclosure_candidates[0].authority
    assert set(authority.__slots__) == {"schema_version", "authority_kind", "source",
        "evidence_ref", "actor_player_id", "event_type", "target_player_id", "result_id",
        "revealed_role_id", "read_visibility", "disclosure_audience",
        "channel_authority_sha256", "world_version", "fact_revision"}
    assert authority.evidence_ref == {"record_kind": "ability_result", "order": 1,
                                      "visibility": "AUTHORIZED_PRIVATE"}
    assert len(authority.channel_authority_sha256) == 64
    await close_composed(socket, network_task, world_task, source)


@pytest.mark.anyio
async def test_t518_all_supported_action_kinds_keep_exact_received_order_and_fields():
    actions = (
        {"type": "chat", "channel": "opaque-public"},
        {"type": "vote", "valid_targets": ["opaque-player"], "target_count": 1,
         "allows_abstain": True},
        {"type": "co_declare", "claimed_role_ids": ["opaque-role"]},
        {"type": "ability", "ability_id": "guard", "description": None,
         "valid_targets": ["opaque-player"], "target_count": 1, "uses_remaining": None},
    )
    values = await composed_bridge(actions=actions)
    socket, network_task, world_task, source, _world, _store, _capture, bridge = values
    bindings = bridge.prepare_current().action_bindings
    assert tuple(item.option_id for item in bindings) == ("o000", "o001", "o002", "o003")
    assert tuple(item.action_kind for item in bindings) == ("chat", "vote", "co_declare", "ability")
    assert all(len(item.action_sha256) == 64 for item in bindings)
    assert bindings[0].channel_authority is not None
    assert all(item.channel_authority is None for item in bindings[1:])
    await close_composed(socket, network_task, world_task, source)


@pytest.mark.anyio
async def test_t518_co_report_is_not_converted_to_another_action_kind():
    values = await composed_bridge(actions=(
        {"type": "chat", "channel": "opaque-public"}, {"type": "co_report"},
    ))
    socket, network_task, world_task, source, _world, _store, _capture, bridge = values
    with pytest.raises(AuthorityCaptureBridgeError, match="UNSUPPORTED_ACTION_KIND"):
        bridge.prepare_current()
    await close_composed(socket, network_task, world_task, source)


@pytest.mark.anyio
async def test_t520_owner_registration_proof_is_one_shot_and_releases_manifest(monkeypatch):
    import ai_client.discussion.authority_capture_bridge_v2 as bridge_module
    import ai_client.runtime as runtime_module

    captured = {}
    original = bridge_module._consume_authority_pending_proof_v2
    original_revalidate = runtime_module._revalidate_authority_pending_v2

    def revalidate(pending, snapshot, registration):
        foreign = validate_discussion_bootstrap(
            _envelope(), network_game_id="opaque-game", player_id="opaque-player")
        with pytest.raises(Exception, match="not owned by the runtime source"):
            original_revalidate(foreign, snapshot, registration)
        return original_revalidate(pending, snapshot, registration)

    def consume(proof, source, world):
        captured["proof"] = proof
        captured["pending"] = proof._secret_refs["pending"]
        captured["bootstrap_receipt"] = proof._secret_refs["bootstrap_receipt"]
        return original(proof, source, world)

    monkeypatch.setattr(bridge_module, "_consume_authority_pending_proof_v2", consume)
    monkeypatch.setattr(runtime_module, "_revalidate_authority_pending_v2", revalidate)
    values = await composed_bridge()
    socket, network_task, world_task, source, world, store, _capture, bridge = values
    registration = source._authority_owner_registration_v2
    assert registration is source._network._authority_owner_registration_v2
    assert registration is source._authority_capability._authority_owner_registration_v2
    assert registration is world._authority_owner_registration_v2
    assert registration is world._inbound_authority._authority_owner_registration_v2
    assert registration is store._authority_owner_registration_v2
    assert registration.exact_discussion_store is store
    assert registration.registered_receipt is source.context_owner_receipt_v2
    assert captured["proof"]._state == "CONSUMED"
    assert captured["proof"]._secret_refs == {}
    assert captured["pending"]._active_authority_proof_v2 is None
    assert not captured["pending"].manifest_is_retained
    assert captured["bootstrap_receipt"]._manifest_material is None
    assert captured["bootstrap_receipt"]._manifest_bytes is None
    legacy_bound_pending = validate_discussion_bootstrap(
        _envelope(), network_game_id="opaque-game", player_id="opaque-player")
    legacy_receipt = legacy_bound_pending._bootstrap_validation_receipt
    legacy_bound_pending.bind(world.snapshot())
    assert legacy_receipt._manifest_material is None
    assert legacy_receipt._manifest_bytes is None
    discarded_pending = validate_discussion_bootstrap(
        _envelope(), network_game_id="opaque-game", player_id="opaque-player")
    discarded_receipt = discarded_pending._bootstrap_validation_receipt
    discarded_pending.discard_manifest()
    assert discarded_receipt._manifest_material is None
    assert discarded_receipt._manifest_bytes is None
    before = (world.snapshot(), store.snapshot, registration.registered_receipt)
    bridge.prepare_current()
    assert before == (world.snapshot(), store.snapshot, registration.registered_receipt)
    await close_composed(socket, network_task, world_task, source)
    source.close()
    assert registration.lifecycle == "RETIRED"


@pytest.mark.anyio
async def test_t520_actual_owners_reject_fake_receipts_ports_and_second_registration():
    from ai_client.discussion.authority_capture_bridge_v2 import (
        _create_authority_capture_bridge_v2, _create_capture_read_port_v2,
        _create_world_authority_read_port_v2, _register_authority_owners_v2,
    )
    values = await composed_bridge()
    socket, network_task, world_task, source, world, store, _capture, bridge = values
    receipt = source.context_owner_receipt_v2
    with pytest.raises(AuthorityCaptureBridgeError, match="OWNER_ISSUER_MISMATCH"):
        _create_world_authority_read_port_v2(world, SimpleNamespace())
    with pytest.raises(AuthorityCaptureBridgeError, match="OWNER_ISSUER_MISMATCH"):
        _create_capture_read_port_v2(store, SimpleNamespace())
    with pytest.raises(AuthorityCaptureBridgeError, match="BRIDGE_OWNER_MISMATCH"):
        _create_authority_capture_bridge_v2(
            SimpleNamespace(), bridge._capture_port, receipt)
    with pytest.raises(AuthorityCaptureBridgeError, match="BRIDGE_OWNER_MISMATCH"):
        _create_authority_capture_bridge_v2(
            bridge._world_port, SimpleNamespace(), receipt)
    with pytest.raises(AuthorityCaptureBridgeError, match="OWNER_ISSUER_MISMATCH"):
        _create_world_authority_read_port_v2(world, receipt)
    foreign_network = NetworkClient(NetworkClientConfig(
        "ws://offline", "opaque-game", "entry"), MemoryStore(), connector=lambda _uri: None)
    foreign_capability = foreign_network.acquire_inbound_authority_capability()
    with pytest.raises(AuthorityCaptureBridgeError, match="OWNER_REGISTRATION_MISMATCH"):
        _register_authority_owners_v2(
            foreign_network, foreign_capability, SimpleNamespace(),
            SimpleNamespace(), SimpleNamespace())
    assert foreign_network._authority_owner_registration_v2 is None
    assert foreign_capability._authority_owner_registration_v2 is None
    await close_composed(socket, network_task, world_task, source)


@pytest.mark.anyio
async def test_t520_network_generation_and_parent_raw_content_are_revalidated():
    from ai_client.discussion.authority_capture_bridge_v2 import _bytes, _plain, _pointer
    from hashlib import sha256

    values = await composed_bridge()
    socket, network_task, world_task, source, world, _store, _capture, bridge = values
    source._network._state._connection_generation += 1
    with pytest.raises(AuthorityCaptureBridgeError, match="STALE_OWNER_READ"):
        bridge.prepare_current()
    source._network._state._connection_generation -= 1

    sidecar = world._inbound_authority._actions[0]
    authority_store = world._inbound_authority
    parent_slice = authority_store._private_sources[
        (sidecar.parent_source.event_id, sidecar.parent_source.source_path)]
    event = parent_slice.event_object
    payload = _plain(event.payload)
    parent = payload if sidecar.parent_source.source_path == "/payload" else payload["action_state"]
    parent["day"] += 1
    object.__setattr__(event, "payload", payload)
    for ref in (sidecar.source, sidecar.parent_source):
        source_slice = authority_store._private_sources[(ref.event_id, ref.source_path)]
        value = _pointer({"payload": event.payload}, ref.source_path)
        canonical = _bytes(value)
        digest = sha256(canonical).hexdigest()
        object.__setattr__(source_slice, "canonical_source_bytes", canonical)
        object.__setattr__(source_slice, "source_sha256", digest)
        object.__setattr__(ref, "source_sha256", digest)
    with pytest.raises(AuthorityCaptureBridgeError, match="ACTION_PARENT_MISMATCH"):
        bridge.prepare_current()
    await close_composed(socket, network_task, world_task, source)


@pytest.mark.anyio
async def test_t520_closed_authority_objects_have_no_instance_dictionary():
    values = await composed_bridge(history=(
        {"type": "game.event", "payload": {"event_type": "PHASE_STARTED",
         "event_payload": {"day": 1, "phase": "day", "phase_ends_at": 10}}},
        {"type": "game.event", "payload": {"event_type": "GUARD_SUCCEEDED",
         "event_payload": {"target_player_id": "opaque-player"}}},
    ))
    socket, network_task, world_task, source, _world, _store, _capture, bridge = values
    material = bridge.prepare_current()
    closed = (material.action_bindings[0],
              material.public_channel_authorities[0],
              material.disclosure_candidates[0],
              material.disclosure_candidates[0].authority)
    for value in closed:
        assert not hasattr(value, "__dict__")
        with pytest.raises((AttributeError, TypeError)):
            value.injected = True
    await close_composed(socket, network_task, world_task, source)


@pytest.mark.anyio
async def test_t520_all_owner_edges_are_rechecked_before_every_read():
    values = await composed_bridge()
    socket, network_task, world_task, source, world, store, _capture, bridge = values
    registration = source._authority_owner_registration_v2
    network = source._network
    capability = source._authority_capability
    authority = world._inbound_authority
    foreign = NetworkClient(NetworkClientConfig(
        "ws://offline", "opaque-game", "entry"), MemoryStore(), connector=lambda _uri: None)
    foreign_capability = foreign.acquire_inbound_authority_capability()
    edges = [(network, "_authority_capability", foreign_capability),
             (capability, "_client", foreign),
             (source, "_network", foreign),
             (source, "_authority_capability", foreign_capability),
             (source, "_world", object()), (world, "_source", object()),
             (world, "_inbound_authority", object()),
             (authority, "_composition_capability", foreign_capability),
             (store, "_bound", object()),
             (registration, "exact_discussion_store", object()),
             (registration, "exact_discussion_store", None),
             (source, "_bound", object()),
             (source, "_context_owner_receipt_v2", object()),
             (source, "_context_owner_receipt_v2", None),
             (registration, "registered_receipt", object()),
             (registration, "registered_receipt", None)]
    edges.extend((owner, "_authority_owner_registration_v2", None)
                 for owner in (network, capability, source, world, authority, store))
    try:
        for owner, field, replacement in edges:
            original = getattr(owner, field)
            setattr(owner, field, replacement)
            try:
                for read in (bridge._world_port.read_current,
                             bridge._capture_port.read_current, bridge.prepare_current):
                    with pytest.raises(AuthorityCaptureBridgeError, match="OWNER_MISMATCH"):
                        read()
            finally:
                setattr(owner, field, original)
            assert bridge.prepare_current().action_bindings
    finally:
        await close_composed(socket, network_task, world_task, source)


@pytest.mark.anyio
@pytest.mark.parametrize(("index", "field", "replacement"), [
    (0, "channel", "different-channel"),
    (1, "valid_targets", []),
    (2, "claimed_role_ids", ["different-role"]),
    (3, "uses_remaining", 1),
])
async def test_t520_coherent_raw_hashes_cannot_replace_typed_action(index, field, replacement):
    from hashlib import sha256
    from ai_client.discussion.authority_capture_bridge_v2 import _bytes, _plain, _pointer

    actions = (
        {"type": "chat", "channel": "opaque-public"},
        {"type": "vote", "valid_targets": ["opaque-player"], "target_count": 1,
         "allows_abstain": True},
        {"type": "co_declare", "claimed_role_ids": ["opaque-role"]},
        {"type": "ability", "ability_id": "guard", "description": None,
         "valid_targets": ["opaque-player"], "target_count": 1, "uses_remaining": None},
    )
    values = await composed_bridge(actions=actions)
    socket, network_task, world_task, source, world, _store, _capture, bridge = values
    authority = world._inbound_authority
    sidecar = authority._actions[index]
    parent_slice = authority._private_sources[
        (sidecar.parent_source.event_id, sidecar.parent_source.source_path)]
    event = parent_slice.event_object
    payload = _plain(event.payload)
    parent = payload if sidecar.parent_source.source_path == "/payload" else payload["action_state"]
    parent["actions"][index][field] = replacement
    object.__setattr__(event, "payload", payload)
    # Keep raw slices and hashes internally consistent: typed equality is the missing check.
    for item in authority._actions:
        for ref in (item.source, item.parent_source):
            source_slice = authority._private_sources[(ref.event_id, ref.source_path)]
            canonical = _bytes(_pointer({"payload": event.payload}, ref.source_path))
            digest = sha256(canonical).hexdigest()
            object.__setattr__(source_slice, "canonical_source_bytes", canonical)
            object.__setattr__(source_slice, "source_sha256", digest)
            object.__setattr__(ref, "source_sha256", digest)
    try:
        with pytest.raises(AuthorityCaptureBridgeError, match="ACTION_RAW_TYPED_MISMATCH"):
            bridge.prepare_current()
    finally:
        await close_composed(socket, network_task, world_task, source)


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["owner_edge", "pending_identity", "receipt_allocation"])
async def test_t520_failed_consume_retires_proof_without_partial_publish(monkeypatch, failure):
    import ai_client.discussion.authority_capture_bridge_v2 as bridge_module
    from ai_client.discussion.context import (
        _EMPTY_AUTHORITY_PROOF_REFS, _revalidate_authority_pending_v2,
    )

    original = bridge_module._consume_authority_pending_proof_v2
    observations = []

    def consume(proof, source, world):
        pending = proof._secret_refs["pending"]
        bootstrap = proof._secret_refs["bootstrap_receipt"]
        registration = source._authority_owner_registration_v2
        if failure == "owner_edge":
            owner, field = source._network, "_authority_capability"
        elif failure == "pending_identity":
            owner, field = source, "_pending"
        else:
            owner, field = bridge_module, "ValidatedContextOwnerReceiptV2"
        saved = getattr(owner, field)

        def fail_allocation(*args, **kwargs):
            raise MemoryError("synthetic allocation failure")

        setattr(owner, field, fail_allocation if failure == "receipt_allocation" else object())
        try:
            with pytest.raises((AuthorityCaptureBridgeError, MemoryError)):
                original(proof, source, world)
            assert proof._state == "RETIRED"
            assert proof._secret_refs is _EMPTY_AUTHORITY_PROOF_REFS
            assert pending._active_authority_proof_v2 is None
            assert source._bound is None
            assert source._context_owner_receipt_v2 is None
            assert registration.registered_receipt is None
            assert pending.manifest_is_retained
            assert bootstrap._manifest_material is pending._manifest_material
            assert bootstrap._manifest_bytes is pending._manifest_bytes
        finally:
            setattr(owner, field, saved)
        fresh = _revalidate_authority_pending_v2(pending, world.snapshot(), registration)
        assert fresh is not proof
        result = original(fresh, source, world)
        assert fresh._secret_refs is _EMPTY_AUTHORITY_PROOF_REFS
        with pytest.raises(AuthorityCaptureBridgeError):
            original(fresh, source, world)
        assert source._bound is result[0]
        assert source._context_owner_receipt_v2 is result[1]
        observations.append(failure)
        return result

    monkeypatch.setattr(bridge_module, "_consume_authority_pending_proof_v2", consume)
    values = await composed_bridge()
    socket, network_task, world_task, source, _world, _store, _capture, bridge = values
    try:
        assert observations == [failure]
        assert bridge.prepare_current().action_bindings
    finally:
        await close_composed(socket, network_task, world_task, source)


@pytest.mark.anyio
async def test_t520_explicit_discard_retires_actual_issued_proof(monkeypatch):
    import ai_client.discussion.authority_capture_bridge_v2 as bridge_module
    from ai_client.discussion.context import (
        _EMPTY_AUTHORITY_PROOF_REFS, _revalidate_authority_pending_v2,
    )

    original = bridge_module._consume_authority_pending_proof_v2
    discarded = []

    def consume(proof, source, world):
        pending = proof._secret_refs["pending"]
        bootstrap = proof._secret_refs["bootstrap_receipt"]
        pending.discard_manifest()
        assert proof._state == "RETIRED"
        assert proof._secret_refs is _EMPTY_AUTHORITY_PROOF_REFS
        assert pending._active_authority_proof_v2 is None
        assert not pending.manifest_is_retained
        assert bootstrap._manifest_material is bootstrap._manifest_bytes is None
        with pytest.raises(AuthorityCaptureBridgeError):
            original(proof, source, world)
        assert source._bound is source._context_owner_receipt_v2 is None
        discarded.append(proof)
        # Only a new, fully validated bootstrap can restart this unbound fixture.
        replacement = validate_discussion_bootstrap(
            _envelope(), network_game_id="opaque-game", player_id="opaque-player")
        source._pending = replacement
        fresh = _revalidate_authority_pending_v2(
            replacement, world.snapshot(), source._authority_owner_registration_v2)
        return original(fresh, source, world)

    monkeypatch.setattr(bridge_module, "_consume_authority_pending_proof_v2", consume)
    values = await composed_bridge()
    socket, network_task, world_task, source, _world, _store, _capture, bridge = values
    try:
        assert len(discarded) == 1
        assert bridge.prepare_current().action_bindings
    finally:
        await close_composed(socket, network_task, world_task, source)
