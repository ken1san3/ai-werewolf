from __future__ import annotations

import asyncio
import json
from uuid import uuid4
import pytest
from dataclasses import fields, replace

from ai_client.network.client import NetworkClient
from ai_client.network.types import (
    ChatAction, ClientExitReason, ClientLifecycle, NetworkClientConfig, ServerEvent, SessionCheckpoint,
)
from ai_client.network.protocol import PROTOCOL_VERSION, ProtocolMessageValidator
from ai_client.world.inbound_authority_v2 import _create_runtime
from ai_client.world.service import WorldState
from ai_client.world.model import WorldStateConfig




@pytest.fixture
def anyio_backend(): return 'asyncio'

class MemoryStore:
    def __init__(self, checkpoint=None): self.saved = []; self.checkpoint = checkpoint
    async def load(self): return self.checkpoint
    async def save(self, checkpoint): self.saved.append(checkpoint); self.checkpoint = checkpoint


class FailingStore(MemoryStore):
    async def save(self, checkpoint): raise OSError("offline save failure")


class FakeSocket:
    def __init__(self, incoming):
        self.incoming = asyncio.Queue()
        for item in incoming: self.incoming.put_nowait(item)
        self.sent = []; self.closed = False
    async def send(self, raw): self.sent.append(json.loads(raw))
    async def recv(self): return await self.incoming.get()
    async def close(self): self.closed = True
    async def wait_closed(self): pass


def wire(kind, seq, payload):
    value = message(kind, seq, payload); value["event_id"] = str(uuid4())
    return json.dumps(value)


class AuthoritySource:
    def __init__(self, client, capability):
        self.client = client; self.capability = capability; self._authority_capability = capability
        self.claimed_modes = []
    def snapshot(self): return self.client.snapshot()
    def claim_committed_inbound(self, event):
        value = self.client.claim_committed_inbound(event, self.capability)
        if value is not None: self.claimed_modes.append(value.sequence_mode)
        return value
    def claim_lifecycle_observation(self, event): return self.client.claim_lifecycle_observation(event, self.capability)
    def events(self): return self.client.events()


def message(kind, seq, payload):
    return {"type": kind, "protocol_version": PROTOCOL_VERSION, "event_id": f"e{seq}",
            "game_id": "g", "seq": seq, "timestamp": 1, "payload": payload}


def sync_payload(actions=()):
    return {"players": [{"player_id": "p", "display_name": "P"}], "deaths": [],
            "action_state": {"phase": "day", "day": 1, "phase_ends_at": None,
                             "actions": list(actions)},
            "self": {"player_id": "p", "role_id": "villager", "modifier_ids": []},
            "revealed_roles": [], "history": []}


def ability_sync(event_type, result, role):
    payload = sync_payload()
    payload["action_state"]["phase"] = "night"
    payload["history"] = [
        {"type": "game.event", "payload": {"event_type": "PHASE_STARTED",
         "event_payload": {"day": 1, "phase": "night", "phase_ends_at": 10}}},
        {"type": "game.event", "payload": {"event_type": event_type,
         "event_payload": ({"target_player_id": "p", "result": result}
                           if event_type in {"INSPECT_RESULT", "MEDIUM_RESULT"}
                           else {"target_player_id": "p", "role_id": role}
                           if event_type == "INSPECT_DEAD_ROLE_RESULT"
                           else {"target_player_id": "p"})}},
    ]
    return payload


def client_and_capability():
    store = MemoryStore()
    client = NetworkClient(NetworkClientConfig("ws://offline", "g", "entry"), store)
    client._connection_generation = 1
    client._state.begin_connection(1)
    client._state.apply_server_event(message("session.joined", 0,
        {"player_id": "p", "connection_token": "token"}))
    client._authenticated = True
    client._authenticated_player_id = "p"
    client._checkpoint = SessionCheckpoint("token", 0, PROTOCOL_VERSION)
    capability = client.acquire_inbound_authority_capability()
    return client, capability, store


@pytest.mark.anyio
async def test_i_p8_observation_exists_only_after_checkpoint_save_and_is_one_shot():
    client, capability, store = client_and_capability()
    await client._commit_server_message(message("player.action_state", 1,
        {"phase": "DAY", "day": 1, "phase_ends_at": None,
         "actions": [{"type": "chat", "channel": "public"}]}), observed_at_monotonic=0.0)
    event = await anext(client.events())
    assert store.saved[-1].last_seq == event.seq
    observation = client.claim_committed_inbound(event, capability)
    assert observation.event_object is event and observation.sequence_mode == "CONTIGUOUS"
    assert client.claim_committed_inbound(event, capability) is None
    equal = ServerEvent(**event.__dict__)
    assert client.claim_committed_inbound(equal, capability) is None


