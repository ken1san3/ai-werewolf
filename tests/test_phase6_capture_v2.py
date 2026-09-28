from __future__ import annotations

from dataclasses import replace

import pytest

from ai_client.discussion.capture_v2 import (
    ActionContextRefV2, StructuralActionOptionCandidateV2, StructuralActionCatalogCandidateV2, AttemptReservationV2,
    StructuralInboundCandidateV2, CaptureV2Error, FreshnessStatusV2,
    StructuralDisclosureCandidateV2, FreshnessWitnessV2, GenerationCaptureV2, PhaseAttributionV2,
    ProvenanceSidecarEntryV2, ProvenanceSidecarRegistryV2,
    RecoveryProofV2, StageProjectionBindingV2, StageSchemaHashV2,
    StateGenerationLeaseV2,
    StructuralStateLeaseSimulatorV2, canonical_sha256_v2, compare_freshness_v2,
    make_structural_action_option_candidate, bind_ability_result_sidecar_from_validated_event,
    make_structural_disclosure_candidate,
    make_structural_complete_action_catalog_candidate,
    deadline_is_current, expires_at_monotonic_us, live_phase_attribution,
    make_capture_v2, make_structural_public_channel_candidate,
    recipient_proof_sha256_v2,
    require_single_public_chat_binding, stage_seed_v2,
    structural_broker_observation_candidate,
    structural_attempt_audit_candidate,
    structural_delivery_observation_candidate,
    structural_recovery_observation_candidate,
    promote_runtime_authority_v2, create_runtime_state_lease_store_v2,
)
from ai_client.discussion.context import (
    AuthorizedChatChannelContext, AuthorizedDiscussionContext, BoundDiscussionContext,
    CountParityWinCondition, canonical_sha256,
)
from ai_client.network.types import ChatAction, ServerEvent, VoteAction
from ai_client.world.model import AbilityResultRecord
from ai_client.discussion.model import DiscussionTrigger


H = "0" * 64
H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64


def source(path="/payload/actions/0", message_type="game.event", *, event="e1", player="p", connection=3, seq=7):
    return StructuralInboundCandidateV2("aiwolf.authenticated-inbound-ref.v2", "g", player, connection,
                                     event, seq, message_type, path, H, "1.2")


def action_sidecar(index=0, *, generation=4, connection=3):
    primary = source(f"/payload/actions/{index}", connection=connection)
    parent = source("/payload", connection=connection)
    context = ActionContextRefV2("aiwolf.action-context-ref.v2", parent, 1, "DAY", connection, generation)
    return ProvenanceSidecarEntryV2("aiwolf.provenance-sidecar-entry.v2", "ACTION_HANDLE", None,
                                    (generation, index), H1, primary, None, context)


def bound_context(public=True):
    context = AuthorizedDiscussionContext(
        "aiwolf.discussion-context.v1", "g", "p", "role", (), H, "team", "side",
        "attack", "inspect", "medium",
        (CountParityWinCondition("count_parity", "side", "other", "gte"),), (), (),
        (AuthorizedChatChannelContext("public", public),), False, (), False,
    )
    return BoundDiscussionContext(H, canonical_sha256(context), context)


def complete_chat_catalog(count=1, public=True):
    actions = [{"type": "chat", "channel": "public"} for _ in range(count)]
    event = ServerEvent("player.action_state", "1.2", "e-cat", "g", 7, 0,
                        {"phase": "DAY", "day": 1, "actions": actions})
    handles = tuple(ChatAction(3, 4, "DAY", 1, "chat", "public") for _ in actions)
    return make_structural_complete_action_catalog_candidate(event, handles, bound_context(public),
        authenticated_player_id="p", connection_generation=3, phase_identity=H)


def chat_binding(index=0):
    sidecar = action_sidecar(index)
    phase = H
    authority = make_structural_public_channel_candidate(
        f"o{index:03d}", "public", H2, sidecar, phase,
        context_channel_id="public", context_is_public=True, action_channel_id="public",
    )
    return StructuralActionOptionCandidateV2("aiwolf.action-option-binding.v2", f"o{index:03d}", "chat", 4, 3,
                                 phase, H1, sidecar, authority)


