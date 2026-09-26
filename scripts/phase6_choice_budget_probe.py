"""T507 offline vocabulary costs and mock-only completion contract; no provider CLI."""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jsonschema import Draft202012Validator
from scripts import phase6_recovery_runner as recovery

gb = recovery.gb
base = recovery.base
KINDS = ('ANSWER', 'CLAIM', 'NONE', 'OPINION_CHANGE', 'QUESTION', 'REBUTTAL', 'RELATION_HYPOTHESIS')
CHOICE, OUTPUT, CONTEXT, K = 64, 448, 8192, 3
SEED = 4242027
TOKENIZER = Path('C:/AIagent/llama-tokenize.exe')
TOKENIZER_SHA = 'a0fbd34a8a3f25fc0f41cbac1ec67e8395a5ef940db33bd07307b5e3dc8cd6a1'
REFERENCE_SHA = '8b0dbe55ec2b9cbf5d604a795676052ebae436a07b0b75046c2c7d36c5865fdc'
DESIGN = ROOT/'Docs/ai/design/PHASE6_CHOICE_BUDGET_COMPATIBILITY_DESIGN.md'
DESIGN_SHA = '4e46c3a573abaefc7a13787593602d44e0dadbbc3435d48ef5fedcdaf6ba4308'
REVIEW = ROOT/'Docs/ai/handoffs/tasks/T507_CHOICE_BUDGET_DESIGN_REVIEW.md'
REVIEW_SHA = '09728adaa874b0737161d0b94da2c9a9f52560b20f321c2e7ad53a6cff9b97a6'


def digest(value):
    return hashlib.sha256(value).hexdigest()


def fixtures():
    """Cost-only toy schema, never inserted into an actual authority projection."""
    schema = deepcopy(gb.choice_schema(base.project(base.cases()[0], 'baseline')))
    if tuple(sorted(schema['properties']['speech_act_kind']['enum'])) != KINDS:
        raise ValueError('KIND_DRIFT')
    schema['properties']['authoritative_fact_ids']['items']['enum'] = ['f000', 'f063']
    rows = []
    for kind in KINDS:
        for count in range(3):
            value = dict(speech_act_kind=kind, authoritative_fact_ids=['f000', 'f063'][:count])
            for order in ('kind-first', 'facts-first'):
                ordered = value if order == 'kind-first' else dict(reversed(list(value.items())))
                for layout in ('compact', 'pretty'):
                    raw = json.dumps(ordered, indent=2) if layout == 'pretty' else json.dumps(ordered, separators=(',', ':'))
                    Draft202012Validator(schema).validate(gb.strict_json(raw))
                    rows.append(dict(kind=kind, facts=count, order=order, layout=layout, raw=raw,
                                     raw_sha256=digest(raw.encode('utf-8')), byte_count=len(raw.encode('utf-8'))))
    return rows


def actual_legality():
    """All 32 existing projections; no toy refs or inferred authority are added."""
    count = 0
    for _, p in recovery.entries():
        before = recovery.canonical_json_bytes(p.canonical_input)
        ids = [x['id'] for x in gb.fact_catalog(p)]
        if len(ids) < 2:
            return False
        for kind in KINDS:
            for size in range(3):
                value = dict(speech_act_kind=kind, authoritative_fact_ids=ids[:size])
                if gb.validate_choice(json.dumps(value), p) != value:
                    return False
            try:
                gb.validate_choice(json.dumps(dict(speech_act_kind=kind, authoritative_fact_ids=['f999'])), p)
            except ValueError:
                pass
            else:
                return False
        if before != recovery.canonical_json_bytes(p.canonical_input):
            return False
        count += 1
    return count == 32


