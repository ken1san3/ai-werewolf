"""Pure adapter from the fixed Phase 6 suite to MinimalOutputV0.

No provider, state mutation, private-update inference, or legacy-output repair lives here.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping
import hashlib
import json

from ai_client.discussion.context import canonical_json_bytes
from ai_client.llm.prompt import _option
from scripts import phase6_minimal_output_probe as probe


GROUNDING_INSTRUCTION = (
    "Grounding mirrors references already used by speech or trigger fields: UTTERANCE for "
    "speech evidence/source/in_reply_to, OPINION_CURRENT for opinion-change causes, REACTION "
    "for the reaction trigger, and PRE_VOTE for pre-vote evidence. It does not establish truth "
    "or public validity. "
)
MINIMAL_V1_INSTRUCTION = (
    "Return only phase6.minimal-output.v0 matching the response schema. "
    "Do not return legacy discussion or update fields. Use only state, option, player, "
    "and evidence values present in the canonical user data. Do not update or infer "
    "assessment, claim, relation, or strategy state. " + GROUNDING_INSTRUCTION + "Return JSON only."
)
MINIMAL_V1_INSTRUCTION_SHA256 = hashlib.sha256(MINIMAL_V1_INSTRUCTION.encode("utf-8")).hexdigest()
UNRESOLVED_REASON = "PRIVATE_UPDATE_REQUIREMENT_UNKNOWN"
FIXED_QUESTION_GROUPS = frozenset({"G01", "G05", "G06", "G07", "G08", "G09", "G10", "G11", "G16"})
NONE_CONTROLS = frozenset({"G14-1", "G14-2"})


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class SuitePublicMetadataV1:
    case_id: str
    ordinal: int
    category: str
    trigger: str
    expected_acts: tuple[str, ...]
    hard_rule_ids: tuple[str, ...]
    is_fixed_question_case: bool
    is_legal_none_control: bool
    update_requirement: bool | None
    unresolved_reason: str | None


@dataclass(frozen=True)
class SuiteCaseV1:
    case: object
    projection: object
    binding: probe.SuiteBindingV1
    schema: Mapping
    public_metadata: SuitePublicMetadataV1

    def __post_init__(self):
        object.__setattr__(self, "schema", _freeze(self.schema))


def _evidence_record(source, actors, channel):
    ref = json.loads(canonical_json_bytes(source))
    return {"ref": ref, "actor_player_ids": list(actors), "channel_id": channel}


def _authority(case, projection) -> dict:
    canonical = probe.plain(projection.canonical_input)
    capture = projection.discussion_capture
    if capture is None:
        raise probe.ProbeError("BINDING_INVALID")
    capture_input = canonical["capture"]
    expected_trigger = json.loads(canonical_json_bytes(capture.trigger))
    if (capture_input["trigger"] != expected_trigger
            or capture_input["base_revision"] != capture.base_revision
            or capture_input["context_sha256"] != capture.context_sha256
            or canonical["context"]["player_id"] != capture.player_id):
        raise probe.ProbeError("BINDING_INVALID")
    request_options = [_option(item, 512) for item in case.request.action_context.options]
    canonical_options = canonical["action_context"]["options"]
    if request_options != canonical_options:
        raise probe.ProbeError("BINDING_INVALID")
    offered = []
    for item in canonical_options:
        kind = item["action_kind"]
        common = {"action_kind": kind, "option_id": item["option_id"]}
        if kind == "chat": common["channel"] = item["channel"]
        elif kind == "vote": common.update(valid_targets=item["valid_targets"], allows_abstain=item["allows_abstain"])
        elif kind == "ability": common.update(valid_targets=item["valid_targets"], target_count=item["target_count"])
        elif kind == "co_declare": common["claimed_role_ids"] = item["claimed_role_ids"]
        else: raise probe.ProbeError("BINDING_INVALID")
        offered.append(common)
    allowed = canonical["grounding"]["allowed_decisions"]
    include_none = (capture.trigger.kind != "PRE_VOTE"
                    or any(item.get("allows_abstain") for item in offered))
    expected_allowed = ([{"kind": "none", "option_id": None}] if include_none else [])
    expected_allowed.extend({"kind": item["action_kind"], "option_id": item["option_id"]} for item in offered)
    if allowed != expected_allowed:
        raise probe.ProbeError("BINDING_INVALID")
    projected = [_evidence_record(item["source"], item["actor_player_ids"], item["channel_id"])
                 for item in canonical["memory"]["records"]]
    captured = [_evidence_record(item.source, item.actor_player_ids, item.channel_id)
                for item in capture.evidence]
    allowed_refs = canonical["grounding"]["allowed_evidence_refs"]
    if [item["ref"] for item in projected] != allowed_refs:
        raise probe.ProbeError("BINDING_INVALID")
    captured_by_ref = {probe.canonical_bytes(item["ref"]): item for item in captured}
    if any(captured_by_ref.get(probe.canonical_bytes(item["ref"])) != item for item in projected):
        raise probe.ProbeError("BINDING_INVALID")
    current_players = sorted(capture_input["current_player_ids"])
    request_players = sorted(player.player_id for player in case.request.snapshot.players)
    if (current_players != request_players
            or sorted(canonical["grounding"]["current"]["alive_player_ids"])
            != sorted(case.request.snapshot.alive_player_ids)):
        raise probe.ProbeError("BINDING_INVALID")
    priors = [{"subject_player_id": item.player_id, "suspicion": item.suspicion,
               "credibility": item.credibility,
               "prior_evidence_identities": [[ref.record_kind.value, ref.order] for ref in item.evidence]}
              for item in capture.state.assessments]
    return {
        "case_id": case.case_id,
        "trigger": capture.trigger.kind,
        "actor_player_id": capture.player_id,
        "current_player_ids": current_players,
        "offered_options": offered,
        "projected_evidence": projected,
        "captured_evidence": captured,
        "reaction_source": (None if capture.trigger.source is None
                            else json.loads(canonical_json_bytes(capture.trigger.source))),
        "prior_assessments": priors,
        "base_revision": capture.base_revision,
        "context_sha256": capture.context_sha256,
        "max_text": 200,
        "max_text_utf8_bytes": 600,
        "max_candidate_utf8_bytes": canonical["limits"]["max_proposal_utf8_bytes"],
    }


def bind_case(case, projection, *, ordinal: int | None = None) -> SuiteCaseV1:
    """Bind one canonical case without deciding its private-update requirement."""
    if projection.messages[1].content.encode("utf-8") != canonical_json_bytes(projection.canonical_input):
        raise probe.ProbeError("BINDING_INVALID")
    profile = projection.short_chat
    if (profile is None or profile.max_text_chars != 200
            or profile.max_text_utf8_bytes != 600):
        raise probe.ProbeError("BINDING_INVALID")
    authority = _authority(case, projection)
    private = probe.canonical_bytes({"prior_assessments": authority["prior_assessments"]})
    binding = probe.bind_suite(authority, projection.messages[1].content.encode("utf-8"), private,
                               update_requirement=None)
    schema = probe.output_schema_suite(authority)
    # Creating the schema is also the closed decision-branch validation. Every offered
    # non-none branch must appear exactly once with the same option identity.
    branches = schema["properties"]["decision"]["oneOf"]
    represented = [(branch["properties"]["kind"].get("const"),
                    branch["properties"].get("option_id", {}).get("const")) for branch in branches]
    allowed = [(item["kind"], item["option_id"])
               for item in probe.plain(projection.canonical_input)["grounding"]["allowed_decisions"]]
    if represented != allowed:
        raise probe.ProbeError("BINDING_INVALID")
    by_identity = {identity: branch for identity, branch in zip(represented, branches)}
    for option in authority["offered_options"]:
        props = by_identity[(option["action_kind"], option["option_id"])]["properties"]
        if option["action_kind"] == "vote":
            if props["target_player_id"].get("enum") != option["valid_targets"]:
                raise probe.ProbeError("BINDING_INVALID")
        elif option["action_kind"] == "ability":
            targets = props["target_player_ids"]
            if (targets["items"].get("enum") != option["valid_targets"]
                    or targets["minItems"] != option["target_count"]
                    or targets["maxItems"] != option["target_count"]):
                raise probe.ProbeError("BINDING_INVALID")
        elif option["action_kind"] == "co_declare":
            if props["claimed_role_id"].get("enum") != option["claimed_role_ids"]:
                raise probe.ProbeError("BINDING_INVALID")
    if ordinal is None:
        try: ordinal = probe.CASE_IDS.index(case.case_id) + 1
        except ValueError: raise probe.ProbeError("BINDING_INVALID") from None
    metadata = SuitePublicMetadataV1(
        case.case_id, ordinal, case.category, authority["trigger"], tuple(case.expected_acts),
        tuple(case.hard_rules), case.case_id[:3] in FIXED_QUESTION_GROUPS,
        case.case_id in NONE_CONTROLS, None, UNRESOLVED_REASON,
    )
    return SuiteCaseV1(case, projection, binding, schema, metadata)


def build_messages(projection, instruction_bytes: bytes) -> tuple[dict, dict]:
    """Preserve the original system prefix and user bytes in native two-role order."""
    messages = projection.messages
    if (len(messages) != 2 or [item.role for item in messages] != ["system", "user"]
            or type(instruction_bytes) is not bytes):
        raise probe.ProbeError("BINDING_INVALID")
    try:
        instruction = instruction_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise probe.ProbeError("BINDING_INVALID") from None
    if (not instruction or any(type(item.content) is not str or not item.content for item in messages)
            or any(instruction in item.content for item in messages)):
        raise probe.ProbeError("BINDING_INVALID")
    return ({"role": "system", "content": messages[0].content + "\n\n" + instruction},
            {"role": "user", "content": messages[1].content})


def candidate_body(suite_case: SuiteCaseV1, model_name: str) -> dict:
    if not isinstance(suite_case, SuiteCaseV1) or type(model_name) is not str or not model_name:
        raise ValueError("invalid candidate body input")
    messages = list(build_messages(suite_case.projection, MINIMAL_V1_INSTRUCTION.encode("utf-8")))
    return {
        "model": model_name,
        "messages": messages,
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "phase6_minimal_output_v0", "strict": True, "schema": probe.plain(suite_case.schema)}},
        "temperature": 0.2, "top_k": 40, "top_p": 0.95, "min_p": 0.05,
        "repeat_penalty": 1.0, "presence_penalty": 0.0, "frequency_penalty": 0.0,
        "seed": 4242026, "max_tokens": 512, "cache_prompt": False,
    }


def candidate_wire(body: Mapping) -> bytes:
    return probe.canonical_bytes(body)


def shadow_without_grounding(body: Mapping) -> dict:
    """Return a tokenize-only prompt variant; this function performs no dispatch."""
    result = json.loads(candidate_wire(body))
    schema = result["response_format"]["json_schema"]["schema"]
    schema["properties"].pop("grounding")
    schema["required"].remove("grounding")
    suffix = "\n\n" + MINIMAL_V1_INSTRUCTION
    messages = result["messages"]
    if (len(messages) != 2 or [item["role"] for item in messages] != ["system", "user"]
            or not messages[0]["content"].endswith(suffix)):
        raise probe.ProbeError("BINDING_INVALID")
    prefix = messages[0]["content"][:-len(suffix)]
    messages[0]["content"] = prefix + "\n\n" + MINIMAL_V1_INSTRUCTION.replace(GROUNDING_INSTRUCTION, "")
    return result


def candidate_body_without_grounding(suite_case: SuiteCaseV1, model_name: str) -> dict:
    """T500 candidate: promote the exact frozen removal, changing no other field."""
    return shadow_without_grounding(candidate_body(suite_case, model_name))
