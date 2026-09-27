"""Finite T510 schema/output-vocabulary witness; never starts a provider."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import phase6_local_staged_probe as probe
from scripts import phase6_choice_budget_probe as tokenizer
from scripts import phase6_recovery_runner as r
from tests.test_phase6_local_staged_probe import fixture_legacy, speech_fixture
from tests.test_phase6_two_call_probe import projection, legacy

DESIGN = ROOT/'Docs/ai/design/PHASE6_LOCAL_STAGED_A_PROBE_DESIGN.md'
DESIGN_SHA = '4cedce90fecb3c81e33815341db71a0d8c79f6c7b8887800fd252914837e9e64'
REVIEW = ROOT/'Docs/ai/handoffs/tasks/T510_LOCAL_STAGED_A_DESIGN_REVIEW.md'
REVIEW_SHA = 'c0f35730219db72e34f5a316f1cc96ad18395ada8e5ef4e5f2cc935f9fe774b7'


def witness_sources():
    # Exact generation closure; runner/statistics do not create these witnesses.
    current = r.sources()
    selected = {k:v for k,v in current.items() if k.startswith(('ai_client/', 'protocol/', 'content/'))}
    names = [
        'scripts/phase6_local_staged_probe.py', 'scripts/phase6_local_staged_offline.py',
        'scripts/phase6_two_call_probe.py', 'scripts/phase6_choice_budget_probe.py',
        'scripts/phase6_recovery_runner.py', 'scripts/phase6_model_comparison.py',
        'scripts/phase6_conversation_suite.py', 'scripts/phase6_context_probe.py',
        'scripts/phase6_grounding_basis_probe.py',
    ]
    # All existing fixture dependencies, excluding the independent runner tests.
    selected.update({k:v for k,v in current.items() if k.startswith('tests/') and k != 'tests/test_phase6_local_staged_runner.py'})
    selected.update({k:current[k] for k in names})
    return selected


def fixtures():
    rows = []
    entries = [(c.case_id, p) for c,p in r.entries()]
    entries.append(('EXTRA_ABILITY', projection('ability')))
    examples=[]
    for case_id, p in entries:
        for kind in probe.p2._decision_branches(probe.plain(p.decision_schema)):
            original = fixture_legacy(p, kind) if case_id != 'EXTRA_ABILITY' else legacy(p, kind)
            examples.append((case_id,p,original))
    for act in probe.ACT_FIELDS:
        p,original,_=speech_fixture(act)
        examples.append(('EXTRA_ACT_'+act,p,original))
    for case_id,p,original in examples:
        kind=original['decision']['kind']
        plan, text = probe.from_legacy(original, p)
        if text is not None:
            cat=probe.catalog(p)
            plan['public_fact_ids']=[x['id'] for x in cat['public_facts'][:2]]
            plan['disclose_fact_ids']=[x['id'] for x in cat['owner_ability'][:1]]
            probe.validate_plan(probe.p2._json_text(plan),p)
        if text is not None:
            probe.presenter_input(plan, p)
        assert probe.validate_final(plan, None if text is None else json.dumps(text), p) == original
        for stage, obj, maximum in [('T', plan,384), *([] if text is None else [('P',text,128)])]:
            for layout in ('compact', 'pretty'):
                raw = (json.dumps(obj, ensure_ascii=False, indent=2) if layout == 'pretty'
                       else probe.canonical_json_bytes(obj).decode('utf-8'))
                rows.append(dict(case_id=case_id, action_kind=kind, stage=stage, layout=layout,
                    maximum=maximum, raw=raw, raw_sha256=r.sha(raw.encode('utf-8')),
                    byte_count=len(raw.encode('utf-8'))))
    return rows


def run(out, reuse=None, reuse_sha=None):
    if r.base.file_hash(DESIGN) != DESIGN_SHA or r.base.file_hash(REVIEW) != REVIEW_SHA:
        raise r.Stop('DESIGN_BINDING')
    out.mkdir(parents=True, exist_ok=False)
    profile = r.base.PROFILES['qw9']
    identities = dict(model=r.base.file_identity(profile[0]), tokenizer=r.base.file_identity(tokenizer.TOKENIZER),
                      config=r.base.file_identity(r.CONFIG))
    cache={}
    if reuse is not None:
        if r.base.file_hash(reuse/'result.json')!=reuse_sha:
            raise r.Stop('REUSE_BINDING')
        old=r.read(reuse/'result.json')
        prior_freeze=r.read(reuse/'freeze.json')
        if (r.base.file_hash(reuse/'freeze.json')!=old['freeze_sha256'] or
                prior_freeze['identities']!=identities or old['gate']!='OFFLINE_WITNESS_PASS'):
            raise r.Stop('REUSE_IDENTITY')
        for row in old['rows']:
            if row['status']=='KNOWN':
                cache[row['raw_sha256']]=row
    frozen = dict(design_sha256=DESIGN_SHA, review_sha256=REVIEW_SHA, source=witness_sources(),
                  identities=identities, cases=r.input_identity(), created_at_utc=r.stamp())
    frozen['reused_vocabulary_result_sha256']=reuse_sha
    r.write(out/'freeze.json', frozen, exclusive=True)
    valid = identities['tokenizer']['sha256'] == tokenizer.TOKENIZER_SHA
    reused_keys=set(cache)
    new_native=0
    results=[]
    for row in fixtures():
        key = row['raw_sha256']
        if key not in cache:
            cache[key] = tokenizer.native_count(tokenizer.TOKENIZER, profile[0], row, identity_ok=valid)
            new_native+=1
        result = {k:v for k,v in row.items() if k != 'raw'}
        result.update({k:cache[key][k] for k in ('status','tokens','reason','token_ids_sha256') if k in cache[key]})
        result['fits'] = result['status']=='KNOWN' and result['tokens'] <= row['maximum']
        result['reused_vocabulary']=key in reused_keys
        results.append(result)
    unchanged = frozen['source'] == witness_sources() and all(
        r.base.file_identity(value['path']) == value for value in identities.values())
    summary = dict(task='T510', gate='OFFLINE_WITNESS_PASS' if unchanged and all(x['fits'] for x in results) else 'OFFLINE_WITNESS_FAIL',
        provider_calls=0, runtime_prompt_gate='NOT_RUN', freeze_sha256=r.base.file_hash(out/'freeze.json'),
        source_unchanged=unchanged, rows=results, witness_count=len(results), unique_native_counts=len(cache),
        new_native_counts=new_native,
        maxima={stage:max(x['tokens'] or 0 for x in results if x['stage']==stage) for stage in ('T','P')})
    r.write(out/'result.json', summary, exclusive=True)
    print(summary['gate'], summary['witness_count'], summary['maxima'], flush=True)
    return 0 if summary['gate']=='OFFLINE_WITNESS_PASS' else 2


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reuse', type=Path)
    parser.add_argument('--reuse-sha')
    args=parser.parse_args()
    try:
        return run(args.output,args.reuse,args.reuse_sha)
    except Exception as error:
        print('T510_OFFLINE_STOPPED', str(error) if type(error) is r.Stop else type(error).__name__, flush=True)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
