import copy
import hashlib
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import phase6_choice_budget_probe as probe
from tests.test_phase6_intent_choice_probe import projection
from tests.test_phase6_quality_grounding import payload, request_with


@pytest.fixture(scope='module')
def matrix():
    return probe.fixtures()


@pytest.fixture
def scene():
    p = projection()
    case = SimpleNamespace(case_id='public-case', request=request_with())
    text = json.dumps(payload(p, text='I need public voting reasons before deciding.'))
    return case, p, text


CHOICE = json.dumps(dict(speech_act_kind='NONE', authoritative_fact_ids=[]))


def test_matrix_is_exact_cartesian_product_and_lossless(matrix):
    assert len(matrix) == len({x['raw_sha256'] for x in matrix}) == 84
    assert {(x['kind'], x['facts'], x['order'], x['layout']) for x in matrix} == {
        (kind, n, order, layout) for kind in probe.KINDS for n in range(3)
        for order in ('kind-first', 'facts-first') for layout in ('compact', 'pretty')}
    for row in matrix:
        parsed = json.loads(row['raw'])
        assert parsed == dict(speech_act_kind=row['kind'], authoritative_fact_ids=['f000', 'f063'][:row['facts']])
        assert list(parsed) == (['speech_act_kind', 'authoritative_fact_ids'] if row['order'] == 'kind-first'
                                else ['authoritative_fact_ids', 'speech_act_kind'])
        assert not row['raw'].endswith('\n')
        assert row['raw_sha256'] == hashlib.sha256(row['raw'].encode()).hexdigest()
        assert row['byte_count'] == len(row['raw'].encode())
        assert ('\n' in row['raw']) == (row['layout'] == 'pretty')
    assert probe.fixtures() == matrix


def test_actual_32_case_authority_not_extended_by_toy_cost_ids():
    assert probe.actual_legality() is True
    for _, p in probe.recovery.entries():
        ids = {x['id'] for x in probe.gb.fact_catalog(p)}
        if 'f063' not in ids:
            with pytest.raises(ValueError):
                probe.gb.validate_choice(json.dumps(dict(speech_act_kind='NONE', authoritative_fact_ids=['f063'])), p)


def test_binary_utf8_is_actual_stdin_bytes_without_windows_translation(matrix):
    row = next(x for x in matrix if x['layout'] == 'pretty')
    received = []
    def run(argv, **kwargs):
        assert '--no-bos' in argv and '--no-parse-special' in argv and argv[-2:] == ['--device', 'none']
        assert kwargs['timeout'] == 30 and type(kwargs['input']) is bytes
        assert 'text' not in kwargs and 'encoding' not in kwargs and 'shell' not in kwargs
        child = subprocess.run([sys.executable, '-c',
            'import sys,hashlib;sys.stdout.write(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'],
            input=kwargs['input'], capture_output=True, timeout=5)
        received.append(child.stdout.decode())
        return SimpleNamespace(returncode=0, stdout=b'[17, 23, 5]')
    result = probe.native_count('tokenizer', 'model', row, identity_ok=True, run=run)
    assert result['status'] == 'KNOWN' and result['tokens'] == 3
    assert received == [result['wire_sha256']] == [row['raw_sha256']]


@pytest.mark.parametrize('stdout', [b'{}', b'[]', b'[true]', b'[-1]', b'[1.0]', b'["1"]', b'NaN', b'broken', b'\xff'])
def test_malformed_native_ids_are_unknown(matrix, stdout):
    result = probe.native_count('exe', 'model', matrix[0], identity_ok=True,
        run=lambda *a, **k: SimpleNamespace(returncode=0, stdout=stdout))
    assert result['status'] == 'UNKNOWN' and result['tokens'] is None