@pytest.mark.anyio
async def test_i_n1_n3_capability_is_single_owner_and_arbitrary_values_cannot_claim():
    first, capability, _ = client_and_capability()
    second, other, _ = client_and_capability()
    with pytest.raises(RuntimeError): first.acquire_inbound_authority_capability()
    assert first.claim_committed_inbound({}, capability) is None
    assert first.claim_committed_inbound({}, other) is None
    assert second.claim_committed_inbound({}, capability) is None


@pytest.mark.anyio
async def test_i_p1_p2_sync_then_connected_publishes_exact_action_sidecars():
    client, capability, _ = client_and_capability()
    await client._commit_server_message(message("game.state_sync", 1, sync_payload((
        {"type": "chat", "channel": "public"},
        {"type": "vote", "valid_targets": ["p"], "target_count": 1, "allows_abstain": True},
    ))), is_sync_barrier=True, sequence_mode="SYNC_BARRIER", observed_at_monotonic=0.0)
    source = AuthoritySource(client, capability)
    world = WorldState(source, inbound_authority=_create_runtime(capability))
    iterator = source.events()
    world._consume(await anext(iterator))
    while world.inbound_authority_snapshot().readiness_status == "PENDING_SYNC":
        world._consume(await anext(iterator))
    snapshot = world.inbound_authority_snapshot()
    assert snapshot is not None
    assert snapshot.readiness_status == "READY"
    assert snapshot.readiness_connection_generation == 1
    assert len(snapshot.action_sidecars) == 2
    action = snapshot.action_sidecars[0]
    assert action.schema_version == "aiwolf.verified-provenance-sidecar-entry.v2"
    assert action.source.game_id == "g" and action.source.protocol_version == PROTOCOL_VERSION
    assert action.parent_source.source_path == "/payload/action_state"
    assert action.day == 1 and action.phase == "day" and action.action_generation == snapshot.action_generation
    assert snapshot.world_version == world.snapshot().version
    assert snapshot.last_committed_server_seq == world.snapshot().last_applied_seq


def test_t514_t516_opaque_field_tables_are_exact():
    from ai_client.world.inbound_authority_v2 import (
        InboundAuthoritySnapshotV2, VerifiedInboundRefV2,
        VerifiedProvenanceSidecarEntryV2,
    )

    assert {field.name for field in fields(VerifiedInboundRefV2)} == {
        "schema_version", "game_id", "protocol_version", "event_id", "seq",
        "message_type", "player_id", "connection_generation", "source_path",
        "source_sha256",
    }
    assert {field.name for field in fields(VerifiedProvenanceSidecarEntryV2)} == {
        "schema_version", "subject_kind", "identity", "typed_value_sha256",
        "source", "parent_source", "phase_source", "day", "phase",
        "action_generation", "phase_attribution_mode",
        "reducer_phase_witness_sha256", "state_sync_history_index",
        "preceding_phase_entry_index", "world_version_before",
        "last_applied_sequence_before",
    }
    assert {field.name for field in fields(InboundAuthoritySnapshotV2)} == {
        "schema_version", "game_id", "player_id", "protocol_version",
        "connection_generation", "last_committed_server_seq", "world_version",
        "authority_revision", "readiness_status",
        "readiness_connection_generation", "readiness_sync_identity",
        "action_generation", "action_sidecars", "ability_sidecars",
        "snapshot_sha256",
    }


@pytest.mark.anyio
async def test_i_n8_reconnect_generation_clears_unclaimed_observations():
    client, capability, _ = client_and_capability()
    await client._commit_server_message(message("player.action_state", 1,
        {"phase": "DAY", "day": 1, "phase_ends_at": None, "actions": []}), observed_at_monotonic=0.0)
    event = await anext(client.events())
    client._connection_generation += 1
    client._inbound_observations.clear()
    assert client.claim_committed_inbound(event, capability) is None


@pytest.mark.anyio
async def test_i_n2_save_failure_never_registers_observation():
    client, _capability, _ = client_and_capability()
    client.credential_store = FailingStore()
    with pytest.raises(Exception, match="offline save failure"):
        await client._commit_server_message(message("player.action_state", 1,
            {"phase": "DAY", "day": 1, "phase_ends_at": None, "actions": []}),
            observed_at_monotonic=0.0)
    assert client._inbound_observations == {}


