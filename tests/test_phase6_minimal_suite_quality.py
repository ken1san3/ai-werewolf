from copy import deepcopy

import pytest

from scripts import phase6_minimal_suite_quality as q
from scripts.phase6_minimal_output_probe import CASE_IDS, canonical_bytes, sha256


def fixture():
    rows, notes = [], []
    for case_id in CASE_IDS:
        rows.append(dict(case_id=case_id, input_sha256='1'*64, raw_sha256='2'*64,
                         mechanical_status='PASS', peer_long_exact_copy=False,
                         speech_act='NONE' if case_id in q.NONE_IDS else 'ANSWER',
                         applicability='UNRESOLVED', call_consumed=1))
        notes.append(dict(case_id=case_id, input_sha256='1'*64, raw_sha256='2'*64,
                          hard='PASS', semantic='PASS', style='PASS', reasons=[],
                          act_text_mismatch=False, question_answer=True if case_id in q.QUESTION_IDS else None,
                          legal_none=True if case_id in q.NONE_IDS else None, copy=False, grounding_supported=True))
    result = dict(experiment='minimal_output_v1', task_id='T499', rows=rows, status='COMPLETE',
                  source_unchanged=True, owned_processes_remaining=0, listener_free=True, provider_calls=32)
    annotation = dict(version='minimal-quality.v1', result_sha256='', rows=notes)
    return result, annotation


def combine(result, annotation):
    result_bytes = canonical_bytes(result)
    annotation = deepcopy(annotation)
    annotation['result_sha256'] = sha256(result_bytes)
    raw = canonical_bytes(annotation)
    baseline = q.baseline_bytes()
    return q.combine(result_bytes, raw, baseline, exact_hashes={
        'result':sha256(result_bytes), 'annotation':sha256(raw), 'baseline':sha256(baseline)})


def test_fixed_denominators_unknown_applicability_not_quality_unknown():
    result, notes = fixture()
    report = combine(result, notes)
    assert report['candidate']['applicability_unresolved'] == 32
    assert report['candidate']['unknown'] == 0
    assert report['candidate']['question_denominator'] == 18
    assert report['candidate']['none_denominator'] == 2
    assert report['decision'] == 'TEST_ONLY_CANDIDATE'
    assert report['product_adoption'] is False
    assert report['baseline']['peer_long_exact_copy'] is None


@pytest.mark.parametrize('mechanical,semantic,expected', [
    ('PASS','PASS','PASS'), ('FAIL','PASS','FAIL'), ('UNKNOWN','PASS','UNKNOWN'),
    ('PASS','FAIL','FAIL'), ('FAIL','UNKNOWN','FAIL'), ('UNKNOWN','FAIL','FAIL'),
    ('PASS','UNKNOWN','UNKNOWN'), ('UNKNOWN','UNKNOWN','UNKNOWN')])
def test_three_value_hard_and(mechanical, semantic, expected):
    result, notes = fixture()
    result['rows'][0]['mechanical_status'] = mechanical
    notes['rows'][0]['hard'] = semantic
    assert combine(result, notes)['rows'][0]['hard'] == expected


@pytest.mark.parametrize('change', ['drop','duplicate','order','raw_hash','input_hash','extra','reason','state','bool'])
def test_annotation_contract_fails_closed(change):
    result, notes = fixture()
    row = notes['rows'][0]
    if change == 'drop': notes['rows'].pop()
    if change == 'duplicate': notes['rows'][1] = deepcopy(row)
    if change == 'order': notes['rows'].reverse()
    if change == 'raw_hash': row['raw_sha256'] = '3'*64
    if change == 'input_hash': row['input_sha256'] = '3'*64
    if change == 'extra': row['private_text'] = 'not allowed'
    if change == 'reason': row['reasons'] = ['arbitrary private exception']
    if change == 'state': row['hard'] = 'OK'
    if change == 'bool': row['copy'] = 1
    with pytest.raises(ValueError): combine(result, notes)


@pytest.mark.parametrize('metric,reason,count', [
    ('fabricated', 'FABRICATED_EVIDENCE',1), ('secret','SECRET_DISCLOSURE',6),
    ('state','STATE_CONTRADICTION',4), ('ability','ABILITY_CONTRADICTION',1), ('copy','COPY',5)])
def test_individual_hard_regression_cannot_be_offset_by_semantic(metric, reason, count):
    result, notes = fixture()
    for row in notes['rows'][:count]:
        row.update(hard='FAIL', reasons=[reason], copy=reason == 'COPY')
    report = combine(result, notes)
    assert not report['gates'][metric]
    assert report['decision'] == 'REJECTED'


def test_no_output_retained_as_unknown_and_no_missing_screen_zero():
    result, notes = fixture()
    result['rows'][0].update(raw_sha256=None, mechanical_status='UNKNOWN', peer_long_exact_copy=None, call_consumed=0)
    notes['rows'][0].update(raw_sha256=None, hard='UNKNOWN', semantic='UNKNOWN', style='UNKNOWN',
                           act_text_mismatch=None, question_answer=None, copy=None, grounding_supported=None)
    report = combine(result, notes)
    assert report['candidate']['denominator'] == 32 and report['candidate']['unknown'] == 1
    assert report['candidate']['screen_executed_count'] == 31
    assert not report['gates']['peer_exact'] and not report['gates']['run_integrity']


def test_safe_projection_does_not_copy_unknown_result_fields():
    result, notes = fixture()
    result['private_path'] = 'secret-private-path'
    result['rows'][0]['raw_text'] = 'secret raw utterance'
    encoded = canonical_bytes(combine(result, notes))
    assert b'secret-private-path' not in encoded and b'secret raw utterance' not in encoded


def test_exact_hash_and_baseline_pins_are_required():
    result, notes = fixture()
    raw = canonical_bytes(result)
    notes['result_sha256'] = sha256(raw)
    annotation = canonical_bytes(notes)
    for baseline, hashes in [
        (q.baseline_bytes(), dict(result='0'*64, annotation=sha256(annotation), baseline=sha256(q.baseline_bytes()))),
        (canonical_bytes({**q.BASELINE,'hard_fail':31}), None),
    ]:
        hashes = hashes or dict(result=sha256(raw), annotation=sha256(annotation), baseline=sha256(baseline))
        with pytest.raises(ValueError): q.combine(raw, annotation, baseline, exact_hashes=hashes)


def test_legal_none_does_not_accept_non_none_structure():
    result, notes = fixture()
    next(r for r in result['rows'] if r['case_id'] in q.NONE_IDS)['speech_act'] = 'ANSWER'
    with pytest.raises(ValueError): combine(result, notes)
