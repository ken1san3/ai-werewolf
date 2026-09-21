from dataclasses import replace
import copy
import json

import pytest
from jsonschema import Draft202012Validator

from ai_client.discussion.context import canonical_json_bytes
from ai_client.llm.decision import DecisionValidationError, parse_llm_output
from ai_client.llm.prompt import project_brain_input
from ai_client.llm.types import LLMBrainConfig, LLMMessage
from ai_client.world import ChatRecord
from scripts.phase6_context_probe import provider_body, wire_bytes
from scripts import phase6_grounding_closed_probe as probe
from tests import test_phase6_semantic_output as semantic_cases


def projection(kind="chat", **kwargs):
    request = semantic_cases._request(kind, **kwargs)
    return project_brain_input(request, config=LLMBrainConfig())


def mutate_projection(p, *, records=None, evidence=None):
    canonical = json.loads(canonical_json_bytes(p.canonical_input))
    if records is not None:
        canonical["memory"]["records"] = records
    capture = copy.copy(p.discussion_capture)
    if evidence is not None:
        object.__setattr__(capture, "evidence", tuple(evidence))
    user = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    messages = (p.messages[0], LLMMessage("user", user))
    changed = copy.copy(p)
    object.__setattr__(changed, "canonical_input", canonical)
    object.__setattr__(changed, "messages", messages)
    object.__setattr__(changed, "discussion_capture", capture)
    return changed


def choice(kind="NONE"):
    return {"speech_act_kind": kind}


def test_grounding_sets_intersection_order_and_claim_actor():
    p = projection("chat", peer=True)
    refs, claims = probe.grounding_sets(p)
    assert refs == ({"record_kind": "chat", "order": 1, "visibility": "PUBLIC"},)
    assert claims == ((refs[0], "peer"),)


def test_projection_only_and_capture_only_refs_are_excluded_not_rejected():
    p = projection("chat", peer=True)
    projected_only = mutate_projection(p, evidence=())
    assert probe.grounding_sets(projected_only) == ((), ())
    capture_only = mutate_projection(p, records=[])
    assert probe.grounding_sets(capture_only) == ((), ())


@pytest.mark.parametrize("mutation", ["visibility_conflict", "projected_duplicate",
                                       "captured_duplicate", "malformed", "negative_order"])
def test_grounding_malformed_is_distinct_from_no_legal(mutation):
    p = projection("chat", peer=True)
    records = json.loads(canonical_json_bytes(p.canonical_input["memory"]["records"]))
    evidence = list(p.discussion_capture.evidence)
    if mutation == "visibility_conflict":
        records[0]["source"]["visibility"] = "AUTHORIZED_PRIVATE"
    elif mutation == "projected_duplicate":
        records.append(copy.deepcopy(records[0]))
    elif mutation == "captured_duplicate":
        evidence.append(evidence[0])
    elif mutation == "malformed":
        records[0]["source"]["extra"] = 1
    else:
        records[0]["source"]["order"] = -1
    changed = mutate_projection(p, records=records, evidence=evidence)
    with pytest.raises(ValueError, match="GROUNDING_INPUT_INVALID") as caught:
        probe.grounding_sets(changed)
    assert not isinstance(caught.value, probe.NoLegalGrounding)


@pytest.mark.parametrize("kind", ["ANSWER", "REBUTTAL", "OPINION_CHANGE"])
def test_required_ref_kind_without_intersection_has_no_legal_grounding(kind):
    p = projection("chat")
    with pytest.raises(probe.NoLegalGrounding, match="NO_LEGAL_GROUNDING"):
        probe.candidate_schema(p, choice(kind))


def test_peer_trigger_excluded_from_intersection_has_no_legal_grounding():
    p = projection("chat", peer=True)
    changed = mutate_projection(p, records=[])
    with pytest.raises(probe.NoLegalGrounding, match="NO_LEGAL_GROUNDING"):
        probe.candidate_schema(changed, choice())