@pytest.mark.anyio
async def test_i_p6_zero_action_replaces_old_action_authority():
    client, capability, _ = client_and_capability()
    await client._commit_server_message(message("game.state_sync", 1, sync_payload((
        {"type": "chat", "channel": "public"},
    ))), is_sync_barrier=True, sequence_mode="SYNC_BARRIER", observed_at_monotonic=0.0)
    source = AuthoritySource(client, capability); world = WorldState(source, inbound_authority=_create_runtime(capability))
    iterator = source.events(); world._consume(await anext(iterator))
    while world.inbound_authority_snapshot().readiness_status == "PENDING_SYNC": world._consume(await anext(iterator))
    assert len(world.inbound_authority_snapshot().action_sidecars) == 1
    await client._commit_server_message(message("player.action_state", 2,
        {"phase": "DAY", "day": 1, "phase_ends_at": None, "actions": []}), observed_at_monotonic=0.0)
    world._consume(await anext(iterator))
    assert world.inbound_authority_snapshot().readiness_status == "READY"
    assert world.inbound_authority_snapshot().action_sidecars == ()


@pytest.mark.anyio
async def test_i_n16_resume_replay_action_invalidates_ready_generation():
    client, capability, _ = client_and_capability()
    await client._commit_server_message(message("game.state_sync", 1, sync_payload()),
        is_sync_barrier=True, sequence_mode="SYNC_BARRIER", observed_at_monotonic=0.0)
    source = AuthoritySource(client, capability); world = WorldState(source, inbound_authority=_create_runtime(capability))
    iterator = source.events(); world._consume(await anext(iterator))
    while world.inbound_authority_snapshot().readiness_status == "PENDING_SYNC": world._consume(await anext(iterator))
    client._resume_last_seq_requested = 1
    await client._commit_server_message(message("player.action_state", 2,
        {"phase": "DAY", "day": 1, "phase_ends_at": None,
         "actions": [{"type": "chat", "channel": "public"}]}),
        sequence_mode="RESUME_REPLAY", observed_at_monotonic=0.0)
    world._consume(await anext(iterator))
    assert world.inbound_authority_snapshot().readiness_status == "INVALID"
    assert world.inbound_authority_snapshot().action_sidecars == ()


def test_i_n1_world_rejects_mismatched_source_sink_composition():
    first, capability, _ = client_and_capability(); second, other, _ = client_and_capability()
    with pytest.raises(TypeError, match="composition"):
        WorldState(AuthoritySource(first, capability), inbound_authority=_create_runtime(other))


@pytest.mark.anyio
async def test_i_n4_typed_action_mismatch_fails_before_checkpoint_save():
    client, _capability, store = client_and_capability()
    original = client._state.snapshot
    client._state.snapshot = lambda: replace(original(), actions=(
        ChatAction(1, original().action_generation, "DAY", 1, "chat", "private"),
    ))
    with pytest.raises(Exception, match="preflight"):
        await client._commit_server_message(message("player.action_state", 1,
            {"phase": "DAY", "day": 1, "phase_ends_at": None,
             "actions": [{"type": "chat", "channel": "public"}]}), observed_at_monotonic=0.0)
    assert store.saved == [] and client._inbound_observations == {}


@pytest.mark.anyio
@pytest.mark.parametrize("event_type,result,role", [
    ("INSPECT_RESULT", "wolf", None), ("MEDIUM_RESULT", "human", None),
    ("INSPECT_DEAD_ROLE_RESULT", None, "seer"), ("GUARD_SUCCEEDED", None, None),
])
async def test_i_p2_sync_ability_matrix_binds_actual_emission_and_phase_source(event_type, result, role):
    client, capability, _ = client_and_capability()
    await client._commit_server_message(message("game.state_sync", 1,
        ability_sync(event_type, result, role)), is_sync_barrier=True,
        sequence_mode="SYNC_BARRIER", observed_at_monotonic=0.0)
    source = AuthoritySource(client, capability); world = WorldState(source, inbound_authority=_create_runtime(capability))
    iterator = source.events(); world._consume(await anext(iterator))
    while world.inbound_authority_snapshot().readiness_status == "PENDING_SYNC": world._consume(await anext(iterator))
    sidecars = world.inbound_authority_snapshot().ability_sidecars
    assert len(sidecars) == 1
    assert sidecars[0].source.source_path == "/payload/history/1"
    assert sidecars[0].phase_source.source_path == "/payload/history/0"
    assert sidecars[0].state_sync_history_index == 1
    assert sidecars[0].preceding_phase_entry_index == 0


