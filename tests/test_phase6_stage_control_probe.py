import copy
import json

import pytest

from ai_client.discussion.context import canonical_json_bytes
from scripts.phase6_context_probe import provider_body, wire_bytes
from scripts import phase6_stage_control_probe as probe
from tests.test_phase6_intent_choice_probe import KINDS, projection


def baseline():
    return provider_body(projection())


@pytest.mark.parametrize("kind", KINDS)
def test_two_role_body_preserves_prefix_and_canonical_user(kind):
    p = projection()
    original = provider_body(p)
    frozen = wire_bytes(original)
    first = probe.choice_body(original)
    assert wire_bytes(original) == frozen
    assert [item["role"] for item in first["messages"]] == ["system", "user"]
    assert first["messages"][0]["content"] == (
        original["messages"][0]["content"] + "\n\n" + probe.CHOICE_INSTRUCTION
    )
    assert first["messages"][1] == original["messages"][1]
    try:
        second = probe.output_body(original, {"speech_act_kind": kind}, p)
    except probe.gc2.NoLegalGrounding:
        return
    canonical = canonical_json_bytes({"speech_act_kind": kind}).decode()
    assert [item["role"] for item in second["messages"]] == ["system", "user"]
    assert second["messages"][0]["content"] == (
        original["messages"][0]["content"] + "\n\n"
        + probe.OUTPUT_INSTRUCTION + "\n" + canonical
    )
    assert second["messages"][1] == original["messages"][1]


@pytest.mark.parametrize("raw", ["{}", '{"speech_act_kind":"OTHER"}',
    '{"speech_act_kind":"NONE","extra":1}',
    '{"speech_act_kind":"NONE","speech_act_kind":"CLAIM"}',
    '{"speech_act_kind":NaN}', '{"x":1e999}', 'text'])
def test_strict_choice_rejected_before_system_construction(raw):
    with pytest.raises(ValueError):
        value = probe.strict_json(raw)
        probe.output_body(baseline(), value, projection())


def test_native_rendered_requires_instruction_and_user_exactly_once():
    body = probe.choice_body(baseline())
    rendered = body["messages"][0]["content"] + body["messages"][1]["content"]
    probe.validate_native_rendered(body, "choice", rendered)
    for changed in (rendered + probe.CHOICE_INSTRUCTION,
                    rendered + body["messages"][1]["content"], "missing"):
        with pytest.raises(ValueError, match="NATIVE_RENDERED_INVALID"):
            probe.validate_native_rendered(body, "choice", changed)


def test_native_output_rejects_noncanonical_or_unknown_choice():
    p = projection()
    body = probe.output_body(baseline(), {"speech_act_kind": "NONE"}, p)
    rendered = body["messages"][0]["content"] + body["messages"][1]["content"]
    probe.validate_native_rendered(body, "output", rendered)
    changed = copy.deepcopy(body)
    changed["messages"][0]["content"] = changed["messages"][0]["content"].replace(
        '{"speech_act_kind":"NONE"}', '{"speech_act_kind": "NONE"}'
    )
    with pytest.raises(ValueError, match="NATIVE_BODY_INVALID"):
        probe.validate_native_rendered(changed, "output", rendered)


def test_native_rendered_requires_full_system_prefix_and_locked_choice():
    p = projection()
    original = baseline()
    body = probe.output_body(original, {"speech_act_kind": "NONE"}, p)
    user = body["messages"][1]["content"]
    for rendered in (
        probe.OUTPUT_INSTRUCTION + '\n{"speech_act_kind":"NONE"}' + user,
        body["messages"][0]["content"].replace(
            '{"speech_act_kind":"NONE"}', ""
        ) + user,
    ):
        with pytest.raises(ValueError, match="NATIVE_RENDERED_INVALID"):
            probe.validate_native_rendered(body, "output", rendered)


def test_instructions_are_existing_exact_bytes():
    from scripts import phase6_intent_choice_probe as ic2
    assert probe.CHOICE_INSTRUCTION == ic2.CHOICE_INSTRUCTION
    assert probe.OUTPUT_INSTRUCTION == ic2.OUTPUT_INSTRUCTION