def test_empty_sets_close_arrays_and_nullable_question_without_false_schema():
    p = projection("chat")
    schema = probe.candidate_schema(p, choice("QUESTION"))
    assert schema["$defs"]["evidence_array"] == {**p.decision_schema["$defs"]["evidence_array"], "maxItems": 0}
    assert schema["$defs"]["claim_updates"] == {**p.decision_schema["$defs"]["claim_updates"], "maxItems": 0}
    assert schema["$defs"]["speech_act"]["properties"]["source"] == {"const": None}
    encoded = json.dumps(schema)
    assert '"not"' not in encoded and '"enum": []' not in encoded


def test_nonempty_refs_and_claim_tuple_are_exact_closed_branches():
    p = projection("chat", peer=True)
    schema = probe.candidate_schema(p, choice("CLAIM"))
    ref_branch = schema["$defs"]["evidence_ref"]["oneOf"][0]
    assert ref_branch["properties"] == {"record_kind": {"const": "chat"},
        "order": {"const": 1}, "visibility": {"const": "PUBLIC"}}
    claim = schema["$defs"]["claim"]["oneOf"][0]
    assert claim["properties"]["claim"] == {"const": {
        "record_kind": "chat", "order": 1, "visibility": "PUBLIC"}}
    assert claim["properties"]["speaker_player_id"] == {"const": "peer"}
    for field in ("verdict", "confidence", "evidence"):
        assert field in claim["properties"]


@pytest.mark.parametrize("actors", [(), ("self",), ("peer", "self"), ("missing",)])
def test_non_single_nonself_current_actor_does_not_create_claim_tuple(actors):
    p = projection("chat", peer=True)
    event = replace(p.discussion_capture.evidence[0], actor_player_ids=actors)
    changed = mutate_projection(p, evidence=(event,))
    refs, claims = probe.grounding_sets(changed)
    assert len(refs) == 1 and claims == ()
    schema = probe.candidate_schema(changed, choice())
    assert schema["$defs"]["claim_updates"] == {**p.decision_schema["$defs"]["claim_updates"], "maxItems": 0}


def test_output_body_changes_only_schema_from_ic2_and_preserves_messages():
    from scripts import phase6_intent_choice_probe as ic2
    p = projection("chat", peer=True)
    baseline = provider_body(p)
    frozen = wire_bytes(baseline)
    old = ic2.output_body(baseline, choice("CLAIM"), p)
    new = probe.output_body(baseline, choice("CLAIM"), p)
    assert wire_bytes(baseline) == frozen
    assert new["messages"] == old["messages"]
    assert new["max_tokens"] == old["max_tokens"] == 480
    old["response_format"]["json_schema"]["schema"] = new["response_format"]["json_schema"]["schema"]
    assert new == old


def test_static_32_by_7_grounding_schema_construction():
    projections = []
    for index in range(32):
        records = tuple(ChatRecord(order, 1, "day", "public", "peer", "peer", f"m{order}")
                        for order in range(1, index % 3))
        projections.append(projection(("chat", "vote", "ability", "co_declare")[index % 4],
                                      records=records))
    outcomes = []
    for p in projections:
        refs, _claims = probe.grounding_sets(p)
        assert 0 <= len(refs) <= 2
        for kind in ("NONE", "CLAIM", "QUESTION", "ANSWER", "REBUTTAL",
                     "OPINION_CHANGE", "RELATION_HYPOTHESIS"):
            try:
                outcomes.append(probe.candidate_schema(p, choice(kind)))
            except probe.NoLegalGrounding:
                outcomes.append(None)
    assert len(outcomes) == 224


@pytest.mark.parametrize("count", range(5))
def test_claim_tuple_schema_accepts_zero_through_four_legal_updates(count):
    records = tuple(ChatRecord(order, 1, "day", "public", "peer", "peer", f"m{order}")
                    for order in range(1, 5))
    request = semantic_cases._request("ability", records=records)
    p = project_brain_input(request, config=LLMBrainConfig())
    value = semantic_cases._payload(request, "ability")
    refs, claims = probe.grounding_sets(p)
    assert len(refs) == len(claims) == 4
    value["discussion"]["claim_updates"] = [{"claim": ref,
        "speaker_player_id": actor, "verdict": "SUPPORTED", "confidence": 50,
        "evidence": []} for ref, actor in claims[:count]]
    raw = json.dumps(value)
    assert Draft202012Validator(probe.candidate_schema(p, choice())).is_valid(value)
    assert probe.validate_final(raw, choice(), p) == value


