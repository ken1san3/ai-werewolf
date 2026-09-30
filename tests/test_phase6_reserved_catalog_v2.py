"""Exact catalog compiler tests; all input is public synthetic fixture data."""
from dataclasses import fields, replace
from types import SimpleNamespace

import pytest

from ai_client.discussion.capture_v2 import canonical_sha256_v2
from ai_client.discussion.model import (
    EvidenceRecordKind, EvidenceRef, EvidenceVisibility, ImportantEvent,
    PlayerAssessment, ClaimAssessment, ClaimVerdict,
)
from ai_client.discussion.reserved_finalize_v2 import _compile_catalog_records as _compile_catalog, _plain, ReservedFinalizeError
from tests.test_phase6_authority_capture_bridge_v2 import composed_bridge, close_composed


@pytest.fixture
def anyio_backend():
    return "asyncio"


def clone(value, **changes):
    names = [f.name for f in fields(value)] if hasattr(value, "__dataclass_fields__") else (vars(value) if isinstance(value, SimpleNamespace) else value.__slots__)
    return SimpleNamespace(**{**{n: getattr(value, n) for n in names}, **changes})


@pytest.fixture
async def catalog_source():
    values = await composed_bridge()
    socket, nt, wt, network, world, store, capture, bridge = values
    try:
        material = bridge.prepare_current()
        yield bridge._read_final_capture_source_v2(material, material._hidden_owner_receipt)
    finally:
        await close_composed(socket, nt, wt, network)


def altered(source, *, state=None, evidence=None, trigger=None, players=None):
    capture = source.exact_capture_read.capture
    selected_state = source.exact_capture_read.state if state is None else state
    selected_capture = clone(capture, state=selected_state,
        state_sha256=canonical_sha256_v2(_plain(selected_state)),
        evidence=capture.evidence if evidence is None else evidence,
        trigger=capture.trigger if trigger is None else trigger)
    inbound = source.exact_inbound_read
    if players is not None:
        inbound = clone(inbound, world_snapshot=clone(inbound.world_snapshot, players=players))
    return clone(source, exact_capture_read=clone(source.exact_capture_read,
        state=selected_state, capture=selected_capture), exact_inbound_read=inbound)


def event(order):
    return ImportantEvent("aiwolf.important-event.v1",
        EvidenceRef(EvidenceRecordKind.CHAT, order, EvidenceVisibility.PUBLIC),
        1, "day", (), (), "opaque-public", 50, "short", 5, 5, False, False)


@pytest.mark.anyio
async def test_initial_catalog_full_bindings_and_no_reply(catalog_source):
    catalog, binding, options = _compile_catalog(catalog_source)
    assert catalog.player_ids == ("p000",)
    assert catalog.reply_ids == () and catalog.utterance_claim_ids == ()
    assert set(binding) == {"schema_version", "generation_catalog", "player_window",
        "fact_window", "observed_claim_window", "player_entries", "reply_entries",
        "fact_entries", "observed_claim_entries", "utterance_claim_entries", "opinion_entries"}
    assert binding["player_entries"][0]["real_player_id"] == "opaque-player"
    action = options["all_action_bindings"][0]
    assert action["provenance_lookup_outcome"] == "EXACT_ONE_MATCH"
    assert action["channel_authority_projection_or_null"]["audience"] == "PUBLIC"
    assert action["action_source_binding"]["typed_value_sha256"] == action["typed_action_sha256"]


@pytest.mark.anyio
async def test_fact_tail_uses_capture_not_state_and_reply_alias(catalog_source):
    events = tuple(event(i) for i in range(55))
    trigger = replace(catalog_source.exact_capture_read.capture.trigger,
        kind="PEER_CHAT", source=events[-1].source)
    source = altered(catalog_source, evidence=events, trigger=trigger)
    catalog, binding, _ = _compile_catalog(source)
    assert len(catalog.fact_ids) == 48 and catalog.reply_ids == ("r000",)
    assert binding["fact_window"] == dict(source_count=55, selected_start=7, selected_count=48, limit=48)
    assert binding["fact_entries"][0]["capture_evidence_index"] == 7
    fact, reply = binding["fact_entries"][-1], binding["reply_entries"][0]
    assert reply["fact_short_id"] == fact["short_id"]
    assert reply["evidence_ref"] is fact["evidence_ref"]
    assert reply["capture_root"] is fact["capture_root"]
    assert fact["provenance_lookup_outcome"] == "ABSENT"
    assert fact["source_ref_or_null"] is None


@pytest.mark.anyio
async def test_reply_outside_window_fails_closed(catalog_source):
    events = tuple(event(i) for i in range(49))
    trigger = replace(catalog_source.exact_capture_read.capture.trigger,
        kind="PEER_CHAT", source=events[0].source)
    with pytest.raises(ReservedFinalizeError, match="CATALOG_UNREPRESENTABLE"):
        _compile_catalog(altered(catalog_source, evidence=events, trigger=trigger))


