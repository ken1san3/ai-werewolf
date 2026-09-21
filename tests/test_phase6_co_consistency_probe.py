"""Public synthetic, no-provider checks of the CO correlation adapter."""
import copy
from dataclasses import fields, replace
import itertools
import json

import pytest
from jsonschema import Draft202012Validator

from ai_client.brain import BrainActionOption
from ai_client.discussion.context import canonical_json_bytes, canonical_sha256
from ai_client.llm.decision import DecisionValidationError, parse_llm_output
from ai_client.llm.prompt import project_brain_input
from ai_client.llm.types import LLMBrainConfig
from ai_client.network import CoDeclareAction
from scripts import phase6_co_consistency_probe as probe
from scripts.phase6_context_probe import provider_body, wire_bytes
from tests.test_phase6_intent_choice_probe import KINDS, projection
from tests.test_phase6_quality_grounding import CONFIG, payload, request_with
from tests.test_phase6_two_call_probe import legacy
from tests import test_phase6_semantic_output as semantic


def co_projection(role_counts=(1,)):
    options = tuple(BrainActionOption(f'action:{i}', CoDeclareAction(
        connection_generation=1, action_generation=1, phase='opaque-phase', day=1,
        type='co_declare', claimed_role_ids=tuple(f'r{j}' for j in range(count))))
        for i, count in enumerate(role_counts))
    return project_brain_input(request_with(trigger='CO_OPPORTUNITY', options=options), config=CONFIG)


def choice(kind='NONE', count=0):
    return {'speech_act_kind': kind, 'authoritative_fact_ids': [f'f{i:03}' for i in range(count)]}


def schema(p, kind='NONE'):
    old = probe.gb1.gc2.candidate_schema(p, {'speech_act_kind': kind})
    return probe.co_consistent_schema(old, p)


def co_value(p, decision=None, judgment='SILENCE', selected=None):
    value = payload(p, kind='none')
    if decision is not None:
        option, role = decision
        value['decision'] = {'kind': 'co_declare', 'option_id': option,
                             'claimed_role_id': role, 'comment': 'A complete statement.'}
        value['discussion'].update(decision_kind='co_declare', option_id=option)
    value['discussion']['co_judgment'] = {'decision': judgment,
        'selected_option_id': None if selected is None else selected[0],
        'claimed_role_id': None if selected is None else selected[1]}
    return value


def legacy_accepts(p, value):
    try:
        parse_llm_output(json.dumps(value), projection=p)
    except DecisionValidationError:
        return False
    return True


def test_co_consistency_matrix_closes_only_tuple_cross_product():
    p = co_projection((2, 1))
    validator = Draft202012Validator(schema(p))
    tuples = [('action:0', 'r0'), ('action:0', 'r1'), ('action:1', 'r0')]
    count = accepted = 0
    for decision, judgment, selected in itertools.product(
            [None, *tuples], ['SILENCE', 'DEFER', 'DECLARE'], [None, *tuples]):
        value = co_value(p, decision, judgment, selected)
        expected = ((decision is None and selected is None and judgment in ('SILENCE', 'DEFER'))
                    or (decision is not None and judgment == 'DECLARE' and selected == decision))
        assert validator.is_valid(value) == expected == legacy_accepts(p, value)
        raw = json.dumps(value)
        if expected:
            assert probe.validate_final(raw, choice(), p) == value
        else:
            with pytest.raises(ValueError, match='CO_CONSISTENCY_SCHEMA_INVALID'):
                probe.validate_final(raw, choice(), p)
        count += 1
        accepted += expected
    assert (count, accepted) == (48, 5)


@pytest.mark.parametrize('role_counts', [(), (1,), (2, 3), (64,)])
def test_canonical_zero_multiple_and_64_tuples_preserve_all_legal_choices(role_counts):
    p = co_projection(role_counts)
    result = schema(p)
    assert len(result['oneOf']) == sum(role_counts) + 2
    values = [co_value(p, judgment=kind) for kind in ('SILENCE', 'DEFER')]
    values += [co_value(p, (f'action:{i}', f'r{j}'), 'DECLARE', (f'action:{i}', f'r{j}'))
               for i, count in enumerate(role_counts) for j in range(count)]
    for value in values:
        assert probe.validate_final(json.dumps(value), choice(), p) == value
        assert legacy_accepts(p, value)


def test_65_tuples_fail_before_conversion_without_truncation():
    p = co_projection((65,))
    with pytest.raises(ValueError, match='CO_TUPLE_LIMIT'):
        schema(p)


@pytest.mark.parametrize('mutation', ['unknown', 'remote', 'open', 'required', 'duplicate_branch',
    'missing_branch', 'missing', 'extra', 'null', 'unknown_kind', 'unknown_co', 'wrong_role',
    'duplicate_role', 'duplicate_option'])