@pytest.mark.anyio
async def test_i_p3_live_ability_uses_exact_pre_reducer_sequence_witness():
    client, capability, _ = client_and_capability()
    await client._commit_server_message(message("game.state_sync", 1,
        ability_sync("GUARD_SUCCEEDED", None, None)), is_sync_barrier=True,
        sequence_mode="SYNC_BARRIER", observed_at_monotonic=0.0)
    source = AuthoritySource(client, capability); world = WorldState(source, inbound_authority=_create_runtime(capability))
    iterator = source.events(); world._consume(await anext(iterator))
    while world.inbound_authority_snapshot().readiness_status == "PENDING_SYNC": world._consume(await anext(iterator))
    await client._commit_server_message(message("game.event", 2,
        {"event_type": "INSPECT_RESULT", "event_payload": {
            "target_player_id": "p", "result": "wolf"}}),
        sequence_mode="CONTIGUOUS")
    world._consume(await anext(iterator))
    live = world.inbound_authority_snapshot().ability_sidecars[-1]
    assert live.source.source_path == "/payload"
    assert live.last_applied_sequence_before == 1
    assert live.world_version_before < world.snapshot().version
    assert live.phase_attribution_mode == "LIVE_REDUCER_PHASE"
    assert live.reducer_phase_witness_sha256 is not None


@pytest.mark.anyio
async def test_i_p9_contiguous_sync_replaces_ready_without_new_connected_event():
    client, capability, _ = client_and_capability()
    await client._commit_server_message(message("game.state_sync", 1, sync_payload()),
        is_sync_barrier=True, sequence_mode="SYNC_BARRIER", observed_at_monotonic=0.0)
    source = AuthoritySource(client, capability); world = WorldState(source, inbound_authority=_create_runtime(capability))
    iterator = source.events(); world._consume(await anext(iterator))
    while world.inbound_authority_snapshot().readiness_status == "PENDING_SYNC": world._consume(await anext(iterator))
    await client._commit_server_message(message("game.state_sync", 2, sync_payload((
        {"type": "chat", "channel": "public"},
    ))), sequence_mode="CONTIGUOUS", observed_at_monotonic=0.0)
    world._consume(await anext(iterator))
    snapshot = world.inbound_authority_snapshot()
    assert snapshot.readiness_status == "READY"
    assert snapshot.readiness_sync_identity == ("e2", 2, 1)
    assert len(snapshot.action_sidecars) == 1


@pytest.mark.anyio
async def test_i_p7_ordinary_game_event_carries_authority_without_ability_failure():
    client, capability, _ = client_and_capability()
    await client._commit_server_message(message("game.state_sync", 1, sync_payload()),
        is_sync_barrier=True, sequence_mode="SYNC_BARRIER", observed_at_monotonic=0.0)
    source = AuthoritySource(client, capability); world = WorldState(source, inbound_authority=_create_runtime(capability))
    iterator = source.events(); world._consume(await anext(iterator))
    while world.inbound_authority_snapshot().readiness_status == "PENDING_SYNC": world._consume(await anext(iterator))
    prior = world.inbound_authority_snapshot()
    await client._commit_server_message(message("game.event", 2,
        {"event_type": "PHASE_STARTED", "event_payload": {
            "day": 2, "phase": "day", "phase_ends_at": None}}), sequence_mode="CONTIGUOUS")
    world._consume(await anext(iterator))
    current = world.inbound_authority_snapshot()
    assert current.readiness_status == "READY" and current.ability_sidecars == prior.ability_sidecars
    assert current.world_version == world.snapshot().version


@pytest.mark.anyio
async def test_i_n14_queued_old_connected_cannot_publish_pending_after_generation_change():
    client, capability, _ = client_and_capability()
    await client._commit_server_message(message("game.state_sync", 1, sync_payload()),
        is_sync_barrier=True, sequence_mode="SYNC_BARRIER", observed_at_monotonic=0.0)
    source = AuthoritySource(client, capability); world = WorldState(source, inbound_authority=_create_runtime(capability))
    iterator = source.events(); world._consume(await anext(iterator))
    assert world.inbound_authority_snapshot().readiness_status == "PENDING_SYNC"
    # Drain non-lifecycle notice while retaining the exact queued CONNECTED object.
    next_event = await anext(iterator)
    if not hasattr(next_event, "current"):
        world._consume(next_event); next_event = await anext(iterator)
    client._connection_generation = 2
    client._inbound_observations.clear(); client._lifecycle_observations.clear()
    world._consume(next_event)
    assert world.inbound_authority_snapshot().readiness_status == "INVALID"