@pytest.mark.anyio
async def test_sorted_players_retain_original_index(catalog_source):
    player = catalog_source.exact_inbound_read.world_snapshot.players[0]
    players = (replace(player, player_id="z-peer"), player,
        replace(player, player_id="a-peer"))
    catalog, binding, _ = _compile_catalog(altered(catalog_source, players=players))
    assert [p["real_player_id"] for p in binding["player_entries"]] == ["a-peer", "opaque-player", "z-peer"]
    assert [p["source_player_index"] for p in binding["player_entries"]] == [2, 1, 0]
    assert catalog.peer_player_ids == ("p000", "p002")


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["duplicate", "no_self", "excess"])
async def test_invalid_player_sets_rejected(catalog_source, mode):
    player = catalog_source.exact_inbound_read.world_snapshot.players[0]
    players = {"duplicate": (player, player), "no_self": (replace(player, player_id="peer"),),
        "excess": (player,) + tuple(replace(player, player_id=f"peer-{i}") for i in range(32))}[mode]
    with pytest.raises(ReservedFinalizeError):
        _compile_catalog(altered(catalog_source, players=players))


@pytest.mark.anyio
async def test_opinion_keeps_order_and_intersects_fact_evidence(catalog_source):
    first, second = event(1), event(2)
    state = replace(catalog_source.exact_capture_read.state,
        assessments=(PlayerAssessment("opaque-player", 25, 75, 50,
            (first.source, second.source, event(99).source)),))
    catalog, binding, _ = _compile_catalog(altered(catalog_source, state=state, evidence=(first, second)))
    assert [o.dimension for o in catalog.opinion_bases] == ["SUSPICION", "CREDIBILITY"]
    assert catalog.opinion_bases[0].allowed_current == (0, 50, 75, 100)
    assert catalog.opinion_bases[0].prior_fact_ids == ("f000", "f001")
    assert binding["opinion_entries"][1]["state_assessment_index"] == 0


@pytest.mark.anyio
async def test_noncanonical_score_not_rounded(catalog_source):
    state = replace(catalog_source.exact_capture_read.state,
        assessments=(PlayerAssessment("opaque-player", 26, 75, 50, ()),))
    with pytest.raises(ReservedFinalizeError):
        _compile_catalog(altered(catalog_source, state=state))


@pytest.mark.anyio
async def test_duplicate_fact_rejected(catalog_source):
    e = event(1)
    with pytest.raises(ReservedFinalizeError):
        _compile_catalog(altered(catalog_source, evidence=(e, e)))


@pytest.mark.anyio
@pytest.mark.parametrize("kind,action", [
    ("PRE_VOTE", {"type": "vote", "valid_targets": ["opaque-player"], "target_count": 1, "allows_abstain": True}),
    ("CO_OPPORTUNITY", {"type": "co_declare", "claimed_role_ids": ["opaque-role"]}),
    ("ABILITY", {"type": "ability", "ability_id": "opaque-ability", "description": None,
        "valid_targets": ["opaque-player"], "target_count": 1, "uses_remaining": None}),
])
async def test_action_trigger_catalogs(kind, action):
    from ai_client.discussion.context import DiscussionAbilityContext
    values = await composed_bridge(actions=(action,))
    socket, nt, wt, network, world, store, capture, bridge = values
    try:
        from ai_client.discussion.state import DiscussionViews
        trigger = replace(capture.trigger, kind=kind,
            owner="reaction_chat" if kind == "CO_OPPORTUNITY" else "vote_ability")
        capture = store.capture(DiscussionViews(world.snapshot(), world.history(),
            world.co_for_day(1), world.ability_results(), world.transport_observations()), trigger)
        material = bridge.prepare_current()
        source = bridge._read_final_capture_source_v2(material, material._hidden_owner_receipt)
        if kind == "ABILITY":
            ability = DiscussionAbilityContext("opaque-ability", "night", 1, 1,
                "single", "alive", 1, None, None, "skip", ())
            context = replace(capture.context, abilities=(ability,))
            source = clone(source, exact_capture_read=clone(source.exact_capture_read,
                capture=clone(source.exact_capture_read.capture, context=context)),
                exact_bridge=SimpleNamespace(_receipt=SimpleNamespace(exact_context_object=context)))
        catalog, binding, options = _compile_catalog(source)
        assert catalog.reply_ids == ()
        assert len(options["all_action_bindings"]) == 1
        if kind == "PRE_VOTE":
            assert catalog.vote_options[0].valid_target_ids == ("p000",)
            assert catalog.vote_options[0].allows_abstain is True
            assert catalog.co_options == catalog.ability_options == ()
        elif kind == "CO_OPPORTUNITY":
            assert catalog.co_options[0].claimed_role_option_ids == ("q000",)
            assert options["role_id_bindings"][0]["real_role_id"] == "opaque-role"
        else:
            assert catalog.ability_options[0].allows_none is True
            assert options["ability_id_bindings"][0]["context_ability_sha256"] == canonical_sha256_v2(_plain(ability))
            for selection, expected in (("random", False), ("unsupported", None)):
                changed_context = replace(context, abilities=(replace(ability, no_selection=selection),))
                changed = clone(source, exact_capture_read=clone(source.exact_capture_read,
                    capture=clone(source.exact_capture_read.capture, context=changed_context)),
                    exact_bridge=SimpleNamespace(_receipt=SimpleNamespace(exact_context_object=changed_context)))
                if expected is None:
                    with pytest.raises(ReservedFinalizeError): _compile_catalog(changed)
                else:
                    assert _compile_catalog(changed)[0].ability_options[0].allows_none is expected
    finally:
        await close_composed(socket, nt, wt, network)