def test_changed_schema_shape_rejected(mutation):
    p = co_projection((2,))
    old = probe.gb1.gc2.candidate_schema(p, {'speech_act_kind': 'NONE'})
    if mutation == 'unknown': old['x-other'] = True
    elif mutation == 'remote': old['$defs']['player_id'] = {'$ref': 'https://example.invalid/schema'}
    elif mutation == 'open': old['properties']['decision']['oneOf'][0]['additionalProperties'] = True
    elif mutation == 'required': old['required'].append('decision')
    elif mutation == 'duplicate_branch': old['properties']['decision']['oneOf'].append(copy.deepcopy(old['properties']['decision']['oneOf'][1]))
    elif mutation == 'missing_branch': old['properties']['discussion']['oneOf'].pop()
    elif mutation == 'missing': del old['properties']['discussion']
    elif mutation == 'extra': old['properties']['decision']['oneOf'][0]['properties']['extra'] = {'const': None}
    elif mutation == 'null': old['properties']['decision'] = None
    elif mutation == 'unknown_kind': old['properties']['decision']['oneOf'][1]['properties']['kind'] = {'const': 'unknown'}
    elif mutation == 'unknown_co': old['$defs']['co']['oneOf'][0]['properties']['decision'] = {'const': 'unknown'}
    elif mutation == 'duplicate_role': old['properties']['decision']['oneOf'][1]['properties']['claimed_role_id']['enum'].append('r0')
    elif mutation == 'duplicate_option': old['$defs']['co']['oneOf'].append(copy.deepcopy(old['$defs']['co']['oneOf'][1]))
    else: old['properties']['decision']['oneOf'][1]['properties']['claimed_role_id'] = {'enum': ['unknown']}
    with pytest.raises(ValueError, match='SCHEMA_SHAPE_CHANGED'):
        probe.co_consistent_schema(old, p)


@pytest.mark.parametrize('kind', ['none', 'chat', 'vote', 'ability', 'co_declare'])
@pytest.mark.parametrize('basis_count', [0, 1, 2])
def test_all_actions_and_basis_counts_through_strict_adapter(kind, basis_count):
    p = projection('chat' if kind == 'none' else kind)
    value = legacy(p, kind)
    assert probe.validate_final(json.dumps(value), choice(count=basis_count), p) == value


@pytest.mark.parametrize('kind,peer', [('chat', False), ('chat', True), ('vote', False), ('ability', False)])
@pytest.mark.parametrize('act', KINDS)
def test_non_co_schema_and_body_exact_invariant(kind, peer, act):
    p = projection(kind, peer=peer)
    original = provider_body(p)
    frozen = wire_bytes(original)
    selected = choice(act)
    try:
        old = probe.gb1.output_body(original, selected, p)
    except probe.gb1.gc2.NoLegalGrounding:
        with pytest.raises(probe.gb1.gc2.NoLegalGrounding):
            probe.output_body(original, selected, p)
        return
    result = probe.output_body(original, selected, p)
    assert result == old and wire_bytes(result) == wire_bytes(old)
    assert wire_bytes(original) == frozen
    copied = probe.co_consistent_schema(old['response_format']['json_schema']['schema'], p)
    assert canonical_json_bytes(copied) == canonical_json_bytes(old['response_format']['json_schema']['schema'])
    copied['type'] = 'null'
    assert old['response_format']['json_schema']['schema']['type'] == 'object'


@pytest.mark.parametrize('case', ['claim', 'question', 'answer', 'rebuttal', 'opinion_change',
    'relation_hypothesis', 'assessment_update', 'claim_update', 'relation_update',
    'strategy_update', 'reaction_score', 'co_judgment', 'pre_vote_reassessment'])
def test_authority_positive_cases_actually_pass_new_adapter(case, monkeypatch):
    original = semantic._parse
    count = []
    def through_adapter(request, value):
        p = project_brain_input(request, config=LLMBrainConfig())
        selected = choice(value['discussion']['speech_act']['kind'])
        assert probe.validate_final(json.dumps(value), selected, p) == value
        count.append(1)
        return original(request, value)
    monkeypatch.setattr(semantic, '_parse', through_adapter)
    semantic.test_p6b_semantic_pass_authority_closed_positive_matrix(case)
    assert count


@pytest.mark.parametrize('case', ['claim', 'question', 'answer', 'rebuttal',
                                 'opinion_change', 'relation_hypothesis'])
