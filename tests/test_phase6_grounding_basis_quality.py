import copy
import hashlib
import json

import pytest

from scripts import phase6_grounding_basis_quality as quality


@pytest.mark.parametrize('structural,semantic,expected', [
    (True,True,True),(True,False,False),(True,None,None),
    (False,True,False),(False,False,False),(False,None,False)])
def test_hard_three_valued_truth_table(structural,semantic,expected):
    assert quality.hard_and(structural,semantic) is expected


@pytest.mark.parametrize('value',[0,1,0.0,1.0,'true',[],{}])
def test_truth_table_never_coerces_boolean(value):
    with pytest.raises(ValueError): quality.hard_and(value,True)
    with pytest.raises(ValueError): quality.hard_and(True,value)


def evidence():
    ids=[f'G{i:02}-1' for i in range(32)]
    measured={'experiment':'grounding_basis_v1','rows':[]}
    annotation={'experiment':'grounding_basis_v1','private_raw_sha256':'a'*64,'rows':[]}
    for cid in ids:
        measured['rows'].append({'case_id':cid,'baseline_input_sha256':'b'*64,'final_output_sha256':'c'*64,
            **{k:True for k in ('choice_schema_pass','candidate_schema_pass','kind_match_pass',
                                'legacy_validator_pass','structural_pass')}})
        annotation['rows'].append({'case_id':cid,'input_sha256':'b'*64,'final_output_sha256':'c'*64,
            'structural_pass':True,'hard_pass':True,'semantic_hard_pass':True,'semantic_pass':True,
            'style_pass':True,'speech_act_consistent':True,'secrecy_violation':False,
            'content_answers_question':None,'reason_codes':['NO_VIOLATION']})
    return ids,measured,annotation


def arguments(ids,result,note):
    result_bytes=json.dumps(result).encode()
    result_sha=hashlib.sha256(result_bytes).hexdigest()
    note=copy.deepcopy(note);note['result_sha256']=result_sha
    annotation_bytes=json.dumps(note).encode()
    return (result_bytes,annotation_bytes),dict(result_sha256=result_sha,
        annotation_sha256=hashlib.sha256(annotation_bytes).hexdigest(),private_raw_sha256='a'*64,case_ids=ids)


def test_structural_failure_preserves_separate_semantic_unknown():
    ids,result,note=evidence()
    result['rows'][0]['legacy_validator_pass']=False
    note['rows'][0].update(structural_pass=False,hard_pass=False,semantic_hard_pass=None)
    args,kwargs=arguments(ids,result,note)
    merged=quality.combine(*args,**kwargs)
    assert merged['rows'][0]['hard_pass'] is False
    assert merged['rows'][0]['semantic_hard_pass'] is None
    assert merged['summary']['hard_pass']=={'FAIL':1,'PASS':31}
    assert merged['summary']['semantic_hard_pass']=={'UNKNOWN':1,'PASS':31}


@pytest.mark.parametrize('mutation', ['duplicate_result','duplicate_annotation','missing_result','missing_annotation',
    'unexpected_case','input_hash','output_hash','private_hash','reported_hard','reported_structure',
    'semantic_missing','semantic_integer','structural_integer','free_form_reason','duplicate_reason','wrong_experiment'])
def test_quality_mismatch_is_rejected(mutation):
    ids,result,note=evidence()
    if mutation=='duplicate_result':result['rows'][-1]=copy.deepcopy(result['rows'][0])
    elif mutation=='duplicate_annotation':note['rows'][-1]=copy.deepcopy(note['rows'][0])
    elif mutation=='missing_result':result['rows'].pop()
    elif mutation=='missing_annotation':note['rows'].pop()
    elif mutation=='unexpected_case':note['rows'][0]['case_id']='unknown'
    elif mutation=='input_hash':note['rows'][0]['input_sha256']='d'*64
    elif mutation=='output_hash':note['rows'][0]['final_output_sha256']='d'*64
    elif mutation=='private_hash':note['private_raw_sha256']='d'*64
    elif mutation=='reported_hard':note['rows'][0]['hard_pass']=False
    elif mutation=='reported_structure':note['rows'][0]['structural_pass']=False
    elif mutation=='semantic_missing':del note['rows'][0]['semantic_hard_pass']
    elif mutation=='semantic_integer':note['rows'][0]['semantic_hard_pass']=1
    elif mutation=='structural_integer':result['rows'][0]['structural_pass']=1
    elif mutation=='free_form_reason':note['rows'][0]['reason_codes']=['PRIVATE TEXT']
    elif mutation=='duplicate_reason':note['rows'][0]['reason_codes']*=2
    else:note['experiment']='stage_control_v1'
    args,kwargs=arguments(ids,result,note)
    with pytest.raises(ValueError,match='QUALITY_BINDING_INVALID'):quality.combine(*args,**kwargs)


@pytest.mark.parametrize('field',['result_sha256','annotation_sha256'])
def test_artifact_hashes_are_exact(field):
    args,kwargs=arguments(*evidence());kwargs[field]='d'*64
    with pytest.raises(ValueError,match='QUALITY_BINDING_INVALID'):quality.combine(*args,**kwargs)


def test_duplicate_json_annotation_key_is_rejected():
    args,kwargs=arguments(*evidence())
    changed=args[1].decode().replace('"semantic_pass": true','"semantic_pass": false,"semantic_pass": true',1).encode()
    kwargs['annotation_sha256']=hashlib.sha256(changed).hexdigest()
    with pytest.raises(ValueError,match='QUALITY_BINDING_INVALID'):quality.combine(args[0],changed,**kwargs)


def test_invalid_output_still_binds_exact_generated_output_hash():
    ids,result,note=evidence()
    row=result['rows'][0]
    row['output']={'final_output_sha256':row.pop('final_output_sha256')}
    row['legacy_validator_pass']=False
    note['rows'][0].update(structural_pass=False,hard_pass=False)
    args,kwargs=arguments(ids,result,note)
    assert quality.combine(*args,**kwargs)['rows'][0]['final_output_sha256']=='c'*64
    note['rows'][0]['final_output_sha256']=None
    args,kwargs=arguments(ids,result,note)
    with pytest.raises(ValueError,match='QUALITY_BINDING_INVALID'):quality.combine(*args,**kwargs)
