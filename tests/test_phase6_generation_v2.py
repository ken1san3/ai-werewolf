from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from ai_client.discussion.generation_v2 import (
    AbilityOptionV2,
    CoOptionV2,
    GenerationCatalogV2,
    GenerationV2Error,
    OpinionBasisV2,
    VoteOptionV2,
    build_generation_v2_schema,
    parse_and_validate_generation_v2_candidate_structure,
)
from ai_client.llm.admission_broker import _structured_request_from_wire
from ai_client.llm.admission_client import _encode_frame, _structured_request_to_wire
from ai_client.llm.backend import OpenAICompatibleBackend
from ai_client.llm.types import (
    GenerationSettings,
    LLMMessage,
    OpenAICompatibleBackendConfig,
    StructuredGenerationRequest,
)


FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "Docs/ai/design/PHASE6_GENERATION_CONTRACT_V2_SCHEMAS.json"
)


def catalog() -> GenerationCatalogV2:
    return GenerationCatalogV2(
        reply_ids=("r000", "r001"),
        player_ids=("p000", "p001", "p002"),
        peer_player_ids=("p001", "p002"),
        fact_ids=("f000", "f001", "f002"),
        disclose_ids=("d000",),
        utterance_claim_ids=("c000", "c001"),
        observed_claim_ids=("k000", "k001"),
        opinion_bases=(
            OpinionBasisV2("u000", "p001", "SUSPICION", 50, (0, 25, 75, 100), ("f000",)),
        ),
        vote_options=(
            VoteOptionV2("o000", ("p001", "p002"), False),
            VoteOptionV2("o001", ("p002",), True),
        ),
        co_options=(CoOptionV2("o100", ("q000",)),),
        ability_options=(
            AbilityOptionV2("o200", 0, (), False),
            AbilityOptionV2("o201", 2, ("p001", "p002"), True),
        ),
    )


