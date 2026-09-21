"""GC2: replay the reviewed IC2 choice and generate one grounding-closed output."""
from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import phase6_grounding_closed_probe as probe
from scripts import phase6_grounding_closed_replay as replay
from scripts import phase6_intent_choice_runner as ic2
from scripts import phase6_two_stage_probe_runtime as runtime
from scripts.phase6_context_probe import wire_bytes

base = ic2.base
GC2 = 'grounding_closed_v1'
EXTRA = ('scripts/phase6_grounding_closed_probe.py', 'tests/test_phase6_grounding_closed_probe.py',
         'scripts/phase6_grounding_closed_replay.py', 'scripts/phase6_grounding_closed_runner.py',
         'tests/test_phase6_grounding_closed_runner.py')
base.EXPERIMENT_SOURCE_DELTA[GC2] = base.EXPERIMENT_SOURCE_DELTA[ic2.IC2] | frozenset(EXTRA)


@dataclass(frozen=True)
class ReplayedProjection:
    original: object
    choice: dict
    binding: dict

    def __getattr__(self, name):
        return getattr(self.original, name)


def sources():
    return {**ic2.sources(), **{p: base.file_hash(ROOT/p) for p in EXTRA}}


def fixed_contract():
    return {**ic2.fixed_contract(), 'experiment': GC2, 'task_id': 'T462',
            'max_provider_calls': 32, 'new_choice_calls': 0, 'reused_choice_count': 32,
            'stage_contract': runtime.asdict(runtime.GC2_CONTRACT)}


def entry(case, p, choice, binding):
    original = base.body_for(p, base.PROFILES['qw9'][0])
    item = {'case_id': case.case_id, 'category': case.category,
        'baseline_common_input_sha256': base.common_hash(original),
        'baseline_input_sha256': base.digest(original), 'projection_sha256': p.prompt_sha256,
        'reused_choice_binding': binding, 'selected_kind': choice['speech_act_kind']}
    try:
        body = probe.output_body(original, choice, p)
        item.update(ic2.output_identity(body), grounding_status='LEGAL')
    except probe.NoLegalGrounding:
        item['grounding_status'] = 'NO_LEGAL_GROUNDING'
    return item


def construct_replay(current, review_sha):
    data = replay.artifact_data()
    binding = replay.source_binding(data['plan'], current, review_sha)
    projections = [(case, base.project(case, 'baseline')) for case in base.cases()]
    bound = replay.replay_cases(projections, data)
    return data['plan'], binding, bound


def prepare(out, baseline_source, review_sha):
    if (out/'plan.json').exists():
        raise base.StopComparison('PLAN_EXISTS')
    current = sources()
    old, binding, bound = construct_replay(current, review_sha)
    if base.file_hash(ic2.CONFIG) != ic2.CONFIG_SHA or base.file_hash(ic2.TOKENIZER) != ic2.TOKENIZER_SHA:
        raise base.StopComparison('FROZEN_FILE_CHANGED')
    plan = {**fixed_contract(), 'source': current, 'replay': binding,
            'cases': [entry(*items) for items in bound],
            'profiles': copy.deepcopy(old['profiles']), 'tokenizer': copy.deepcopy(old['tokenizer'])}
    plan['baseline'] = base.baseline_binding(plan, baseline_source)
    # Recheck exact model/runtime/tokenizer identities even before claiming any run.
    verify_identities(plan)
    out.mkdir(parents=True, exist_ok=True)
    runtime.write_json(out/'plan.json', plan, exclusive=True)
    print('PLAN_READY', len(bound), flush=True)


def verify_identities(plan):
    profile = plan['profiles']['qw9']
    if profile['argv'] != base.launch_args('qw9') or not profile['available']:
        raise base.StopComparison('RUNTIME_UNAVAILABLE')
    for item in [profile['model'], *profile['runtime_files'], plan['tokenizer']]:
        if base.file_identity(item['path']) != item:
            raise base.StopComparison('FROZEN_FILE_CHANGED')