def witness(**changes):
    recipient = recipient_proof_sha256_v2("INITIAL_CHAT", (chat_binding().channel_authority,))
    args = dict(base_revision=2, world_version=5, last_applied_sequence=7, fact_revision=3,
                phase_identity=H, action_generation=4, connection_generation=3,
                input_sha256=H1, catalog_sha256=H2, option_catalog_sha256=H3,
                recipient_proof_sha256=recipient, profile_bundle_sha256="5" * 64,
                stage_schema_sha256s=(StageSchemaHashV2("chat_plan", "6" * 64),
                                      StageSchemaHashV2("message", "7" * 64)),
                lease_owner="seg1", lease_status="PREPARING", expires_at_monotonic_us=10_000_000)
    args.update(changes)
    return FreshnessWitnessV2(**args)


def capture(owner="seg1", **changes):
    authority = chat_binding().channel_authority
    recipient = recipient_proof_sha256_v2("INITIAL_CHAT", (authority,))
    args = dict(base_capture_id="8" * 64, game_id="g", player_id="p", base_revision=2,
                trigger=DiscussionTrigger("reaction_chat", "INITIAL_CHAT", 1, "DAY", 3, 4, 1, None), world_version=5,
                last_applied_sequence=7, fact_revision=3, phase_identity=H, action_generation=4,
                connection_generation=3, channel_authorities=(authority,),
                recipient_proof_sha256=recipient, catalog_sha256=H2, option_catalog_sha256=H3,
                input_sha256=H1, profile_bundle_sha256="5" * 64,
                stage_schema_sha256s=witness().stage_schema_sha256s, state_lease_id=owner,
                expires_at_monotonic_us=10_000_000)
    args.update(changes)
    return make_capture_v2(**args)


def projection(stage="chat_plan", plan=None, messages="a" * 64):
    schema = "6" * 64 if stage == "chat_plan" else "7" * 64
    base = dict(schema_version="aiwolf.stage-projection-binding.v2", stage=stage,
                schema_sha256=schema, base_input_sha256=H1, accepted_plan_sha256=plan,
                canonical_input_sha256="9" * 64, messages_sha256=messages)
    return StageProjectionBindingV2(**base, projection_sha256=canonical_sha256_v2(base))


def active_store():
    store = StructuralStateLeaseSimulatorV2()
    prepared = store.prepare("seg1", witness())
    reserved = store.finalize_capture(prepared.lease_revision, capture(), witness(), 1.0)
    store.activate_with_observation(reserved.lease_revision, broker_obs("claim", "seg1", "GRANTED"))
    return store


def broker_obs(operation, segment, status):
    return structural_broker_observation_candidate({
        "operation": operation, "owner_invocation_id": "seg1",
        "broker_segment_id": segment, "status": status,
    })


def audit_obs(reservation, status, sequence):
    return structural_attempt_audit_candidate({
        "reservation": reservation, "status": status, "audit_sequence": sequence,
    })


def delivery_obs(capture_id, status="DELIVERED"):
    return structural_delivery_observation_candidate({"capture_id": capture_id, "status": status})


def test_c_p1_public_chat_and_hash_dag_are_deterministic():
    binding = require_single_public_chat_binding(complete_chat_catalog())
    assert binding.channel_authority.recipient_scope == "SERVER_FILTERED_PUBLIC"
    assert capture().capture_id == capture().capture_id
    with pytest.raises(CaptureV2Error, match="capture hash"):
        replace(capture(), world_version=6)
    changed = capture(catalog_sha256="f" * 64)
    assert changed.capture_id != capture().capture_id