@pytest.mark.parametrize('failure', ['exit', 'missing', 'timeout', 'identity', 'wire'])
def test_native_failures_do_not_fallback_or_dispatch_with_bad_identity(matrix, failure):
    row = copy.deepcopy(matrix[0])
    calls = []
    if failure == 'wire':
        row['raw_sha256'] = '0'*64
    def run(*args, **kwargs):
        calls.append(args)
        if failure == 'missing':
            raise FileNotFoundError()
        if failure == 'timeout':
            raise subprocess.TimeoutExpired('tokenizer', 30)
        return SimpleNamespace(returncode=2, stdout=b'[1]')
    result = probe.native_count('exe', 'model', row, identity_ok=failure != 'identity', run=run)
    assert result['status'] == 'UNKNOWN' and result['tokens'] is None
    assert len(calls) == (0 if failure in ('identity', 'wire') else 1)


def known_matrix(matrix):
    rows = [dict(x, status='KNOWN', tokens=1) for x in copy.deepcopy(matrix)]
    rows[-1]['tokens'] = 51
    next(x for x in rows if x['kind'] == 'OPINION_CHANGE' and x['facts'] == 2
         and x['order'] == 'facts-first' and x['layout'] == 'pretty')['tokens'] = 48
    return rows


@pytest.mark.parametrize('mutation', ['none', 'missing', 'duplicate', 'unknown', 'max', 'exemplar', 'boolean', 'identity', 'legality'])
def test_capacity_gate_is_exact_not_proxy_or_budget_expansion(matrix, mutation):
    rows = known_matrix(matrix)
    if mutation == 'missing': rows.pop(0)
    elif mutation == 'duplicate': rows[0] = rows[1]
    elif mutation == 'unknown': rows[0].update(status='UNKNOWN', tokens=None)
    elif mutation == 'max': rows[-1]['tokens'] = 52
    elif mutation == 'exemplar':
        next(x for x in rows if x['tokens'] == 48)['tokens'] = 47
    elif mutation == 'boolean': rows[0]['tokens'] = True
    result = probe.capacity(rows, identity_ok=mutation != 'identity', legality_ok=mutation != 'legality')
    assert (result == 'OFFLINE_CAPACITY_CANDIDATE') == (mutation == 'none')


@pytest.mark.parametrize('budget', [64, 448])
def test_context_reserve_boundary(budget):
    assert probe.context_fits(8192-budget-1, budget)
    assert not probe.context_fits(8192-budget, budget)
    for count in (-1, True, None, 3.5):
        assert not probe.context_fits(count, budget)
    assert not probe.context_fits(1, 512)
    assert probe.CHOICE+probe.OUTPUT == 512
    assert probe.CHOICE+probe.K*probe.OUTPUT == 1408


@pytest.mark.parametrize('raw,finish,status', [
    (CHOICE, 'length', 'CHOICE_LENGTH'), ('{', 'length', 'CHOICE_LENGTH'),
    ('', 'stop', 'CHOICE_EMPTY'), (None, None, 'CHOICE_UNKNOWN'), (CHOICE, None, 'CHOICE_UNKNOWN'),
    ('{', 'stop', 'CHOICE_JSON_INVALID'), ('{}', 'stop', 'CHOICE_SCHEMA_INVALID'),
    ('{"speech_act_kind":"NONE","authoritative_fact_ids":["f999"]}', 'stop', 'CHOICE_SCHEMA_INVALID'),
])
def test_bad_choice_never_generates_output(scene, raw, finish, status):
    case, p, _ = scene
    transport = probe.MockTransport([(raw, finish)])
    row = probe.mock_case(case, p, transport)
    assert row['choice_status'] == status
    assert row['calls'] == len(transport.requests) == 1
    assert row['output_attempts'] == [] and row['accepted'] is None
    assert row['content_status'] == 'UNKNOWN' and row['semantic_outcome'] == 0


