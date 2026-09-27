from copy import deepcopy

import pytest

from scripts import phase6_local_staged_statistics as stats


def row():
    return dict(case_id='G01-1', seed=4242027, status='ACCEPTED', structural_pass=True,
        provider_calls=2, final_output_sha256='a'*64, semantic_annotation='PASS',hard_annotation='PASS',
        style_annotation='PASS',content_answers_question=True,act_text_mismatch=False,legal_none=None,violations=[])


@pytest.mark.parametrize('status,mapped',[('T_K_EXHAUSTED','CHOICE_INVALID'),('P_K_EXHAUSTED','FILTER_EXHAUSTED'),
    ('T_TRANSPORT_ERROR','CHOICE_ERROR'),('P_TIMEOUT','OUTPUT_ERROR'),('NOT_RUN','NOT_RUN'),('DEADLINE','ERROR')])
def test_view_retains_failed_status_and_never_manufactures_acceptance(status,mapped):
    value=row();value.update(status=status,structural_pass=False,final_output_sha256=None,
        semantic_annotation='UNKNOWN',hard_annotation='UNKNOWN',style_annotation='UNKNOWN')
    before=deepcopy(value)
    view=stats.comparison_view([value])[0]
    assert view['status']==mapped and view['original_status']==status
    assert view['final_output_sha256'] is None and view['semantic_annotation']=='UNKNOWN'
    assert value==before


def test_view_rejects_missing_annotation_and_stale_structural_success():
    value=row();del value['hard_annotation']
    with pytest.raises(ValueError):stats.comparison_view([value])
    value=row();value['status']='P_K_EXHAUSTED'
    with pytest.raises(ValueError):stats.comparison_view([value])


def test_bootstrap_lower_identical_to_original_and_upper_same_resamples():
    differences=[-1,0,1/3,-2/3]*8
    lower,upper=stats.bounds(differences)
    assert lower==stats.prior._bootstrap_lower_bound(differences)
    assert lower<=sum(differences)/len(differences)<=upper


def test_baseline_binding_is_checked_without_fallback(tmp_path):
    with pytest.raises((ValueError,OSError)):stats.baseline_rows(tmp_path)
