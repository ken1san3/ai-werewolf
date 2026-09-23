"""One frozen, local Qwen minimal-output measurement. No game or retry."""
from __future__ import annotations

import argparse
from collections import Counter
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


def source_identity():
    # The old plan's exact hashed list is reused, never expanded by a wildcard.
    paths = tuple(baseline_plan()['source']) + EXTRA_SOURCES
    return {p: base.file_hash(ROOT/p) for p in dict.fromkeys(paths)}


def git_head(*, clean=False):
    def git(*args):
        item = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, check=True)
        return item.stdout.decode('utf-8').strip()
    require(git('branch', '--show-current') == 'experiment/speech-act-kind-first-20260919', 'SOURCE_CHANGED')
    if clean:
        require(not git('status', '--porcelain', '--', *source_identity()), 'SOURCE_CHANGED')
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


def frozen_case(suite):
    from scripts import phase6_minimal_suite_adapter as adapter
    body = adapter.candidate_body(suite, Path(base.PROFILES['qw9'][0]).name)
    raw = adapter.candidate_wire(body)
    require(raw == wire_bytes(body) and body['max_tokens'] == 512, 'CASE_BINDING_CHANGED')
    return dict(case_id=suite.case.case_id, input_sha256=base.digest(body), wire_sha256=probe.sha256(raw),
                schema_sha256=base.digest(body['response_format']['json_schema']['schema']),
                messages_sha256=base.digest(body['messages']), wire_bytes=len(raw),
                schema_bytes=len(wire_bytes(body['response_format']['json_schema']['schema'])))


def approval_identity():
    approval = probe._strict_json((ROOT/TOOL_APPROVAL).read_bytes())
    require(approval.get('verdict') == 'APPROVED' and approval.get('source') == source_identity()
            and approval.get('design_sha256') == base.file_hash(ROOT/DESIGN), 'SOURCE_CHANGED')
    return {p:base.file_hash(ROOT/p) for p in DOC_REFS}


def prepare(out, baseline_aggregate_path=None):
    out = Path(out)
    baseline = quality.baseline_bytes()
    if baseline_aggregate_path is not None:
        require(Path(baseline_aggregate_path).read_bytes() == baseline, 'SOURCE_CHANGED')
    head = git_head(clean=True)
    sources, approvals, external = source_identity(), approval_identity(), external_identity()
    prepared = suite_cases()
    plan = dict(version=1, experiment=EXPERIMENT, task_id=TASK, runner=RUNNER,
                run_id=uuid.uuid4().hex, head=head, source=sources, approvals=approvals, external=external,
                argv=base.launch_args('qw9'), sampling=base.SAMPLING, context=8192,
                request_seconds=60, model_seconds=1200, load_seconds=180, outer_seconds=1320,
                max_provider_calls=32, retry=0, repair=0, fallback=0,
                baseline_sha256=probe.sha256(baseline), baseline_artifacts=BASELINE_HASHES,
                legacy_instruction_present=True, cases=[frozen_case(s) for s in prepared])
    out.mkdir(parents=True, exist_ok=False)
    durable(out/'baseline-aggregate.json', baseline, exclusive=True)
    durable(out/'plan.json', plan, exclusive=True)
    return plan


def verify(plan):
    require(plan.get('experiment') == EXPERIMENT and plan.get('task_id') == TASK and plan.get('runner') == RUNNER
            and plan.get('version') == 1 and type(plan.get('run_id')) is str and len(plan['run_id']) == 32, 'SOURCE_CHANGED')
    expected = dict(sampling=base.SAMPLING, context=8192, request_seconds=60, model_seconds=1200,
                    load_seconds=180, outer_seconds=1320, max_provider_calls=32, retry=0, repair=0, fallback=0)
    require(all(wire_bytes(plan.get(k)) == wire_bytes(v) for k,v in expected.items()), 'SOURCE_CHANGED')
    require(plan['head'] == git_head(clean=True) and plan['source'] == source_identity()
            and plan['approvals'] == approval_identity(), 'SOURCE_CHANGED')
    require(plan['external'] == external_identity() and plan['argv'] == base.launch_args('qw9'), 'RUNTIME_CHANGED')
    require(plan['baseline_sha256'] == probe.sha256(quality.baseline_bytes())
            and plan['baseline_artifacts'] == BASELINE_HASHES and plan['legacy_instruction_present'] is True, 'SOURCE_CHANGED')
    suites = suite_cases()
    require(plan['cases'] == [frozen_case(s) for s in suites], 'CASE_BINDING_CHANGED')
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


def initial_row(suite, frozen):
    return {**frozen, 'category':suite.case.category, 'trigger':suite.binding.authority_without_update['trigger'],
            'status':'NOT_STARTED', 'error':None, 'call_consumed':0, 'raw_sha256':None,
            'mechanical_status':'UNKNOWN', 'applicability':'UNRESOLVED', 'speech_act':None,
            'peer_long_exact_copy':None, 'semantic':None, 'hard':None, 'style':None}


