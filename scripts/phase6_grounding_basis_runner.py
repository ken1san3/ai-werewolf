"""Finite GB1 runner: choose authoritative basis IDs before the locked output."""
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
from scripts import phase6_grounding_basis_probe as probe
from scripts import phase6_two_stage_probe_runtime as runtime
from scripts.phase6_context_probe import wire_bytes

GB1 = 'grounding_basis_v1'
TASK = 'T480'
RUNNER = 'scripts/phase6_grounding_basis_runner.py'
TOKENIZER = Path('C:/AIagent/llama-tokenize.exe')
TOKENIZER_SHA = 'a0fbd34a8a3f25fc0f41cbac1ec67e8395a5ef940db33bd07307b5e3dc8cd6a1'
CONFIG = Path('C:/AIagent/agent/config.toml')
CONFIG_SHA = '43e509956d96492cace8393bdc3fd598ab2418315ccdfd82e144b876241f3bab'
EXTRA = ('scripts/phase6_grounding_basis_probe.py', 'tests/test_phase6_grounding_basis_probe.py',
         'tests/test_phase6_grounding_basis_native.py',
         'tests/test_phase6_grounding_basis_runner.py',
         'scripts/phase6_grounding_basis_quality.py', 'tests/test_phase6_grounding_basis_quality.py',
         'scripts/phase6_stage_control_probe.py', 'tests/test_phase6_stage_control_probe.py',
         'scripts/phase6_grounding_basis_runner.py',
         'scripts/phase6_intent_choice_probe.py', 'tests/test_phase6_intent_choice_probe.py',
         'scripts/phase6_grounding_closed_probe.py', 'tests/test_phase6_grounding_closed_probe.py',
         'scripts/phase6_two_call_runner.py',
         'scripts/phase6_probe_outer.py', 'tests/test_phase6_probe_outer.py',
         'scripts/phase6_two_stage_probe_runtime.py', 'tests/test_phase6_two_stage_probe_runtime.py',
         'scripts/phase6_model_comparison.py', 'tests/test_phase6_model_comparison.py',
         'tests/test_phase6_stage_control_native.py',
         'tests/fixtures/phase6_p2_runtime_golden.json')
PRODUCT_SOURCE = {
    'ai_client/brain/controller.py': '2f3499c8b2f9043ecbe69a8011e0bcbfe5b481c107042617720bf69774682db4',
    'ai_client/brain/invocation.py': '666f1c3d39f37e8a91aff99e14f852b2c18b36e80bf8894e93ae0605914e73be',
    'ai_client/brain/__init__.py': '02de95f98e1207773adf408a089b8d25186fc078fea70e9473c1373883b8de09',
}
PRODUCT_BASE = '598f34da07296b788f75ac85e475eca952b35b37'
TESTED_COMMIT = 'f101ccbea286c3bd7c0e175dd610059df4983a51'
CI_RUN = 35568882536
CI_PATH = 'logs/t476-continuation/ci-readiness-approved.json'
TOOL_AUTHORITY = 'logs/t476-continuation/gb1-tool-approval.json'
TOOL_REPORT = 'Docs/ai/handoffs/tasks/T478_GROUNDING_BASIS_TOOL_REVIEW.md'
APPROVAL_FILES = {
    'Docs/ai/design/PHASE3_5_CAPTURE_READINESS_REPAIR.md': 'dabfe42537d6470bb80adceb05be307efd12d657b7ed9959a6c3f462316adb30',
    'Docs/ai/handoffs/tasks/T482_CAPTURE_READINESS_DESIGN_REVIEW.md': '1977d0c4eb506b3407bfb2deecbcf424ed8f7a3092f5f1646f1e4bbf36c87d5b',
    'Docs/ai/handoffs/tasks/T482_CAPTURE_READINESS_TOOL_REVIEW.md': '7d2716a1cc6bd67fdfbb4075bc0e7ee27df91389d7a812aad104729d424556ca',
    'Docs/ai/handoffs/tasks/T484_CAPTURE_READINESS_COMPLETION.md': '3da15c6f4dce1e46dc02ec18cfa79235b9f0b01afb5b02bc976267da57ed82c1',
    'logs/t484-readiness-completion/run-safe.json': '57c964f359f017399d496f627eaae31dca948a3b5cce94aa9e0d5ee06a94d172',
    'Docs/ai/design/PHASE6_GROUNDING_ISOLATION_PROBE_DESIGN.md': '132fb2817443651d7009e13a6c87811e04bd851a17a20679af4aee5de7552526',
    'Docs/ai/handoffs/tasks/T478_GROUNDING_BASIS_DESIGN_REVIEW.md': '2d0a53aecf7756a57312e1f9f657ab35eaa1eb6fc167fdb47590d1c7e53f6fd2',
}
base.EXPERIMENT_SOURCE_DELTA[GB1] = (
    base.EXPERIMENT_SOURCE_DELTA[base.K1] | frozenset(EXTRA) | frozenset(PRODUCT_SOURCE))


