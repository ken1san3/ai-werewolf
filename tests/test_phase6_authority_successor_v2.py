"""Offline registered-owner lifecycle, failure atomicity and bounded lineage."""
from __future__ import annotations

import asyncio
import copy
import gc
import weakref
from dataclasses import replace

import pytest

from ai_client.discussion.authority_capture_bridge_v2 import (
    AuthorityCaptureBridgeError, _register_authority_owners_v2,
    _validate_owner_registration_structure_v2, _validate_owner_registration_v2,
)
from ai_client.discussion.context import validate_discussion_bootstrap
from ai_client.discussion.state import DiscussionViews
from ai_client.network.client import NetworkClient
from ai_client.network.types import NetworkClientConfig
from ai_client.runtime import _Phase6NetworkEventSource, _create_phase6_v2_inbound_world
from ai_client.world.inbound_authority_v2 import (
    AuthoritySuccessorTicketV2, _create_runtime, _release_successor_ticket_v2,
)
from ai_client.world.model import Freshness
from ai_client.world.service import WorldState
from tests.test_phase6_authority_capture_bridge_v2 import (
    MemoryStore, close_composed, composed_bridge, wire,
)
from tests.test_phase6_discussion_context import _envelope


@pytest.fixture
def anyio_backend():
    return "asyncio"


def unstarted():
    pending = validate_discussion_bootstrap(
        _envelope(), network_game_id="opaque-game", player_id="opaque-player")
    network = NetworkClient(NetworkClientConfig(
        "ws://offline", "opaque-game", "entry"), MemoryStore(), connector=lambda _uri: None)
    source, world = _create_phase6_v2_inbound_world(network, pending)
    return network, source, world


def assert_released(ticket, state):
    assert ticket.state == state
    for field in ("exact_registration", "exact_world", "issuer_capability",
                  "exact_predecessor_authority", "exact_successor_authority",
                  "exact_parent_commit_ticket_or_null", "parent_commit_lineage_identity_or_null"):
        assert getattr(ticket, field) is None


@pytest.mark.anyio
async def test_initial_registration_and_many_commits_keep_bounded_lineage():
    _network, source, world = unstarted()
    registration = world._authority_owner_registration_v2
    assert world._authority_owner_mode_v2 == "REGISTERED_OWNER"
    assert registration.discussion_owner_stage == "NO_RECEIPT"
    assert world._abort_bundle.successor_ticket.state == "ARMED_ABORT"
    assert world._abort_bundle.successor_ticket.parent_commit_lineage_identity_or_null is None
    previous_objects = []
    for _ in range(50):
        previous_objects.append(weakref.ref(world._inbound_authority))
        old_abort = world._abort_bundle.successor_ticket
        old_version = world.snapshot().version
        world._commit()
        assert_released(old_abort, "RETIRED")
        assert world._inbound_authority is registration.exact_authority_runtime
        assert world._inbound_authority._prepared_successor_ticket_v2 is None
        assert world.snapshot().version == old_version + 1
        child = world._abort_bundle.successor_ticket
        assert child.state == "ARMED_ABORT"
        assert child.exact_parent_commit_ticket_or_null is None
        assert child.parent_commit_lineage_identity_or_null == world._inbound_authority._published_successor_lineage_v2
        assert child.exact_predecessor_authority is world._inbound_authority
        assert child.exact_successor_authority is world._abort_bundle.invalid_authority
        _validate_owner_registration_structure_v2(registration)
    gc.collect()
    assert all(ref() is None for ref in previous_objects)
    source.close()


@pytest.mark.anyio
async def test_initial_registration_failure_publishes_no_owner_or_formal_bundle(monkeypatch):
    original = WorldState._prepare_abort_bundle
    captured = []

    def fail(self, *args, **kwargs):
        if kwargs.get("initial_registration") is not None:
            captured.append((self, kwargs["initial_registration"]))
            raise MemoryError("synthetic initial bundle allocation")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(WorldState, "_prepare_abort_bundle", fail)
    with pytest.raises(MemoryError, match="initial bundle"):
        unstarted()
    world, registration = captured[0]
    assert world._authority_owner_mode_v2 == "REGISTRATION_PENDING"
    assert world._abort_bundle is None
    assert world._authority_successor_publish_capability_v2 is None
    for owner in (registration.exact_network_client, registration.exact_claim_capability,
                  registration.exact_runtime_source, registration.exact_authority_runtime, world):
        assert owner._authority_owner_registration_v2 is None
    with pytest.raises(RuntimeError, match="registration is pending"):
        await world.run()
    with pytest.raises(RuntimeError, match="registration is pending"):
        world._commit()


