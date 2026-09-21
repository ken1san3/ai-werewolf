"""Strict replay of the single immutable IC2 choice run; no provider fallback."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts import phase6_intent_choice_runner as ic2
from scripts.phase6_context_probe import wire_bytes

base = ic2.base
ROOT = ic2.ROOT
OLD = ROOT/'logs/t448-quality-cycles/frozen-ic2'
SAVED_SOURCE = ROOT/'logs/t448-quality-cycles/ic2-approved-source'
ARTIFACTS = {
    'plan': ('logs/t448-quality-cycles/frozen-ic2/plan.json', 'fd333d24b9907baaa6509abbbf33f550988214a8930b9bd8cd9ccadd744c14da'),
    'result': ('logs/t448-quality-cycles/frozen-ic2/qw9-results.json', 'cf1924995825431a6797486a528468a25ac2f337d70e5bfa7da3fb2b7dc3c3e2'),
    'safe': ('Docs/ai/handoffs/tasks/T458_SAFE_RESULTS.json', '43acb2a1fa7887063d7a74a753eb4eb9f9de355b8106455d3ec9467cc91a2134'),
    'binding': ('logs/t448-quality-cycles/frozen-ic2/t458-private-binding-safe.json', 'a54b72270a8b217845c8520254cff0f0ae183aee0eba154b15d33bbdbe3a7df8'),
}
CI_REVIEW = ('Docs/ai/handoffs/tasks/T461_CI_PORTABILITY_REVIEW.md', 'a629fbb8240420d4b5d0ba827a69366572c1498641596171b2ff93fe5427837c')
CI_SOURCE = {
    'tests/test_phase6_two_stage_probe_runtime.py': 'b5cd8c822e42a743b6c9ff126f1111e29c5e81334d460688ee2156ce0cee5e46',
    'tests/fixtures/phase6_p2_runtime_golden.json': 'af32f20946ad3aeec6dbf7cd8386ad52842b2fdf13b473aebc47a135e5e5ccc7',
}
TOOL_REVIEW = 'Docs/ai/handoffs/tasks/T461_GROUNDING_CLOSED_TOOL_REVIEW.md'
DELTA_PATHS = frozenset({
    'scripts/phase6_two_stage_probe_runtime.py', 'scripts/phase6_probe_outer.py',
    'tests/test_phase6_two_stage_probe_runtime.py', 'tests/test_phase6_probe_outer.py'})


def require(value):
    if not value:
        raise base.StopComparison('REPLAY_BINDING_MISMATCH')


def load(path):
    return ic2.probe.strict_json(Path(path).read_text(encoding='utf-8'))


def artifact_data():
    values = {}
    for name, (path, sha) in ARTIFACTS.items():
        require(base.file_hash(ROOT/path) == sha)
        values[name] = load(ROOT/path)
    return values


def source_binding(old, current, review_sha):
    """Exact old -> portability -> GC2 hash chain, with separate review identities."""
    require(base.file_hash(ROOT/CI_REVIEW[0]) == CI_REVIEW[1])
    require(base.file_hash(ROOT/TOOL_REVIEW) == review_sha and len(review_sha) == 64)
    require(base.file_hash(SAVED_SOURCE/'manifest.json') == 'd5d9b9708073383c965352b26b31cdfa302fd0b1b150fef951edd93d87075b02')
    saved = load(SAVED_SOURCE/'manifest.json')
    require(len(saved) == 9)
    for path, sha in saved.items():
        require(old['source'].get(path) == sha)
        require(base.file_hash(SAVED_SOURCE/Path(path).name) == sha)
    require(set(old['source']) <= set(current))
    entries = []
    for path, original in old['source'].items():
        previous = original
        if path in CI_SOURCE:
            previous = CI_SOURCE[path]
            entries.append(dict(path=path, old_sha256=original, new_sha256=previous,
                reason='CI_MOCK_ARGV_PORTABILITY', review_sha256=CI_REVIEW[1]))
        if current[path] != previous:
            require(path in DELTA_PATHS)
            entries.append(dict(path=path, old_sha256=previous, new_sha256=current[path],
                reason='GC2_REPLAY_OUTPUT_LIFECYCLE', review_sha256=review_sha))
    return {'artifacts': {name: sha for name, (_, sha) in ARTIFACTS.items()},
            'source_delta': entries, 'tool_review_sha256': review_sha,
            'old_source_manifest_sha256': base.file_hash(SAVED_SOURCE/'manifest.json')}


def bind_case(case, projection, entry, row, safe, record, request, consumed):
    """Validate existing private bytes internally, returning only choice and safe binding."""
    original = base.body_for(projection, base.PROFILES['qw9'][0])
    body = ic2.probe.choice_body(original)
    cid = case.case_id
    require(entry == ic2.entry(case, projection))
    require(all(v['case_id'] == cid for v in (entry, row, safe, record, consumed)))
    require(record['stage'] == consumed['stage'] == 'choice')
    require(request == wire_bytes(body) == wire_bytes(record['input']))
    sha = hashlib.sha256(request).hexdigest()
    require(sha == row['choice']['wire_sha256'] == safe['choice_wire_sha256'] == consumed['wire_sha256'] == record['wire_sha256'])
    require(consumed['input_sha256'] == row['choice']['input_sha256'] == base.digest(body))
    require(type(consumed['max_tokens']) is int and consumed['max_tokens'] == 32)
    require(consumed['status'] == 'STARTED' and row['choice']['status'] == 'GENERATED')
    text = record['final_content']
    require(type(text) is str)
    raw_sha = hashlib.sha256(text.encode()).hexdigest()
    require(raw_sha == row['choice']['final_output_sha256'] == safe['choice_output_sha256'])
    choice = ic2.probe.validate_choice(text, projection)
    require(choice['speech_act_kind'] == row['selected_kind'] == safe['selected_kind'])
    require(record['finish_reason'] == row['choice']['finish_reason'] == 'stop')
    require(safe['baseline_input_sha256'] == row['baseline_input_sha256'] == base.digest(original))
    require(safe['projection_sha256'] == row['projection_sha256'] == projection.prompt_sha256)
    binding = ic2.choice_binding(case, projection, original, body, text, choice)
    require(binding == row['binding'])
    return choice, binding


def replay_cases(projections, data):
    old, result, safe, summary = (data[key] for key in ('plan', 'result', 'safe', 'binding'))
    require(result['plan_sha256'] == ARTIFACTS['plan'][1])
    require(safe['integrity']['result_sha256'] == ARTIFACTS['result'][1])
    require(safe['integrity']['binding_safe_sha256'] == ARTIFACTS['binding'][1])
    require(safe['binding'] == summary and summary['consumed_matches'] == 64)
    require(result['status'] == 'COMPLETE' and result['source_unchanged'] and result['config_unchanged'])
    require(result['owned_processes_remaining'] == 0 and result['new_provider_calls'] == 64)
    require(result['runtime'] == old['baseline']['runtime'])
    ids = [c.case_id for c, _ in projections]
    require(len(ids) == len(set(ids)) == 32)
    groups = [old['cases'], result['rows'], safe['cases']]
    require(all([v['case_id'] for v in group] == ids for group in groups))
    # One fixed locator, never directory discovery or latest-cache fallback.
    private = Path(load(OLD/'qw9-locator.json')['path'])
    records = [ic2.probe.strict_json(line) for line in (private/'raw.jsonl').read_text(encoding='utf-8').splitlines()]
    require(len(records) == summary['raw_records'])
    require(all(v['stage'] in ('choice', 'output', 'final') and v['case_id'] in ids for v in records))
    choices = [v for v in records if v['stage'] == 'choice']
    require([v['case_id'] for v in choices] == ids)
    bound = []
    for (case, p), entry, row, safe_row, raw in zip(projections, *groups, choices, strict=True):
        choice, binding = bind_case(case, p, entry, row, safe_row, raw,
            (private/(case.case_id+'.choice.request.bin')).read_bytes(),
            load(private/(case.case_id+'.choice.consumed.json')))
        bound.append((case, p, choice, binding))
    return bound