@pytest.mark.anyio
async def test_actual_ability_fact_and_disclosure_provenance():
    history = (
        {"type": "game.event", "payload": {"event_type": "PHASE_STARTED",
            "event_payload": {"day": 1, "phase": "day", "phase_ends_at": 10}}},
        {"type": "game.event", "payload": {"event_type": "GUARD_SUCCEEDED",
            "event_payload": {"target_player_id": "opaque-player"}}},
    )
    values = await composed_bridge(history=history)
    socket, nt, wt, network, world, store, capture, bridge = values
    try:
        material = bridge.prepare_current()
        source = bridge._read_final_capture_source_v2(material, material._hidden_owner_receipt)
        catalog, binding, options = _compile_catalog(source)
        ability_facts = [f for f in binding["fact_entries"]
            if f["evidence_ref"]["record_kind"] == "ability_result"]
        assert len(ability_facts) == 1
        assert ability_facts[0]["provenance_lookup_outcome"] == "EXACT_ONE_MATCH"
        assert ability_facts[0]["source_ref_or_null"] is not None
        assert catalog.disclose_ids == ("d000",)
        assert options["disclosure_bindings"][0]["provenance_lookup_outcome"] == "EXACT_ONE_MATCH"
        inbound = source.exact_inbound_read
        sidecar = inbound.ability_sidecars[0]
        for sidecars in ((sidecar, sidecar), (clone(sidecar, typed_value_sha256="0"*64),)):
            changed = clone(source, exact_inbound_read=clone(inbound, ability_sidecars=sidecars))
            with pytest.raises(ReservedFinalizeError):
                _compile_catalog(changed)
    finally:
        await close_composed(socket, nt, wt, network)


@pytest.mark.anyio
async def test_claim_window_and_exact_assessment_binding(catalog_source):
    claims = tuple(ClaimAssessment(event(i).source, "opaque-player",
        next(iter(ClaimVerdict)), 50, ()) for i in range(32))
    state = replace(catalog_source.exact_capture_read.state, claims=claims)
    catalog, binding, _ = _compile_catalog(altered(catalog_source, state=state))
    assert catalog.observed_claim_ids == tuple(f"k{i:03d}" for i in range(32))
    assert binding["observed_claim_window"] == dict(source_count=32, selected_start=0, selected_count=32, limit=32)
    assert binding["observed_claim_entries"][0]["claim_assessment_projection"] == _plain(claims[0])
    assert binding["observed_claim_entries"][-1]["state_claim_index"] == 31


@pytest.mark.anyio
@pytest.mark.parametrize("mutation", ["binding_hash", "action_duplicate", "context_foreign"])
async def test_source_binding_tamper_fails(catalog_source, mutation):
    source = catalog_source
    if mutation == "context_foreign":
        source = clone(source, exact_bridge=SimpleNamespace(_receipt=SimpleNamespace(
            exact_context_object=replace(source.exact_capture_read.capture.context))))
    elif mutation == "action_duplicate":
        inbound = source.exact_inbound_read
        source = clone(source, exact_inbound_read=clone(inbound,
            action_sidecars=inbound.action_sidecars * 2))
    else:
        binding = source.exact_prepared_material.action_bindings[0]
        changed = clone(binding, action_sha256="0" * 64)
        changed._projection = lambda: {**binding._projection(), "action_sha256": "0" * 64}
        material = source.exact_prepared_material
        source = clone(source, exact_prepared_material=SimpleNamespace(
            **{**dict(material._values), "action_bindings": (changed,)}))
    with pytest.raises(ReservedFinalizeError):
        _compile_catalog(source)


@pytest.mark.anyio
async def test_claim_source_33_keeps_first_32_and_exact_window(catalog_source):
    original = catalog_source.exact_capture_read.state
    claims = tuple(ClaimAssessment(event(i).source, "opaque-player",
        next(iter(ClaimVerdict)), 50, ()) for i in range(33))
    # The compiler boundary is tested independently of the current v1 state cap.
    state = object.__new__(type(original))
    for item in fields(original):
        object.__setattr__(state, item.name, claims if item.name == "claims" else getattr(original, item.name))
    catalog, binding, _ = _compile_catalog(altered(catalog_source, state=state))
    assert len(catalog.observed_claim_ids) == 32
    assert binding["observed_claim_window"] == dict(source_count=33, selected_start=0, selected_count=32, limit=32)
    assert [entry["state_claim_index"] for entry in binding["observed_claim_entries"]] == list(range(32))