@pytest.mark.anyio
async def test_structural_mode_preserves_copy_abort_but_cannot_upgrade_to_issuer():
    pending = validate_discussion_bootstrap(
        _envelope(), network_game_id="opaque-game", player_id="opaque-player")
    network = NetworkClient(NetworkClientConfig(
        "ws://offline", "opaque-game", "entry"), MemoryStore(), connector=lambda _uri: None)
    source = _Phase6NetworkEventSource(network, pending, enable_inbound_authority_v2=True)
    authority = _create_runtime(source._authority_capability)
    world = WorldState(source, inbound_authority=authority)
    source.attach_world(world)
    assert world._authority_owner_mode_v2 == "UNREGISTERED_STRUCTURAL"
    assert world._abort_bundle.successor_ticket is None
    with pytest.raises(AuthorityCaptureBridgeError, match="OWNER_REGISTRATION_MISMATCH"):
        _register_authority_owners_v2(network, source._authority_capability, source, authority, world)
    world._commit()
    assert world._inbound_authority is not authority
    assert world._abort_bundle.successor_ticket is None
    world._abort_inbound_authority()
    assert world.snapshot().freshness is Freshness.FAILED
    assert world.inbound_authority_snapshot().readiness_status == "INVALID"
    assert source.context_owner_receipt_v2 is None


@pytest.mark.anyio
async def test_registered_mode_and_current_authority_cannot_silently_downgrade():
    _network, source, world = unstarted()
    registration = world._authority_owner_registration_v2
    original_authority = world._inbound_authority
    before = (world.snapshot(), world._abort_bundle, world._update_event)
    for owner, field, replacement in (
        (world, "_authority_owner_mode_v2", "UNREGISTERED_STRUCTURAL"),
        (world, "_authority_owner_registration_v2", None),
        (world, "_inbound_authority", copy.copy(original_authority)),
        (registration, "exact_authority_runtime", copy.copy(original_authority)),
    ):
        saved = getattr(owner, field)
        setattr(owner, field, replacement)
        try:
            with pytest.raises((ValueError, AuthorityCaptureBridgeError)):
                world._commit()
            assert (world.snapshot(), world._abort_bundle, world._update_event) == before
        finally:
            setattr(owner, field, saved)
    world._commit()
    assert registration.exact_authority_runtime is world._inbound_authority
    source.close()


@pytest.mark.anyio
async def test_saved_abort_rejects_field_mutations_and_same_value_ticket_copies():
    _network, source, world = unstarted()
    registration = world._authority_owner_registration_v2
    bundle = world._abort_bundle
    ticket = bundle.successor_ticket
    before = (world.snapshot(), world._inbound_authority, registration.exact_authority_runtime,
              world._abort_bundle, world._update_event)
    fields = [
        (ticket, "exact_world", object()), (ticket, "exact_registration", object()),
        (ticket, "issuer_capability", object()),
        (ticket, "exact_predecessor_authority", copy.copy(world._inbound_authority)),
        (ticket, "exact_successor_authority", copy.copy(bundle.invalid_authority)),
        (ticket, "expected_world_version", 999), (ticket, "expected_last_applied_seq", 999),
        (ticket, "state", "PUBLISHED"), (ticket, "transition_kind", "COMMIT"),
        (ticket, "terminal_only", False), (ticket, "exact_parent_commit_ticket_or_null", object()),
        (ticket, "parent_commit_lineage_identity_or_null", "foreign"),
        (bundle.invalid_authority, "_authority_owner_registration_v2", None),
        (bundle.invalid_authority, "_prepared_successor_ticket_v2", None),
    ]
    for owner, field, replacement in fields:
        saved = getattr(owner, field)
        setattr(owner, field, replacement)
        try:
            with pytest.raises(ValueError):
                world._abort_inbound_authority()
            assert (world.snapshot(), world._inbound_authority, registration.exact_authority_runtime,
                    world._abort_bundle, world._update_event) == before
        finally:
            setattr(owner, field, saved)
    world._abort_bundle = replace(bundle, successor_ticket=copy.copy(ticket))
    with pytest.raises(ValueError):
        world._abort_inbound_authority()
    world._abort_bundle = bundle
    world._abort_inbound_authority()
    assert_released(ticket, "PUBLISHED")
    assert world._inbound_authority is registration.exact_authority_runtime
    assert world.snapshot().freshness is Freshness.FAILED
    with pytest.raises(RuntimeError, match="already consumed"):
        world._abort_inbound_authority()
    source.close()