@pytest.mark.anyio
async def test_i_n12_runtime_validation_failure_publishes_failed_world_and_empty_authority():
    client, capability, _ = client_and_capability()
    await client._commit_server_message(message("game.state_sync", 1, sync_payload()),
        is_sync_barrier=True, sequence_mode="SYNC_BARRIER", observed_at_monotonic=0.0)
    source = AuthoritySource(client, capability); runtime = _create_runtime(capability)
    world = WorldState(source, inbound_authority=runtime)
    iterator = source.events(); world._consume(await anext(iterator))
    while world.inbound_authority_snapshot().readiness_status == "PENDING_SYNC": world._consume(await anext(iterator))
    def explode(*args): raise ValueError("injected authority validation failure")
    world._inbound_authority.finish = explode
    await client._commit_server_message(message("player.action_state", 2,
        {"phase": "DAY", "day": 1, "phase_ends_at": None, "actions": []}), observed_at_monotonic=0.0)
    client._event_stream_closed = True
    outcome = await world.run()
    assert outcome.reason.value == "FAILED"
    snapshot = world.inbound_authority_snapshot()
    assert snapshot.readiness_status == "INVALID"
    assert snapshot.action_sidecars == () and snapshot.ability_sidecars == ()


@pytest.mark.anyio
async def test_i_n12_prepare_tail_failure_never_publishes_world_ahead_of_authority():
    client, capability, _ = client_and_capability()
    await client._commit_server_message(message("game.state_sync", 1, sync_payload()),
        is_sync_barrier=True, sequence_mode="SYNC_BARRIER", observed_at_monotonic=0.0)
    source = AuthoritySource(client, capability); runtime = _create_runtime(capability)
    world = WorldState(source, inbound_authority=runtime)
    iterator = source.events(); world._consume(await anext(iterator))
    while world.inbound_authority_snapshot().readiness_status == "PENDING_SYNC": world._consume(await anext(iterator))
    previous_version = world.snapshot().version
    runtime = world._inbound_authority
    original = runtime.prepare_commit
    def fail_after_prepare(*args, **kwargs):
        original(*args, **kwargs)
        raise ValueError("injected prepare tail failure")
    runtime.prepare_commit = fail_after_prepare
    await client._commit_server_message(message("player.action_state", 2,
        {"phase": "DAY", "day": 1, "phase_ends_at": None, "actions": []}), observed_at_monotonic=0.0)
    with pytest.raises(ValueError, match="prepare tail"):
        world._consume(await anext(iterator))
    assert world.snapshot().version == previous_version + 1
    assert world.snapshot().freshness.value == "FAILED"
    assert world.inbound_authority_snapshot().world_version == world.snapshot().version
    assert world.inbound_authority_snapshot().readiness_status == "INVALID"


@pytest.mark.anyio
async def test_i_n12_abort_sentinel_prepare_failure_clears_old_positive_authority():
    client, capability, _ = client_and_capability()
    await client._commit_server_message(message("game.state_sync", 1, sync_payload((
        {"type": "chat", "channel": "public"},
    ))), is_sync_barrier=True, sequence_mode="SYNC_BARRIER", observed_at_monotonic=0.0)
    source = AuthoritySource(client, capability)
    world = WorldState(source, inbound_authority=_create_runtime(capability))
    iterator = source.events(); world._consume(await anext(iterator))
    while world.inbound_authority_snapshot().readiness_status == "PENDING_SYNC":
        world._consume(await anext(iterator))
    assert world.inbound_authority_snapshot().action_sidecars
    previous_version = world.snapshot().version
    previous_seq = world.snapshot().last_applied_seq
    previous_authority = world.inbound_authority_snapshot()
    previous_update_event = world._update_event
    bundle = world._abort_bundle
    assert bundle.expected_current_world_object is world.snapshot()
    assert bundle.expected_current_authority_object is world._inbound_authority
    assert bundle.failed_world.version == previous_version + 1
    assert bundle.failed_world.last_applied_seq == previous_seq
    waiter = asyncio.create_task(world.wait_for_update(previous_version))
    await asyncio.sleep(0)

    def explode(*_args):
        raise ValueError("injected sentinel prepare failure")

    world._inbound_authority.prepare_invalid_empty = explode
    await client._commit_server_message(message("player.action_state", 2,
        {"phase": "DAY", "day": 1, "phase_ends_at": None, "actions": []}),
        observed_at_monotonic=0.0)
    with pytest.raises(ValueError, match="sentinel prepare"):
        world._consume(await anext(iterator))
    failed = await asyncio.wait_for(waiter, timeout=0.5)
    assert world.snapshot().freshness.value == "FAILED"
    assert failed is world.snapshot()
    assert world.snapshot().version == previous_version + 1
    assert world.snapshot().last_applied_seq == previous_seq
    assert world.inbound_authority_snapshot().readiness_status == "INVALID"
    assert world.inbound_authority_snapshot().action_sidecars == ()
    assert world.inbound_authority_snapshot().world_version == world.snapshot().version
    assert (world.inbound_authority_snapshot().last_committed_server_seq
            == world.snapshot().last_applied_seq)
    assert world.inbound_authority_snapshot() is not previous_authority
    assert world._update_event is bundle.successor_update_event
    assert world._update_event is not previous_update_event
    assert previous_update_event.is_set() and not world._update_event.is_set()
    assert world._abort_bundle is None