def test_c_p1_n3_pure_action_adapter_binds_real_subtree_context_and_handle():
    event = ServerEvent("player.action_state", "1.2", "e1", "g", 7, 0,
                        {"phase": "DAY", "day": 1, "actions": [{"type": "chat", "channel": "public"}]})
    handle = ChatAction(3, 4, "DAY", 1, "chat", "public")
    phase = canonical_sha256_v2({"game": "g", "day": 1, "phase": "DAY"})
    binding = make_structural_action_option_candidate(event, handle, bound_context(),
        authenticated_player_id="p", connection_generation=3, received_index=0,
        option_id="o000", phase_identity=phase)
    assert binding.source.source.source_sha256 == canonical_sha256_v2(event.payload["actions"][0])
    with pytest.raises(CaptureV2Error):
        make_structural_action_option_candidate(event, replace(handle, channel="forged"), bound_context(),
            authenticated_player_id="p", connection_generation=3, received_index=0,
            option_id="o000", phase_identity=phase)
    private = make_structural_action_option_candidate(event, handle, bound_context(False),
        authenticated_player_id="p", connection_generation=3, received_index=0,
        option_id="o000", phase_identity=phase)
    with pytest.raises(CaptureV2Error, match="public authority"):
        require_single_public_chat_binding(complete_chat_catalog(public=False))


def test_c_n1_n2_private_or_multiple_chat_is_rejected_without_selection():
    with pytest.raises(CaptureV2Error): require_single_public_chat_binding(complete_chat_catalog(2))
    with pytest.raises(CaptureV2Error): StructuralActionCatalogCandidateV2(object(), (chat_binding(),))


def test_c_p2_live_sidecar_and_c_n4_phase_hash_mutation():
    inbound = source("/payload")
    phase = live_phase_attribution(inbound, 1, "DAY", 4, 6)
    entry = ProvenanceSidecarEntryV2("aiwolf.provenance-sidecar-entry.v2", "HISTORY_RECORD",
                                     ("ability_result", 2), None, H1, inbound, phase, None)
    registry = ProvenanceSidecarRegistryV2(); registry.add(entry)
    with pytest.raises(CaptureV2Error): registry.add(entry)
    with pytest.raises(CaptureV2Error, match="witness"):
        ProvenanceSidecarEntryV2("aiwolf.provenance-sidecar-entry.v2", "HISTORY_RECORD",
                                 ("ability_result", 3), None, H1, inbound,
                                 replace(phase, reducer_phase_witness_sha256=H2), None)
    authority = chat_binding().channel_authority
    disclosure = StructuralDisclosureCandidateV2("aiwolf.disclosure-authority.v2", "SELF_ABILITY_REPORT",
        entry, ("ability_result", 2, "AUTHORIZED_PRIVATE"), "p", "INSPECT_RESULT", "target",
        "wolf", None, "AUTHORIZED_PRIVATE", "PUBLIC", canonical_sha256_v2(authority), 5, 3)
    assert disclosure.read_visibility == "AUTHORIZED_PRIVATE"
    with pytest.raises(CaptureV2Error, match="actor"):
        replace(disclosure, actor_player_id="other")


def test_c_p2_n4_pure_ability_adapter_binds_real_event_payload_and_typed_record():
    payload = {"event_type": "INSPECT_RESULT", "event_payload": {
        "target_player_id": "target", "result": "wolf", "role_id": None,
    }}
    event = ServerEvent("game.event", "1.2", "ability-e", "g", 8, 0, payload)
    record = AbilityResultRecord(2, 1, "NIGHT", "INSPECT_RESULT", "target", "wolf", None)
    inbound = StructuralInboundCandidateV2("aiwolf.authenticated-inbound-ref.v2", "g", "p", 3,
        "ability-e", 8, "game.event", "/payload", canonical_sha256_v2(payload), "1.2")
    phase = live_phase_attribution(inbound, 1, "NIGHT", 5, 7)
    sidecar = bind_ability_result_sidecar_from_validated_event(event, record, phase, bound_context(),
        connection_generation=3)
    assert sidecar.typed_value_sha256 == canonical_sha256_v2(record)
    with pytest.raises(CaptureV2Error, match="differs"):
        bind_ability_result_sidecar_from_validated_event(event, replace(record, result_id="human"), phase,
            bound_context(), connection_generation=3)
    with pytest.raises(CaptureV2Error, match="game"):
        bind_ability_result_sidecar_from_validated_event(replace(event, game_id="other"), record, phase,
            bound_context(), connection_generation=3)
    authority = chat_binding().channel_authority
    disclosure = make_structural_disclosure_candidate(record, sidecar, authority,
        actor_player_id="p", world_version=5, fact_revision=3)
    assert disclosure.result_id == "wolf"
    with pytest.raises(CaptureV2Error, match="typed ability"):
        make_structural_disclosure_candidate(replace(record, result_id="human"), sidecar, authority,
            actor_player_id="p", world_version=5, fact_revision=3)