@pytest.mark.anyio
async def test_prepared_abort_requires_exact_commit_parent_and_raw_copy_has_no_ticket():
    _network, source, world = unstarted()
    planned = world._make_snapshot(version=world.snapshot().version + 1)
    successor, parent = world._prepare_registered_commit_v2(planned)
    bundle = world._prepare_abort_bundle(planned, successor, parent_ticket=parent)
    child = bundle.successor_ticket
    assert child.state == "PREPARED"
    assert child.exact_parent_commit_ticket_or_null is parent
    assert child.parent_commit_lineage_identity_or_null is None
    with pytest.raises(ValueError):
        world._prepare_abort_bundle(planned, successor, parent_ticket=copy.copy(parent))
    with pytest.raises(ValueError):
        world._prepare_abort_bundle(planned, successor)
    raw = successor.prepare_invalid_empty(planned.version + 1, planned.last_applied_seq)
    assert raw._prepared_successor_ticket_v2 is None
    assert raw._published_successor_lineage_v2 is None
    _release_successor_ticket_v2(child, "RETIRED")
    _release_successor_ticket_v2(parent, "RETIRED")
    assert_released(child, "RETIRED")
    assert_released(parent, "RETIRED")
    assert successor._prepared_successor_ticket_v2 is None
    source.close()


@pytest.mark.anyio
async def test_owner_stages_progress_and_network_generation_does_not_block_transition(monkeypatch):
    stages = []
    original = WorldState._commit

    def track(self):
        original(self)
        if self._authority_owner_registration_v2 is not None:
            stages.append(self._authority_owner_registration_v2.discussion_owner_stage)

    monkeypatch.setattr(WorldState, "_commit", track)
    values = await composed_bridge()
    socket, network_task, world_task, source, world, store, capture, bridge = values
    generation = source._network._state._connection_generation
    try:
        source._network._state._connection_generation += 1
        world._commit()
        assert set(stages) == {"NO_RECEIPT", "RECEIPT_ONLY", "STORE_ATTACHED"}
        assert world._inbound_authority is world._authority_owner_registration_v2.exact_authority_runtime
        with pytest.raises(AuthorityCaptureBridgeError, match="STALE_OWNER_READ"):
            bridge.prepare_current()
        source._network._state._connection_generation = generation
        store.capture(DiscussionViews(world.snapshot(), world.history(), world.co_for_day(1),
                      world.ability_results(), world.transport_observations()), capture.trigger)
        assert bridge.prepare_current().world_version == world.snapshot().version
    finally:
        source._network._state._connection_generation = generation
        await close_composed(socket, network_task, world_task, source)


@pytest.mark.anyio
async def test_retired_source_allows_terminal_stop_but_no_positive_transfer_or_read():
    values = await composed_bridge()
    socket, network_task, world_task, source, world, _store, _capture, bridge = values
    registration = world._authority_owner_registration_v2
    source.close()
    before = world._inbound_authority
    with pytest.raises(ValueError, match="terminal transfer"):
        world._prepare_registered_commit_v2(world.snapshot())
    assert world._inbound_authority is before
    with pytest.raises(AuthorityCaptureBridgeError):
        bridge.prepare_current()
    await world.stop()
    assert registration.lifecycle == "RETIRED"
    assert world._inbound_authority is registration.exact_authority_runtime
    assert world._inbound_authority is not before
    assert world.inbound_authority_snapshot().readiness_status == "INVALID"
    assert not world._inbound_authority._private_sources
    assert world.snapshot().freshness in {Freshness.ENDED, Freshness.FAILED}
    await close_composed(socket, network_task, world_task, source)