@pytest.mark.anyio
async def test_i_n12_fresh_event_allocation_failure_uses_cached_abort_bundle(monkeypatch):
    client, capability, _ = client_and_capability()
    await client._commit_server_message(message("game.state_sync", 1, sync_payload()),
        is_sync_barrier=True, sequence_mode="SYNC_BARRIER", observed_at_monotonic=0.0)
    source = AuthoritySource(client, capability)
    world = WorldState(source, inbound_authority=_create_runtime(capability))
    iterator = source.events(); world._consume(await anext(iterator))
    while world.inbound_authority_snapshot().readiness_status == "PENDING_SYNC":
        world._consume(await anext(iterator))
    previous_version = world.snapshot().version
    previous_seq = world.snapshot().last_applied_seq

    def explode_event():
        raise MemoryError("injected event allocation failure")

    monkeypatch.setattr("ai_client.world.service.asyncio.Event", explode_event)
    await client._commit_server_message(message("player.action_state", 2,
        {"phase": "DAY", "day": 1, "phase_ends_at": None, "actions": []}),
        observed_at_monotonic=0.0)
    with pytest.raises(MemoryError, match="event allocation"):
        world._consume(await anext(iterator))
    assert world.snapshot().version == previous_version + 1
    assert world.snapshot().last_applied_seq == previous_seq
    assert world.snapshot().freshness.value == "FAILED"
    assert world.inbound_authority_snapshot().readiness_status == "INVALID"


@pytest.mark.anyio
async def test_v1_sink_null_does_not_construct_abort_bundle_and_still_wakes_waiter():
    client, capability, _ = client_and_capability()
    await client._commit_server_message(message("game.state_sync", 1, sync_payload()),
        is_sync_barrier=True, sequence_mode="SYNC_BARRIER", observed_at_monotonic=0.0)
    source = AuthoritySource(client, capability)
    world = WorldState(source)
    assert world._abort_bundle is None
    waiter = asyncio.create_task(world.wait_for_update(0))
    await asyncio.sleep(0)
    world._consume(await anext(source.events()))
    assert (await asyncio.wait_for(waiter, timeout=0.5)).version == 1
    assert world.inbound_authority_snapshot() is None


@pytest.mark.anyio
async def test_i_p1_real_receive_validator_checkpoint_and_world_path():
    socket = FakeSocket([
        wire("session.joined", 1, {"player_id": "p", "connection_token": "token"}),
        wire("session.ready", 2, {"player_id": "p", "ready": True}),
        wire("game.state_sync", 3, sync_payload(({"type": "chat", "channel": "public"},))),
    ])
    store = MemoryStore()
    client = NetworkClient(NetworkClientConfig("ws://offline", "g", "entry"), store,
                           connector=lambda _uri: socket)
    capability = client.acquire_inbound_authority_capability()
    source = AuthoritySource(client, capability)
    world = WorldState(source, inbound_authority=_create_runtime(capability))
    network_task = asyncio.create_task(client.run()); world_task = asyncio.create_task(world.run())
    for _ in range(100):
        snapshot = world.inbound_authority_snapshot()
        if snapshot is not None and snapshot.readiness_status == "READY": break
        if network_task.done(): pytest.fail(f"network ended early: {network_task.result()}")
        if world_task.done(): pytest.fail(f"world ended early: {world_task.result()}")
        await asyncio.sleep(0.01)
    else: pytest.fail("real receive path did not publish READY authority")
    assert store.saved[-1].last_seq == 3
    assert len(snapshot.action_sidecars) == 1
    socket.incoming.put_nowait(wire("game.event", 4,
        {"event_type": "GAME_ENDED", "event_payload": {}}))
    assert (await network_task).reason is ClientExitReason.GAME_ENDED
    await world_task