def compact(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def validate(stage: str, value: object):
    return parse_and_validate_generation_v2_candidate_structure(stage, compact(value), catalog())


def thaw(value: object) -> object:
    if isinstance(value, dict) or hasattr(value, "items"):
        return {key: thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [thaw(child) for child in value]
    return value


def valid_plan(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "reply_to": "r000",
        "act": "ANSWER",
        "subject_player_id": None,
        "topic": "EVENT",
        "stance": "UNCERTAIN",
        "opinion_basis_id": None,
        "opinion_current": None,
        "claim_id": None,
        "fact_ids": ["f000"],
        "disclose_ids": [],
    }
    value.update(changes)
    return value


def test_factory_matches_all_representative_schema_subtree_bytes() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    for stage, expected in fixture["schemas"].items():
        assert compact(build_generation_v2_schema(stage, catalog())) == compact(expected)


def test_factory_adapts_catalog_enum_const_and_repeats_option_branches() -> None:
    changed = GenerationCatalogV2(
        reply_ids=("r005",), player_ids=("p009", "p010"), peer_player_ids=("p010",),
        fact_ids=("f009",), disclose_ids=("d009",), utterance_claim_ids=("c009",),
        observed_claim_ids=("k009",),
        opinion_bases=(OpinionBasisV2("u009", "p010", "CREDIBILITY", 25, (0, 50, 75, 100)),),
        vote_options=(VoteOptionV2("o009", ("p010",), True),),
        co_options=(CoOptionV2("o010", ("q009", "q010")),),
        ability_options=(AbilityOptionV2("o011", 1, ("p010",), True),),
    )
    chat = build_generation_v2_schema("chat_plan", changed)
    assert chat["oneOf"][0]["properties"]["reply_to"]["enum"] == ["r005"]
    assert chat["oneOf"][3]["properties"]["opinion_basis_id"]["const"] == "u009"
    co = build_generation_v2_schema("co_opportunity", changed)
    assert [(b["properties"]["co_option_id"]["const"], b["properties"]["claimed_role_option_id"]["const"])
            for b in co["oneOf"][2:]] == [("o010", "q009"), ("o010", "q010")]
    ability = build_generation_v2_schema("ability", changed)
    assert [b["properties"]["decision"]["const"] for b in ability["oneOf"]] == ["USE", "NONE"]


@pytest.mark.parametrize(
    ("stage", "value"),
    [
        ("chat_plan", valid_plan()),
        ("message", {"message": "短い発言"}),
        ("pre_vote", {"vote_option_id": "o000", "decision": "VOTE", "ranked_player_ids": ["p002"], "assessment_updates": [], "claim_assessments": [], "fact_ids": []}),
        ("pre_vote", {"vote_option_id": "o001", "decision": "ABSTAIN", "ranked_player_ids": [], "assessment_updates": [], "claim_assessments": [], "fact_ids": []}),
        ("co_opportunity", {"decision": "SILENCE", "co_option_id": None, "claimed_role_option_id": None, "comment": None, "fact_ids": []}),
        ("co_opportunity", {"decision": "DECLARE", "co_option_id": "o100", "claimed_role_option_id": "q000", "comment": "占い師です", "fact_ids": ["f000"]}),
        ("ability", {"decision": "USE", "ability_option_id": "o200", "target_player_ids": [], "fact_ids": []}),
        ("ability", {"decision": "USE", "ability_option_id": "o201", "target_player_ids": ["p001", "p002"], "fact_ids": []}),
        ("ability", {"decision": "NONE", "ability_option_id": "o201", "target_player_ids": [], "fact_ids": []}),
    ],
)
def test_all_stages_accept_representative_positive_values(stage: str, value: object) -> None:
    candidate = validate(stage, value)
    assert candidate.stage == stage
    assert thaw(candidate.value) == value


@pytest.mark.parametrize(
    "raw",
    [
        '{"message":"a","message":"b"}',
        '{"message":NaN}',
        '{"message":Infinity}',
        '{"message":-Infinity}',
        '{"message":"ok"} trailing',
    ],
)
def test_strict_json_rejects_duplicates_nonfinite_and_trailing_data(raw: str) -> None:
    with pytest.raises(GenerationV2Error):
        parse_and_validate_generation_v2_candidate_structure("message", raw, catalog())


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"message": "ok", "extra": 1},
        {"message": "あ" * 200},  # 200 scalars, 600 bytes is legal; next test crosses bytes.
    ],
)
def test_message_missing_extra_and_byte_boundary(value: object) -> None:
    if value == {"message": "あ" * 200}:
        assert thaw(validate("message", value).value) == value
    else:
        with pytest.raises(GenerationV2Error):
            validate("message", value)
    with pytest.raises(GenerationV2Error):
        validate("message", {"message": "あ" * 199 + "😀"})


@pytest.mark.parametrize(
    "value",
    [
        valid_plan(reply_to=None),  # reply-only act cannot be spontaneous
        valid_plan(reply_to="r999"),
        valid_plan(fact_ids=["f999"]),
        valid_plan(fact_ids=["f000", "f000"]),
        valid_plan(extra=None),
        valid_plan(act="QUESTION", stance=None, disclose_ids=[], subject_player_id=None),
    ],
)
def test_chat_plan_rejects_invalid_branch_ids_duplicates_and_shape(value: object) -> None:
    with pytest.raises(GenerationV2Error):
        validate("chat_plan", value)


def test_opinion_change_factory_limits_candidate_ids_but_defers_authority_cause_proof() -> None:
    spontaneous = valid_plan(reply_to=None, act="OPINION_CHANGE", subject_player_id="p001", topic="RELATION", stance=None,
                             opinion_basis_id="u000", opinion_current=75, fact_ids=["f000"])
    # The schema proves candidate membership.  Whether f000 is new cannot be
    # decided without the later authority-backed capture projection.
    assert thaw(validate("chat_plan", spontaneous).value) == spontaneous
    spontaneous["fact_ids"] = ["f001"]
    assert thaw(validate("chat_plan", spontaneous).value) == spontaneous
    spontaneous["reply_to"] = "r000"
    spontaneous["fact_ids"] = []
    assert thaw(validate("chat_plan", spontaneous).value) == spontaneous