@pytest.mark.anyio
async def test_retired_terminal_prepare_failure_uses_pre_retirement_abort(monkeypatch):
    values = await composed_bridge()
    socket, network_task, world_task, source, world, _store, _capture, _bridge = values
    registration = world._authority_owner_registration_v2
    cached = world._abort_bundle
    event = world._update_event
    source.close()

    def fail(*args, **kwargs):
        raise MemoryError("synthetic retired terminal prepare")

    monkeypatch.setattr(world, "_prepare_authority_copy_v2", fail)
    with pytest.raises(MemoryError, match="retired terminal"):
        await world.stop()
    assert world_task.done()
    assert registration.lifecycle == "RETIRED"
    assert world.snapshot() is cached.failed_world
    assert world._inbound_authority is registration.exact_authority_runtime is cached.invalid_authority
    assert event.is_set()
    assert_released(cached.successor_ticket, "PUBLISHED")
    assert not world._inbound_authority._private_sources
    # This in-memory socket does not wake recv() on close; finish its receive queue explicitly.
    socket.incoming.put_nowait(wire("game.event", source.snapshot().last_seq + 1,
        {"event_type": "GAME_ENDED", "event_payload": {}}))
    assert (await asyncio.wait_for(network_task, 1)).reason.value == "GAME_ENDED"


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["snapshot", "abort_allocation", "child_lineage"])
async def test_prepare_failure_uses_saved_abort_retires_candidates_and_wakes_waiter(monkeypatch, failure):
    import ai_client.world.service as service_module

    values = await composed_bridge()
    socket, network_task, world_task, source, world, _store, _capture, _bridge = values
    registration = world._authority_owner_registration_v2
    cached = world._abort_bundle
    previous = world.snapshot()
    event = world._update_event
    waiter = asyncio.create_task(world.wait_for_update(previous.version))
    await asyncio.sleep(0)
    created = []
    original_ticket_init = AuthoritySuccessorTicketV2.__init__

    def track_ticket(self, *args, **kwargs):
        original_ticket_init(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(AuthoritySuccessorTicketV2, "__init__", track_ticket)
    if failure == "snapshot":
        def explode(*args, **kwargs):
            raise MemoryError("synthetic snapshot allocation")
        monkeypatch.setattr(world, "_make_snapshot", explode)
    elif failure == "abort_allocation":
        def explode(*args, **kwargs):
            raise MemoryError("synthetic abort bundle allocation")
        monkeypatch.setattr(service_module._AbortPublishBundleV2, "__init__", explode)
    else:
        original_bundle = world._prepare_abort_bundle

        def wrong_parent(*args, **kwargs):
            bundle = original_bundle(*args, **kwargs)
            bundle.successor_ticket.exact_parent_commit_ticket_or_null = object()
            return bundle
        monkeypatch.setattr(world, "_prepare_abort_bundle", wrong_parent)
    socket.incoming.put_nowait(wire("game.event", source.snapshot().last_seq + 1, {
        "event_type": "PHASE_STARTED",
        "event_payload": {"day": 1, "phase": "day", "phase_ends_at": None},
    }))
    result = await asyncio.wait_for(world_task, 1)
    assert result.freshness is Freshness.FAILED
    assert await asyncio.wait_for(waiter, 1) is world.snapshot()
    assert world.snapshot() is cached.failed_world
    assert world._inbound_authority is registration.exact_authority_runtime is cached.invalid_authority
    assert world._abort_bundle is None
    assert event.is_set()
    assert not world._update_event.is_set()
    assert_released(cached.successor_ticket, "PUBLISHED")
    for ticket in created:
        assert_released(ticket, "RETIRED")
    assert not world._inbound_authority._private_sources
    monkeypatch.undo()
    await close_composed(socket, network_task, world_task, source)
