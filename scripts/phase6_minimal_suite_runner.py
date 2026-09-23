"""One frozen, local Qwen minimal-output measurement. No game or retry."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import phase6_model_comparison as base
from scripts import phase6_minimal_output_probe as probe
from scripts import phase6_minimal_suite_quality as quality
from scripts.phase6_context_probe import wire_bytes
from tests.fixtures.phase6_evidence import create_private_evidence_container

EXPERIMENT = "minimal_output_v1"
TASK = "T499"
RUNNER = "scripts/phase6_minimal_suite_runner.py"
BASELINE_DIR = ROOT / "logs/t424-model-comparison/frozen-v2"
CONFIG = Path("C:/AIagent/agent/config.toml")
TOKENIZER = Path("C:/AIagent/llama-tokenize.exe")
PINNED = {
    str(CONFIG): "43e509956d96492cace8393bdc3fd598ab2418315ccdfd82e144b876241f3bab",
    str(TOKENIZER): "a0fbd34a8a3f25fc0f41cbac1ec67e8395a5ef940db33bd07307b5e3dc8cd6a1",
    base.PROFILES['qw9'][0]: "03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8",
    str(base.STOCK): "3c21330df1049f49a11e08e695be84c1138886a5effe5fc702a3b08c8f0e5b8a",
}
BASELINE_HASHES = {
    "plan.json": "c878456dc15b00b87549a8b91d29eba8ef64e63cb7cc972da1eff40942f4c1c4",
    "t427-safe-summary.json": "4557f7a43069b8cf1a3c4990590e828ef337f35599551fb7646f8ed51fda93a0",
}
DESIGN = "Docs/ai/design/PHASE6_MINIMAL_SUITE_BINDING_DESIGN.md"
TOOL_APPROVAL = "Docs/ai/handoffs/tasks/T497_MINIMAL_TOOL_APPROVAL.json"
DOC_REFS = (
    DESIGN, "Docs/ai/handoffs/tasks/T497_MINIMAL_DESIGN_REVIEW.md",
    "Docs/ai/handoffs/tasks/T497_MINIMAL_TOOL_REVIEW.md", TOOL_APPROVAL,
    "Docs/ai/handoffs/tasks/T492_MINIMAL_OFFLINE_DESIGN_REVIEW.md",
    "Docs/ai/handoffs/tasks/T495_MINIMAL_OFFLINE_EXECUTION.md",
)
EXTRA_SOURCES = (
    RUNNER, "scripts/phase6_minimal_suite_adapter.py", "scripts/phase6_minimal_suite_quality.py",
    "scripts/phase6_minimal_output_probe.py", "scripts/phase6_probe_outer.py",
    "tests/test_phase6_minimal_suite_adapter.py", "tests/test_phase6_minimal_suite_runner.py",
    "tests/test_phase6_minimal_suite_quality.py", "tests/test_phase6_minimal_output_applicability.py",
    "tests/fixtures/phase6_minimal_output_cases.py", "tests/test_phase6_semantic_completion.py",
    "tests/test_phase6_memory_projection.py", "tests/fixtures/phase6_semantic_backend.py",
    "scripts/run_phase5_local_smoke.py", "tests/test_phase6_probe_outer.py", "tests/test_phase6_gpu_monitor.py",
    "scripts/phase6_none_reason_probe.py", "scripts/phase6_schema_order_probe.py",
    "scripts/phase6_kind_first_probe.py",
)
ERRORS = frozenset(("PLAN_EXISTS", "SOURCE_CHANGED", "CONFIG_CHANGED", "MODEL_CHANGED", "RUNTIME_CHANGED",
    "CASE_BINDING_CHANGED", "CONTEXT_OVERFLOW", "REQUEST_TOO_LARGE", "TEMPLATE_INVALID", "TOKEN_MISMATCH",
    "MODEL_TIME_BUDGET", "HTTP_ERROR", "RESPONSE_TOO_LARGE", "OUTPUT_INVALID", "PRIVATE_EVIDENCE_ERROR", "CLEANUP_ERROR"))


@dataclass(frozen=True)
class RunnerProfile:
    experiment: str
    task: str
    runner: str
    design: str
    tool_approval: str
    doc_refs: tuple[str, ...]
    extra_sources: tuple[str, ...]
    claim_name: str
    outer_task: str
    without_grounding: bool = False


LEGACY_PROFILE = RunnerProfile(
    EXPERIMENT, TASK, RUNNER, DESIGN, TOOL_APPROVAL, DOC_REFS, EXTRA_SOURCES,
    "minimal-output-v1.run.claim", "T499OUTER")


class RunError(ValueError):
    def __init__(self, code):
        if code not in ERRORS:
            raise ValueError("INVALID_ERROR_ENUM")
        self.code = code
        super().__init__(code)


def require(ok, code):
    if not ok:
        raise RunError(code)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def durable(path, data, *, exclusive=False):
    raw = data if type(data) is bytes else wire_bytes(data)
    try:
        with Path(path).open('xb' if exclusive else 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        require(Path(path).read_bytes() == raw, "PRIVATE_EVIDENCE_ERROR")
    except FileExistsError:
        raise
    except OSError:
        raise RunError('PRIVATE_EVIDENCE_ERROR') from None


def claim(path, identity):
    try:
        durable(path, identity, exclusive=True)
    except FileExistsError:
        raise RunError("PLAN_EXISTS") from None


def baseline_plan():
    for name, expected in BASELINE_HASHES.items():
        require(base.file_hash(BASELINE_DIR/name) == expected, "SOURCE_CHANGED")
    report = ROOT / "Docs/ai/handoffs/tasks/T478_GROUNDING_BASIS_QUALITY.md"
    require(base.file_hash(report) == "f4c17f6e148d6df8df215318ba686d81587491269d28a1bc6083f132883ce34e", "SOURCE_CHANGED")
    return probe._strict_json((BASELINE_DIR/'plan.json').read_bytes())


def source_identity(profile=LEGACY_PROFILE):
    # The old plan's exact hashed list is reused, never expanded by a wildcard.
    paths = tuple(baseline_plan()['source']) + profile.extra_sources
    return {p: base.file_hash(ROOT/p) for p in dict.fromkeys(paths)}


def git_head(*, clean=False, profile=LEGACY_PROFILE):
    def git(*args):
        item = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, check=True)
        return item.stdout.decode('utf-8').strip()
    require(git('branch', '--show-current') == 'experiment/speech-act-kind-first-20260919', 'SOURCE_CHANGED')
    if clean:
        require(not git('status', '--porcelain', '--', *source_identity(profile)), 'SOURCE_CHANGED')
    return git('rev-parse', 'HEAD')


def external_identity():
    identities = {}
    old = baseline_plan()['profiles']['qw9']
    for name, expected in PINNED.items():
        ident = base.file_identity(name)
        require(ident['sha256'] == expected, 'CONFIG_CHANGED' if name == str(CONFIG) else 'MODEL_CHANGED')
        identities[name] = ident
    require(identities[base.PROFILES['qw9'][0]]['size'] == 5680522464, 'MODEL_CHANGED')
    for old_item in old['runtime_files']:
        ident = base.file_identity(old_item['path'])
        require(ident['sha256'] == old_item['sha256'] and ident['size'] == old_item['size'], 'RUNTIME_CHANGED')
        identities[old_item['path']] = ident
    require(base.launch_args('qw9') == old['argv'], 'RUNTIME_CHANGED')
    return identities


def suite_cases():
    from scripts import phase6_minimal_suite_adapter as adapter
    from scripts.phase6_conversation_suite import cases, project
    rows = tuple(adapter.bind_case(c, project(c, 'baseline')) for c in cases())
    require(tuple(s.case.case_id for s in rows) == probe.CASE_IDS, 'CASE_BINDING_CHANGED')
    distribution = Counter(s.binding.authority_without_update['trigger'] for s in rows)
    require(distribution == {'INITIAL_CHAT':4, 'PEER_CHAT':24, 'CO_OPPORTUNITY':2, 'PRE_VOTE':2}, 'CASE_BINDING_CHANGED')
    return rows


def derived_delta_contract():
    from scripts import phase6_minimal_suite_adapter as adapter
    old = adapter.MINIMAL_V1_INSTRUCTION
    new = old.replace(adapter.GROUNDING_INSTRUCTION, '')
    require(old.count(adapter.GROUNDING_INSTRUCTION) == 1 and new != old, 'CASE_BINDING_CHANGED')
    return {
        'removed_schema_json_pointers':['/properties/grounding', '/required/3'],
        'legacy_instruction_sha256':probe.sha256(old.encode('utf-8')),
        'candidate_instruction_sha256':probe.sha256(new.encode('utf-8')),
        'grounding_instruction_clause_sha256':probe.sha256(adapter.GROUNDING_INSTRUCTION.encode('utf-8')),
    }


def frozen_case(suite, profile=LEGACY_PROFILE):
    from scripts import phase6_minimal_suite_adapter as adapter
    builder = adapter.candidate_body_without_grounding if profile.without_grounding else adapter.candidate_body
    body = builder(suite, Path(base.PROFILES['qw9'][0]).name)
    raw = adapter.candidate_wire(body)
    require(raw == wire_bytes(body) and body['max_tokens'] == 512, 'CASE_BINDING_CHANGED')
    frozen = dict(case_id=suite.case.case_id, input_sha256=base.digest(body), wire_sha256=probe.sha256(raw),
                  schema_sha256=base.digest(body['response_format']['json_schema']['schema']),
                  messages_sha256=base.digest(body['messages']), wire_bytes=len(raw),
                  schema_bytes=len(wire_bytes(body['response_format']['json_schema']['schema'])))
    if profile.without_grounding:
        legacy_body = adapter.candidate_body(suite, Path(base.PROFILES['qw9'][0]).name)
        expected = deepcopy(legacy_body)
        schema = expected['response_format']['json_schema']['schema']
        require(schema['required'][3] == 'grounding', 'CASE_BINDING_CHANGED')
        del schema['properties']['grounding']
        del schema['required'][3]
        suffix = '\n\n' + adapter.MINIMAL_V1_INSTRUCTION
        system = expected['messages'][0]['content']
        require(system.endswith(suffix), 'CASE_BINDING_CHANGED')
        expected['messages'][0]['content'] = system[:-len(suffix)] + '\n\n' + \
            adapter.MINIMAL_V1_INSTRUCTION.replace(adapter.GROUNDING_INSTRUCTION, '')
        require(expected == body, 'CASE_BINDING_CHANGED')
        frozen.update(legacy_schema_sha256=base.digest(
                          legacy_body['response_format']['json_schema']['schema']),
                      legacy_messages_sha256=base.digest(legacy_body['messages']))
    return frozen


def approval_identity(profile=LEGACY_PROFILE):
    approval = probe._strict_json((ROOT/profile.tool_approval).read_bytes())
    require(approval.get('verdict') == 'APPROVED' and approval.get('source') == source_identity(profile)
            and approval.get('design_sha256') == base.file_hash(ROOT/profile.design), 'SOURCE_CHANGED')
    if profile.without_grounding:
        require(approval.get('report') == profile.doc_refs[-2]
                and approval.get('report_sha256') == base.file_hash(ROOT/approval['report']), 'SOURCE_CHANGED')
    return {p:base.file_hash(ROOT/p) for p in profile.doc_refs}


def prepare(out, baseline_aggregate_path=None, profile=LEGACY_PROFILE):
    out = Path(out)
    baseline = quality.baseline_bytes()
    if baseline_aggregate_path is not None:
        require(Path(baseline_aggregate_path).read_bytes() == baseline, 'SOURCE_CHANGED')
    head = git_head(clean=True, profile=profile)
    sources = source_identity() if profile is LEGACY_PROFILE else source_identity(profile)
    approvals = approval_identity() if profile is LEGACY_PROFILE else approval_identity(profile)
    external = external_identity()
    prepared = suite_cases()
    plan = dict(version=1, experiment=profile.experiment, task_id=profile.task, runner=profile.runner,
                run_id=uuid.uuid4().hex, head=head, source=sources, approvals=approvals, external=external,
                argv=base.launch_args('qw9'), sampling=base.SAMPLING, context=8192,
                request_seconds=60, model_seconds=1200, load_seconds=180, outer_seconds=1320,
                max_provider_calls=32, retry=0, repair=0, fallback=0,
                baseline_sha256=probe.sha256(baseline), baseline_artifacts=BASELINE_HASHES,
                legacy_instruction_present=True,
                cases=[frozen_case(s, profile) for s in prepared])
    if profile.without_grounding:
        plan['derived_delta_contract'] = derived_delta_contract()
    out.mkdir(parents=True, exist_ok=False)
    durable(out/'baseline-aggregate.json', baseline, exclusive=True)
    durable(out/'plan.json', plan, exclusive=True)
    return plan


def verify(plan, profile=LEGACY_PROFILE):
    require(plan.get('experiment') == profile.experiment and plan.get('task_id') == profile.task
            and plan.get('runner') == profile.runner
            and plan.get('version') == 1 and type(plan.get('run_id')) is str and len(plan['run_id']) == 32, 'SOURCE_CHANGED')
    expected = dict(sampling=base.SAMPLING, context=8192, request_seconds=60, model_seconds=1200,
                    load_seconds=180, outer_seconds=1320, max_provider_calls=32, retry=0, repair=0, fallback=0)
    require(all(wire_bytes(plan.get(k)) == wire_bytes(v) for k,v in expected.items()), 'SOURCE_CHANGED')
    sources = source_identity() if profile is LEGACY_PROFILE else source_identity(profile)
    approvals = approval_identity() if profile is LEGACY_PROFILE else approval_identity(profile)
    require(plan['head'] == git_head(clean=True, profile=profile) and plan['source'] == sources
            and plan['approvals'] == approvals, 'SOURCE_CHANGED')
    require(plan['external'] == external_identity() and plan['argv'] == base.launch_args('qw9'), 'RUNTIME_CHANGED')
    require(plan['baseline_sha256'] == probe.sha256(quality.baseline_bytes())
            and plan['baseline_artifacts'] == BASELINE_HASHES
            and plan['legacy_instruction_present'] is True, 'SOURCE_CHANGED')
    if profile.without_grounding:
        require(plan.get('derived_delta_contract') == derived_delta_contract(), 'SOURCE_CHANGED')
    else:
        require('derived_delta_contract' not in plan, 'SOURCE_CHANGED')
    suites = suite_cases()
    require(plan['cases'] == [frozen_case(s, profile) for s in suites], 'CASE_BINDING_CHANGED')
    return suites


def host_idle():
    require(base.port_free(), 'RUNTIME_CHANGED')
    query = "@(Get-Process -Name llama-server,llama-tokenize -ErrorAction SilentlyContinue).Count"
    item = subprocess.run(['powershell', '-NoProfile', '-Command', query], capture_output=True, timeout=20,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    require(item.returncode == 0 and item.stdout.strip() == b'0', 'RUNTIME_CHANGED')


def owned_listener(process):
    # Popen retains the original process handle; liveness brackets PID observation.
    return process.poll() is None and base.owned_listener(process) and process.poll() is None


def cleanup_owned(monitor, provider, *, clock=time.monotonic):
    deadline = clock()+25
    result = {}
    remaining = 0
    for name, process in (('monitor', monitor), ('provider', provider)):
        if process is None:
            result[name+'_exit'] = None
            continue
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=max(.001, min(10, deadline-clock())))
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=max(.001, deadline-clock()))
            result[name+'_exit'] = process.poll()
        except Exception:
            result[name+'_cleanup_error'] = 'CLEANUP_ERROR'
            try:
                if process.poll() is None:
                    process.kill()  # Original Popen handle only, never a PID lookup.
                    if clock() < deadline:
                        process.wait(timeout=deadline-clock())
            except Exception:
                pass
        try:
            remaining += process.poll() is None
        except Exception:
            remaining += 1
    result['owned_processes_remaining'] = remaining
    return result


def runtime_matches(identity):
    return (identity.get('build') == 'b10697-093adb242'
            and identity.get('template_sha256') == 'c17a933c26907f0982a96e5cb3b6a5ef393f1722f13558ebda7be039649cb4cd'
            and identity.get('n_ctx') == 8192 and identity.get('slots') == 1
            and Path(identity['model_path']).resolve() == Path(base.PROFILES['qw9'][0]).resolve())


def initial_row(suite, frozen, profile=LEGACY_PROFILE):
    row = {**frozen, 'category':suite.case.category, 'trigger':suite.binding.authority_without_update['trigger'],
           'status':'NOT_STARTED', 'error':None, 'call_consumed':0, 'raw_sha256':None,
           'mechanical_status':'UNKNOWN', 'applicability':'UNRESOLVED', 'speech_act':None,
           'peer_long_exact_copy':None, 'semantic':None, 'hard':None, 'style':None}
    if profile.without_grounding:
        row.update(derived_grounding_item_count=None, derived_grounding_purpose_counts=None)
    return row


def measure_prompt(body, private, case_id, label, profile=LEGACY_PROFILE):
    from scripts import phase6_minimal_suite_adapter as adapter
    allowed = ('candidate',) if profile.without_grounding else ('full', 'shadow')
    require(label in allowed, 'TEMPLATE_INVALID')
    instruction = adapter.MINIMAL_V1_INSTRUCTION.replace(adapter.GROUNDING_INSTRUCTION, '') \
        if profile.without_grounding or label == 'shadow' else adapter.MINIMAL_V1_INSTRUCTION
    messages = body.get('messages')
    require(type(messages) is list and len(messages) == 2, 'TEMPLATE_INVALID')
    require(all(type(m) is dict and set(m) == {'role','content'} and type(m['content']) is str
                and m['content'] for m in messages), 'TEMPLATE_INVALID')
    require([m['role'] for m in messages] == ['system','user'], 'TEMPLATE_INVALID')
    system, user = (m['content'] for m in messages)
    suffix = '\n\n'+instruction
    require(system.endswith(suffix) and system.count(instruction) == 1, 'TEMPLATE_INVALID')
    prefix = system[:-len(suffix)]
    require(bool(prefix), 'TEMPLATE_INVALID')
    raw = wire_bytes(body)
    durable(private/(case_id+'.'+label+'.request.bin'), raw, exclusive=True)
    def sink(rendered):
        require(type(rendered) is str and all(rendered.count(part) == 1
                    for part in (system, prefix, instruction, user)), 'TEMPLATE_INVALID')
        require(rendered.index(prefix) < rendered.index(instruction) < rendered.index(user), 'TEMPLATE_INVALID')
        durable(private/(case_id+'.'+label+'.rendered.bin'), rendered.encode('utf-8'), exclusive=True)
    try:
        measured = base.count_prompt(body, wire_payload=raw, private_sink=sink)
    except base.StopComparison as error:
        code = str(error)
        raise RunError(code if code in ('CONTEXT_OVERFLOW', 'MODEL_TIME_BUDGET') else 'TEMPLATE_INVALID') from None
    measured['rendered_bytes_sha256'] = base.file_hash(private/(case_id+'.'+label+'.rendered.bin'))
    return measured


def stage(suite, row, private, identity, save, budget, profile=LEGACY_PROFILE):
    from scripts import phase6_minimal_suite_adapter as adapter
    from ai_client.brain.controller import _cross_player_public_copy
    require(row['call_consumed'] == 0 and budget['calls'] < 32, 'PLAN_EXISTS')
    require(base._RUN_DEADLINE is not None and base._RUN_DEADLINE-time.monotonic() >= 60, 'MODEL_TIME_BUDGET')
    builder = adapter.candidate_body_without_grounding if profile.without_grounding else adapter.candidate_body
    body = builder(suite, Path(base.PROFILES['qw9'][0]).name)
    payload = adapter.candidate_wire(body)
    frozen = frozen_case(suite, profile)
    require(frozen == {k:row[k] for k in frozen}, 'CASE_BINDING_CHANGED')
    label = 'candidate' if profile.without_grounding else 'full'
    full = (measure_prompt(body, private, row['case_id'], label) if profile is LEGACY_PROFILE
            else measure_prompt(body, private, row['case_id'], label, profile))
    # Keep this dispatch contract explicit in addition to the native helper's gate.
    require(type(full['prompt_tokens_actual']) is int and full['prompt_tokens_actual'] >= 0, 'TOKEN_MISMATCH')
    require(full['prompt_tokens_actual']+512+1 <= 8192, 'CONTEXT_OVERFLOW')
    if profile.without_grounding:
        row.update(candidate_prompt_tokens=full['prompt_tokens_actual'],
                   candidate_rendered_sha256=full['rendered_bytes_sha256'],
                   schema_changes_rendered_prompt=full['schema_changes_rendered_prompt'])
    else:
        shadow = measure_prompt(adapter.shadow_without_grounding(body), private, row['case_id'], 'shadow')
        require(type(shadow['prompt_tokens_actual']) is int and shadow['prompt_tokens_actual'] >= 0, 'TOKEN_MISMATCH')
        row.update(full_prompt_tokens=full['prompt_tokens_actual'], shadow_prompt_tokens=shadow['prompt_tokens_actual'],
                   grounding_prompt_token_delta=full['prompt_tokens_actual']-shadow['prompt_tokens_actual'],
                   full_rendered_sha256=full['rendered_bytes_sha256'], shadow_rendered_sha256=shadow['rendered_bytes_sha256'],
                   schema_changes_rendered_prompt=full['schema_changes_rendered_prompt'])
    marker = {**identity, 'case_id':row['case_id'], 'ordinal':probe.CASE_IDS.index(row['case_id']),
              'request_sha256':row['input_sha256'], 'wire_sha256':row['wire_sha256'], 'schema_sha256':row['schema_sha256']}
    claim(private/(row['case_id']+'.generation.claim'), marker)
    row.update(call_consumed=1, status='STARTED', started_at_utc=stamp())
    budget['calls'] += 1
    save()  # Durable call consumption precedes every generation dispatch.
    started = time.monotonic()
    try:
        response = base.request('/v1/chat/completions', body, timeout=60, wire_payload=payload)
        choice = response['choices'][0]
        text = choice['message'].get('content')
        require(type(text) is str, 'OUTPUT_INVALID')
        raw = text.encode('utf-8')
        durable(private/(row['case_id']+'.output.bin'), raw, exclusive=True)
        usage = response.get('usage', {})
        durable(private/(row['case_id']+'.response.json'), {'usage':usage, 'finish_reason':choice.get('finish_reason')}, exclusive=True)
        row.update(raw_sha256=probe.sha256(raw), raw_bytes=len(raw),
                   provider_prompt_tokens=usage.get('prompt_tokens') if type(usage.get('prompt_tokens')) is int else None,
                   completion_tokens=usage.get('completion_tokens') if type(usage.get('completion_tokens')) is int else None,
                   finish_reason=base.fixed_value(choice.get('finish_reason'), {'stop','length','content_filter','tool_calls'}))
        require(row['provider_prompt_tokens'] == full['prompt_tokens_actual'] and type(row['provider_prompt_tokens']) is int,
                'TOKEN_MISMATCH')
        require(type(row['completion_tokens']) is int and 0 <= row['completion_tokens'] <= 512, 'TOKEN_MISMATCH')
        require(not choice['message'].get('reasoning_content') and row['finish_reason'] == 'stop', 'OUTPUT_INVALID')
        if profile.without_grounding:
            from scripts import phase6_derived_grounding_snapshot as snapshot
            validation = probe.validate_without_grounding(raw, suite.binding)
            checked = validation.probe_result
        else:
            checked = probe.validate_suite(raw, suite.binding)
        # Read candidate fields only after the profile validator has established
        # the closed top-level shape and required fields.
        value = probe._strict_json(raw)
        text = value['utterance']
        if profile.without_grounding:
            counts = Counter(item.purpose for item in validation.derived_grounding)
            try:
                snapshot.write_validation_snapshot(
                    private, case_id=row['case_id'], raw_sha256=row['raw_sha256'],
                    input_sha256=row['input_sha256'], schema_sha256=row['schema_sha256'],
                    validation=validation)
                # Exercise the process-boundary contract now; semantic evaluation uses this helper later.
                restored = snapshot.read_validation_snapshot(
                    private, case_id=row['case_id'], raw_sha256=row['raw_sha256'],
                    input_sha256=row['input_sha256'], schema_sha256=row['schema_sha256'],
                    expected_probe_result=checked)
            except snapshot.SnapshotError:
                raise RunError('PRIVATE_EVIDENCE_ERROR') from None
            require(restored == validation, 'PRIVATE_EVIDENCE_ERROR')
            row.update(derived_grounding_item_count=len(validation.derived_grounding),
                       derived_grounding_purpose_counts={key:counts.get(key, 0) for key in
                           ('UTTERANCE', 'OPINION_CURRENT', 'REACTION', 'PRE_VOTE')})
        else:
            refs = [probe._ref_key(g['ref']) for g in value['grounding']]
            row.update(grounding_item_count=len(refs), distinct_grounding_ref_count=len(set(refs)),
                       duplicate_ref_occurrences=len(refs)-len(set(refs)))
        row.update(status='COMPLETE', mechanical_status='PASS', applicability=checked.applicability.removeprefix('APPLICABILITY_'),
                   speech_act=value['speech_act']['kind'], private_before_sha256=checked.private_before_sha256,
                   private_after_sha256=checked.private_after_sha256,
                   peer_long_exact_copy=False if text is None else _cross_player_public_copy(suite.case.request, ' '.join(text.casefold().split())))
    except probe.ProbeError as error:
        row.update(status='OUTPUT_INVALID', error='OUTPUT_INVALID', validation_code=error.code,
                   mechanical_status='FAIL', applicability='INVALID')
    except RunError as error:
        row.update(status='ERROR', error=error.code, mechanical_status='FAIL' if error.code == 'OUTPUT_INVALID' else 'UNKNOWN')
    except Exception:
        row.update(status='ERROR', error='HTTP_ERROR', mechanical_status='UNKNOWN')
    finally:
        row.update(ended_at_utc=stamp(), latency_real_sec=time.monotonic()-started)
        save()


def run(out, profile=LEGACY_PROFILE):
    out = Path(out)
    plan = probe._strict_json((out/'plan.json').read_bytes())
    suites = verify(plan) if profile is LEGACY_PROFILE else verify(plan, profile)
    require((out/'baseline-aggregate.json').read_bytes() == quality.baseline_bytes(), 'SOURCE_CHANGED')
    host_idle()
    identity = dict(task_id=profile.task, experiment=profile.experiment, runner=profile.runner,
                    plan_sha256=base.file_hash(out/'plan.json'), run_id=plan['run_id'])
    claim(out/profile.claim_name, identity)
    result = {**identity, 'status':'STARTING', 'clock_domain':'REAL', 'retry':0, 'repair':0,
              'provider_calls':0, 'rows':[initial_row(s, f) if profile is LEGACY_PROFILE
                                          else initial_row(s, f, profile)
                                          for s,f in zip(suites, plan['cases'])]}
    start = time.monotonic()
    budget = {'calls':0}
    proc = monitor = private = None
    def save():
        result.update(provider_calls=budget['calls'], real_duration_sec=time.monotonic()-start)
        durable(out/'results.json', result)
    save()
    base._RUN_DEADLINE = start+1200
    try:
        private = create_private_evidence_container(ROOT/'logs/phase6-private-evidence', evidence_kind='synthetic',
                                                    task_id=profile.task, created_at_utc=datetime.now(timezone.utc))
        durable(out/'private-locator.json', {'path':str(private)}, exclusive=True)
        for suite in suites:
            binding = suite.binding
            durable(private/(suite.case.case_id+'.binding.json'), {
                'authority':probe.plain(binding.authority_without_update),
                'canonical_user_sha256':probe.sha256(binding.canonical_user_bytes),
                'update_requirement':binding.update_requirement}, exclusive=True)
        with (private/'server.log').open('xb') as log:
            proc = subprocess.Popen(plan['argv'], cwd=Path(plan['argv'][0]).parent, stdout=log, stderr=subprocess.STDOUT,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        while True:
            require(proc.poll() is None and time.monotonic()-start < 180, 'RUNTIME_CHANGED')
            try:
                if base.request('/health', timeout=2).get('status') == 'ok':
                    break
            except Exception:
                pass  # Bounded health polling only; never retry generation.
            time.sleep(.25)
        runtime = base.runtime()
        require(owned_listener(proc) and runtime_matches(runtime), 'RUNTIME_CHANGED')
        durable(private/'runtime.json', runtime, exclusive=True)
        result['runtime'] = base.safe_runtime(runtime)
        with (private/'monitor.log').open('xb') as log:
            monitor = subprocess.Popen([sys.executable, str(ROOT/'scripts/monitor_phase6_gpu.py'), '--output',
                str(private/'gpu.jsonl'), '--max-seconds','1200','--watch-pid',str(os.getpid())],
                stdout=log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        for suite, row in zip(suites, result['rows']):
            require(owned_listener(proc) and base.runtime() == runtime, 'RUNTIME_CHANGED')
            if profile is LEGACY_PROFILE:
                stage(suite, row, private, identity, save, budget)
            else:
                stage(suite, row, private, identity, save, budget, profile)
        result['status'] = 'COMPLETE'
    except RunError as error:
        result.update(status='STOPPED', error=error.code)
    except Exception:
        result.update(status='STOPPED', error='PRIVATE_EVIDENCE_ERROR')
    finally:
        base._RUN_DEADLINE = None
        result.update(cleanup_owned(monitor, proc))
        result['listener_free'] = base.port_free()
        if result['owned_processes_remaining'] != 0 or not result['listener_free'] or any(k.endswith('_cleanup_error') for k in result):
            result.update(status='STOPPED', error='CLEANUP_ERROR')
        try:
            verify(plan) if profile is LEGACY_PROFILE else verify(plan, profile)
            result['source_unchanged'] = True
        except Exception:
            result.update(source_unchanged=False, status='STOPPED', error='SOURCE_CHANGED')
        if private is not None:
            try:
                base.attach_performance(result, private)
            except Exception:
                result.update(status='STOPPED', error='PRIVATE_EVIDENCE_ERROR')
        for row in result['rows']:
            if row['status'] in ('NOT_STARTED','STARTED'):
                row.update(status='NOT_STARTED' if not row['call_consumed'] else 'ERROR',
                           error=result.get('error', 'MODEL_TIME_BUDGET'))
        save()
    return result


def supervise(out, profile=LEGACY_PROFILE):
    from scripts.phase6_probe_outer import supervise as outer
    out = Path(out)
    plan = probe._strict_json((out/'plan.json').read_bytes())
    verify(plan) if profile is LEGACY_PROFILE else verify(plan, profile)
    claim(out/'outer.claim', {'plan_sha256':base.file_hash(out/'plan.json'), 'run_id':plan['run_id']})
    private = create_private_evidence_container(ROOT/'logs/phase6-private-evidence', evidence_kind='synthetic',
                                                task_id=profile.outer_task, created_at_utc=datetime.now(timezone.utc))
    durable(out/'outer-private-locator.json', {'path':str(private)}, exclusive=True)
    return outer([sys.executable, str(ROOT/profile.runner), '--output', str(out.resolve()), '--run'],
                 out/'outer', raw_directory=private, limit_seconds=1320)


def cli(profile=LEGACY_PROFILE):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare', action='store_true')
    group.add_argument('--run', action='store_true')
    group.add_argument('--supervise', action='store_true')
    args = parser.parse_args()
    try:
        if args.prepare:
            prepare(args.output, profile=profile)
            print('PREPARED')
            return 0
        result = supervise(args.output, profile) if args.supervise else run(args.output, profile)
        ok = result.get('status') == 'COMPLETE' if args.run else (
            result.get('exit_code') == 0 and result.get('ownership_complete') is True and result.get('owned_alive_after') == 0)
        print('COMPLETE' if ok else 'STOPPED')
        return 0 if ok else 2
    except Exception as error:
        print(error.code if isinstance(error, RunError) else 'PRIVATE_EVIDENCE_ERROR')
        return 2


def main():
    return cli()


if __name__ == '__main__':
    raise SystemExit(main())