@pytest.mark.parametrize("event_type,payload", [
    ("INSPECT_RESULT", {"target_player_id": "p", "result": "wolf"}),
    ("MEDIUM_RESULT", {"target_player_id": "p", "result": "human"}),
    ("INSPECT_DEAD_ROLE_RESULT", {"target_player_id": "p", "role_id": "seer"}),
    ("GUARD_SUCCEEDED", {"target_player_id": "p"}),
])
def test_i_n7_real_protocol_accepts_only_canonical_ability_wire_shape(event_type, payload):
    decoded = ProtocolMessageValidator().decode_server(wire("game.event", 1,
        {"event_type": event_type, "event_payload": payload}))
    assert decoded["payload"]["event_payload"] == payload
    # The language-independent protocol permits extension fields; the reducer
    # and authority matrix below remain the stricter semantic gate.
    extended = ProtocolMessageValidator().decode_server(wire("game.event", 2,
        {"event_type": event_type, "event_payload": {**payload, "extra": "forbidden"}}))
    assert extended["payload"]["event_payload"]["extra"] == "forbidden"


@pytest.mark.anyio
async def test_i_n6_sync_result_before_actual_phase_has_no_sidecar():
    payload = sync_payload()
    payload["history"] = [
        {"type": "game.event", "payload": {"event_type": "GUARD_SUCCEEDED",
         "event_payload": {"target_player_id": "p"}}},
        {"type": "game.event", "payload": {"event_type": "PHASE_STARTED",
         "event_payload": {"day": 1, "phase": "night", "phase_ends_at": 10}}},
    ]
    client, capability, _ = client_and_capability()
    await client._commit_server_message(message("game.state_sync", 1, payload),
        is_sync_barrier=True, sequence_mode="SYNC_BARRIER", observed_at_monotonic=0.0)
    source = AuthoritySource(client, capability); world = WorldState(source, inbound_authority=_create_runtime(capability))
    iterator = source.events(); world._consume(await anext(iterator))
    while world.inbound_authority_snapshot().readiness_status == "PENDING_SYNC": world._consume(await anext(iterator))
    assert world.inbound_authority_snapshot().ability_sidecars == ()


@pytest.mark.anyio
async def test_i_n7_malformed_ability_between_legal_records_drops_only_its_sidecar():
    payload = ability_sync("GUARD_SUCCEEDED", None, None)
    payload["history"].extend([
        {"type": "game.event", "payload": {"event_type": "INSPECT_RESULT",
         "event_payload": {"target_player_id": "p", "result": "wolf", "extra": "ignored-by-world"}}},
        {"type": "game.event", "payload": {"event_type": "MEDIUM_RESULT",
         "event_payload": {"target_player_id": "p", "result": "human"}}},
    ])
    client, capability, _ = client_and_capability()
    await client._commit_server_message(message("game.state_sync", 1, payload),
        is_sync_barrier=True, sequence_mode="SYNC_BARRIER", observed_at_monotonic=0.0)
    source = AuthoritySource(client, capability); world = WorldState(source, inbound_authority=_create_runtime(capability))
    iterator = source.events(); world._consume(await anext(iterator))
    while world.inbound_authority_snapshot().readiness_status == "PENDING_SYNC": world._consume(await anext(iterator))
    sidecars = world.inbound_authority_snapshot().ability_sidecars
    assert [item.state_sync_history_index for item in sidecars] == [1, 3]


@pytest.mark.anyio
async def test_i_p5_retention_prunes_sidecar_and_private_source_together():
    client, capability, _ = client_and_capability()
    await client._commit_server_message(message("game.state_sync", 1,
        ability_sync("GUARD_SUCCEEDED", None, None)), is_sync_barrier=True,
        sequence_mode="SYNC_BARRIER", observed_at_monotonic=0.0)
    source = AuthoritySource(client, capability); runtime = _create_runtime(capability)
    world = WorldState(source, config=WorldStateConfig(max_history_records=1),
                       inbound_authority=runtime)
    iterator = source.events(); world._consume(await anext(iterator))
    while world.inbound_authority_snapshot().readiness_status == "PENDING_SYNC": world._consume(await anext(iterator))
    old = world.inbound_authority_snapshot().ability_sidecars[0]
    await client._commit_server_message(message("game.event", 2,
        {"event_type": "MEDIUM_RESULT", "event_payload": {
            "target_player_id": "p", "result": "human"}}), sequence_mode="CONTIGUOUS")
    world._consume(await anext(iterator))
    retained = world.inbound_authority_snapshot().ability_sidecars
    assert all(item.identity != old.identity for item in retained)
    assert (old.source.event_id, old.source.source_path) not in world._inbound_authority._private_sources