@pytest.mark.parametrize("event_type,result,role", [
    ("INSPECT_RESULT", "wolf", None),
    ("MEDIUM_RESULT", "human", None),
    ("INSPECT_DEAD_ROLE_RESULT", None, "seer"),
    ("GUARD_SUCCEEDED", None, None),
])
def test_disclosure_legal_event_matrix(event_type, result, role):
    inbound = source("/payload")
    phase = live_phase_attribution(inbound, 1, "NIGHT", 4, 6)
    sidecar = ProvenanceSidecarEntryV2("aiwolf.provenance-sidecar-entry.v2", "HISTORY_RECORD",
        ("ability_result", 2), None, H1, inbound, phase, None)
    value = StructuralDisclosureCandidateV2("aiwolf.disclosure-authority.v2", "SELF_ABILITY_REPORT", sidecar,
        ("ability_result", 2, "AUTHORIZED_PRIVATE"), "p", event_type, "target", result, role,
        "AUTHORIZED_PRIVATE", "PUBLIC", H2, 5, 3)
    assert value.event_type == event_type
    bad_result = None if result is not None else "unexpected"
    bad_role = None if role is not None else "unexpected"
    with pytest.raises(CaptureV2Error): replace(value, result_id=bad_result, revealed_role_id=bad_role)


def test_c_p3_sync_phase_source_must_precede_and_share_authenticated_envelope():
    record = source("/payload/history/3", "game.state_sync", event="sync")
    phase_source = source("/payload/history/1", "game.state_sync", event="sync")
    phase = PhaseAttributionV2("aiwolf.phase-attribution.v2", "SYNC_REPLAY_PHASE", 1, "DAY",
                               None, None, 3, 1, phase_source, None)
    ProvenanceSidecarEntryV2("aiwolf.provenance-sidecar-entry.v2", "HISTORY_RECORD",
                             ("ability_result", 1), None, H1, record, phase, None)
    with pytest.raises(CaptureV2Error):
        replace(phase, preceding_phase_entry_index=4)
    with pytest.raises(CaptureV2Error, match="another authenticated envelope"):
        ProvenanceSidecarEntryV2("aiwolf.provenance-sidecar-entry.v2", "HISTORY_RECORD",
                                 ("ability_result", 1), None, H1, replace(record, server_event_id="other"), phase, None)
    for changed in (replace(record, server_seq=8), replace(record, protocol_version="1.3")):
        with pytest.raises(CaptureV2Error, match="another authenticated envelope"):
            ProvenanceSidecarEntryV2("aiwolf.provenance-sidecar-entry.v2", "HISTORY_RECORD",
                                     ("ability_result", 1), None, H1, changed, phase, None)


