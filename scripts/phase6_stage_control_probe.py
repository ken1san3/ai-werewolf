"""Pure stage-control adapters for the Phase 6 SC2 probe."""
from __future__ import annotations

from copy import deepcopy
import json

from ai_client.discussion.context import canonical_json_bytes
from ai_client.llm.types import PromptProjection
from scripts import phase6_grounding_closed_probe as gc2
from scripts import phase6_intent_choice_probe as ic2


CHOICE_TOKENS = ic2.CHOICE_TOKENS
OUTPUT_TOKENS = ic2.OUTPUT_TOKENS
CHOICE_INSTRUCTION = ic2.CHOICE_INSTRUCTION
OUTPUT_INSTRUCTION = ic2.OUTPUT_INSTRUCTION


strict_json = ic2.strict_json
validate_choice = ic2.validate_choice
validate_final = gc2.validate_final


def _baseline(value):
    body = deepcopy(value)
    if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
        raise ValueError("BODY_SHAPE_CHANGED")
    messages = body["messages"]
    if (len(messages) != 2 or any(not isinstance(item, dict) for item in messages)
            or set(messages[0]) != {"role", "content"}
            or set(messages[1]) != {"role", "content"}
            or messages[0]["role"] != "system" or messages[1]["role"] != "user"
            or not isinstance(messages[0]["content"], str)
            or not isinstance(messages[1]["content"], str)):
        raise ValueError("BODY_SHAPE_CHANGED")
    try:
        schema = body["response_format"]["json_schema"]["schema"]
    except (KeyError, TypeError):
        raise ValueError("BODY_SHAPE_CHANGED") from None
    if not isinstance(schema, dict):
        raise ValueError("BODY_SHAPE_CHANGED")
    return body


def choice_body(baseline):
    body = _baseline(baseline)
    schema = ic2.choice_schema(body["response_format"]["json_schema"]["schema"])
    body["messages"][0]["content"] += "\n\n" + CHOICE_INSTRUCTION
    body["response_format"]["json_schema"]["schema"] = schema
    body["max_tokens"] = CHOICE_TOKENS
    return body


def output_body(baseline, choice, projection):
    if not isinstance(projection, PromptProjection):
        raise TypeError("projection must be PromptProjection")
    choice_value = validate_choice(
        json.dumps(choice, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
        projection,
    )
    body = _baseline(baseline)
    canonical = canonical_json_bytes(choice_value).decode("utf-8")
    body["messages"][0]["content"] += (
        "\n\n" + OUTPUT_INSTRUCTION + "\n" + canonical
    )
    body["response_format"]["json_schema"]["schema"] = gc2.candidate_schema(
        projection, choice_value
    )
    body["max_tokens"] = OUTPUT_TOKENS
    return body


def validate_native_rendered(body, stage_name, rendered) -> None:
    if stage_name not in {"choice", "output"}:
        raise ValueError("NATIVE_STAGE_INVALID")
    if not isinstance(rendered, str):
        raise ValueError("NATIVE_RENDERED_INVALID")
    value = _baseline(body)
    messages = value["messages"]
    system = messages[0]["content"]
    user = messages[1]["content"]
    if stage_name == "choice":
        instruction = CHOICE_INSTRUCTION
        suffix = "\n\n" + instruction
        if value.get("max_tokens") != CHOICE_TOKENS:
            raise ValueError("NATIVE_BODY_INVALID")
    else:
        marker = "\n\n" + OUTPUT_INSTRUCTION + "\n"
        if marker not in system or value.get("max_tokens") != OUTPUT_TOKENS:
            raise ValueError("NATIVE_BODY_INVALID")
        raw_choice = system.rsplit(marker, 1)[-1]
        choice = strict_json(raw_choice)
        if set(choice) != {"speech_act_kind"} or choice["speech_act_kind"] not in ic2._KINDS:
            raise ValueError("NATIVE_BODY_INVALID")
        canonical = canonical_json_bytes(choice).decode("utf-8")
        instruction = OUTPUT_INSTRUCTION
        suffix = marker + canonical
    if not system.endswith(suffix) or system.count(instruction) != 1:
        raise ValueError("NATIVE_BODY_INVALID")
    rendered_bytes = rendered.encode("utf-8", errors="strict")
    if (rendered_bytes.count(system.encode("utf-8")) != 1
            or rendered_bytes.count(instruction.encode("utf-8")) != 1
            or rendered_bytes.count(user.encode("utf-8")) != 1):
        raise ValueError("NATIVE_RENDERED_INVALID")
