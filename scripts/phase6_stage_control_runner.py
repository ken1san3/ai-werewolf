"""Finite SC2 runner: move unchanged stage controls to the system message."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import phase6_model_comparison as base
from scripts import phase6_stage_control_probe as probe
from scripts import phase6_two_stage_probe_runtime as runtime
from scripts.phase6_context_probe import wire_bytes

SC2 = 'stage_control_v1'
TASK = 'T471'
RUNNER = 'scripts/phase6_stage_control_runner.py'
TOKENIZER = Path('C:/AIagent/llama-tokenize.exe')
TOKENIZER_SHA = 'a0fbd34a8a3f25fc0f41cbac1ec67e8395a5ef940db33bd07307b5e3dc8cd6a1'
CONFIG = Path('C:/AIagent/agent/config.toml')
CONFIG_SHA = '43e509956d96492cace8393bdc3fd598ab2418315ccdfd82e144b876241f3bab'
EXTRA = ('scripts/phase6_stage_control_probe.py', 'tests/test_phase6_stage_control_probe.py',
         'scripts/phase6_stage_control_runner.py', 'tests/test_phase6_stage_control_runner.py',
         'scripts/phase6_intent_choice_probe.py', 'tests/test_phase6_intent_choice_probe.py',
         'scripts/phase6_grounding_closed_probe.py', 'tests/test_phase6_grounding_closed_probe.py',
         'scripts/phase6_two_call_runner.py',
         'scripts/phase6_probe_outer.py', 'tests/test_phase6_probe_outer.py',
         'scripts/phase6_two_stage_probe_runtime.py', 'tests/test_phase6_two_stage_probe_runtime.py',
         'scripts/phase6_model_comparison.py', 'tests/test_phase6_model_comparison.py',
         'tests/test_phase6_stage_control_native.py',
         'tests/fixtures/phase6_p2_runtime_golden.json')
base.EXPERIMENT_SOURCE_DELTA[SC2] = base.EXPERIMENT_SOURCE_DELTA[base.K1] | frozenset(EXTRA)


def sources():
    return {**base.source_identity(base.K1), **{p: base.file_hash(ROOT/p) for p in EXTRA}}


def stamp():
    return datetime.now(timezone.utc).isoformat()


def prior_stage_metadata():
    gc2 = json.loads((ROOT/'logs/t448-quality-cycles/frozen-gc2/qw9-results.json').read_text(encoding='utf-8'))
    ic2 = json.loads((ROOT/'logs/t448-quality-cycles/frozen-ic2/qw9-results.json').read_text(encoding='utf-8'))
    by_case = {row['case_id']: row for row in ic2['rows']}
    result = []
    for row in gc2['rows']:
        stages = {}
        for name, source in (('choice', by_case[row['case_id']].get('choice')),
                             ('output', row.get('output'))):
            if not isinstance(source, dict):
                stages[name] = None
                continue
            stages[name] = {
                'rendered_prompt_sha256': source.get('rendered_prompt_sha256'),
                'prompt_tokens_actual': source.get('prompt_tokens_actual'),
                'rendered_prompt_utf8_sha256': None,
                'rendered_prompt_utf8_bytes': None,
                'rendered_prompt_utf8_capture_status': 'NOT_CAPTURED',
            }
        result.append({'case_id': row['case_id'], 'selected_kind': row.get('selected_kind'),
                       'stages': stages})
    if len(result) != 32:
        raise base.StopComparison('PRIOR_EVIDENCE_INVALID')
    return result


def prior_schema_hashes():
    data = json.loads((ROOT/'logs/t448-quality-cycles/gc2-offline-diagnostics.json').read_text(encoding='utf-8'))
    return {(row['case_id'], row['kind']): row['schema_sha256']
            for row in data['grammars']}


def fixed_contract():
    return {'schema': 1, 'experiment': SC2, 'experiment_version': 1, 'task_id': TASK,
            'runner': RUNNER,
            'sampling': base.SAMPLING, 'context': base.CONTEXT,
            'request_seconds': base.REQUEST_SECONDS, 'model_seconds': base.MODEL_SECONDS,
            'load_seconds': base.LOAD_SECONDS, 'max_generations_per_model': 32,
            'max_provider_calls': 64, 'choice_tokens': probe.CHOICE_TOKENS,
            'output_tokens': probe.OUTPUT_TOKENS, 'retry': 0, 'repair': 0,
            'choice_instruction_sha256': base.digest(probe.CHOICE_INSTRUCTION),
            'output_instruction_sha256': base.digest(probe.OUTPUT_INSTRUCTION),
            'stage_contract': runtime.asdict(runtime.SC2_CONTRACT),
            'c5_helper': {'path': 'scripts/phase6_grounding_closed_probe.py',
                'sha256': '81c8e287c9af6db76afcee2272ef3d4b2e5bb56870c96c73a7e7d3c456a79221'},
            'c5_review': {'path': 'Docs/ai/handoffs/tasks/T461_EMPTY_ARRAY_COMPATIBILITY_REVIEW.md',
                'sha256': 'ed68adc6378b3fadfbb21f2c6d96e93ec089e99357d6430f1bc6e80f7247b2e9'},
            'prior_evidence': {
                'gc2_result': {'path': 'logs/t448-quality-cycles/frozen-gc2/qw9-results.json',
                    'sha256': '0bd1e42fd6bc48fdfb9fcd95d2efd18b140109ec2129a5e333c027b352cd0ae0'},
                'ic2_result': {'path': 'logs/t448-quality-cycles/frozen-ic2/qw9-results.json',
                    'sha256': 'cf1924995825431a6797486a528468a25ac2f337d70e5bfa7da3fb2b7dc3c3e2'},
                'gc2_schema_diagnostics': {'path': 'logs/t448-quality-cycles/gc2-offline-diagnostics.json',
                    'sha256': '793909508162ce6b69762a0e2226eaedbbcc248207681dcbe85eb6823c809474'},
            },
            'config_sha256': CONFIG_SHA,
            'tokenizer_argv': [str(TOKENIZER), '-m', base.PROFILES['qw9'][0],
                               '--stdin', '--ids', '--no-bos', '--no-escape']}


write_json = runtime.write_json


def output_identity(body):
    schema = body['response_format']['json_schema']['schema']
    return {'branch_sha256': base.digest(schema['$defs']['speech_act']),
            'schema_sha256': base.digest(schema), 'input_sha256': base.digest(body),
            'messages_sha256': base.digest(body['messages']),
            'wire_sha256': hashlib.sha256(wire_bytes(body)).hexdigest()}


def entry(case, projection):
    original = base.body_for(projection, base.PROFILES['qw9'][0])
    body = probe.choice_body(original)
    return {'case_id': case.case_id, 'category': case.category,
            'baseline_common_input_sha256': base.common_hash(original),
            'baseline_input_sha256': base.digest(original),
            'baseline_messages_sha256': base.digest(original['messages']),
            'projection_sha256': projection.prompt_sha256,
            'choice_input_sha256': base.digest(body),
            'choice_wire_sha256': hashlib.sha256(wire_bytes(body)).hexdigest(),
            'choice_schema_sha256': base.digest(body['response_format']['json_schema']['schema']),
            'choice_messages_sha256': base.digest(body['messages']),
            'choice_wire_bytes': len(wire_bytes(body)),
            'c5_grammar_compat_changed': case.case_id in {'G04-1', 'G04-2', 'G14-1', 'G14-2'},
            'locked_outputs': {kind: _locked_identity(case.case_id, original, kind, projection)
                for kind in body['response_format']['json_schema']['schema']['properties']['speech_act_kind']['enum']}}


def _locked_identity(case_id, original, kind, projection):
    try:
        result = output_identity(probe.output_body(
            original, {'speech_act_kind': kind}, projection))
        old = prior_schema_hashes().get((case_id, kind))
        result.update(gc2_schema_sha256=old,
                      same_kind_schema_changed=(old is not None and old != result['schema_sha256']))
        return result
    except probe.gc2.NoLegalGrounding:
        return {'grounding_status': 'NO_LEGAL_GROUNDING',
                'gc2_schema_sha256': prior_schema_hashes().get((case_id, kind)),
                'same_kind_schema_changed': None}


def prepare(out, baseline_source):
    out.mkdir(parents=True, exist_ok=True)
    if (out/'plan.json').exists():
        raise base.StopComparison('PLAN_EXISTS')
    if base.file_hash(CONFIG) != CONFIG_SHA or base.file_hash(TOKENIZER) != TOKENIZER_SHA:
        raise base.StopComparison('FROZEN_FILE_CHANGED')
    contract = fixed_contract()
    for item in [contract['c5_helper'], contract['c5_review'],
                 *contract['prior_evidence'].values()]:
        if base.file_hash(ROOT/item['path']) != item['sha256']:
            raise base.StopComparison('FROZEN_SOURCE_CHANGED')
    model, quant, server, _ = base.PROFILES['qw9']
    profile = {'model': base.file_identity(model), 'quantization': quant,
               'argv': base.launch_args('qw9'),
               'runtime_files': [base.file_identity(p) for p in [server, *sorted(server.parent.glob('*.dll'))]],
               'available': server.is_file()}
    plan = {**contract,
            'source': sources(), 'cases': [entry(c, base.project(c, 'baseline')) for c in base.cases()],
            'prior_stage_metadata': prior_stage_metadata(),
            'profiles': {'qw9': profile}, 'tokenizer': base.file_identity(TOKENIZER)}
    plan['baseline'] = base.baseline_binding(plan, baseline_source)
    write_json(out/'plan.json', plan, exclusive=True)
    print('PLAN_READY', len(plan['cases']), flush=True)


def verify(plan):
    # Canonical bytes distinguish bool/int and numeric representations as well as values.
    if any(wire_bytes(plan.get(key)) != wire_bytes(value) for key, value in fixed_contract().items()):
        raise base.StopComparison('PLAN_CONTRACT_CHANGED')
    if sources() != plan['source'] or base.file_hash(CONFIG) != plan['config_sha256'] or plan['config_sha256'] != CONFIG_SHA:
        raise base.StopComparison('FROZEN_SOURCE_CHANGED')
    for item in [plan['c5_helper'], plan['c5_review'],
                 *plan['prior_evidence'].values()]:
        if base.file_hash(ROOT/item['path']) != item['sha256']:
            raise base.StopComparison('FROZEN_SOURCE_CHANGED')
    if plan.get('prior_stage_metadata') != prior_stage_metadata():
        raise base.StopComparison('PRIOR_EVIDENCE_INVALID')
    if base.baseline_binding(plan, plan['baseline']['directory']) != plan['baseline']:
        raise base.StopComparison('BASELINE_MISMATCH')
    profile = plan['profiles']['qw9']
    if profile['argv'] != base.launch_args('qw9') or not profile['available']:
        raise base.StopComparison('RUNTIME_UNAVAILABLE')
    for item in [profile['model'], *profile['runtime_files'], plan['tokenizer']]:
        if base.file_identity(item['path']) != item:
            raise base.StopComparison('FROZEN_FILE_CHANGED')
    projections = [(c, base.project(c, 'baseline')) for c in base.cases()]
    if [entry(c, p) for c, p in projections] != plan['cases'] or len(projections) != 32:
        raise base.StopComparison('FROZEN_INPUT_CHANGED')
    if not base.port_free():
        raise base.StopComparison('PORT_IN_USE')
    return profile, projections


def choice_binding(case, projection, baseline, body, raw, value):
    output = probe.output_body(baseline, value, projection)
    return {'case_id': case.case_id, 'projection_sha256': projection.prompt_sha256,
            'baseline_input_sha256': base.digest(baseline),
            'baseline_messages_sha256': base.digest(baseline['messages']),
            'choice_wire_sha256': hashlib.sha256(wire_bytes(body)).hexdigest(),
            'choice_raw_sha256': hashlib.sha256(raw.encode()).hexdigest(),
            'canonical_choice_sha256': base.digest(value),
            'selected_kind': value['speech_act_kind'], **output_identity(output)}


def locked_output(case, projection, baseline, body, raw, value, binding):
    if probe.validate_choice(raw, projection) != value:
        raise base.StopComparison('CHOICE_BINDING_MISMATCH')
    expected_first = probe.choice_body(baseline)
    if wire_bytes(body) != wire_bytes(expected_first):
        raise base.StopComparison('CHOICE_BINDING_MISMATCH')
    if choice_binding(case, projection, baseline, body, raw, value) != binding:
        raise base.StopComparison('CHOICE_BINDING_MISMATCH')
    return probe.output_body(baseline, value, projection)


def dispatch(context, name, body, row):
    try:
        return context.dispatch(name, body)
    except base.StopComparison as error:
        if str(error) == 'MODEL_TIME_BUDGET' and not row[name+'_provider_calls']:
            row.update(status=name.upper()+'_NOT_STARTED', error_kind='MODEL_TIME_BUDGET')
        raise


def process_case(context, case, projection, row):
    from jsonschema import Draft202012Validator
    baseline = base.body_for(projection, base.PROFILES['qw9'][0])
    body = probe.choice_body(baseline)
    raw = dispatch(context, 'choice', body, row)
    if raw is None:
        row.update(status='CHOICE_ERROR', generation_status='ERROR')
        return
    try:
        value = probe.validate_choice(raw, projection)
    except (ValueError, base.DecisionValidationError):
        row.update(status='CHOICE_INVALID', generation_status='INVALID', choice_schema_pass=False)
        return
    row.update(status='CHOICE_VALID', choice_schema_pass=True, selected_kind=value['speech_act_kind'])
    try:
        binding = choice_binding(case, projection, baseline, body, raw, value)
        row['binding'] = binding
        frozen = entry(case, projection)['locked_outputs'][value['speech_act_kind']]
        row.update(c5_grammar_compat_changed=(case.case_id in {'G04-1', 'G04-2', 'G14-1', 'G14-2'}),
                   gc2_schema_sha256=frozen.get('gc2_schema_sha256'),
                   sc2_schema_sha256=binding['schema_sha256'],
                   same_kind_schema_changed=frozen.get('same_kind_schema_changed'))
        next_body = locked_output(case, projection, baseline, body, raw, value, binding)
    except probe.gc2.NoLegalGrounding:
        row.update(status='NO_LEGAL_GROUNDING', generation_status='INVALID',
                   error_kind='NO_LEGAL_GROUNDING')
        return
    text = dispatch(context, 'output', next_body, row)
    if text is None:
        row.update(status='OUTPUT_ERROR', generation_status='ERROR')
        return
    row.update(candidate_schema_pass=False, kind_match_pass=False)
    try:
        final = probe.strict_json(text)
        discussion = final.get('discussion')
        act = discussion.get('speech_act') if type(discussion) is dict else None
        row['kind_match_pass'] = type(act) is dict and act.get('kind') == value['speech_act_kind']
        row['candidate_schema_pass'] = Draft202012Validator(
            next_body['response_format']['json_schema']['schema']).is_valid(final)
        if not row['candidate_schema_pass'] or not row['kind_match_pass']:
            raise ValueError('OUTPUT_SCHEMA')
        probe.validate_final(text, value, projection)
        row['legacy_validator_pass'] = True
    except (ValueError, base.DecisionValidationError):
        if row['candidate_schema_pass'] and row['kind_match_pass']:
            row['legacy_validator_pass'] = False
        row.update(status='OUTPUT_INVALID', generation_status='INVALID')
        return
    # Keep the exact stage2 raw; no serialization or metadata repair.
    return runtime.CaseOutcome(text, binding)


def initial_row(case, projection):
    return {'case_id': case.case_id, 'category': case.category,
        'baseline_input_sha256': base.digest(base.body_for(projection, base.PROFILES['qw9'][0])),
        'projection_sha256': projection.prompt_sha256, 'status': 'CHOICE_NOT_STARTED',
        'c5_grammar_compat_changed': case.case_id in {'G04-1','G04-2','G14-1','G14-2'},
        'gc2_schema_sha256': None, 'sc2_schema_sha256': None, 'same_kind_schema_changed': None,
        'generation_status': 'NOT_STARTED', 'choice_provider_calls': 0, 'output_provider_calls': 0,
        'new_provider_calls': 0, 'retry_count': 0, 'repair_count': 0,
        'completion_tokens': 0, 'provider_prompt_tokens': 0,
        'choice_schema_pass': None, 'candidate_schema_pass': None, 'kind_match_pass': None,
        'legacy_validator_pass': None, 'hard_pass': None, 'semantic_pass': None, 'style_pass': None}


def final_source(plan):
    bound = [plan['c5_helper'], plan['c5_review'], *plan['prior_evidence'].values()]
    return {'source_unchanged': sources() == plan['source'] and all(
                base.file_hash(ROOT/item['path']) == item['sha256'] for item in bound),
            'config_unchanged': base.file_hash(CONFIG) == plan['config_sha256']}


def run(out):
    return runtime.run_two_stage_probe(out, contract=runtime.SC2_CONTRACT,
        callbacks=runtime.TwoStageCallbacks(verify, initial_row, process_case, final_source))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare', action='store_true')
    mode.add_argument('--run', choices=['qw9'])
    parser.add_argument('--baseline-source', type=Path)
    args = parser.parse_args()
    if args.prepare:
        prepare(args.output, args.baseline_source)
    else:
        if args.baseline_source is not None:
            parser.error('baseline source is prepare-only')
        raise SystemExit(run(args.output))