def native_count(executable, model, row, *, identity_ok, run=None):
    argv = [str(executable), '-m', str(model), '--stdin', '--ids', '--no-bos', '--no-escape',
            '--no-parse-special', '--offline', '-ngl', '0', '--device', 'none']
    raw = row['raw'].encode('utf-8')
    result = {k: v for k, v in row.items() if k != 'raw'}
    result.update(status='UNKNOWN', tokens=None, argv=argv, wire_sha256=digest(raw), reason='IDENTITY')
    if not identity_ok:
        return result
    if result['wire_sha256'] != row['raw_sha256']:
        result['reason'] = 'WIRE_HASH'
        return result
    try:
        completed = (run or subprocess.run)(argv, input=raw, capture_output=True, timeout=30,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if completed.returncode:
            result['reason'] = 'EXIT'
            return result
        ids = json.loads(completed.stdout.decode('utf-8'))
        if not isinstance(ids, list) or not ids or any(type(x) is not int or x < 0 for x in ids):
            raise ValueError('IDS')
    except subprocess.TimeoutExpired:
        result['reason'] = 'TIMEOUT'
        return result
    except (OSError, ValueError, UnicodeError):
        result['reason'] = 'TOKENIZER_OR_IDS'
        return result
    result.update(status='KNOWN', tokens=len(ids), reason='NONE', token_ids_sha256=digest(completed.stdout))
    return result


def capacity(rows, *, identity_ok, legality_ok):
    expected = {x['raw_sha256'] for x in fixtures()}
    if not identity_ok or any(x['status'] != 'KNOWN' for x in rows):
        return 'OFFLINE_GATE_UNKNOWN'
    if (not legality_ok or len(rows) != 84 or {x['raw_sha256'] for x in rows} != expected
            or any(type(x['tokens']) is not int or x['tokens'] < 1 for x in rows)):
        return 'OFFLINE_GATE_CLOSED'
    exemplar = next(x for x in rows if x['kind'] == 'OPINION_CHANGE' and x['facts'] == 2
                    and x['layout'] == 'pretty' and x['order'] == 'facts-first')
    maximum = max(x['tokens'] for x in rows)
    if maximum != 51 or exemplar['tokens'] != 48 or ((maximum+1+15)//16)*16 != CHOICE:
        return 'OFFLINE_GATE_CLOSED'
    return 'OFFLINE_CAPACITY_CANDIDATE' if CHOICE+OUTPUT == 512 else 'OFFLINE_GATE_CLOSED'


def context_fits(prompt_tokens, budget):
    return (type(prompt_tokens) is int and prompt_tokens >= 0 and budget in (CHOICE, OUTPUT)
            and prompt_tokens+budget+1 <= CONTEXT)


@dataclass
class MockTransport:
    """In-memory replies only. No sockets, HTTP, game state, or process supervision."""
    replies: list
    prompt_tokens: int = 2000
    requests: list = field(default_factory=list)
    seen: set = field(default_factory=set)

    def send(self, body):
        key = digest(recovery.wire_bytes(body))
        if key in self.seen:
            raise ValueError('DUPLICATE_DISPATCH')
        if len(self.requests) >= 128:
            raise ValueError('CALL_CAP')
        self.seen.add(key)
        self.requests.append(deepcopy(body))
        return self.replies.pop(0) if self.replies else (None, None)


def mock_case(case, p, transport):
    if type(transport) is not MockTransport:
        raise TypeError('MOCK_ONLY')
    row = dict(case_id=case.case_id, choice_status='CHOICE_NOT_RUN', status='NOT_RUN',
               calls=0, output_attempts=[], accepted=None, semantic_outcome=0, content_status='UNKNOWN')
    body = recovery.baseline_body(p, 'gm12', SEED)
    choice_request = gb.choice_body(body, p)
    choice_request['max_tokens'] = CHOICE
    if not context_fits(transport.prompt_tokens, CHOICE):
        return row
    raw, finish = transport.send(choice_request)
    row['calls'] += 1
    if finish == 'length':
        status = 'CHOICE_LENGTH'
    elif raw is None or finish != 'stop':
        status = 'CHOICE_UNKNOWN'
    elif raw == '':
        status = 'CHOICE_EMPTY'
    else:
        try:
            gb.strict_json(raw)
        except ValueError:
            status = 'CHOICE_JSON_INVALID'
        else:
            try:
                choice = gb.validate_choice(raw, p)
            except ValueError:
                status = 'CHOICE_SCHEMA_INVALID'
            else:
                status = 'CHOICE_ACCEPTED'
    row.update(choice_status=status, status=status)
    if status != 'CHOICE_ACCEPTED':
        return row
    row['choice_sha256'] = digest(raw.encode('utf-8'))
    try:
        output = gb.output_body(body, choice, p)
    except gb.gc2.NoLegalGrounding:
        row['status'] = 'NO_LEGAL_GROUNDING'
        return row
    output['max_tokens'] = OUTPUT
    for attempt in range(K):
        output['seed'] = SEED + 1009*attempt
        if not context_fits(transport.prompt_tokens, OUTPUT):
            row['status'] = 'OUTPUT_NOT_RUN'
            return row
        text, finish = transport.send(output)
        row['calls'] += 1
        if finish == 'length':
            reject = 'OUTPUT_LENGTH'
        elif text is None or finish != 'stop':
            reject = 'OUTPUT_UNKNOWN'
        else:
            checked = recovery.mechanical(case, p, text, choice)
            reject = checked['reject_code']
        row['output_attempts'].append(dict(reject_code=reject, choice_sha256=row['choice_sha256']))
        if reject == 'NONE':
            row.update(status='ACCEPTED', accepted=text, content_status='GENERATED')
            return row
        if reject == 'OUTPUT_UNKNOWN':
            row['status'] = reject
            return row
    row['status'] = 'OUTPUT_K_EXHAUSTED'
    return row


def mock_suite(entries, transport):
    if len(entries) != 32 or len({c.case_id for c, _ in entries}) != 32:
        raise ValueError('CASE_SET')
    rows, stopped = [], False
    for case, projection in entries:
        if stopped:
            rows.append(dict(case_id=case.case_id, choice_status='CHOICE_NOT_RUN', status='NOT_RUN',
                             calls=0, accepted=None, semantic_outcome=0, content_status='UNKNOWN'))
            continue
        row = mock_case(case, projection, transport)
        rows.append(row)
        stopped = row['choice_status'] != 'CHOICE_ACCEPTED'
    return rows


def native_measure(out, reference):
    """One exclusive offline matrix; completed/partial directories are never overwritten."""
    out.mkdir(parents=True, exist_ok=False)
    if (base.file_hash(DESIGN) != DESIGN_SHA or base.file_hash(REVIEW) != REVIEW_SHA
            or base.file_hash(reference) != REFERENCE_SHA):
        raise ValueError('APPROVAL_OR_REFERENCE_BINDING')
    plan = json.loads(reference.read_text(encoding='utf-8'))
    frozen = dict(source=recovery.sources(), design_sha256=DESIGN_SHA, review_sha256=REVIEW_SHA,
                  reference_sha256=REFERENCE_SHA, profiles={}, tokenizer=None, tokenizer_build=None)
    try:
        frozen['tokenizer'] = base.file_identity(TOKENIZER)
        if frozen['tokenizer']['sha256'] != TOKENIZER_SHA:
            raise ValueError('TOKENIZER_IDENTITY')
        version = subprocess.run([str(TOKENIZER), '--version'], capture_output=True, timeout=30,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        build = (version.stdout+version.stderr).decode('utf-8')
        if version.returncode or '10697' not in build or '093adb242' not in build:
            raise ValueError('TOKENIZER_BUILD')
        frozen['tokenizer_build'] = '10697/093adb242'
        for model in ('qw9', 'gm12'):
            profile = plan['profiles'][model]
            current = [base.file_identity(x['path']) for x in [profile['model'], *profile['runtime_files']]]
            frozen['profiles'][model] = current
        config = base.file_identity(recovery.CONFIG)
        frozen['config'] = config
    except (OSError, ValueError, subprocess.TimeoutExpired):
        config = None
    recovery.write(out/'freeze.json', frozen, exclusive=True)
    start = time.monotonic()
    all_rows = []
    legality = actual_legality()
    stable = {}
    for model in ('qw9', 'gm12'):
        profile = plan['profiles'][model]
        identity_ok = (frozen['tokenizer_build'] is not None and config == plan['config']
                       and frozen['profiles'].get(model) == [profile['model'], *profile['runtime_files']])
        for item in fixtures():
            row = native_count(TOKENIZER, profile['model']['path'], item,
                               identity_ok=identity_ok and time.monotonic()-start < 360)
            row.update(model=model, identity_sha256=digest(recovery.wire_bytes(frozen['profiles'].get(model))))
            all_rows.append(row)
        try:
            stable[model] = (identity_ok and base.file_identity(TOKENIZER) == frozen['tokenizer']
                and [base.file_identity(x['path']) for x in frozen['profiles'][model]] == frozen['profiles'][model]
                and base.file_identity(recovery.CONFIG) == config)
        except OSError:
            stable[model] = False
    unchanged = (recovery.sources() == frozen['source'] and base.file_hash(DESIGN) == DESIGN_SHA
                 and base.file_hash(REVIEW) == REVIEW_SHA)
    summaries = {}
    for model in ('qw9', 'gm12'):
        subset = [x for x in all_rows if x['model'] == model]
        summaries[model] = {layout: dict(known=sum(x['status'] == 'KNOWN' for x in subset if x['layout'] == layout),
            min=min((x['tokens'] for x in subset if x['layout'] == layout and x['status'] == 'KNOWN'), default=None),
            max=max((x['tokens'] for x in subset if x['layout'] == layout and x['status'] == 'KNOWN'), default=None))
            for layout in ('compact', 'pretty')}
    gate = capacity([x for x in all_rows if x['model'] == 'gm12'], identity_ok=stable['gm12'] and unchanged,
                    legality_ok=legality)
    result = dict(rows=all_rows, summaries=summaries, gate=gate, identity_stable=stable,
        source_stable=unchanged, real_projection_legality=legality, real_duration_sec=time.monotonic()-start,
        clock_domain='REAL', provider_calls=0, inference_calls=0, choice=CHOICE, output=OUTPUT,
        eos_reserve=1, context=CONTEXT, provider_authorized=False,
        qwen_diagnostic='KNOWN' if stable['qw9'] and all(x['status'] == 'KNOWN' for x in all_rows if x['model'] == 'qw9')
                        else 'QWEN_DIAGNOSTIC_UNKNOWN', freeze_sha256=base.file_hash(out/'freeze.json'))
    recovery.write(out/'result.json', result, exclusive=True)
    recovery.write(out/'seal.json', dict(result_sha256=base.file_hash(out/'result.json')), exclusive=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', action='store_true', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reference-plan', type=Path, default=ROOT/'logs/t506-quality-recovery/program-v1/plan.json')
    args = parser.parse_args()
    report = native_measure(args.output, args.reference_plan)
    print(json.dumps(dict(gate=report['gate'], summaries=report['summaries'], provider_calls=0)))
    raise SystemExit(0 if report['gate'] == 'OFFLINE_CAPACITY_CANDIDATE' else 1)
