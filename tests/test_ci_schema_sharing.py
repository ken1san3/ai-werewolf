"""Offline checks of representation-only sharing against the approved contract."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path

import httpx
import jsonschema
import pytest

from ai_client.brain import BrainActionOption
from ai_client.discussion.context import canonical_json_bytes
from ai_client.discussion.projection import _option, discussion_output_schema
from ai_client.llm.backend import OpenAICompatibleBackend
from ai_client.llm.decision import DecisionValidationError, parse_llm_output
from ai_client.llm.prompt import project_brain_input
from ai_client.llm.types import LLMMessage, StructuredGenerationRequest
from ai_client.network import AbilityAction, ChatAction, CoDeclareAction, VoteAction
from tests.test_phase4_llm_backend import _config, _success_payload
from tests.test_phase6_quality_grounding import CONFIG, payload, request_with
from tests.test_phase6_memory_projection import _chats


CASES = [
    (trigger, (), 8) for trigger in ("INITIAL_CHAT", "PEER_CHAT", "CO_OPPORTUNITY", "ABILITY")
] + [("PRE_VOTE", flags, count) for flags in ((False,), (True,), (False, True), (True, False, True))
     for count in (1, 8, 32)]


def schema_case(trigger, flags, count):
    targets = tuple(f"candidate-{i}" for i in range(count))
    common = dict(connection_generation=1, action_generation=1, phase="opaque-phase", day=1)
    if trigger == "PRE_VOTE":
        handles = [VoteAction(**common, type="vote", valid_targets=targets[index:] or targets,
                              target_count=1, allows_abstain=flag) for index, flag in enumerate(flags)]
    else:
        handles = [{
            "INITIAL_CHAT": ChatAction(**common, type="chat", channel="opaque-public"),
            "PEER_CHAT": ChatAction(**common, type="chat", channel="opaque-public"),
            "CO_OPPORTUNITY": CoDeclareAction(**common, type="co_declare", claimed_role_ids=("claim-a", "claim-b")),
            "ABILITY": AbilityAction(**common, type="ability", ability_id="inspect", description=None,
                                     valid_targets=targets, target_count=1, uses_remaining=1),
        }[trigger]]
    options = tuple(BrainActionOption(f"action:{index}", handle) for index, handle in enumerate(handles))
    request = request_with(trigger=trigger, options=options, chats=_chats(1) if trigger == "PEER_CHAT" else ())
    return dict(options=[_option(option, 512) for option in options], capture=request.discussion,
                max_text=200, player_ids=("opaque-self", "opaque-peer", *targets))


def expanded_schema(schema):
    """Inline local references; refuse siblings/cycles instead of overlooking them."""
    def expand(node, chain=()):
        if isinstance(node, list):
            return [expand(item, chain) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            assert set(node) == {"$ref"}
            ref = node["$ref"]
            assert ref.startswith("#/$defs/") and ref not in chain
            return expand(schema["$defs"][ref.removeprefix("#/$defs/")], (*chain, ref))
        return {key: expand(value, chain) for key, value in node.items() if key != "$defs"}
    return expand(schema)


@pytest.mark.parametrize("trigger,flags,count", CASES)
def test_expanded_schema_exactly_matches_pre_cleanup_contract(trigger, flags, count):
    # Generated once from 6f876ff, before sharing. Never regenerate on failure.
    expected = json.loads(Path(__file__).with_name("fixtures").joinpath("ci_schema_contract_hashes.json").read_text())
    schema = discussion_output_schema(**schema_case(trigger, flags, count))
    jsonschema.Draft202012Validator.check_schema(schema)
    expanded = expanded_schema(schema)
    key = f"{trigger}:{','.join(str(int(flag)) for flag in flags)}:{count}"
    assert hashlib.sha256(canonical_json_bytes(expanded)).hexdigest() == expected[key]


@pytest.mark.parametrize("abstain", [False, True])
def test_shared_candidates_keep_schema_and_semantic_rejections(abstain):
    option = BrainActionOption("action:0", VoteAction(1, 1, "opaque-phase", 1, "vote",
                                                    ("opaque-peer",), 1, abstain))
    projection = project_brain_input(request_with(trigger="PRE_VOTE", options=(option,)), config=CONFIG)
    schema = json.loads(canonical_json_bytes(projection.decision_schema))
    validator = jsonschema.Draft202012Validator(schema)
    value = payload(projection, kind="none")
    value["decision"] = {"kind": "vote", "option_id": "action:0", "target_player_id": "opaque-peer"}
    value["discussion"].update(decision_kind="vote", option_id="action:0",
        pre_vote_reassessment={"option_id": "action:0", "ranked_target_player_ids": ["opaque-peer"],
                               "preferred_target_player_id": "opaque-peer", "evidence": []})
    assert validator.is_valid(value)
    assert parse_llm_output(json.dumps(value), projection=projection)
    for ranked in (["not-offered"], ["opaque-peer", "opaque-peer"], ["opaque-peer"] * 33):
        invalid = copy.deepcopy(value)
        invalid["discussion"]["pre_vote_reassessment"]["ranked_target_player_ids"] = ranked
        assert not validator.is_valid(invalid)
        with pytest.raises(DecisionValidationError):
            parse_llm_output(json.dumps(invalid), projection=projection)
    invalid = copy.deepcopy(value)
    invalid["decision"]["target_player_id"] = "not-offered"
    assert not validator.is_valid(invalid)
    with pytest.raises(DecisionValidationError):
        parse_llm_output(json.dumps(invalid), projection=projection)


def test_provider_wire_preserves_shared_local_references():
    schema = discussion_output_schema(**schema_case("PRE_VOTE", (True,), 8))
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=json.loads(_success_payload()))

    async def run():
        backend = OpenAICompatibleBackend(_config(), transport=httpx.MockTransport(handler))
        try:
            await backend.generate(StructuredGenerationRequest(request_id="schema-sharing",
                messages=(LLMMessage("user", "Return a legal vote."),), output_schema=schema))
        finally:
            await backend.aclose()

    asyncio.run(run())
    assert len(captured) == 1
    wire_schema = captured[0]["response_format"]["json_schema"]["schema"]
    assert wire_schema == schema
    assert captured[0]["response_format"]["json_schema"]["strict"] is True
    assert wire_schema["$defs"]["vote_ranks_0"]["items"] == {"$ref": "#/$defs/vote_targets_0"}
    jsonschema.Draft202012Validator.check_schema(wire_schema)
    assert expanded_schema(wire_schema) == expanded_schema(schema)
