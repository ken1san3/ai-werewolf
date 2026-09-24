import copy

import pytest

from scripts import phase6_recovery_quality as q


@pytest.fixture
def experiment(tmp_path):
    q.runner.write(tmp_path/'plan.json', {'experiment': 'synthetic-test'})
    for arm in q.runner.ARMS:
        for seed in q.runner.SEEDS:
            directory = tmp_path/q.runner.block_name('qw9', arm, seed)
            directory.mkdir()
            result = {'model': 'qw9', 'arm': arm, 'seed': seed, 'integrity': True,
                'owned_processes_remaining': 0, 'plan_sha256': q.runner.base.file_hash(tmp_path/'plan.json'),
                'rows': [dict(case_id=cid, seed=seed, status='ACCEPTED', structural_pass=True,
                    provider_calls=1 if arm == 'baseline' else 2, final_output_sha256='a'*64) for cid in q.stats.CASE_IDS]}
            q.runner.write(directory/'result.json', result)
            q.runner.write(directory/'seal.json', {'result_sha256': q.runner.base.file_hash(directory/'result.json')})
            annotation = q.annotation_template(tmp_path, 'qw9', arm, seed)
            for row in annotation['rows']:
                row.update(semantic_annotation='PASS', hard_annotation='PASS', style_annotation='PASS',
                    act_text_mismatch=True, content_answers_question=True if row['case_id'] in q.stats.QUESTION_CASE_IDS else None,
                    legal_none=True if row['case_id'].startswith('G14-') else None)
            q.runner.write(directory/'annotations.json', annotation)
    return tmp_path


def test_all_96_bound_annotations_and_content_metadata_separation(experiment):
    result = q.assemble(experiment, 'qw9', 'gb1')
    assert len(result['rows']) == 96 and result['integrity']
    assert result['summary']['metrics']['semantic']['passes'] == 96
    assert result['summary']['product_absolute_safety_eligible']


@pytest.mark.parametrize('mutation', ['result_hash', 'output_hash', 'duplicate', 'rubric', 'seed'])
def test_annotation_substitution_fails_closed(experiment, mutation):
    path = experiment/q.runner.block_name('qw9', 'gb1', q.runner.SEEDS[0])/'annotations.json'
    value = q.runner.read(path)
    if mutation == 'result_hash': value['result_sha256'] = 'b'*64
    elif mutation == 'output_hash': value['rows'][0]['final_output_sha256'] = 'b'*64
    elif mutation == 'duplicate': value['rows'][1] = copy.deepcopy(value['rows'][0])
    elif mutation == 'rubric': value['rubric'] = 'after_results_changed'
    else: value['rows'][0]['seed'] += 1
    q.runner.write(path, value)
    with pytest.raises(ValueError):
        q.assemble(experiment, 'qw9', 'gb1')


def test_selection_freezes_all_result_and_annotation_dependencies(experiment):
    result = q.select(experiment)
    selection = q.runner.read(experiment/'selection.json')
    assert selection['arm'] == result['selection']['arm_id'] == 'filter'
    assert len(selection['inputs']) == 18
    for path, digest in selection['inputs'].items():
        assert q.runner.base.file_hash(experiment/path) == digest
    with pytest.raises(FileExistsError):
        q.select(experiment)