def test_sync_ability_adapter_resolves_actual_phase_source_hash_and_result_index():
    phase_entry = {"type": "game.event", "payload": {"event_type": "PHASE_STARTED",
        "event_payload": {"day": 1, "phase": "NIGHT", "phase_ends_at": 10}}}
    result_entry = {"type": "game.event", "payload": {"event_type": "MEDIUM_RESULT",
        "event_payload": {"target_player_id": "target", "result": "human", "role_id": None}}}
    event = ServerEvent("game.state_sync", "1.2", "sync", "g", 9, 0,
                        {"history": [phase_entry, result_entry]})
    phase_source = StructuralInboundCandidateV2("aiwolf.authenticated-inbound-ref.v2", "g", "p", 3,
        "sync", 9, "game.state_sync", "/payload/history/0", canonical_sha256_v2(phase_entry), "1.2")
    phase = PhaseAttributionV2("aiwolf.phase-attribution.v2", "SYNC_REPLAY_PHASE", 1, "NIGHT",
                               None, None, 1, 0, phase_source, None)
    record = AbilityResultRecord(3, 1, "NIGHT", "MEDIUM_RESULT", "target", "human", None)
    sidecar = bind_ability_result_sidecar_from_validated_event(event, record, phase, bound_context(),
        connection_generation=3, history_index=1)
    assert sidecar.source.source_path == "/payload/history/1"
    with pytest.raises(CaptureV2Error, match="actual history"):
        bind_ability_result_sidecar_from_validated_event(event, record,
            replace(phase, phase_source=replace(phase_source, source_sha256=H2)), bound_context(),
            connection_generation=3, history_index=1)
    wrong_index = replace(phase, state_sync_history_index=9)
    with pytest.raises(CaptureV2Error, match="result entry"):
        bind_ability_result_sidecar_from_validated_event(event, record, wrong_index, bound_context(),
            connection_generation=3, history_index=1)


def test_c_n3_action_parent_generation_and_source_path_are_exact():
    sidecar = action_sidecar()
    with pytest.raises(CaptureV2Error): replace(sidecar.action_context, connection_generation=4)
    with pytest.raises(CaptureV2Error): replace(sidecar, source=replace(sidecar.source, source_path="/payload/actions/9"))


def test_c_p4_all_received_options_remain_ordered_and_resolve_only_selected_id():
    event = ServerEvent("player.action_state", "1.2", "votes", "g", 7, 0,
        {"phase": "DAY", "day": 1, "actions": [
            {"type": "vote", "valid_targets": ["p"], "target_count": 1, "allows_abstain": False},
            {"type": "vote", "valid_targets": ["q"], "target_count": 1, "allows_abstain": True},
        ]})
    handles = (VoteAction(3, 4, "DAY", 1, "vote", ("p",), 1, False),
               VoteAction(3, 4, "DAY", 1, "vote", ("q",), 1, True))
    catalog = make_structural_complete_action_catalog_candidate(event, handles, bound_context(),
        authenticated_player_id="p", connection_generation=3, phase_identity=H)
    assert catalog.resolve("o001").source.action_identity == (4, 1)
    with pytest.raises(CaptureV2Error): catalog.resolve("o999")
    with pytest.raises(CaptureV2Error, match="exactly match"):
        make_structural_complete_action_catalog_candidate(event, tuple(reversed(handles)), bound_context(),
            authenticated_player_id="p", connection_generation=3, phase_identity=H)


def test_c_p8_preparing_finalize_and_closed_lifecycle():
    store = active_store(); lease = store.lease
    assert lease.status == "ACTIVE" and lease.capture_id == capture().capture_id
    plan_projection = projection()
    lease = store.register_projection(lease.lease_revision, plan_projection)
    first = AttemptReservationV2("aiwolf.attempt-reservation.v2", "chat_plan", 1,
        f"phase6-v2:{lease.capture_id}:chat_plan:1", stage_seed_v2(lease.capture_id, "chat_plan", 1),
        "seg1", 1, plan_projection.projection_sha256, H)
    lease = store.reserve_attempt(lease.lease_revision, first)
    lease = store.start_attempt(lease.lease_revision, audit_obs(first, "STARTED", 1))
    lease = store.finish_attempt(lease.lease_revision, audit_obs(first, "ACCEPTED", 2), accepted_plan_sha256=H3)
    message_projection = projection("message", H3)
    lease = store.register_projection(lease.lease_revision, message_projection)
    second = AttemptReservationV2("aiwolf.attempt-reservation.v2", "message", 1,
        f"phase6-v2:{lease.capture_id}:message:1", stage_seed_v2(lease.capture_id, "message", 1),
        "seg1", 2, message_projection.projection_sha256, H)
    lease = store.reserve_attempt(lease.lease_revision, second)
    lease = store.start_attempt(lease.lease_revision, audit_obs(second, "STARTED", 3))
    lease = store.finish_attempt(lease.lease_revision, audit_obs(second, "ACCEPTED", 4))
    lease = store.complete_active_segment(lease.lease_revision, "seg1")
    staged = store.transition(lease.lease_revision, "STAGED")
    committed = store.transition(staged.lease_revision, "COMMITTED")
    with pytest.raises(CaptureV2Error): store.transition(committed.lease_revision, "RELEASED")
    released = store.release_with_terminal_acks(
        committed.lease_revision, (broker_obs("release", "seg1", "RELEASED"),),
        delivery_obs(committed.capture_id),
    )
    assert released.status == "RELEASED"
    with pytest.raises(CaptureV2Error): store.transition(released.lease_revision, "ACTIVE")