_AUTHORITY = ["claim", "question", "answer", "rebuttal", "opinion_change",
    "relation_hypothesis", "assessment_update", "claim_update", "relation_update",
    "strategy_update", "reaction_score", "co_judgment", "pre_vote_reassessment"]


@pytest.mark.parametrize("case", _AUTHORITY)
def test_authority_positive_matrix_matches_legacy_parser(case, monkeypatch):
    original = semantic_cases._parse
    measured = []
    def closed_path(request, value):
        p = project_brain_input(request, config=LLMBrainConfig())
        selected = choice(value["discussion"]["speech_act"]["kind"])
        old = parse_llm_output(json.dumps(value), projection=p)
        final = probe.validate_final(json.dumps(value), selected, p)
        assert final == value
        assert parse_llm_output(json.dumps(final), projection=p) == old
        measured.append(True)
        return original(request, value)
    monkeypatch.setattr(semantic_cases, "_parse", closed_path)
    semantic_cases.test_p6b_semantic_pass_authority_closed_positive_matrix(case)
    assert measured


def test_authority_negative_suites_are_never_broadened(monkeypatch):
    original = semantic_cases.parse_llm_output
    counts = {"accept": 0, "reject": 0, "no_legal": 0}
    def compare(raw, *, projection):
        try:
            value = json.loads(raw)
            selected = choice(value["discussion"]["speech_act"]["kind"])
        except (ValueError, KeyError, TypeError):
            selected = choice()
        try:
            result = original(raw, projection=projection)
        except DecisionValidationError:
            try:
                probe.validate_final(raw, selected, projection)
            except probe.NoLegalGrounding:
                counts["no_legal"] += 1
            except (ValueError, DecisionValidationError):
                counts["reject"] += 1
            else:
                raise AssertionError("GC2 broadened legacy rejection")
            raise
        try:
            assert probe.validate_final(raw, selected, projection) == json.loads(raw)
            counts["accept"] += 1
        except probe.NoLegalGrounding:
            counts["no_legal"] += 1
        return result
    monkeypatch.setattr(semantic_cases, "parse_llm_output", compare)
    semantic_cases.test_p6b_visibility_matrix_rejects_upgrade_downgrade_and_public_inference_misuse()
    semantic_cases.test_p6b_peer_actor_addressee_and_claim_speaker_binding_is_mechanical()
    semantic_cases.test_p6b_identity_nullability_option_handle_family_and_base_revision_mutations_fail()
    semantic_cases.test_p6b_opinion_change_requires_exact_prior_and_new_evidence()
    semantic_cases.test_p6b_trigger_specific_reaction_co_and_pre_vote_semantics_are_exact()
    assert counts["reject"] >= 10 and counts["accept"] + counts["no_legal"] > 0


def test_validate_final_passes_raw_unchanged_to_legacy_parser(monkeypatch):
    p = projection("chat")
    value = semantic_cases._payload(semantic_cases._request("chat"), "none")
    raw = json.dumps(value, indent=2)
    seen = []
    monkeypatch.setattr(probe, "parse_llm_output",
                        lambda text, *, projection: seen.append(text))
    assert probe.validate_final(raw, choice(), p) == value
    assert seen == [raw]


@pytest.mark.parametrize("name", ["evidence_array", "claim_updates"])
def test_empty_array_preserves_items_for_constrained_decoder(name):
    # The converter only applies min/maxItems in its items/prefixItems branch.
    # JSON Schema alone would also accept the broken shape without items.
    p = projection("chat", records=())
    schema = probe.candidate_schema(p, choice("QUESTION"))
    closed = schema["$defs"][name]
    assert "items" in closed
    assert closed["items"] == p.decision_schema["$defs"][name]["items"]
    assert closed["maxItems"] == 0
    root = {"$defs": schema["$defs"], **closed}
    validator = Draft202012Validator(root)
    assert validator.is_valid([])
    for nonempty in ([None], [{}], ["x"], [0], [True], [[], []]):
        assert not validator.is_valid(nonempty)