def sources():
    return {**base.source_identity(base.K1), **{p: base.file_hash(ROOT/p) for p in EXTRA}}


def product_approval():
    """Read only the fixed independent approval chain; never infer it from current bytes."""
    try:
        if any(base.file_hash(ROOT/p) != sha for p, sha in {**PRODUCT_SOURCE, **APPROVAL_FILES}.items()):
            raise ValueError
        ci = probe.strict_json((ROOT/CI_PATH).read_text(encoding='utf-8-sig'))
        names = {'test (3.10)', 'test (3.11)', 'test (3.12)', 'test (3.13)',
                 'completion', 'windows-private', 'protocol-schema-minimum', 'docs', 'content'}
        if (ci['databaseId'] != CI_RUN or ci['headSha'] != TESTED_COMMIT
                or ci['status'] != 'completed' or ci['conclusion'] != 'success'
                or len(ci['jobs']) != 9 or {j['name'] for j in ci['jobs']} != names
                or any(j['conclusion'] != 'success' for j in ci['jobs'])):
            raise ValueError
        # Verify the tested Git tree as well as the working tree. PID or branch name is not provenance.
        changes = []
        for path, sha in PRODUCT_SOURCE.items():
            content = subprocess.run(['git', 'show', TESTED_COMMIT+':'+path], cwd=ROOT,
                capture_output=True, check=True).stdout
            previous = subprocess.run(['git', 'show', PRODUCT_BASE+':'+path], cwd=ROOT,
                capture_output=True, check=True).stdout
            # Git stores LF; the approved Owner checkout uses CRLF. Bind both exact domains.
            working = (ROOT/path).read_bytes()
            if content != working.replace(b'\r\n', b'\n'):
                raise ValueError
            changes.append({'path':path, 'old_git_sha256':hashlib.sha256(previous).hexdigest(),
                'tested_git_sha256':hashlib.sha256(content).hexdigest(), 'working_sha256':sha})
        authority = probe.strict_json((ROOT/TOOL_AUTHORITY).read_text(encoding='utf-8'))
        if (set(authority) != {'schema','experiment','verdict','report','report_sha256','source','source_sha256'}
                or type(authority['schema']) is not int or authority['schema'] != 1
                or authority['experiment'] != GB1 or authority['verdict'] != 'APPROVED'
                or authority['report'] != TOOL_REPORT
                or base.file_hash(ROOT/TOOL_REPORT) != authority['report_sha256']
                or authority['source'] != sources()
                or authority['source_sha256'] != base.digest(authority['source'])):
            raise ValueError
        return {'baseline_commit':PRODUCT_BASE, 'tested_commit':TESTED_COMMIT,
            'ci_run_id':CI_RUN, 'ci_sha256':base.file_hash(ROOT/CI_PATH),
            'product_changes':changes, 'approval_files':dict(APPROVAL_FILES),
            'tool_authority_sha256':base.file_hash(ROOT/TOOL_AUTHORITY),
            'tool_report_sha256':authority['report_sha256'], 'tool_source_sha256':authority['source_sha256']}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        raise base.StopComparison('PRODUCT_SOURCE_NOT_APPROVED') from None


def stamp():
    return datetime.now(timezone.utc).isoformat()