def test_bare_primitives_cannot_promote_authority_or_terminal_state():
    store = StructuralStateLeaseSimulatorV2(); prepared = store.prepare("seg1", witness())
    reserved = store.finalize_capture(prepared.lease_revision, capture(), witness(), 1.0)
    with pytest.raises(CaptureV2Error): store.transition(reserved.lease_revision, "ACTIVE")
    with pytest.raises(CaptureV2Error): store.activate_with_observation(reserved.lease_revision, "GRANTED")
    with pytest.raises(CaptureV2Error): require_single_public_chat_binding((chat_binding(),))
    candidate = structural_broker_observation_candidate({
        "operation": "claim", "owner_invocation_id": "seg1",
        "broker_segment_id": "seg1", "status": "GRANTED",
    })
    with pytest.raises(CaptureV2Error, match="not connected"):
        promote_runtime_authority_v2(candidate)
    with pytest.raises(CaptureV2Error, match="not connected"):
        create_runtime_state_lease_store_v2()


def test_capture_binds_exact_channel_generation_and_recipient_tuple():
    base = capture()
    with pytest.raises(CaptureV2Error, match="capture generation"):
        capture(action_generation=5)
    with pytest.raises(CaptureV2Error, match="recipient proof"):
        capture(recipient_proof_sha256="f" * 64)


def test_direct_lease_construction_rejects_impossible_finalized_shapes():
    store = StructuralStateLeaseSimulatorV2(); preparing = store.prepare("seg1", witness())
    reserved = store.finalize_capture(preparing.lease_revision, capture(), witness(), 1.0)
    with pytest.raises(CaptureV2Error, match="RESERVED"):
        replace(reserved, accepted_plan_sha256=H1)
    with pytest.raises(CaptureV2Error, match="staged"):
        replace(reserved, status="STAGED")
    with pytest.raises(CaptureV2Error, match="only valid"):
        replace(reserved, recovery_proof_sha256=H1)


@pytest.mark.parametrize("change,expected", [
    ({"world_version": 6}, FreshnessStatusV2.STALE),
    ({"connection_generation": 4}, FreshnessStatusV2.LEASE_INVALID),
    ({"recipient_proof_sha256": "f" * 64}, FreshnessStatusV2.LEASE_INVALID),
])
def test_c_n5_n6_n7_freshness_mutations_fail_closed(change, expected):
    assert compare_freshness_v2(witness(), witness(**change), 1.0) is expected


def test_c_n8_deadline_uses_integer_safe_rounding():
    assert expires_at_monotonic_us(1.0000009) == 1_000_000
    assert deadline_is_current(0.999999, 1_000_000)
    assert not deadline_is_current(1.0, 1_000_000)
    assert compare_freshness_v2(witness(), witness(), 10.0) is FreshnessStatusV2.LEASE_INVALID