@pytest.mark.parametrize(
    "value",
    [
        {"vote_option_id": "o000", "decision": "ABSTAIN", "ranked_player_ids": [], "assessment_updates": [], "claim_assessments": [], "fact_ids": []},
        {"vote_option_id": "o001", "decision": "VOTE", "ranked_player_ids": ["p001"], "assessment_updates": [], "claim_assessments": [], "fact_ids": []},
        {"vote_option_id": "o001", "decision": "VOTE", "ranked_player_ids": [], "assessment_updates": [], "claim_assessments": [], "fact_ids": []},
    ],
)
def test_pre_vote_rejects_option_specific_abstain_target_and_count(value: object) -> None:
    with pytest.raises(GenerationV2Error):
        validate("pre_vote", value)


def test_vote_option_may_explicitly_allow_self_but_not_outside_player_catalog() -> None:
    values = dict(catalog().__dict__)
    values["vote_options"] = (VoteOptionV2("o000", ("p000",), False),)
    self_vote_catalog = GenerationCatalogV2(**values)
    candidate = {
        "vote_option_id": "o000",
        "decision": "VOTE",
        "ranked_player_ids": ["p000"],
        "assessment_updates": [],
        "claim_assessments": [],
        "fact_ids": [],
    }
    parsed = parse_and_validate_generation_v2_candidate_structure(
        "pre_vote", compact(candidate), self_vote_catalog
    )
    assert thaw(parsed.value) == candidate

    values["vote_options"] = (VoteOptionV2("o000", ("p999",), False),)
    with pytest.raises(GenerationV2Error, match="outside"):
        GenerationCatalogV2(**values)


def test_pre_vote_rejects_duplicate_assessment_identities() -> None:
    assessment = {"player_id": "p001", "suspicion": 50, "credibility": 50, "confidence": 50, "fact_ids": []}
    claim = {"claim_id": "k000", "verdict": "UNVERIFIED", "confidence": 50, "fact_ids": []}
    base = {"vote_option_id": "o000", "decision": "VOTE", "ranked_player_ids": ["p001"], "assessment_updates": [assessment, assessment], "claim_assessments": [], "fact_ids": []}
    with pytest.raises(GenerationV2Error, match="player_id"):
        validate("pre_vote", base)
    base["assessment_updates"] = []
    base["claim_assessments"] = [claim, claim]
    with pytest.raises(GenerationV2Error, match="claim_id"):
        validate("pre_vote", base)


@pytest.mark.parametrize(
    "value",
    [
        {"decision": "DECLARE", "co_option_id": "o100", "claimed_role_option_id": "q999", "comment": "x", "fact_ids": []},
        {"decision": "DECLARE", "co_option_id": "o100", "claimed_role_option_id": "q000", "comment": "", "fact_ids": []},
        {"decision": "SILENCE", "co_option_id": "o100", "claimed_role_option_id": None, "comment": None, "fact_ids": []},
    ],
)
def test_co_rejects_option_role_comment_and_silence_mismatch(value: object) -> None:
    with pytest.raises(GenerationV2Error):
        validate("co_opportunity", value)


@pytest.mark.parametrize(
    "value",
    [
        {"decision": "USE", "ability_option_id": "o201", "target_player_ids": ["p001"], "fact_ids": []},
        {"decision": "USE", "ability_option_id": "o201", "target_player_ids": ["p001", "p001"], "fact_ids": []},
        {"decision": "USE", "ability_option_id": "o201", "target_player_ids": ["p001", "p000"], "fact_ids": []},
        {"decision": "NONE", "ability_option_id": "o200", "target_player_ids": [], "fact_ids": []},
    ],
)
def test_ability_rejects_option_specific_target_count_target_and_none(value: object) -> None:
    with pytest.raises(GenerationV2Error):
        validate("ability", value)


@pytest.mark.parametrize(
    "mutation",
    [
        {"reply_ids": ("r000", "r000")},
        {"player_ids": ("player",)},
        {"peer_player_ids": ("p999",)},
    ],
)
def test_catalog_is_typed_bounded_and_rejects_invalid_ids(mutation: dict[str, object]) -> None:
    values = dict(catalog().__dict__)
    values.update(mutation)
    with pytest.raises(GenerationV2Error):
        GenerationCatalogV2(**values)


