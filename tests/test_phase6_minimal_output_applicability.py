"""Unexecuted preparation: finite public-synthetic offline contract tests.

Run manually: python -m pytest tests/test_phase6_minimal_output_applicability.py -q
No model output, private archive, provider, game, or quality score is involved.
"""
from copy import deepcopy
from dataclasses import replace
import json
import socket
import subprocess
import asyncio

import httpx
import pytest
from jsonschema import Draft202012Validator

from scripts import phase6_minimal_output_probe as probe
from tests.fixtures.phase6_minimal_output_cases import (
    SEMANTIC_RUBRICS, positive_cases, public_32_metadata, ref,
)


@pytest.fixture(autouse=True)
def forbid_runtime_actions(monkeypatch):
    # Imports of existing data types can transitively load these modules. Runtime
    # actions, including construction of a state store, are forbidden instead.
    from ai_client.discussion.state import DiscussionStateStore
    from ai_client.llm.backend import OpenAICompatibleBackend
    from ai_client.llm.brain import LLMBrain
    from ai_client.network import NetworkClient
    calls = []

    def forbidden(*args, **kwargs):
        calls.append("FORBIDDEN_RUNTIME_ACTION")
        raise AssertionError("offline case attempted a runtime action")

    for cls, names in (
        (socket.socket, ("connect", "connect_ex", "bind", "listen")),
        (DiscussionStateStore, ("__init__", "stage", "commit", "observe_authoritative")),
        (OpenAICompatibleBackend, ("__init__",)), (LLMBrain, ("__init__",)),
        (NetworkClient, ("__init__",)), (httpx.Client, ("request",)),
        (httpx.AsyncClient, ("request",)),
    ):
        for name in names:
            monkeypatch.setattr(cls, name, forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", forbidden)
    yield
    assert calls == []


def case(number):
    return positive_cases()[number - 1]


def run_case(item, candidate=None, binding=None):
    value = item.candidate if candidate is None else candidate
    return probe.validate(probe.canonical_bytes(value), item.binding if binding is None else binding)


def reject(raw, binding, code):
    with pytest.raises(probe.ProbeError) as caught:
        probe.validate(raw, binding)
    assert caught.value.code == code
    assert caught.value.applicability == "APPLICABILITY_INVALID"


def set_path(value, path, replacement):
    node = value
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = replacement


@pytest.mark.parametrize("number", range(1, 14), ids=[f"P{i:02}" for i in range(1, 14)])
def test_public_positive_contracts_preserve_bytes_and_private_view(number):
    item = case(number)
    candidate_before = probe.canonical_bytes(item.candidate)
    host_before = probe.canonical_bytes(item.binding.authority)
    private_before = item.binding.canonical_private_view_bytes
    result = run_case(item)
    assert result.applicability == "APPLICABILITY_COVERED"
    assert result.semantic_status == "NOT_EVALUATED"
    assert result.raw_sha256 == probe.sha256(candidate_before)
    assert result.input_sha256 == probe.sha256(host_before)
    assert result.private_before_sha256 == result.private_after_sha256 == probe.sha256(private_before)
    assert probe.canonical_bytes(item.candidate) == candidate_before
    assert probe.canonical_bytes(item.binding.authority) == host_before
    assert item.binding.canonical_private_view_bytes == private_before


def test_casepack_exact_coverage_and_structural_schema_only():
    cases = positive_cases()
    assert [c.case_id for c in cases] == [f"P{i:02}" for i in range(1, 14)]
    assert {c.binding.authority["trigger"] for c in cases} == {
        "INITIAL_CHAT", "PEER_CHAT", "CO_OPPORTUNITY", "PRE_VOTE", "ABILITY"}
    assert {c.candidate["decision"]["kind"] for c in cases} == {"none", "chat", "vote", "ability", "co_declare"}
    assert {c.candidate["speech_act"]["kind"] for c in cases} == {
        "NONE", "CLAIM", "QUESTION", "ANSWER", "REBUTTAL", "OPINION_CHANGE", "RELATION_HYPOTHESIS"}
    for item in cases:
        schema = probe.output_schema(item.binding.authority)
        Draft202012Validator.check_schema(schema)
        assert Draft202012Validator(schema).is_valid(item.candidate)
        assert set(schema["properties"]) == {"schema_version", "decision", "speech_act", "grounding", "utterance", "trigger_detail"}


@pytest.mark.parametrize("number", range(1, 14))
def test_decision_schema_is_closed_over_trigger_and_offered_options(number):
    item = case(number)
    host = probe.plain(item.binding.authority)
    schema = probe.output_schema(host)
    validator = Draft202012Validator(schema)
    decisions = [c.candidate["decision"] for c in positive_cases()]
    offered = {o["option_id"]: o for o in host["offered_options"]}
    for decision in decisions:
        value = deepcopy(item.candidate)
        value["decision"] = deepcopy(decision)
        allowed = (host["trigger"] != "PRE_VOTE" or any(o.get("allows_abstain", False) for o in offered.values())) if decision["kind"] == "none" else decision["option_id"] in offered
        assert validator.is_valid(value) is allowed
    for key, invalid in (("option_id", "missing"), ("target_player_id", "p-c"),
                         ("claimed_role_id", "missing"), ("target_player_ids", ["p-c"])):
        if key in item.candidate["decision"]:
            value = deepcopy(item.candidate)
            value["decision"][key] = invalid
            assert not validator.is_valid(value)


def test_no_offers_and_zero_target_schema_remain_well_formed():
    for number in (1, 10):
        item = case(number)
        host = probe.plain(item.binding.authority)
        host["offered_options"] = []
        schema = probe.output_schema(host)
        Draft202012Validator.check_schema(schema)
        value = deepcopy(item.candidate)
        value["decision"] = {"kind": "none"}
        assert Draft202012Validator(schema).is_valid(value) is (number == 1)
    item = case(12)
    host = probe.plain(item.binding.authority)
    host["offered_options"][0].update(valid_targets=[], target_count=0)
    value = deepcopy(item.candidate)
    value["decision"]["target_player_ids"] = []
    schema = probe.output_schema(host)
    Draft202012Validator.check_schema(schema)
    assert Draft202012Validator(schema).is_valid(value)
    assert run_case(item, value, probe.bind(host)).applicability == "APPLICABILITY_COVERED"


@pytest.mark.parametrize("raw,code", [
    (b"", "JSON_INVALID"), (b"\xff", "JSON_INVALID"), (b"{", "JSON_INVALID"),
    (b'{} {}', "JSON_INVALID"), (b'{"x":NaN}', "JSON_INVALID"),
    (b'{"x":Infinity}', "JSON_INVALID"), (b'{"x":-Infinity}', "JSON_INVALID"),
    (b'{"x":1e999}', "JSON_INVALID"), (b'{"x":0,"x":1}', "JSON_INVALID"),
    (b'{"x":{"a":0,"a":1}}', "JSON_INVALID"), (b'{"x":"\\ud800"}', "JSON_INVALID"),
    (b'[]', "SHAPE_INVALID"), (b'null', "SHAPE_INVALID"), (b'{}', "SHAPE_INVALID"),
    (b'{"x":0}', "SHAPE_INVALID"),
])
def test_strict_json_and_closed_shape(raw, code):
    reject(raw, case(1).binding, code)


def test_raw_cap_precedes_json_and_private_key_precedes_shape():
    item = case(1)
    reject(b"x" * 16385, item.binding, "TEXT_INVALID")
    for value in ({"assessment_updates": []}, {"extra": [{"private_updates": None}]}):
        reject(probe.canonical_bytes(value), item.binding, "PRIVATE_UPDATE_FORBIDDEN")
    raw = b" \n" + probe.canonical_bytes(item.candidate) + b"\t"
    assert probe.validate(raw, item.binding).raw_sha256 == probe.sha256(raw)


@pytest.mark.parametrize("key", sorted(probe.FORBIDDEN))
def test_private_update_keys_rejected_at_every_object_depth(key):
    item = case(2)
    for path in ((), ("speech_act",), ("extra",)):
        value = deepcopy(item.candidate)
        target = value
        for part in path:
            target = target.setdefault(part, {})
        target[key] = []
        reject(probe.canonical_bytes(value), item.binding, "PRIVATE_UPDATE_FORBIDDEN")
    value = deepcopy(item.candidate)
    value["utterance"] = f"The literal identifier {key} is just text."
    assert run_case(item, value).applicability == "APPLICABILITY_COVERED"


@pytest.mark.parametrize("number", [1, 2, 3, 4, 5, 6, 7])
def test_each_speech_branch_rejects_missing_extra_null_and_unknown_kind(number):
    item = case(number)
    for key in item.candidate["speech_act"]:
        value = deepcopy(item.candidate)
        del value["speech_act"][key]
        reject(probe.canonical_bytes(value), item.binding, "SHAPE_INVALID")
    for replacement in (None, {"kind": "UNKNOWN"}, {**item.candidate["speech_act"], "extra": None}):
        value = deepcopy(item.candidate)
        value["speech_act"] = replacement
        reject(probe.canonical_bytes(value), item.binding, "SHAPE_INVALID")


@pytest.mark.parametrize("number,path,value,code", [
    (2, ("decision", "option_id"), "missing", "SHAPE_INVALID"),
    (2, ("speech_act", "subject_player_id"), "not-present", "VALUE_NOT_OFFERED"),
    (10, ("decision", "target_player_id"), "p-c", "SHAPE_INVALID"),
    (10, ("decision", "target_player_id"), None, "SHAPE_INVALID"),
    (12, ("decision", "target_player_ids"), [], "SHAPE_INVALID"),
    (12, ("decision", "target_player_ids"), ["p-b", "p-b"], "SHAPE_INVALID"),
    (12, ("decision", "target_player_ids"), ["p-c"], "SHAPE_INVALID"),
    (9, ("decision", "claimed_role_id"), "unoffered-role", "SHAPE_INVALID"),
    (9, ("trigger_detail", "claimed_role_id"), "claim-b", "VALUE_NOT_OFFERED"),
    (8, ("trigger_detail", "decision"), "DEFER", None),
    (4, ("speech_act", "source_interpretation"), "CLAIM", "SHAPE_INVALID"),
    (4, ("speech_act", "addressee_player_id"), "p-c", "BINDING_INVALID"),
    (5, ("speech_act", "addressee_player_id"), "p-a", "BINDING_INVALID"),
    (6, ("speech_act", "prior"), 79, "VALUE_NOT_OFFERED"),
    (6, ("speech_act", "current"), 80, "SHAPE_INVALID"),
    (6, ("speech_act", "current"), 101, "SHAPE_INVALID"),
    (6, ("speech_act", "current"), True, "SHAPE_INVALID"),
    (6, ("speech_act", "current"), 30.0, "SHAPE_INVALID"),
    (6, ("speech_act", "causes"), [], "SHAPE_INVALID"),
    (6, ("speech_act", "causes"), [ref(1)], "VALUE_NOT_OFFERED"),
    (7, ("speech_act", "target_player_id"), "p-b", "SHAPE_INVALID"),
    (3, ("trigger_detail", "score"), True, "SHAPE_INVALID"),
    (2, ("speech_act", "evidence", 0, "order"), True, "SHAPE_INVALID"),
    (2, ("speech_act", "evidence", 0, "order"), 1.0, "SHAPE_INVALID"),
    (2, ("speech_act", "evidence", 0, "order"), 999, "BINDING_INVALID"),
    (2, ("speech_act", "evidence", 0, "visibility"), "AUTHORIZED_PRIVATE", "BINDING_INVALID"),
    (3, ("grounding", 0, "purpose"), "UTTERANCE", "BINDING_INVALID"),
    (3, ("trigger_detail", "trigger"), ref(2), "BINDING_INVALID"),
    (10, ("trigger_detail", "option_id"), "missing", "VALUE_NOT_OFFERED"),
    (10, ("trigger_detail", "ranked_target_player_ids"), ["p-b", "p-b"], "SHAPE_INVALID"),
    (10, ("trigger_detail", "ranked_target_player_ids"), ["p-b", "p-c"], "VALUE_NOT_OFFERED"),
    (10, ("trigger_detail", "preferred_target_player_id"), "p-c", "SHAPE_INVALID"),
])
def test_closed_and_cross_field_negatives(number, path, value, code):
    item = case(number)
    candidate = deepcopy(item.candidate)
    set_path(candidate, path, value)
    if code is None:
        assert run_case(item, candidate).applicability == "APPLICABILITY_COVERED"
    else:
        reject(probe.canonical_bytes(candidate), item.binding, code)


@pytest.mark.parametrize("number", [3, 8, 10, 11])
def test_trigger_detail_is_flat_required_and_not_optional(number):
    item = case(number)
    for replacement in (None, {"wrapped": item.candidate["trigger_detail"]}):
        value = deepcopy(item.candidate)
        value["trigger_detail"] = replacement
        reject(probe.canonical_bytes(value), item.binding, "SHAPE_INVALID")


def test_nontrigger_detail_forbidden_and_contextful_co_report_rejected():
    item = case(1)
    value = deepcopy(item.candidate)
    value["trigger_detail"] = {"trigger": ref(1), "score": 50, "reason": "DIRECT_QUESTION"}
    reject(probe.canonical_bytes(value), item.binding, "SHAPE_INVALID")
    value = deepcopy(item.candidate)
    value["decision"] = {"kind": "co_report", "option_id": "co:0"}
    reject(probe.canonical_bytes(value), item.binding, "SHAPE_INVALID")
    value["decision"] = {"kind": "ability", "option_id": "ability:0", "target_player_ids": ["p-b"]}
    reject(probe.canonical_bytes(value), item.binding, "SHAPE_INVALID")


def test_no_decision_option_and_co_silence_declare_conflicts():
    item = case(1)
    value = deepcopy(item.candidate)
    value["decision"]["option_id"] = None
    reject(probe.canonical_bytes(value), item.binding, "SHAPE_INVALID")
    for number, decision in ((8, {"kind": "co_declare", "option_id": "co:0", "claimed_role_id": "claim-a"}),
                             (9, {"kind": "none"})):
        item = case(number)
        value = deepcopy(item.candidate)
        value["decision"] = decision
        reject(probe.canonical_bytes(value), item.binding, "VALUE_NOT_OFFERED")


@pytest.mark.parametrize("change", ["captured-only", "projected-only", "visibility", "actor", "channel"])
def test_projected_and_captured_exact_intersection(change):
    item = case(4)
    host = probe.plain(item.binding.authority)
    if change == "captured-only":
        host["projected_evidence"].pop(0)
    elif change == "projected-only":
        host["captured_evidence"].pop(0)
    elif change == "visibility":
        host["captured_evidence"][0]["ref"]["visibility"] = "AUTHORIZED_PRIVATE"
    elif change == "actor":
        host["captured_evidence"][0]["actor_player_ids"] = ["p-c"]
    else:
        host["captured_evidence"][0]["channel_id"] = "other-channel"
    reject(probe.canonical_bytes(item.candidate), probe.bind(host), "BINDING_INVALID")


@pytest.mark.parametrize("change", ["visibility", "actor", "channel"])
def test_unreferenced_shared_record_metadata_must_also_match(change):
    item = case(1)  # NONE, no candidate references.
    host = probe.plain(item.binding.authority)
    record = host["captured_evidence"][1]
    if change == "visibility":
        record["ref"]["visibility"] = "AUTHORIZED_PRIVATE"
    elif change == "actor":
        record["actor_player_ids"] = ["p-b"]
    else:
        record["channel_id"] = "other-channel"
    reject(probe.canonical_bytes(item.candidate), probe.bind(host), "BINDING_INVALID")


@pytest.mark.parametrize("field", ["projected_evidence", "captured_evidence"])
def test_unreferenced_one_sided_record_is_not_a_binding_conflict(field):
    item = case(1)
    host = probe.plain(item.binding.authority)
    host[field].pop(1)
    assert run_case(item, binding=probe.bind(host)).applicability == "APPLICABILITY_COVERED"


def test_self_source_wrong_source_kind_and_channel_are_rejected():
    item = case(4)
    host = probe.plain(item.binding.authority)
    for field in ("projected_evidence", "captured_evidence"):
        host[field][0]["actor_player_ids"] = ["p-a"]
    reject(probe.canonical_bytes(item.candidate), probe.bind(host), "BINDING_INVALID")
    host = probe.plain(item.binding.authority)
    host["offered_options"][0]["channel"] = "other-channel"
    reject(probe.canonical_bytes(item.candidate), probe.bind(host), "BINDING_INVALID")
    value = deepcopy(item.candidate)
    value["speech_act"]["in_reply_to"] = ref(3, "ability_result", "AUTHORIZED_PRIVATE")
    value["speech_act"]["addressee_player_id"] = "p-a"
    value["grounding"][0]["ref"] = ref(3, "ability_result", "AUTHORIZED_PRIVATE")
    reject(probe.canonical_bytes(value), item.binding, "BINDING_INVALID")


def test_private_evidence_legality_does_not_imply_public_disclosure_safety():
    item = case(2)
    value = deepcopy(item.candidate)
    private_ref = ref(3, "ability_result", "AUTHORIZED_PRIVATE")
    value["speech_act"]["evidence"] = [private_ref]
    value["grounding"] = [{"purpose": "UTTERANCE", "ref": private_ref}]
    assert run_case(item, value).semantic_status == "NOT_EVALUATED"
    item = case(7)
    value = deepcopy(item.candidate)
    value["speech_act"]["evidence"] = [private_ref]
    reject(probe.canonical_bytes(value), item.binding, "SHAPE_INVALID")


def test_opinion_prior_new_cause_and_unknown_update_need():
    item = case(6)
    host = probe.plain(item.binding.authority)
    host["prior_assessments"] = []
    reject(probe.canonical_bytes(item.candidate), probe.bind(host), "VALUE_NOT_OFFERED")
    host = probe.plain(item.binding.authority)
    host["requires_private_update"] = True
    result = run_case(item, binding=probe.bind(host))
    assert result.applicability == "APPLICABILITY_UNRESOLVED"
    assert result.private_before_sha256 == result.private_after_sha256


def test_pre_vote_abstention_and_option_target_identity():
    item = case(11)
    host = probe.plain(item.binding.authority)
    host["offered_options"][0]["allows_abstain"] = False
    reject(probe.canonical_bytes(item.candidate), probe.bind(host), "SHAPE_INVALID")
    item = case(10)
    host = probe.plain(item.binding.authority)
    host["offered_options"].append({**host["offered_options"][0], "option_id": "vote:1"})
    value = deepcopy(item.candidate)
    value["trigger_detail"]["option_id"] = "vote:1"
    reject(probe.canonical_bytes(value), probe.bind(host), "VALUE_NOT_OFFERED")


@pytest.mark.parametrize("change", ["input-digest", "private-digest", "input-bytes", "private-bytes",
                                   "actor", "revision", "prior", "option", "context"])
def test_sidecar_bytes_digest_and_all_authority_fields_are_bound(change):
    item = case(1)
    binding = item.binding
    if change == "input-digest":
        binding = replace(binding, input_sha256="0" * 64)
    elif change == "private-digest":
        binding = replace(binding, captured_private_state_sha256="0" * 64)
    elif change == "input-bytes":
        raw = binding.canonical_input_bytes + b" "
        binding = replace(binding, canonical_input_bytes=raw, input_sha256=probe.sha256(raw))
    elif change == "private-bytes":
        raw = b'{"prior_assessments":[]}'
        binding = replace(binding, canonical_private_view_bytes=raw, captured_private_state_sha256=probe.sha256(raw))
    else:
        host = probe.plain(binding.authority)
        if change == "actor": host["actor_player_id"] = "p-c"
        elif change == "revision": host["base_revision"] += 1
        elif change == "prior": host["prior_assessments"][0]["suspicion"] = 79
        elif change == "option": host["offered_options"][0]["channel"] = "other"
        else: host["context_sha256"] = "b" * 64
        binding = replace(binding, authority=host)
    reject(probe.canonical_bytes(item.candidate), binding, "BINDING_INVALID")


def test_sidecar_is_deeply_read_only():
    binding = case(1).binding
    with pytest.raises(TypeError):
        binding.authority["prior_assessments"][0]["suspicion"] = 0
    with pytest.raises(TypeError):
        binding.authority["current_player_ids"][0] = "other"


def test_grounding_exact_set_order_independent_not_silently_repaired():
    item = case(4)
    value = deepcopy(item.candidate)
    value["grounding"].reverse()
    before = probe.canonical_bytes(value)
    assert run_case(item, value).applicability == "APPLICABILITY_COVERED"
    assert probe.canonical_bytes(value) == before
    for transform in (lambda x: x.pop(), lambda x: x.append(deepcopy(x[0]))):
        value = deepcopy(item.candidate)
        transform(value["grounding"])
        reject(probe.canonical_bytes(value), item.binding, "BINDING_INVALID")


def test_grounding_over_eight_is_legal_when_each_original_field_is_bounded():
    item = case(4)
    host = probe.plain(item.binding.authority)
    records = [{"ref": ref(n), "actor_player_ids": ["p-b"], "channel_id": "public"} for n in range(1, 10)]
    host["projected_evidence"] = deepcopy(records)
    host["captured_evidence"] = deepcopy(records)
    value = deepcopy(item.candidate)
    value["speech_act"]["evidence"] = [ref(n) for n in range(2, 10)]
    value["grounding"] = [{"purpose": "UTTERANCE", "ref": ref(n)} for n in range(1, 10)]
    value["grounding"].append({"purpose": "REACTION", "ref": ref(1)})
    assert len(value["grounding"]) == 10
    assert run_case(item, value, probe.bind(host)).applicability == "APPLICABILITY_COVERED"
    value["speech_act"]["evidence"].reverse()
    reject(probe.canonical_bytes(value), probe.bind(host), "SHAPE_INVALID")
    value["speech_act"]["evidence"] = [ref(n) for n in range(1, 10)]
    reject(probe.canonical_bytes(value), probe.bind(host), "SHAPE_INVALID")


@pytest.mark.parametrize("number", [2, 9])
@pytest.mark.parametrize("text,allowed", [(None, False), ("", False), ("   ", True),
    ("x" * 188 + ".", True), ("x" * 190, False), ("x" * 199 + ".", True),
    ("x" * 200 + ".", False), ("x" * 189 + '.")', True),
    ("🙂" * 150 + ".", False), ("a" * 188 + "?", True)])
def test_text_bounds_are_detected_without_cut_or_repair(number, text, allowed):
    item = case(number)
    value = deepcopy(item.candidate)
    value["utterance"] = text
    before = deepcopy(value)
    if allowed:
        assert run_case(item, value).applicability == "APPLICABILITY_COVERED"
    else:
        reject(probe.canonical_bytes(value), item.binding, "TEXT_INVALID")
    assert value == before


@pytest.mark.parametrize("number", [1, 10, 11, 12, 13])
def test_non_text_actions_require_null_utterance(number):
    item = case(number)
    value = deepcopy(item.candidate)
    value["utterance"] = "Unexpected text."
    reject(probe.canonical_bytes(value), item.binding, "TEXT_INVALID")


def test_semantic_rubric_is_not_mechanical_pass_or_regex_classifier():
    item = case(4)
    value = deepcopy(item.candidate)
    # An unrelated short message still has only a structural diagnostic result.
    value["utterance"] = "The weather is pleasant."
    result = run_case(item, value)
    assert result.semantic_status == "NOT_EVALUATED"
    assert not hasattr(result, "hard_pass") and not hasattr(result, "semantic_pass")
    assert set(SEMANTIC_RUBRICS) == {"ACT_TEXT", "QUESTION_RESPONSE", "GROUND_SUPPORT", "PRIVATE_DISCLOSURE", "CO_STRATEGY"}
    assert all(type(v) is str and v for v in SEMANTIC_RUBRICS.values())


def test_legacy_fields_are_not_constructed_or_defaulted():
    item = case(2)
    raw = probe.canonical_bytes(item.candidate)
    run_case(item)
    assert set(item.candidate) == {"schema_version", "decision", "speech_act", "grounding", "utterance", "trigger_detail"}
    assert all(key.encode() not in raw for key in probe.FORBIDDEN)
    assert "message" not in item.candidate["decision"]
    value = deepcopy(item.candidate)
    value["decision"]["message"] = value["utterance"]
    reject(probe.canonical_bytes(value), item.binding, "SHAPE_INVALID")


def test_public_32_metadata_is_total_and_unresolved_without_execution():
    rows = public_32_metadata()
    assert all(row["requires_private_update"] is None for row in rows)
    result = probe.metadata_coverage(rows)
    assert result["trigger_counts"] == {"INITIAL_CHAT": 4, "PEER_CHAT": 24,
                                         "CO_OPPORTUNITY": 2, "PRE_VOTE": 2, "ABILITY": 0}
    assert result["total"] == result["covered"] + result["invalid"] + result["unresolved"] == 32
    assert result["covered"] == result["invalid"] == 0 and result["unresolved"] == 32
    assert result["none_controls"] == ["G14-1", "G14-2"]
    assert result["semantic_status"] == "NOT_EVALUATED"


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "extra", "unknown", "trigger", "flag"])
def test_metadata_cannot_drop_or_relabel_cases(mutation):
    rows = public_32_metadata()
    if mutation == "missing": rows.pop()
    elif mutation == "duplicate": rows[-1] = deepcopy(rows[0])
    elif mutation == "extra": rows.append(deepcopy(rows[0]))
    elif mutation == "unknown": rows[0]["case_id"] = "NEW"
    elif mutation == "trigger": rows[0]["trigger"] = "ABILITY"
    else: rows[0]["requires_private_update"] = "false"
    with pytest.raises(probe.ProbeError, match="BINDING_INVALID"):
        probe.metadata_coverage(rows)