def test_c_p6_attempts_span_two_segments_with_unique_identity_and_ordinals():
    store = active_store()
    lease = store.register_projection(store.lease.lease_revision, projection())
    first = AttemptReservationV2("aiwolf.attempt-reservation.v2", "chat_plan", 1, f"phase6-v2:{lease.capture_id}:chat_plan:1",
        stage_seed_v2(lease.capture_id, "chat_plan", 1), "seg1", 1, projection().projection_sha256, H)
    lease = store.reserve_attempt(lease.lease_revision, first)
    lease = store.start_attempt(lease.lease_revision, audit_obs(first, "STARTED", 1))
    lease = store.finish_attempt(lease.lease_revision, audit_obs(first, "SCHEMA_INVALID", 2))
    second = replace(first, attempt=2, request_id=f"phase6-v2:{lease.capture_id}:chat_plan:2",
                     derived_seed=stage_seed_v2(lease.capture_id, "chat_plan", 2), call_ordinal=2)
    lease = store.reserve_attempt(lease.lease_revision, second)
    lease = store.start_attempt(lease.lease_revision, audit_obs(second, "STARTED", 3))
    lease = store.finish_attempt(lease.lease_revision, audit_obs(second, "ACCEPTED", 4), accepted_plan_sha256=H3)
    lease = store.complete_active_segment(lease.lease_revision, "seg1")
    lease = store.register_segment(lease.lease_revision, broker_obs("reserve_successor", "seg2", "OFFERED"))
    lease = store.activate_segment(lease.lease_revision, broker_obs("claim", "seg2", "GRANTED"))
    lease = store.register_projection(lease.lease_revision, projection("message", H3))
    for attempt, status in ((1, "SCHEMA_INVALID"), (2, "ACCEPTED")):
        p = projection("message", H3)
        reservation = AttemptReservationV2("aiwolf.attempt-reservation.v2", "message", attempt, f"phase6-v2:{lease.capture_id}:message:{attempt}",
            stage_seed_v2(lease.capture_id, "message", attempt), "seg2", attempt, p.projection_sha256, H)
        lease = store.reserve_attempt(lease.lease_revision, reservation)
        lease = store.start_attempt(lease.lease_revision, audit_obs(reservation, "STARTED", 4 + attempt * 2 - 1))
        lease = store.finish_attempt(lease.lease_revision, audit_obs(reservation, status, 4 + attempt * 2))
    assert len(lease.attempt_ledger) == 4
    assert [e.call_ordinal for e in lease.attempt_ledger] == [1, 2, 1, 2]


def test_c_n9_n10_segment_attempt_reuse_and_unclosed_attempt_are_rejected():
    store = active_store(); lease = store.register_projection(store.lease.lease_revision, projection())
    reservation = AttemptReservationV2("aiwolf.attempt-reservation.v2", "chat_plan", 1, f"phase6-v2:{lease.capture_id}:chat_plan:1",
        stage_seed_v2(lease.capture_id, "chat_plan", 1), "seg1", 1, projection().projection_sha256, H)
    lease = store.reserve_attempt(lease.lease_revision, reservation)
    with pytest.raises(CaptureV2Error): store.reserve_attempt(lease.lease_revision, reservation)
    lease = store.start_attempt(lease.lease_revision, audit_obs(reservation, "STARTED", 1))
    with pytest.raises(CaptureV2Error): store.register_segment(lease.lease_revision, broker_obs("reserve_successor", "seg2", "OFFERED"))


def test_attempt_audit_must_match_reservation_and_strict_sequence():
    store = active_store(); lease = store.register_projection(store.lease.lease_revision, projection())
    reservation = AttemptReservationV2("aiwolf.attempt-reservation.v2", "chat_plan", 1,
        f"phase6-v2:{lease.capture_id}:chat_plan:1", stage_seed_v2(lease.capture_id, "chat_plan", 1),
        "seg1", 1, projection().projection_sha256, H)
    lease = store.reserve_attempt(lease.lease_revision, reservation)
    wrong = replace(reservation, provider_request_sha256=H1)
    with pytest.raises(CaptureV2Error, match="does not match"):
        store.start_attempt(lease.lease_revision, audit_obs(wrong, "STARTED", 1))
    lease = store.start_attempt(lease.lease_revision, audit_obs(reservation, "STARTED", 2))
    with pytest.raises(CaptureV2Error, match="does not match"):
        store.finish_attempt(lease.lease_revision, audit_obs(reservation, "SCHEMA_INVALID", 2))


