"""T500 public synthetic contracts; no historical output or provider access."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace

import pytest

from scripts import phase6_minimal_output_probe as p
from tests.fixtures.phase6_minimal_output_cases import positive_cases, ref
from tests.test_phase6_minimal_output_applicability import forbid_runtime_actions  # noqa: F401


def fixture(number, *, requirement=None):
    item = positive_cases()[number - 1]
    host = p.plain(item.binding.authority)
    del host['requires_private_update']
    binding = p.bind_suite(host, b'{"public":"synthetic"}',
                           item.binding.canonical_private_view_bytes, update_requirement=requirement)
    value = deepcopy(item.candidate)
    del value['grounding']
    return value, binding


def validate(value, binding):
    return p.validate_without_grounding(p.canonical_bytes(value), binding)


def error(raw, binding, code):
    with pytest.raises(p.ProbeError) as caught:
        p.validate_without_grounding(raw, binding)
    assert caught.value.code == code


@pytest.mark.parametrize('number', range(1, 14))
def test_all_branches_preserve_acceptance_bytes_private_state_and_unknown(number):
    value, binding = fixture(number)
    raw = p.canonical_bytes(value)
    before = binding.private_bytes
    original = positive_cases()[number - 1].candidate
    legacy = p.validate_suite(p.canonical_bytes(original), binding)
    result = p.validate_without_grounding(raw, binding)
    assert replace(result.probe_result, raw_sha256=legacy.raw_sha256) == legacy
    assert result.probe_result.applicability == 'APPLICABILITY_UNRESOLVED'
    assert result.probe_result.raw_sha256 == p.sha256(raw)
    assert result.probe_result.private_before_sha256 == result.probe_result.private_after_sha256
    assert binding.private_bytes == before
    assert p.canonical_bytes(value) == raw
    expected = {(g['purpose'], p.canonical_bytes(g['ref'])) for g in original['grounding']}
    assert {(g.purpose, g.canonical_ref_value) for g in result.derived_grounding} == expected


@pytest.mark.parametrize('number', range(1, 14))
def test_schema_delta_exactly_two_json_pointers(number):
    _, binding = fixture(number)
    expected = p.output_schema_suite(binding.authority_without_update)
    del expected['properties']['grounding']
    expected['required'].remove('grounding')
    assert p.output_schema_without_grounding(binding.authority_without_update) == expected


@pytest.mark.parametrize('number', [4, 5])
def test_duplicate_refs_share_canonical_value_not_json_object_and_keep_distinct_purpose(number):
    value, binding = fixture(number)
    value['speech_act']['evidence'] = [ref(1)]
    assert value['speech_act']['evidence'][0] is not value['speech_act']['in_reply_to']
    result = validate(value, binding)
    assert [(g.purpose, g.ref_key) for g in result.derived_grounding] == [
        ('UTTERANCE', ('chat', 1, 'PUBLIC')), ('REACTION', ('chat', 1, 'PUBLIC'))]
    assert all(g.canonical_ref_value == p.canonical_bytes(ref(1)) for g in result.derived_grounding)
    with pytest.raises(FrozenInstanceError):
        result.derived_grounding[0].purpose = 'PRE_VOTE'


def test_derive_runs_once_and_same_view_reaches_binding_validation(monkeypatch):
    value, binding = fixture(4)
    derive, bindings = p.derive_grounding_view, p._bindings
    produced, consumed = [], []
    def observe_derive(value):
        view = derive(value)
        produced.append(view)
        return view
    def observe_binding(*args, **kwargs):
        consumed.append(kwargs['derived_grounding'])
        return bindings(*args, **kwargs)
    monkeypatch.setattr(p, 'derive_grounding_view', observe_derive)
    monkeypatch.setattr(p, '_bindings', observe_binding)
    result = validate(value, binding)
    assert len(produced) == len(consumed) == 1
    assert result.derived_grounding is produced[0] is consumed[0]


@pytest.mark.parametrize('number', range(1, 8))
@pytest.mark.parametrize('change', ['missing', 'extra', 'null', 'unknown'])
def test_speech_shapes_remain_closed(number, change):
    value, binding = fixture(number)
    if change == 'missing': del value['speech_act']['kind']
    elif change == 'extra': value['speech_act']['unexpected'] = 1
    elif change == 'null': value['speech_act'] = None
    else: value['speech_act']['kind'] = 'INVENTED'
    error(p.canonical_bytes(value), binding, 'SHAPE_INVALID')


@pytest.mark.parametrize('raw', [b'{', b'{}{}', b'{"x":NaN}', b'{"x":Infinity}',
    b'{"x":1e999}', b'{"x":1,"x":2}', b'"\\ud800"', b'\xff'])
def test_strict_json(raw):
    error(raw, fixture(1)[1], 'JSON_INVALID')


@pytest.mark.parametrize('field', ['grounding', 'unknown'])
def test_generated_mirror_is_rejected_not_stripped(field):
    value, binding = fixture(1)
    value[field] = []
    error(p.canonical_bytes(value), binding, 'SHAPE_INVALID')


@pytest.mark.parametrize('key', sorted(p.FORBIDDEN))
def test_no_subjective_update_can_be_added(key):
    value, binding = fixture(1)
    value['speech_act'][key] = []
    error(p.canonical_bytes(value), binding, 'PRIVATE_UPDATE_FORBIDDEN')


@pytest.mark.parametrize('change', ['projected-only', 'captured-only', 'actor', 'channel',
    'visibility', 'self', 'multiple', 'missing-reference'])
def test_reference_authority_visibility_and_actor_still_fail_closed(change):
    value, binding = fixture(4)
    host = p.plain(binding.authority_without_update)
    if change == 'projected-only': host['captured_evidence'].pop(0)
    elif change == 'captured-only': host['projected_evidence'].pop(0)
    elif change == 'actor': host['captured_evidence'][0]['actor_player_ids'] = ['p-c']
    elif change == 'channel': host['captured_evidence'][0]['channel_id'] = 'other'
    elif change == 'visibility': host['captured_evidence'][0]['ref']['visibility'] = 'AUTHORIZED_PRIVATE'
    elif change in ('self', 'multiple'):
        for catalog in ('projected_evidence', 'captured_evidence'):
            host[catalog][0]['actor_player_ids'] = ['p-a'] if change == 'self' else ['p-b', 'p-c']
    else: value['speech_act']['in_reply_to'] = ref(999)
    if change == 'multiple':
        with pytest.raises(p.ProbeError, match='BINDING_INVALID'):
            p.bind_suite(host, binding.canonical_user_bytes, binding.private_bytes, update_requirement=None)
        return
    bound = p.bind_suite(host, binding.canonical_user_bytes, binding.private_bytes, update_requirement=None)
    error(p.canonical_bytes(value), bound, 'BINDING_INVALID')


@pytest.mark.parametrize('number,path,bad,code', [
    (4, ('speech_act', 'addressee_player_id'), 'p-c', 'BINDING_INVALID'),
    (4, ('trigger_detail', 'trigger'), ref(2), 'BINDING_INVALID'),
    (4, ('speech_act', 'in_reply_to'), ref(3, 'ability_result', 'AUTHORIZED_PRIVATE'), 'BINDING_INVALID'),
    (6, ('speech_act', 'prior'), 10, 'VALUE_NOT_OFFERED'),
    (6, ('speech_act', 'causes'), [ref(1)], 'VALUE_NOT_OFFERED'),
    (2, ('decision', 'option_id'), 'invented', 'SHAPE_INVALID'),
    (9, ('decision', 'claimed_role_id'), 'invented-role', 'SHAPE_INVALID'),
    (9, ('trigger_detail', 'claimed_role_id'), 'claim-b', 'VALUE_NOT_OFFERED'),
    (10, ('decision', 'target_player_id'), 'p-c', 'SHAPE_INVALID'),
    (10, ('trigger_detail', 'ranked_target_player_ids'), ['p-c'], 'SHAPE_INVALID'),
    (12, ('decision', 'target_player_ids'), ['p-c'], 'SHAPE_INVALID'),
    (12, ('decision', 'target_player_ids'), ['p-b', 'p-b'], 'SHAPE_INVALID'),
    (1, ('utterance',), 'I was executed.', 'TEXT_INVALID'),
    (2, ('utterance',), 'x' * 200, 'TEXT_INVALID'),
    (2, ('utterance',), 'x' * 201 + '.', 'TEXT_INVALID'),
])
def test_nonmirror_errors_are_preserved(number, path, bad, code):
    value, binding = fixture(number)
    node = value
    for key in path[:-1]: node = node[key]
    node[path[-1]] = bad
    error(p.canonical_bytes(value), binding, code)
    old = deepcopy(value)
    old['grounding'] = p.expected_grounding(old)
    with pytest.raises(p.ProbeError) as caught:
        p.validate_suite(p.canonical_bytes(old), binding)
    assert caught.value.code == code


@pytest.mark.parametrize('change', ['authority-hash', 'user-hash', 'private-hash', 'private-bytes', 'version'])
def test_binding_tamper(change):
    value, binding = fixture(4)
    fields = {'authority-hash': {'authority_sha256': '0'*64},
              'user-hash': {'canonical_user_sha256': '0'*64},
              'private-hash': {'private_sha256': '0'*64},
              'private-bytes': {'private_bytes': b'{}'}, 'version': {'binding_version': 'unknown'}}
    error(p.canonical_bytes(value), replace(binding, **fields[change]), 'BINDING_INVALID')


def test_missing_mirror_removal_does_not_create_refs_or_semantic_truth():
    value, binding = fixture(2)
    value['speech_act']['evidence'] = []
    value['utterance'] = 'I inspected p-c and got a result.'
    result = validate(value, binding)
    assert result.derived_grounding == ()
    assert result.probe_result.semantic_status == 'NOT_EVALUATED'
    assert 'grounding' not in value


def test_raw_cap_is_not_applied_to_virtual_old_mirror():
    value, binding = fixture(4)
    raw = p.canonical_bytes(value)
    host = p.plain(binding.authority_without_update)
    host['max_candidate_utf8_bytes'] = len(raw)
    bound = p.bind_suite(host, binding.canonical_user_bytes, binding.private_bytes, update_requirement=None)
    assert p.validate_without_grounding(raw, bound).probe_result.raw_sha256 == p.sha256(raw)
    error(raw + b' ', bound, 'TEXT_INVALID')


def test_private_ability_ref_is_checked_but_never_interpreted_as_public_truth():
    value, binding = fixture(2)
    value['speech_act']['evidence'] = [ref(3, 'ability_result', 'AUTHORIZED_PRIVATE')]
    result = validate(value, binding)
    assert result.derived_grounding[0].ref_key == ('ability_result', 3, 'AUTHORIZED_PRIVATE')
    assert result.probe_result.semantic_status == 'NOT_EVALUATED'
    value['speech_act']['evidence'][0]['visibility'] = 'PUBLIC'
    error(p.canonical_bytes(value), binding, 'SHAPE_INVALID')


@pytest.mark.parametrize('number', [4, 5])
def test_within_array_duplicate_remains_invalid_before_derivation(number, monkeypatch):
    value, binding = fixture(number)
    value['speech_act']['evidence'] = [ref(1), ref(1)]
    def forbidden(*args):
        raise AssertionError('invalid shape must not be derived')
    monkeypatch.setattr(p, 'derive_grounding_view', forbidden)
    error(p.canonical_bytes(value), binding, 'SHAPE_INVALID')
    old = deepcopy(value)
    old['grounding'] = p.expected_grounding(old)
    with pytest.raises(p.ProbeError, match='SHAPE_INVALID'):
        p.validate_suite(p.canonical_bytes(old), binding)


@pytest.mark.parametrize('number,container,key', [(6, 'speech_act', 'causes'),
                                                (10, 'trigger_detail', 'evidence')])
def test_other_ref_arrays_keep_duplicate_rejection(number, container, key):
    value, binding = fixture(number)
    value[container][key] = [ref(2), ref(2)]
    error(p.canonical_bytes(value), binding, 'SHAPE_INVALID')


@pytest.mark.parametrize('requirement,status', [(None, 'APPLICABILITY_UNRESOLVED'),
    (True, 'APPLICABILITY_UNRESOLVED'), (False, 'APPLICABILITY_COVERED')])
def test_co_defer_and_private_update_tristate_are_preserved(requirement, status):
    value, binding = fixture(8, requirement=requirement)
    value['trigger_detail']['decision'] = 'DEFER'
    result = validate(value, binding)
    assert result.derived_grounding == ()
    assert result.probe_result.applicability == status