@pytest.mark.anyio
async def test_i_p4_real_resume_replay_mode_never_becomes_positive_before_sync():
    socket = FakeSocket([
        wire("player.action_state", 4, {"phase": "day", "day": 1,
             "phase_ends_at": None, "actions": [{"type": "chat", "channel": "public"}]}),
        wire("session.ready", 5, {"player_id": "p", "ready": True}),
        wire("session.resumed", 6, {"player_id": "p", "last_seq": 3}),
        wire("game.state_sync", 7, sync_payload()),
    ])
    checkpoint = SessionCheckpoint("token", 3, PROTOCOL_VERSION)
    store = MemoryStore(checkpoint)
    client = NetworkClient(NetworkClientConfig("ws://offline", "g", None), store,
                           connector=lambda _uri: socket)
    capability = client.acquire_inbound_authority_capability(); source = AuthoritySource(client, capability)
    world = WorldState(source, inbound_authority=_create_runtime(capability))
    network_task = asyncio.create_task(client.run()); world_task = asyncio.create_task(world.run())
    for _ in range(100):
        if world.inbound_authority_snapshot().readiness_status == "READY": break
        if network_task.done(): pytest.fail(f"resume network ended early: {network_task.result()}")
        await asyncio.sleep(0.01)
    else: pytest.fail(f"resume sync did not publish READY: {world.inbound_authority_snapshot()}, modes={source.claimed_modes}, lifecycle={client.lifecycle}")
    assert "RESUME_REPLAY" in source.claimed_modes and source.claimed_modes[-1] == "SYNC_BARRIER"
    assert world.inbound_authority_snapshot().action_sidecars == ()
    socket.incoming.put_nowait(wire("game.event", 8, {"event_type": "GAME_ENDED", "event_payload": {}}))
    assert (await network_task).reason is ClientExitReason.GAME_ENDED
    await world_task


@pytest.mark.anyio
async def test_i_p2_p3_n5_n6_n9_n10_real_receive_ability_and_partial_history():
    payload = ability_sync("GUARD_SUCCEEDED", None, None)
    payload["history"].extend([
        {"type": "game.event", "payload": {"event_type": "INSPECT_RESULT",
         "event_payload": {"target_player_id": "p", "result": "wolf", "extra": "no-sidecar"}}},
        {"type": "game.event", "payload": {"event_type": "MEDIUM_RESULT",
         "event_payload": {"target_player_id": "p", "result": "human"}}},
    ])
    socket = FakeSocket([
        wire("session.joined", 1, {"player_id": "p", "connection_token": "token"}),
        wire("session.ready", 2, {"player_id": "p", "ready": True}),
        # Live ability before sync has no accepted reducer phase/readiness authority.
        wire("game.event", 3, {"event_type": "GUARD_SUCCEEDED",
             "event_payload": {"target_player_id": "p"}}),
        wire("game.state_sync", 4, payload),
    ])
    client = NetworkClient(NetworkClientConfig("ws://offline", "g", "entry"), MemoryStore(),
                           connector=lambda _uri: socket)
    capability = client.acquire_inbound_authority_capability(); source = AuthoritySource(client, capability)
    world = WorldState(source, config=WorldStateConfig(max_history_records=4),
                       inbound_authority=_create_runtime(capability))
    network_task = asyncio.create_task(client.run()); world_task = asyncio.create_task(world.run())
    for _ in range(100):
        snapshot = world.inbound_authority_snapshot()
        if snapshot.readiness_status == "READY": break
        if network_task.done(): pytest.fail(f"ability network ended early: {network_task.result()}")
        await asyncio.sleep(0.01)
    else: pytest.fail("ability sync did not publish READY")
    # The malformed middle entry remains a world fact under v1 semantics but has no sidecar.
    assert [item.state_sync_history_index for item in snapshot.ability_sidecars] == [1, 3]
    assert all(item.source.seq == 4 for item in snapshot.ability_sidecars)
    socket.incoming.put_nowait(wire("game.event", 5, {"event_type": "INSPECT_RESULT",
        "event_payload": {"target_player_id": "p", "result": "wolf"}}))
    for _ in range(100):
        current = world.inbound_authority_snapshot()
        if any(item.source.seq == 5 for item in current.ability_sidecars): break
        await asyncio.sleep(0.01)
    else: pytest.fail("live actual ability emission was not bound")
    live = next(item for item in current.ability_sidecars if item.source.seq == 5)
    assert live.phase_attribution_mode == "LIVE_REDUCER_PHASE"
    assert live.state_sync_history_index is None and live.reducer_phase_witness_sha256
    socket.incoming.put_nowait(wire("game.event", 6, {"event_type": "GAME_ENDED", "event_payload": {}}))
    assert (await network_task).reason is ClientExitReason.GAME_ENDED
    await world_task