def fixed_contract():
    return {'schema': 1, 'experiment': GB1, 'experiment_version': 1, 'task_id': TASK,
            'runner': RUNNER,
            'sampling': base.SAMPLING, 'context': base.CONTEXT,
            'request_seconds': base.REQUEST_SECONDS, 'model_seconds': base.MODEL_SECONDS,
            'load_seconds': base.LOAD_SECONDS, 'max_generations_per_model': 32,
            'max_provider_calls': 64, 'choice_tokens': probe.CHOICE_TOKENS,
            'output_tokens': probe.OUTPUT_TOKENS, 'retry': 0, 'repair': 0,
            'choice_instruction_sha256': base.digest(probe.CHOICE_INSTRUCTION),
            'output_instruction_sha256': base.digest(probe.OUTPUT_INSTRUCTION),
            'stage_contract': runtime.asdict(runtime.GB1_CONTRACT),
            'c5_helper': {'path': 'scripts/phase6_grounding_closed_probe.py',
                'sha256': '81c8e287c9af6db76afcee2272ef3d4b2e5bb56870c96c73a7e7d3c456a79221'},
            'c5_review': {'path': 'Docs/ai/handoffs/tasks/T461_EMPTY_ARRAY_COMPATIBILITY_REVIEW.md',
                'sha256': 'ed68adc6378b3fadfbb21f2c6d96e93ec089e99357d6430f1bc6e80f7247b2e9'},
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
    body = probe.choice_body(original, projection)
    return {'case_id': case.case_id, 'category': case.category,
            **probe.catalog_identity(projection),
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
            original, {'speech_act_kind': kind, 'authoritative_fact_ids': []}, projection))
        return result
    except probe.gc2.NoLegalGrounding:
        return {'grounding_status': 'NO_LEGAL_GROUNDING'}


def prepare(out, baseline_source):
    chain = product_approval()
    out.mkdir(parents=True, exist_ok=True)
    if (out/'plan.json').exists():
        raise base.StopComparison('PLAN_EXISTS')
    if base.file_hash(CONFIG) != CONFIG_SHA or base.file_hash(TOKENIZER) != TOKENIZER_SHA:
        raise base.StopComparison('FROZEN_FILE_CHANGED')
    contract = fixed_contract()
    for item in [contract['c5_helper'], contract['c5_review']]:
        if base.file_hash(ROOT/item['path']) != item['sha256']:
            raise base.StopComparison('FROZEN_SOURCE_CHANGED')
    model, quant, server, _ = base.PROFILES['qw9']
    profile = {'model': base.file_identity(model), 'quantization': quant,
               'argv': base.launch_args('qw9'),
               'runtime_files': [base.file_identity(p) for p in [server, *sorted(server.parent.glob('*.dll'))]],
               'available': server.is_file()}
    plan = {**contract,
            'source': sources(), 'cases': [entry(c, base.project(c, 'baseline')) for c in base.cases()],
            'product_approval': chain,
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
    for item in [plan['c5_helper'], plan['c5_review']]:
        if base.file_hash(ROOT/item['path']) != item['sha256']:
            raise base.StopComparison('FROZEN_SOURCE_CHANGED')
    if plan.get('product_approval') != product_approval():
        raise base.StopComparison('PRODUCT_SOURCE_NOT_APPROVED')
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
            'selected_kind': value['speech_act_kind'], 'selected_fact_count':len(value['authoritative_fact_ids']),
            **probe.catalog_identity(projection), **output_identity(output)}


def locked_output(case, projection, baseline, body, raw, value, binding):
    if probe.validate_choice(raw, projection) != value:
        raise base.StopComparison('CHOICE_BINDING_MISMATCH')
    expected_first = probe.choice_body(baseline, projection)
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
    body = probe.choice_body(baseline, projection)
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
        row['selected_fact_count'] = len(value['authoritative_fact_ids'])
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
        'selected_fact_count': None, **probe.catalog_identity(projection),
        'generation_status': 'NOT_STARTED', 'choice_provider_calls': 0, 'output_provider_calls': 0,
        'new_provider_calls': 0, 'retry_count': 0, 'repair_count': 0,
        'completion_tokens': 0, 'provider_prompt_tokens': 0,
        'choice_schema_pass': None, 'candidate_schema_pass': None, 'kind_match_pass': None,
        'legacy_validator_pass': None, 'hard_pass': None, 'semantic_pass': None, 'style_pass': None}


def final_source(plan):
    bound = [plan['c5_helper'], plan['c5_review']]
    return {'source_unchanged': sources() == plan['source'] and product_approval() == plan['product_approval'] and all(
                base.file_hash(ROOT/item['path']) == item['sha256'] for item in bound),
            'config_unchanged': base.file_hash(CONFIG) == plan['config_sha256']}


def run(out):
    return runtime.run_two_stage_probe(out, contract=runtime.GB1_CONTRACT,
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