@pytest.mark.parametrize(
    "allowed_current",
    [
        (0, 25, 75),
        (100, 75, 25, 0),
        (0, 25, 50, 75, 100),
    ],
)
def test_opinion_current_catalog_requires_ordered_exact_prior_complement(
    allowed_current: tuple[int, ...],
) -> None:
    values = dict(catalog().__dict__)
    values["opinion_bases"] = (
        OpinionBasisV2("u000", "p001", "SUSPICION", 50, allowed_current),
    )
    with pytest.raises(GenerationV2Error, match="ordered complement"):
        GenerationCatalogV2(**values)


@pytest.mark.parametrize(
    ("stage", "bad_catalog"),
    [
        ([], catalog()),
        ("message", {}),
    ],
)
def test_public_factory_closes_invalid_direct_arguments_to_domain_error(
    stage: object, bad_catalog: object,
) -> None:
    with pytest.raises(GenerationV2Error):
        build_generation_v2_schema(stage, bad_catalog)


@pytest.mark.parametrize(
    ("stage", "raw", "candidate_catalog"),
    [
        ([], b"{}", None),
        ("unknown", b"{}", None),
        ("message", object(), None),
        ("message", b"{}", object()),
    ],
)
def test_candidate_structure_api_closes_invalid_argument_types_to_domain_error(
    stage: object, raw: object, candidate_catalog: object,
) -> None:
    with pytest.raises(GenerationV2Error):
        parse_and_validate_generation_v2_candidate_structure(
            stage, raw, catalog() if candidate_catalog is None else candidate_catalog
        )


def test_candidate_structure_rejects_recursive_exponent_overflow_before_schema() -> None:
    with pytest.raises(GenerationV2Error, match="non-finite"):
        parse_and_validate_generation_v2_candidate_structure(
            "message", '{"message":[1e309]}', catalog()
        )


def test_candidate_structure_result_is_recursively_frozen() -> None:
    parsed = validate("pre_vote", {
        "vote_option_id": "o000",
        "decision": "VOTE",
        "ranked_player_ids": ["p001"],
        "assessment_updates": [],
        "claim_assessments": [],
        "fact_ids": [],
    })
    with pytest.raises(TypeError):
        parsed.value["decision"] = "ABSTAIN"
    assert isinstance(parsed.value["ranked_player_ids"], tuple)


def test_all_factory_schemas_survive_admission_and_actual_v2_payload_bytes() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    async def exercise() -> None:
        transport_calls: list[httpx.Request] = []

        def fail_if_sent(request: httpx.Request) -> httpx.Response:
            transport_calls.append(request)
            raise AssertionError("offline payload construction must not call provider transport")

        backend = OpenAICompatibleBackend(
            OpenAICompatibleBackendConfig(
                endpoint="http://127.0.0.1:8080/v1/chat/completions",
                model="offline",
                generation=GenerationSettings(max_output_tokens=17),
                max_request_bytes=200000,
            ),
            transport=httpx.MockTransport(fail_if_sent),
        )
        try:
            for stage, expected in fixture["schemas"].items():
                schema = build_generation_v2_schema(stage, catalog())
                request = StructuredGenerationRequest(
                    request_id=f"capture:{stage}:1",
                    messages=(LLMMessage("user", stage),),
                    output_schema=schema,
                    generation_profile="phase6_v2",
                    max_output_tokens=512,
                    seed=0x12345678,
                )
                frame = _encode_frame(
                    200000,
                    "GENERATE",
                    structured_request=_structured_request_to_wire(request),
                )
                decoded = json.loads(frame[4:])["structured_request"]
                restored = _structured_request_from_wire(decoded)
                assert restored.generation_profile == "phase6_v2"
                assert restored.max_output_tokens == 512
                assert restored.seed == 0x12345678
                payload_bytes = backend._request_payload(restored)
                payload = json.loads(payload_bytes)
                actual = payload["response_format"]["json_schema"]["schema"]
                expected_bytes = compact(expected)
                assert compact(actual) == expected_bytes
                assert expected_bytes in payload_bytes
                assert payload["max_tokens"] == 512
                assert payload["seed"] == 0x12345678
            assert transport_calls == []
        finally:
            await backend.aclose()

    asyncio.run(exercise())
