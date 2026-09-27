"""T510 paired statistics view. Saved baseline annotations are never changed."""
from copy import deepcopy
import random
from pathlib import Path

from scripts import phase6_recovery_runner as r
from scripts import phase6_recovery_statistics as prior

SAFE = r.ROOT/'Docs/ai/handoffs/tasks/T506_SAFE_RESULTS.json'
SAFE_SHA = 'b765b77776f7a968ec0532d4aa7c90a9e4ef42042e5a1352e134548a0ff1411e'
BASELINE_ROOT = r.ROOT/'logs/t506-quality-recovery/program-v1'
ANNOTATION_FIELDS = ('semantic_annotation', 'style_annotation', 'hard_annotation',
                     'content_answers_question', 'act_text_mismatch', 'legal_none', 'violations')
STATUS_MAP = dict(ACCEPTED='ACCEPTED', NOT_RUN='NOT_RUN',
    T_INVALID='CHOICE_INVALID', P_INVALID='OUTPUT_INVALID',
    T_K_EXHAUSTED='CHOICE_INVALID', P_K_EXHAUSTED='FILTER_EXHAUSTED',
    T_TRANSPORT_ERROR='CHOICE_ERROR', T_TIMEOUT='CHOICE_ERROR',
    P_TRANSPORT_ERROR='OUTPUT_ERROR', P_TIMEOUT='OUTPUT_ERROR')


def join_annotations(result, annotation):
    source = {row['case_id']:row for row in result['rows']}
    annotated = {row['case_id']:row for row in annotation['rows']}
    if len(source)!=32 or len(annotated)!=32 or source.keys()!=annotated.keys():
        raise ValueError('ANNOTATION_COVERAGE')
    rows=[]
    for case_id, row in source.items():
        note=annotated[case_id]
        if note['seed']!=row['seed'] or note['final_output_sha256']!=row['final_output_sha256']:
            raise ValueError('ANNOTATION_BINDING')
        value=deepcopy(row)
        value.update({field:deepcopy(note[field]) for field in ANNOTATION_FIELDS})
        rows.append(value)
    return rows


def baseline_rows(root=BASELINE_ROOT):
    if r.base.file_hash(SAFE)!=SAFE_SHA:
        raise ValueError('BASELINE_SAFE_BINDING')
    bindings=r.read(SAFE)['groups']['qw9-baseline']['bindings']
    rows=[]
    for seed in prior.SEEDS:
        block='qw9-baseline-'+str(seed)
        for name in ('result.json','annotations.json'):
            relative=block+'/'+name
            if r.base.file_hash(Path(root)/relative)!=bindings[relative]:
                raise ValueError('BASELINE_ROW_BINDING')
        result=r.read(Path(root)/block/'result.json')
        annotation=r.read(Path(root)/block/'annotations.json')
        if annotation['result_sha256']!=bindings[block+'/result.json']:
            raise ValueError('BASELINE_ANNOTATION_BINDING')
        rows.extend(join_annotations(result, annotation))
    prior._index_rows(rows)
    return rows


def comparison_view(rows):
    result=[]
    for row in rows:
        value=deepcopy(row)
        value['original_status']=row['status']
        value['status']=STATUS_MAP.get(row['status'], 'ERROR')
        # This function never invents an output, label, UNKNOWN, or acceptance.
        prior._validate_row(value)
        if value['status']!='ACCEPTED' and value['structural_pass']:
            raise ValueError('FAILED_ROW_MARKED_ACCEPTED')
        result.append(value)
    return result


def bounds(differences):
    rng=random.Random(prior.BOOTSTRAP_SEED)
    size=len(differences)
    if not size:
        raise ValueError('EMPTY_CLUSTERS')
    samples=[sum(differences[rng.randrange(size)] for _ in range(size))/size
             for _ in range(prior.BOOTSTRAP_SAMPLES)]
    samples.sort()
    return samples[4999],samples[94999]


def compare(baseline, candidate, *, comparison_integrity):
    if type(comparison_integrity) is not bool:
        raise ValueError('INTEGRITY_TYPE')
    baseline=prior._index_rows(baseline)
    candidate=prior._index_rows(comparison_view(candidate))
    configurations={'semantic':(prior.CASE_IDS,-2/32), 'question':(prior.QUESTION_CASE_IDS,-1/18),
                    'style':(prior.CASE_IDS,-2/32), 'hard':(prior.CASE_IDS,-1/32)}
    metrics={}
    for metric,(case_ids,margin) in configurations.items():
        expected=[(cid,seed) for cid in case_ids for seed in prior.SEEDS]
        complete=all(k in baseline and k in candidate for k in expected)
        unknown=any(prior._unknown(baseline.get(k),metric) or prior._unknown(candidate.get(k),metric) for k in expected)
        differences=prior._cluster_differences(baseline,candidate,metric,case_ids)
        lower,upper=bounds(differences)
        decision='INCONCLUSIVE'
        if comparison_integrity and complete and not unknown:
            if lower>=margin: decision='NONINFERIOR'
            elif upper<margin: decision='INFERIOR'
        metrics[metric]=dict(clusters=len(case_ids), observed_difference=sum(differences)/len(differences),
            lower_95=lower, upper_95=upper, margin=margin, complete=complete, has_unknown=unknown,decision=decision)
    return dict(comparison_integrity=comparison_integrity, bootstrap_seed=prior.BOOTSTRAP_SEED,
                bootstrap_samples=prior.BOOTSTRAP_SAMPLES, metrics=metrics,
                summary=prior.summarize(list(candidate.values())))