@pytest.mark.parametrize('raw', ['[]', 'null', '{"speech_act_kind":NaN}',
    '{"speech_act_kind":Infinity}', '{"speech_act_kind":1e999}',
    '{"speech_act_kind":"NONE","speech_act_kind":"ANSWER","authoritative_fact_ids":[]}',
    '{"speech_act_kind":"NONE","authoritative_fact_ids":null}',
    '{"speech_act_kind":"NONE","authoritative_fact_ids":["f000","f000"]}',
    '{"speech_act_kind":"invalid","authoritative_fact_ids":[]}',
    '{"speech_act_kind":"NONE","authoritative_fact_ids":[],"extra":0}',
])
def test_strict_invalid_choice_never_repaired(scene, raw):
    case, p, _ = scene
    row = probe.mock_case(case, p, probe.MockTransport([(raw, 'stop')]))
    assert row['choice_status'] in ('CHOICE_JSON_INVALID', 'CHOICE_SCHEMA_INVALID')
    assert row['calls'] == 1 and row['accepted'] is None


@pytest.mark.parametrize('lengths', [0, 1, 2, 3])
def test_length_never_accepts_legal_raw_k_exact_and_early_stop(scene, lengths):
    case, p, raw = scene
    transport = probe.MockTransport([(CHOICE, 'stop'), *[(raw, 'length')]*lengths, (raw, 'stop')])
    row = probe.mock_case(case, p, transport)
    assert row['calls'] == len(transport.requests) == 1+min(lengths+1, 3)
    assert [x['reject_code'] for x in row['output_attempts']][:lengths] == ['OUTPUT_LENGTH']*lengths
    assert all(x['choice_sha256'] == probe.digest(CHOICE.encode()) for x in row['output_attempts'])
    assert row['accepted'] == (raw if lengths < 3 else None)
    assert row['status'] == ('ACCEPTED' if lengths < 3 else 'OUTPUT_K_EXHAUSTED')
    for i, request in enumerate(transport.requests[1:]):
        assert request['max_tokens'] == 448 and request['seed'] == probe.SEED+1009*i
        legacy = probe.gb.output_body(probe.recovery.baseline_body(p, 'gm12', probe.SEED), json.loads(CHOICE), p)
        legacy.update(max_tokens=448, seed=probe.SEED+1009*i)
        assert request == legacy  # No prompt, schema, sampling, or state-update edits.


def test_mock_only_and_duplicate_send_rejection(scene):
    case, p, _ = scene
    with pytest.raises(TypeError, match='MOCK_ONLY'):
        probe.mock_case(case, p, object())
    transport = probe.MockTransport([(None, None)])
    body = {'seed': 1}
    transport.send(body)
    with pytest.raises(ValueError, match='DUPLICATE_DISPATCH'):
        transport.send(body)
    assert len(transport.requests) == 1


def test_call_cap_is_not_reset_by_unknown():
    transport = probe.MockTransport([])
    for seed in range(128):
        assert transport.send({'seed': seed}) == (None, None)
    with pytest.raises(ValueError, match='CALL_CAP'):
        transport.send({'seed': 128})
    assert len(transport.requests) == 128


def test_first_choice_failure_stops_remaining_keeps_all_32():
    transport = probe.MockTransport([(CHOICE, 'length')])
    rows = probe.mock_suite(probe.recovery.entries(), transport)
    assert len(rows) == 32 and sum(x['calls'] for x in rows) == 1
    assert rows[0]['choice_status'] == 'CHOICE_LENGTH'
    assert all(x['choice_status'] == 'CHOICE_NOT_RUN' for x in rows[1:])
    assert all(x['accepted'] is None and x['semantic_outcome'] == 0 for x in rows)


def test_context_blocks_choice_and_output_independently(scene):
    case, p, raw = scene
    no_choice = probe.MockTransport([], prompt_tokens=8192-64)
    assert probe.mock_case(case, p, no_choice)['calls'] == 0
    no_output = probe.MockTransport([(CHOICE, 'stop'), (raw, 'stop')], prompt_tokens=8192-448)
    row = probe.mock_case(case, p, no_output)
    assert row['calls'] == 1 and row['status'] == 'OUTPUT_NOT_RUN'


def test_native_measure_never_overwrites_partial_directory(tmp_path):
    out = tmp_path/'existing'
    out.mkdir()
    (out/'keep').write_bytes(b'preserve')
    with pytest.raises(FileExistsError):
        probe.native_measure(out, tmp_path/'absent.json')
    assert (out/'keep').read_bytes() == b'preserve'