def verify(plan):
    if any(wire_bytes(plan.get(key)) != wire_bytes(value) for key, value in fixed_contract().items()):
        raise base.StopComparison('PLAN_CONTRACT_CHANGED')
    current = sources()
    if current != plan['source'] or base.file_hash(ic2.CONFIG) != ic2.CONFIG_SHA:
        raise base.StopComparison('FROZEN_SOURCE_CHANGED')
    old, binding, bound = construct_replay(current, plan['replay']['tool_review_sha256'])
    if binding != plan['replay'] or [entry(*items) for items in bound] != plan['cases']:
        raise base.StopComparison('REPLAY_BINDING_MISMATCH')
    if plan['profiles'] != old['profiles'] or plan['tokenizer'] != old['tokenizer']:
        raise base.StopComparison('REPLAY_BINDING_MISMATCH')
    if base.baseline_binding(plan, plan['baseline']['directory']) != plan['baseline']:
        raise base.StopComparison('BASELINE_MISMATCH')
    verify_identities(plan)
    if not base.port_free():
        raise base.StopComparison('PORT_IN_USE')
    return plan['profiles']['qw9'], [(c, ReplayedProjection(p, choice, binding)) for c,p,choice,binding in bound]


def initial_row(case, p):
    row = ic2.initial_row(case, p.original)
    row.update(status='OUTPUT_NOT_STARTED', choice_schema_pass=True,
        selected_kind=p.choice['speech_act_kind'], binding=p.binding,
        choice={'status':'REUSED', 'source_raw_sha256':p.binding['choice_raw_sha256'],
                'source_wire_sha256':p.binding['choice_wire_sha256']})
    return row


def process_case(context, case, p, row):
    from jsonschema import Draft202012Validator
    original = base.body_for(p.original, base.PROFILES['qw9'][0])
    try:
        body = probe.output_body(original, p.choice, p.original)
    except probe.NoLegalGrounding:
        row.update(status='NO_LEGAL_GROUNDING', generation_status='INVALID', error_kind='NO_LEGAL_GROUNDING')
        return
    text = ic2.dispatch(context, 'output', body, row)
    if text is None:
        row.update(status='OUTPUT_ERROR', generation_status='ERROR')
        return
    row.update(candidate_schema_pass=False, kind_match_pass=False)
    try:
        final = ic2.probe.strict_json(text)
        discussion = final.get('discussion')
        act = discussion.get('speech_act') if type(discussion) is dict else None
        row['kind_match_pass'] = type(act) is dict and act.get('kind') == p.choice['speech_act_kind']
        row['candidate_schema_pass'] = Draft202012Validator(body['response_format']['json_schema']['schema']).is_valid(final)
        if not row['candidate_schema_pass'] or not row['kind_match_pass']:
            raise ValueError('OUTPUT_SCHEMA')
        probe.validate_final(text, p.choice, p.original)
        row['legacy_validator_pass'] = True
    except (ValueError, base.DecisionValidationError):
        if row['candidate_schema_pass'] and row['kind_match_pass']:
            row['legacy_validator_pass'] = False
        row.update(status='OUTPUT_INVALID', generation_status='INVALID')
        return
    return runtime.CaseOutcome(text, row['binding'])


def final_source(plan):
    return {'source_unchanged': sources() == plan['source'],
            'config_unchanged': base.file_hash(ic2.CONFIG) == ic2.CONFIG_SHA}


def run(out):
    return runtime.run_replayed_choice_output_probe(out, contract=runtime.GC2_CONTRACT,
        callbacks=runtime.TwoStageCallbacks(verify, initial_row, process_case, final_source))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare', action='store_true')
    mode.add_argument('--run', choices=['qw9'])
    parser.add_argument('--baseline-source', type=Path)
    parser.add_argument('--tool-review-sha')
    args = parser.parse_args()
    if args.prepare:
        if not args.tool_review_sha or not args.baseline_source:
            parser.error('prepare requires baseline source and reviewed source identity')
        prepare(args.output, args.baseline_source, args.tool_review_sha)
    else:
        if args.baseline_source is not None or args.tool_review_sha is not None:
            parser.error('freeze inputs are prepare-only')
        raise SystemExit(run(args.output))
