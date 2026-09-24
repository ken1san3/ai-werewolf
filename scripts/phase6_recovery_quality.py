"""Hash-bound T506 annotation assembly; no automated interpretation of text."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import phase6_recovery_runner as runner
from scripts import phase6_recovery_statistics as stats

ANNOTATION_FIELDS = ('semantic_annotation', 'hard_annotation', 'style_annotation',
                     'content_answers_question', 'act_text_mismatch', 'legal_none', 'violations')


def measured_block(out, model, arm, seed):
    directory = out/runner.block_name(model, arm, seed)
    result = runner.read(directory/'result.json')
    seal = runner.read(directory/'seal.json')
    if runner.base.file_hash(directory/'result.json') != seal['result_sha256']:
        raise ValueError('RESULT_BINDING')
    if result['plan_sha256'] != runner.base.file_hash(out/'plan.json'):
        raise ValueError('PLAN_BINDING')
    if (result['model'], result['arm'], result['seed']) != (model, arm, seed):
        raise ValueError('BLOCK_BINDING')
    if [r['case_id'] for r in result['rows']] != list(stats.CASE_IDS):
        raise ValueError('CASE_COVERAGE')
    return result, seal['result_sha256']


def annotation_template(out, model, arm, seed):
    result, digest = measured_block(out, model, arm, seed)
    return {'result_sha256': digest, 'rubric': 't506_content_only_v1',
        'annotator': 'MAIN_EXPLORATORY', 'rows': [dict(case_id=row['case_id'], seed=seed,
            final_output_sha256=row['final_output_sha256'], semantic_annotation='UNKNOWN',
            hard_annotation='UNKNOWN', style_annotation='UNKNOWN', content_answers_question=None,
            act_text_mismatch=None, legal_none=None, violations=[]) for row in result['rows']]}


def assemble(out, model, arm):
    rows, integrity, bindings = [], True, {}
    for seed in runner.SEEDS:
        directory = out/runner.block_name(model, arm, seed)
        result, digest = measured_block(out, model, arm, seed)
        annotation_path = directory/'annotations.json'
        annotation = runner.read(annotation_path)
        if (annotation['result_sha256'] != digest or annotation['rubric'] != 't506_content_only_v1'
                or annotation['annotator'] != 'MAIN_EXPLORATORY'
                or [r['case_id'] for r in annotation['rows']] != list(stats.CASE_IDS)):
            raise ValueError('ANNOTATION_BINDING')
        for measured, note in zip(result['rows'], annotation['rows']):
            if note['seed'] != seed or note['final_output_sha256'] != measured['final_output_sha256']:
                raise ValueError('ANNOTATION_OUTPUT_BINDING')
            row = {k: measured[k] for k in ('case_id', 'seed', 'structural_pass', 'status',
                                           'final_output_sha256', 'provider_calls')}
            row.update({k: note[k] for k in ANNOTATION_FIELDS})
            rows.append(row)
        integrity &= (result['integrity'] is True and result['owned_processes_remaining'] == 0)
        for path in (directory/'result.json', annotation_path):
            bindings[path.relative_to(out).as_posix()] = runner.base.file_hash(path)
    return {'rows': rows, 'integrity': integrity, 'bindings': bindings, 'summary': stats.summarize(rows)}


def select(out):
    arms = {arm: assemble(out, 'qw9', arm) for arm in runner.ARMS}
    candidates = {arm: item for arm, item in arms.items() if arm != 'baseline'}
    selected = stats.select_candidate({k: v['rows'] for k, v in candidates.items()},
        comparison_integrity={k: v['integrity'] for k, v in candidates.items()})
    comparisons = {arm: stats.compare(arms['baseline']['rows'], item['rows'],
        comparison_integrity=arms['baseline']['integrity'] and item['integrity'])
        for arm, item in candidates.items()}
    report = {'arms': {k: v['summary'] for k, v in arms.items()}, 'comparisons': comparisons,
              'selection': selected}
    runner.write(out/'qwen-comparison.json', report, exclusive=True)
    # Selection API returns the fixed arm identity; bind every result and annotation.
    runner.write(out/'selection.json', {'arm': selected['arm_id'],
        'inputs': {p: digest for item in arms.values() for p, digest in item['bindings'].items()},
        'qwen_comparison_sha256': runner.base.file_hash(out/'qwen-comparison.json')}, exclusive=True)
    return report


def inspect(out, model, arm, seed):
    """Internal meaning review only. Caller must keep this output out of public logs."""
    import json
    result, _ = measured_block(out, model, arm, seed)
    private = Path(runner.read(out/runner.block_name(model, arm, seed)/'locator.json')['path'])
    for row in result['rows']:
        name = row['case_id']+'.final.json'
        path = private/name
        if not path.exists():
            print(json.dumps({'case_id': row['case_id'], 'status': row['status'], 'content': None}))
            continue
        if runner.base.file_hash(path) != result['private_artifacts'][name]:
            raise ValueError('PRIVATE_ARTIFACT_BINDING')
        raw = runner.read(path)['final_content']
        if raw is not None and runner.sha(raw.encode('utf-8')) != row['final_output_sha256']:
            raise ValueError('FINAL_OUTPUT_BINDING')
        try:
            value = runner.gb.strict_json(raw) if raw is not None else None
        except (ValueError, runner.base.DecisionValidationError):
            value = None  # Ambiguous/invalid JSON is not silently recovered.
        print(json.dumps({'case_id': row['case_id'], 'status': row['status'],
                          'selected_kind': row.get('selected_kind'), 'content': value}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--select', action='store_true')
    parser.add_argument('--inspect', nargs=3)
    args = parser.parse_args()
    if args.select:
        select(args.output)
        print('QWEN_COMPARISON_SEALED')
    elif args.inspect:
        model, arm, seed = args.inspect
        inspect(args.output, model, arm, int(seed))
    else:
        parser.error('choose --select or --inspect')


if __name__ == '__main__':
    main()