def measure_prompt(body, private, case_id, label):
    raw = wire_bytes(body)
    durable(private/(case_id+'.'+label+'.request.bin'), raw, exclusive=True)
    def sink(rendered):
        require(type(rendered) is str and all(m['content'] in rendered for m in body['messages']), 'TEMPLATE_INVALID')
        durable(private/(case_id+'.'+label+'.rendered.bin'), rendered.encode('utf-8'), exclusive=True)
    try:
        measured = base.count_prompt(body, wire_payload=raw, private_sink=sink)
    except base.StopComparison as error:
        code = str(error)
        raise RunError(code if code in ('CONTEXT_OVERFLOW', 'MODEL_TIME_BUDGET') else 'TEMPLATE_INVALID') from None
    measured['rendered_bytes_sha256'] = base.file_hash(private/(case_id+'.'+label+'.rendered.bin'))
    return measured


def stage(suite, row, private, identity, save, budget):
    from scripts import phase6_minimal_suite_adapter as adapter
    from ai_client.brain.controller import _cross_player_public_copy
    require(row['call_consumed'] == 0 and budget['calls'] < 32, 'PLAN_EXISTS')
    require(base._RUN_DEADLINE is not None and base._RUN_DEADLINE-time.monotonic() >= 60, 'MODEL_TIME_BUDGET')
    body = adapter.candidate_body(suite, Path(base.PROFILES['qw9'][0]).name)
    payload = adapter.candidate_wire(body)
    require(frozen_case(suite) == {k:row[k] for k in frozen_case(suite)}, 'CASE_BINDING_CHANGED')
    full = measure_prompt(body, private, row['case_id'], 'full')
    # Keep this dispatch contract explicit in addition to the native helper's gate.
    require(type(full['prompt_tokens_actual']) is int and full['prompt_tokens_actual'] >= 0, 'TOKEN_MISMATCH')
    require(full['prompt_tokens_actual']+512+1 <= 8192, 'CONTEXT_OVERFLOW')
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
        checked = probe.validate_suite(raw, suite.binding)
        value = probe._strict_json(raw)
        refs = [probe._ref_key(g['ref']) for g in value['grounding']]
        text = value['utterance']
        row.update(status='COMPLETE', mechanical_status='PASS', applicability=checked.applicability.removeprefix('APPLICABILITY_'),
                   speech_act=value['speech_act']['kind'], grounding_item_count=len(refs),
                   distinct_grounding_ref_count=len(set(refs)), duplicate_ref_occurrences=len(refs)-len(set(refs)),
                   private_before_sha256=checked.private_before_sha256, private_after_sha256=checked.private_after_sha256,
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


def run(out):
    out = Path(out)
    plan = probe._strict_json((out/'plan.json').read_bytes())
    suites = verify(plan)
    require((out/'baseline-aggregate.json').read_bytes() == quality.baseline_bytes(), 'SOURCE_CHANGED')
    host_idle()
    identity = dict(task_id=TASK, experiment=EXPERIMENT, runner=RUNNER,
                    plan_sha256=base.file_hash(out/'plan.json'), run_id=plan['run_id'])
    claim(out/'minimal-output-v1.run.claim', identity)
    result = {**identity, 'status':'STARTING', 'clock_domain':'REAL', 'retry':0, 'repair':0,
              'provider_calls':0, 'rows':[initial_row(s,f) for s,f in zip(suites, plan['cases'])]}
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
                                                    task_id=TASK, created_at_utc=datetime.now(timezone.utc))
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
            stage(suite, row, private, identity, save, budget)
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
            verify(plan)
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


def supervise(out):
    from scripts.phase6_probe_outer import supervise as outer
    out = Path(out)
    plan = probe._strict_json((out/'plan.json').read_bytes())
    verify(plan)
    claim(out/'outer.claim', {'plan_sha256':base.file_hash(out/'plan.json'), 'run_id':plan['run_id']})
    private = create_private_evidence_container(ROOT/'logs/phase6-private-evidence', evidence_kind='synthetic',
                                                task_id='T499OUTER', created_at_utc=datetime.now(timezone.utc))
    durable(out/'outer-private-locator.json', {'path':str(private)}, exclusive=True)
    return outer([sys.executable, str(ROOT/RUNNER), '--output', str(out.resolve()), '--run'],
                 out/'outer', raw_directory=private, limit_seconds=1320)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare', action='store_true')
    group.add_argument('--run', action='store_true')
    group.add_argument('--supervise', action='store_true')
    args = parser.parse_args()
    try:
        if args.prepare:
            prepare(args.output)
            print('PREPARED')
            return 0
        result = supervise(args.output) if args.supervise else run(args.output)
        ok = result.get('status') == 'COMPLETE' if args.run else (
            result.get('exit_code') == 0 and result.get('ownership_complete') is True and result.get('owned_alive_after') == 0)
        print('COMPLETE' if ok else 'STOPPED')
        return 0 if ok else 2
    except Exception as error:
        print(error.code if isinstance(error, RunError) else 'PRIVATE_EVIDENCE_ERROR')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