def test_co_keeps_each_speech_act_and_basis_legal(case, monkeypatch):
    original = semantic._parse
    checked = []
    def through_co(request, value):
        # Preserve the canonical evidence/prior state of each positive case.
        # Only the public test's offered action and trigger become CO.
        reference = semantic._request('co_declare')
        trigger = replace(request.discussion.trigger, kind='CO_OPPORTUNITY',
                          owner='reaction_chat', source=None, mapping_order=0)
        material = {f.name: getattr(request.discussion, f.name) for f in fields(request.discussion)
                    if f.name != 'capture_id'}
        material['trigger'] = trigger
        capture = replace(request.discussion, trigger=trigger, capture_id=canonical_sha256(material))
        changed = replace(request, discussion=capture,
            action_context=replace(request.action_context, options=reference.action_context.options))
        p = project_brain_input(changed, config=LLMBrainConfig())
        v = copy.deepcopy(value)
        v['decision'] = {'kind': 'none'}
        v['discussion'].update(decision_kind='none', option_id=None, reaction=None,
            pre_vote_reassessment=None, co_judgment={'decision': 'SILENCE',
                'selected_option_id': None, 'claimed_role_id': None})
        for count in (0, 1, 2):
            assert probe.validate_final(json.dumps(v), choice(v['discussion']['speech_act']['kind'], count), p) == v
        checked.append(1)
        return original(request, value)
    monkeypatch.setattr(semantic, '_parse', through_co)
    semantic.test_p6b_semantic_pass_authority_closed_positive_matrix(case)
    assert checked


def test_legacy_only_authority_negative_remains_rejected(monkeypatch):
    original = semantic.parse_llm_output
    counts = {'reject': 0, 'accept': 0, 'no_legal': 0}
    def through_adapter(raw, *, projection):
        selected = choice(json.loads(raw)['discussion']['speech_act']['kind'])
        try:
            result = original(raw, projection=projection)
        except DecisionValidationError:
            with pytest.raises((ValueError, DecisionValidationError)):
                probe.validate_final(raw, selected, projection)
            counts['reject'] += 1
            raise
        try:
            assert probe.validate_final(raw, selected, projection) == json.loads(raw)
            counts['accept'] += 1
        except probe.gb1.gc2.NoLegalGrounding:
            counts['no_legal'] += 1
        return result
    monkeypatch.setattr(semantic, 'parse_llm_output', through_adapter)
    semantic.test_p6b_visibility_matrix_rejects_upgrade_downgrade_and_public_inference_misuse()
    semantic.test_p6b_peer_actor_addressee_and_claim_speaker_binding_is_mechanical()
    semantic.test_p6b_identity_nullability_option_handle_family_and_base_revision_mutations_fail()
    semantic.test_p6b_opinion_change_requires_exact_prior_and_new_evidence()
    semantic.test_p6b_trigger_specific_reaction_co_and_pre_vote_semantics_are_exact()
    assert counts['reject'] >= 10 and counts['accept'] + counts['no_legal'] > 0


@pytest.mark.parametrize('raw', ['{}', '[]', 'null', '{"x":NaN}', '{"x":Infinity}',
    '{"x":1e999}', '{"x":1,"x":2}'])
def test_strict_json_rejection_no_repair(raw):
    with pytest.raises((ValueError, DecisionValidationError)):
        probe.validate_final(raw, choice(), co_projection())


@pytest.mark.parametrize('mutation', ['option', 'role', 'discussion_kind', 'discussion_option',
    'null', 'missing', 'extra', 'empty_text', 'long_text'])
def test_malformed_co_never_repaired(mutation):
    p = co_projection()
    value = co_value(p, ('action:0', 'r0'), 'DECLARE', ('action:0', 'r0'))
    if mutation in ('option', 'role'):
        value['decision']['option_id' if mutation == 'option' else 'claimed_role_id'] = 'unknown'
    elif mutation == 'discussion_kind': value['discussion']['decision_kind'] = 'none'
    elif mutation == 'discussion_option': value['discussion']['option_id'] = None
    elif mutation == 'null': value['discussion']['co_judgment'] = None
    elif mutation == 'missing': del value['decision']['comment']
    elif mutation == 'extra': value['decision']['extra'] = None
    elif mutation == 'empty_text': value['decision']['comment'] = ''
    else: value['decision']['comment'] = 'x' * 201
    frozen = copy.deepcopy(value)
    assert not legacy_accepts(p, value)
    with pytest.raises((ValueError, DecisionValidationError)):
        probe.validate_final(json.dumps(value), choice(), p)
    assert value == frozen


def test_raw_bytes_unchanged_at_existing_legacy_boundary(monkeypatch):
    p = co_projection()
    value = co_value(p)
    raw = json.dumps(value, indent=3)
    seen = []
    monkeypatch.setattr(probe.gb1.gc2, 'parse_llm_output', lambda text, *, projection: seen.append(text))
    assert probe.validate_final(raw, choice(), p) == value
    assert seen == [raw]


def test_co_schema_preserves_defs_and_other_body_fields():
    p = co_projection((2, 1))
    baseline = provider_body(p)
    old = probe.gb1.output_body(baseline, choice(), p)
    new = probe.output_body(baseline, choice(), p)
    expected = copy.deepcopy(old)
    expected['response_format']['json_schema']['schema'] = new['response_format']['json_schema']['schema']
    assert new == expected
    assert new['response_format']['json_schema']['schema']['$defs'] == old['response_format']['json_schema']['schema']['$defs']