@pytest.mark.parametrize("terminal", ["BACKEND_FAILED", "TIMEOUT"])
def test_terminal_uncertainty_forbids_retry(terminal):
    store = active_store(); lease = store.register_projection(store.lease.lease_revision, projection())
    first = AttemptReservationV2("aiwolf.attempt-reservation.v2", "chat_plan", 1,
        f"phase6-v2:{lease.capture_id}:chat_plan:1", stage_seed_v2(lease.capture_id, "chat_plan", 1),
        "seg1", 1, projection().projection_sha256, H)
    lease = store.reserve_attempt(lease.lease_revision, first)
    lease = store.start_attempt(lease.lease_revision, audit_obs(first, "STARTED", 1))
    lease = store.finish_attempt(lease.lease_revision, audit_obs(first, terminal, 2))
    retry = replace(first, attempt=2, request_id=f"phase6-v2:{lease.capture_id}:chat_plan:2",
                    derived_seed=stage_seed_v2(lease.capture_id, "chat_plan", 2), call_ordinal=2)
    with pytest.raises(CaptureV2Error, match="uncertainty"):
        store.reserve_attempt(lease.lease_revision, retry)


def test_c_n11_projection_retry_must_be_byte_equal():
    store = active_store(); lease = store.register_projection(store.lease.lease_revision, projection())
    assert store.register_projection(lease.lease_revision, projection()) is lease
    with pytest.raises(CaptureV2Error, match="drift"):
        store.register_projection(lease.lease_revision, projection(messages=H2))


def test_c_n12_prepare_race_invalidates_and_blocks_new_capture():
    store = StructuralStateLeaseSimulatorV2(); lease = store.prepare("seg1", witness())
    invalid = store.finalize_capture(lease.lease_revision, capture(), witness(world_version=6), 1.0)
    assert invalid.status == "INVALIDATED"
    with pytest.raises(CaptureV2Error): store.prepare("other", witness())


def test_finalize_capture_rejects_self_consistent_capture_from_another_freshness():
    store = StructuralStateLeaseSimulatorV2(); lease = store.prepare("seg1", witness())
    other = capture(world_version=6)
    with pytest.raises(CaptureV2Error, match="freshness"):
        store.finalize_capture(lease.lease_revision, other, witness(), 1.0)
    other_hash = capture(catalog_sha256="f" * 64)
    with pytest.raises(CaptureV2Error, match="hash DAG"):
        store.finalize_capture(lease.lease_revision, other_hash, witness(), 1.0)


def test_c_n13_cleanup_uncertainty_requires_exact_recovery_proof():
    store = active_store(); lease = store.transition(store.lease.lease_revision, "INVALIDATED")
    pending = store.transition(lease.lease_revision, "RECOVERY_PENDING")
    with pytest.raises(CaptureV2Error): store.transition(pending.lease_revision, "RELEASED")
    fingerprint = "b" * 64
    terminal = broker_obs("release", "seg1", "RELEASED")
    proof = RecoveryProofV2("aiwolf.state-lease-recovery-proof.v2", "ALL_SEGMENT_TERMINAL_ACKS", "seg1", ("seg1",), (terminal.record_sha256,), fingerprint,
                            False, None, 1, H1)
    with pytest.raises(CaptureV2Error): store.recover(pending.lease_revision, replace(proof, owner_invocation_id="other"), fingerprint)
    observation = structural_recovery_observation_candidate(proof, (terminal,))
    released = store.recover(pending.lease_revision, observation, fingerprint)
    assert released.status == "RELEASED" and released.recovery_proof_sha256 == canonical_sha256_v2(proof)
    claim = broker_obs("claim", "seg1", "GRANTED")
    bad = replace(proof, terminal_ack_record_sha256s=(claim.record_sha256,))
    with pytest.raises(CaptureV2Error, match="observations"):
        structural_recovery_observation_candidate(bad, (claim,))
