"""Public synthetic/offline regression for the approved T512 harness contract."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import importlib
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from scripts import phase6_harness_v2 as h


@pytest.fixture(scope='module')
def artifacts():
    return h.build_artifacts(h.validate_spec(h.SPEC.read_bytes()))


@pytest.fixture
def no_external(monkeypatch):
    import httpx
    def forbidden(*args, **kwargs):
        raise AssertionError('EXTERNAL_SIDE_EFFECT')
    for name in ('Popen','run','call','check_output','check_call'):
        monkeypatch.setattr(subprocess, name, forbidden)
    for name in ('Client','AsyncClient','request','get','post'):
        monkeypatch.setattr(httpx, name, forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'bind', forbidden)
    for module_name in ('scripts.phase6_model_comparison','scripts.phase6_recovery_runner','scripts.phase6_probe_outer'):
        module = importlib.import_module(module_name)
        for name in ('owned_listener','process_alive','request','execute_call','run_block','supervise'):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, forbidden)


def test_fixture_exact_closed_spec():
    assert h.validate_spec(h.SPEC.read_bytes()) == h.expected_spec()
    assert h.digest(h.SPEC.read_bytes()) == h.SPEC_SHA


@pytest.mark.parametrize('mutate', [
    lambda x: x.update(extra=True), lambda x: x.update(module='os'),
    lambda x: x.update(path='C:/secret'), lambda x: x['case_ids'].reverse(),
    lambda x: x['profiles'].append('UNKNOWN'), lambda x: x['seeds'].append(x['seeds'][0]),
    lambda x: x['pf1_targets'].pop(), lambda x: x['pf2_expectations'][0].update(registry_key='CALLABLE'),
])
def test_closed_spec_rejects_mutation(mutate):
    value = h.expected_spec(); mutate(value)
    with pytest.raises(h.IntegrityError): h.validate_spec(h.canonical(value))


@pytest.mark.parametrize('raw', [b'{"x":1,"x":2}',b'{"x":NaN}',b'{"x":Infinity}',b'{"x":-Infinity}',b'\xff',b'{',
                               b'{"x":1e309}',b'[-1e309]',b'{"x":[{"y":-1e999}]}'])
def test_strict_json_rejects_invalid(raw):
    with pytest.raises(h.IntegrityError): h.strict_json(raw)


@pytest.mark.parametrize('profile', h.PROFILES[:7])
def test_pf1_exact_historical_routes(artifacts, profile):
    artifact = next(x for x in artifacts if x['profile'] == profile)
    target = h.pf1_target(profile)
    checked = h.analyze_pf1(artifact['request'].encode(), target)
    expected = 'PASS' if profile in ('K1_KIND_FIRST','WP2_CHAT_PLAN_FIXTURE') else 'FAIL'
    assert checked['status'] == expected
    assert sorted(set(x['route'] for x in checked['rows'])) == sorted(target['reachable_routes'])
    assert len(checked['rows']) == len(target['reachable_routes']) * target['branch_count']
    if expected == 'FAIL':
        assert checked['partition']['kind:"NONE"'] == ['NONE']
        assert any(x['leading_key'] == 'addressee_player_id' and x['reachable_acts'] == ['ANSWER'] for x in checked['rows'])
    elif profile == 'K1_KIND_FIRST':
        assert all(x['leading_key'] == 'kind' and x['act_position'] == 0 for x in checked['rows'])
    else:
        assert checked['partition']['reply_to:null'] == ['CLAIM','NONE','OPINION_CHANGE','QUESTION']
        assert all(x['leading_key'] == 'reply_to' and x['act_position'] == 1 for x in checked['rows'])


@pytest.mark.parametrize('mutation', ['alias','direct_alias','type_missing','duplicate_required','properties_type',
    'impossible_selector','unsupported','unknown_keyword','cycle','branch_count','missing_route','external'])
def test_pf1_unknown_is_not_partial_pass(artifacts, mutation):
    artifact = next(x for x in artifacts if x['profile'] == 'K1_KIND_FIRST')
    value = h.strict_json(artifact['request'])
    target = h.pf1_target('K1_KIND_FIRST')
    schema = h.pointer(value,target['schema_pointer'])
    if mutation == 'alias':
        schema['properties']['alias'] = {'properties':{'speech_act':{'$ref':'#/$defs/speech_act'}}}
    elif mutation == 'direct_alias': schema['properties']['alias'] = {'$ref':'#/$defs/speech_act'}
    elif mutation == 'type_missing': del schema['$defs']['speech_act']['oneOf'][0]['type']
    elif mutation == 'duplicate_required': schema['$defs']['speech_act']['oneOf'][0]['required'].append('kind')
    elif mutation == 'properties_type': schema['$defs']['speech_act']['oneOf'][0]['properties'] = []
    elif mutation == 'impossible_selector': schema['$defs']['speech_act']['oneOf'][0]['properties']['kind']['type']='integer'
    elif mutation == 'unsupported': schema['allOf'] = [{}]
    elif mutation == 'unknown_keyword': schema['not'] = {'type':'null'}
    elif mutation == 'cycle': schema['$defs']['speech_act'] = {'$ref':'#/$defs/speech_act'}
    elif mutation == 'external': schema['$defs']['speech_act'] = {'$ref':'https://invalid.example/schema'}
    elif mutation == 'branch_count': schema['$defs']['speech_act']['oneOf'].pop()
    else: del schema['properties']['discussion']['oneOf'][0]['properties']['speech_act']
    raw = json.dumps(value,ensure_ascii=False).encode()
    target['expected_sha256'] = h.digest(raw)
    assert h.analyze_pf1(raw,target)['status'] == 'UNKNOWN'


def g01(artifacts, profile='GB1'):
    return deepcopy(next(x for x in artifacts if x['profile'] == profile and x['case_id'] == 'G01-1'))


def positive_inventory(artifacts):
    inventory = g01(artifacts)['inventory']
    value = inventory['canonical_input']
    matches = [(i,r) for i,r in enumerate(value['memory']['records']) if r['source'] == value['capture']['trigger']['source']]
    assert len(matches) == 1
    i, record = matches[0]
    inventory['items'].append(h.bind_item(value,'REPLY','r000',f'/memory/records/{i}', 'TRIGGER_CHAT',
                                        inventory['output_channel_id'], record['source']))
    return inventory


def test_gb1_missing_reply_and_exact_positive(artifacts):
    expect = h.expectation('G01-1','GB1')
    before = h.check_pf2(expect,g01(artifacts)['inventory'])
    assert (before['status'],before['reason']) == ('FAIL','MISSING_REPLY_CANDIDATE')
    positive = positive_inventory(artifacts)
    after = h.check_pf2(expect,positive)
    assert after['status'] == 'PASS'
    assert after['candidate_presence_authority_only'] is True
    assert after['required_count'] == after['present_count'] == 1


@pytest.mark.parametrize('bad',[None,{}, {'case_id':'G99-1','profile':'GB1'}, {'case_id':'G01-1','profile':'UNKNOWN'}])
def test_pf2_unknown_expectation_is_closed(artifacts,bad):
    assert h.check_pf2(bad,positive_inventory(artifacts))['status']=='UNKNOWN'


@pytest.mark.parametrize('mutation', ['actor','channel','projection','private','duplicate','pointer','authority','extra','binding','source','source_kind'])
def test_pf2_binding_negative(artifacts, mutation):
    inventory = positive_inventory(artifacts)
    item = inventory['items'][-1]
    if mutation == 'actor': item['actor_player_id'] = 'player-0'
    elif mutation == 'channel': item['output_channel_id'] = 'private-wolves'
    elif mutation == 'projection': item['projection_sha256'] = 'a'*64
    elif mutation == 'private': item['source_ref']['visibility'] = 'AUTHORIZED_PRIVATE'
    elif mutation == 'duplicate': inventory['items'].append(deepcopy(item))
    elif mutation == 'pointer': item['source_pointer'] = '/missing'
    elif mutation == 'authority': item['read_authority'] = 'FORBIDDEN'
    elif mutation == 'extra': item['secret'] = 'PRIVATE_MARKER'
    elif mutation == 'source': item['source_ref'] = {'record_kind':'chat','order':999,'visibility':'PUBLIC'}
    elif mutation == 'source_kind': item['source_kind'] = 'RECENT_CHAT'
    if mutation != 'binding':
        item['binding_sha256'] = h.digest(h.canonical({k:v for k,v in item.items() if k != 'binding_sha256'}))
    else: item['binding_sha256'] = '0'*64
    assert h.check_pf2(h.expectation('G01-1','GB1'),inventory)['status'] != 'PASS'


def test_all_32_case_expectations_retained(artifacts):
    for profile in ('PRODUCT_V1','GB1'):
        rows = [x for x in artifacts if x['profile'] == profile]
        assert len(rows) == 96
        assert {x['case_id'] for x in rows} == set(h.CASE_IDS)
        for row in rows:
            checked = h.check_pf2(h.expectation(row['case_id'],profile),row['inventory'])
            assert checked['status'] in ('PASS','FAIL','UNKNOWN')
            if row['case_id'].startswith('G14'):
                assert checked['status'] == 'PASS'


@pytest.mark.parametrize('kind', ['REPLY','FACT','PLAYER','OPINION'])
@pytest.mark.parametrize('mutation', ['source_kind','namespace','source_ref'])
def test_all_leaf_provenance_rehashed_is_not_authority(artifacts,kind,mutation):
    case_id = 'G04-1' if kind == 'OPINION' else 'G03-1' if kind == 'PLAYER' else 'G01-1'
    inventory = deepcopy(next(x['inventory'] for x in artifacts if x['profile']=='PRODUCT_V1' and x['case_id']==case_id))
    item=next(x for x in inventory['items'] if x['inventory_kind']==kind)
    if mutation=='source_kind': item['source_kind']='WRONG_KIND'
    elif mutation=='namespace': item['id']='x999'
    else: item['source_ref']={'record_kind':'chat','order':999,'visibility':'PUBLIC'}
    item['binding_sha256']=h.digest(h.canonical({k:v for k,v in item.items() if k!='binding_sha256'}))
    assert h.check_pf2(h.expectation(case_id,'PRODUCT_V1'),inventory)['status'] != 'PASS'


def test_saved_native_counterexample_is_actual_case_legal(artifacts):
    token = next(x for x in artifacts if x['profile'] == 'T506_CHOICE32')
    assert token['schema']['properties']['authoritative_fact_ids']['items']['enum'][0] == 'f000'
    checked = h.check_pf3(token['contract'],token['schema'],token['evidence'])
    assert (checked['status'],checked['reason'],checked['observed_tokens']) == ('FAIL','FAIL_BUDGET',37)
    assert checked['max_tokens'] == 32


@pytest.mark.parametrize('mutation', ['identity','hash','invalid','missing','negative','token_forgery','source_bytes','freeze_bytes'])
def test_pf3_invalid_evidence_is_unknown(artifacts, mutation):
    token = deepcopy(next(x for x in artifacts if x['profile'] == 'T506_CHOICE32'))
    evidence = token['evidence']
    if mutation == 'identity': evidence['identity'] = {'model':'other'}
    elif mutation == 'hash': evidence['rows'][0]['raw_sha256'] = '0'*64
    elif mutation == 'invalid':
        evidence['rows'][0]['raw'] = '{"speech_act_kind":"FAKE"}'
        evidence['rows'][0]['raw_sha256'] = h.digest(evidence['rows'][0]['raw'].encode())
    elif mutation == 'missing': evidence['rows'] = []
    elif mutation == 'token_forgery': evidence['rows'][0]['tokens'] = 1
    elif mutation == 'source_bytes': evidence['source_bytes'] += ' '
    elif mutation == 'freeze_bytes': evidence['freeze_bytes'] += ' '
    else: evidence['rows'][0]['tokens'] = -1
    assert h.check_pf3(token['contract'],token['schema'],evidence)['status'] == 'UNKNOWN'


def test_pf3_witness_never_proves_maximum(artifacts):
    token = deepcopy(next(x for x in artifacts if x['profile'] == 'T506_CHOICE32'))
    token['contract']['max_tokens'] = 512
    assert h.check_pf3(token['contract'],token['schema'],token['evidence'])['status'] == 'UNKNOWN'
    token['contract']['mode'] = 'FINITE_EXHAUSTIVE'
    assert h.check_pf3(token['contract'],token['schema'],token['evidence'])['status'] == 'UNKNOWN'
    assert h.check_pf3({'mode':'UNBOUNDED_OR_UNPROVEN','max_tokens':512},{'type':'string'}, {})['status'] == 'UNKNOWN'


@pytest.fixture
def owner():
    lease = h.LeaseIdentity('host',42,1234,'a'*64,'b'*32,'c'*64)
    process = h.ProcessObservation('OBSERVED','host',42,1234,'a'*64,True)
    listener = h.ListenerObservation('OBSERVED','http://127.0.0.1:8000',1,42,1234,'a'*64)
    return lease,process,listener


def test_owner_identity_is_immutable(owner):
    lease,process,listener = owner
    assert h.evaluate_owner(*owner)['status'] == 'PASS'
    with pytest.raises(FrozenInstanceError): lease.pid = 8
    assert h.evaluate_owner(lease,replace(process,creation_time_ns=9999),listener)['status'] == 'FAIL'


@pytest.mark.parametrize('mutation', ['pid','host','executable','missing','access','ambiguous','count','listener_owner','bool_pid'])
def test_owner_negative(owner, mutation):
    lease,p,l = owner
    if mutation == 'pid': p = replace(p,pid=43)
    elif mutation == 'host': p = replace(p,host_id='other')
    elif mutation == 'executable': p = replace(p,executable_sha256='b'*64)
    elif mutation == 'missing': p = replace(p,creation_time_ns=None)
    elif mutation == 'access': p = replace(p,status='ACCESS_DENIED')
    elif mutation == 'ambiguous': l = replace(l,status='AMBIGUOUS')
    elif mutation == 'count': l = replace(l,listener_count=2)
    elif mutation == 'listener_owner': l = replace(l,owner_creation_time_ns=999)
    else: p = replace(p,pid=True)
    assert h.evaluate_owner(lease,p,l)['status'] != 'PASS'


def test_one_client_multiple_responses_disconnect_and_no_reconnect(owner):
    event = h.TransportObservation('OPEN','conn-1',0,42,1234,True)
    state = h.transition_transport(h.TransportState(),*owner,event)
    assert state.state == 'CONNECTED' and state.client_creations == 1
    for _ in range(4):
        state = h.transition_transport(state,*owner,replace(event,event='RESPONSE'))
        assert state.state == 'CONNECTED' and state.client_creations == 1
    assert h.transition_transport(state,*owner,replace(event,event='RESPONSE',connection_id='conn-2')).state == 'REJECTED'
    disconnected = h.transition_transport(state,*owner,replace(event,event='DISCONNECT'))
    assert disconnected.state == 'STOP_REQUIRED'
    assert h.transition_transport(disconnected,*owner,event).state == 'REJECTED'
    assert h.transition_transport(state,*owner,replace(event,event='RECONNECT_ATTEMPT')).state == 'REJECTED'
    lease,p,l=owner
    assert h.transition_transport(state,replace(lease,nonce128='d'*32),p,l,replace(event,event='RESPONSE')).state == 'REJECTED'
    assert h.transition_transport(state,replace(lease,manifest_sha256='d'*64),p,l,replace(event,event='RESPONSE')).state == 'REJECTED'


def test_cleanup_requires_both_observed_absence(owner):
    lease,p,l = owner
    gone = replace(p,status='MISSING',alive=False)
    no_listener = replace(l,listener_count=0)
    assert h.evaluate_cleanup(lease,gone,no_listener)['status'] == 'PASS'
    assert h.evaluate_cleanup(lease,gone,replace(no_listener,status='ACCESS_DENIED'))['status'] != 'PASS'
    assert h.evaluate_cleanup(lease,replace(gone,pid=99),no_listener)['status'] != 'PASS'
    assert h.evaluate_cleanup(*owner)['status'] != 'PASS'


@pytest.mark.parametrize('bad', [None,{},0,'not-lease'])
def test_invalid_lease_close_never_throws(owner,bad):
    _,p,l=owner
    gone=replace(p,status='MISSING',alive=False); missing=replace(l,listener_count=0)
    assert h.evaluate_cleanup(bad,gone,missing)['status']=='UNKNOWN'
    assert h.transition_transport(h.TransportState(),bad,gone,missing,h.TransportObservation('CLOSE',None,None,None,None,None)).state=='UNKNOWN'


@pytest.mark.parametrize('mutation',['observation_type','negative_pid','bad_status','bool_count','bad_host','bad_event'])
def test_invalid_owner_shapes_are_unknown(owner,mutation):
    lease,p,l=owner
    event=h.TransportObservation('CLOSE',None,None,None,None,None)
    if mutation=='observation_type': p={}
    elif mutation=='negative_pid': p=replace(p,pid=-1)
    elif mutation=='bad_status': p=replace(p,status='UNKNOWN_ENUM')
    elif mutation=='bool_count': l=replace(l,listener_count=False)
    elif mutation=='bad_host': lease=replace(lease,host_id=[])
    else: event=replace(event,event='UNKNOWN_ENUM')
    assert h.transition_transport(h.TransportState(),lease,p,l,event).state=='UNKNOWN'


@pytest.mark.parametrize('groups,wanted', [([[],[]],'UNKNOWN'),([{'status':'PASS'}],'PASS'),
    ([{'status':'PASS'},{'status':'NOT_RUN'}],'UNKNOWN'),([{'status':'UNKNOWN'},{'status':'FAIL'}],'FAIL'),
    ([{}],'UNKNOWN')])
def test_aggregate_fail_closed(groups,wanted):
    assert h.aggregate_preflight(*groups) == wanted


@pytest.fixture
def report(artifacts):
    return h.preflight(dict(artifacts=artifacts,manifest_sha256='a'*64,source_freeze_sha256='b'*64),'T512H2-20260927T120000000000Z-TEST000')


def test_safe_report_all_denominators_and_unmeasured(report):
    assert report['provider_calls'] == report['inference_calls'] == 0
    assert len(report['pf1']) == 7 and len(report['pf2']) == 192 and len(report['pf3']) == 6
    assert report['pf4'] == [{'status':'NOT_RUN','reason':'PROVIDER_NOT_AUTHORIZED'}]
    assert report['status'] == 'FAIL'  # Known historical negatives, not harness malfunction.
    assert all(x['status'] == 'UNKNOWN' for x in report['pf3'][1:])
    assert h.public_report(report) == report


@pytest.mark.parametrize('mutation', ['top','nested','reason','fixture','stage','date','path','hash','calls'])
def test_public_allowlist_rejects_secret_in_fields_and_values(report,mutation):
    bad = deepcopy(report)
    if mutation == 'top': bad['private'] = 'PRIVATE_MARKER'
    elif mutation == 'nested': bad['pf1'][0]['source_pointer'] = '/private'
    elif mutation == 'reason': bad['pf2'][0]['reason'] = 'PRIVATE_MARKER'
    elif mutation == 'fixture': bad['pf1'][0]['fixture_id'] = 'PRIVATE_MARKER'
    elif mutation == 'stage': bad['pf3'][0]['stage_id'] = 'PRIVATE_MARKER'
    elif mutation == 'date': bad['started_at_utc'] = 'PRIVATE_MARKER'
    elif mutation == 'path': bad['locator_id'] = 'C:/private'
    elif mutation == 'hash': bad['manifest_sha256'] = 'PRIVATE_MARKER'
    else: bad['provider_calls'] = 1
    with pytest.raises(h.IntegrityError,match='PUBLIC_REPORT_FIELD'):
        h.public_report(bad)


@pytest.mark.parametrize('mutation',['status','empty','pf1_duplicate','pf2_missing','pf3_duplicate','pf4_missing','gate_reason','status_reason','boolean_calls'])
def test_public_denominators_and_status_are_not_editable(report,mutation):
    bad=deepcopy(report)
    if mutation=='status': bad['status']='PASS'
    elif mutation=='empty':
        for k in ('pf1','pf2','pf3','pf4'): bad[k]=[]
        bad['status']='PASS'
    elif mutation=='pf1_duplicate': bad['pf1'][-1]=deepcopy(bad['pf1'][0])
    elif mutation=='pf2_missing': bad['pf2'].pop()
    elif mutation=='pf3_duplicate': bad['pf3'][-1]=deepcopy(bad['pf3'][0])
    elif mutation=='pf4_missing': bad['pf4']=[]
    elif mutation=='gate_reason': bad['pf1'][0]['reason']='MISSING_REPLY_CANDIDATE'
    elif mutation=='status_reason': bad['pf1'][0]['status']='PASS'
    else: bad['provider_calls']=False
    with pytest.raises(h.IntegrityError,match='PUBLIC_REPORT_FIELD'): h.public_report(bad)


def test_unrun_pf4_participates_in_preflight_aggregate(artifacts,monkeypatch):
    original=h.aggregate_preflight; captured=[]
    def traced(*groups): captured.append(deepcopy(groups)); return original(*groups)
    monkeypatch.setattr(h,'aggregate_preflight',traced)
    h.preflight(dict(artifacts=artifacts,manifest_sha256='a'*64,source_freeze_sha256='b'*64),'T512H2-20260927T120000000000Z-TEST000')
    assert captured and all(len(groups)==4 and groups[-1]==[{'status':'NOT_RUN','reason':'PROVIDER_NOT_AUTHORIZED'}] for groups in captured)
    assert original([{'status':'PASS'}],[{'status':'PASS'}],[{'status':'PASS'}],[{'status':'NOT_RUN'}])=='UNKNOWN'


@pytest.mark.parametrize('command', ['probe','run'])
def test_disabled_before_every_side_effect(monkeypatch,no_external,command,capsys):
    def forbidden(*args,**kwargs): raise AssertionError('FILESYSTEM_SIDE_EFFECT')
    monkeypatch.setattr(Path,'read_bytes',forbidden)
    monkeypatch.setattr(Path,'mkdir',forbidden)
    monkeypatch.setattr(h,'acl_observation',forbidden)
    assert h.main([command,'--anything','private']) == 4
    assert capsys.readouterr().out == 'COMMAND_DISABLED_BY_AUTHORITY\n'


def test_pure_helpers_import_and_build_without_external_calls(no_external,monkeypatch):
    for name in ('scripts.phase6_recovery_runner','scripts.phase6_intent_first_probe','scripts.phase6_two_call_probe',
                 'scripts.phase6_minimal_suite_adapter','scripts.phase6_local_staged_probe','scripts.phase6_kind_first_probe',
                 'scripts.phase6_choice_budget_probe'):
        importlib.reload(importlib.import_module(name))
    assert len(h.build_artifacts(h.expected_spec())) == 199


def test_prepare_acl_unknown_before_build(monkeypatch,tmp_path):
    base = tmp_path/'base'; (base/'synthetic').mkdir(parents=True)
    monkeypatch.setattr(h,'BASE',base)
    def forbidden(*args,**kwargs): raise AssertionError('BUILD_NOT_ALLOWED')
    monkeypatch.setattr(h,'build_artifacts',forbidden)
    with pytest.raises(h.IntegrityError,match='PRIVATE_ACL_UNVERIFIED'):
        h.prepare_cache(h.SPEC.read_bytes(),'T512HARNESS',private_factory=forbidden,acl_observer=lambda _:False)


def test_cache_roundtrip_no_rebuild_and_detect_drift(monkeypatch,tmp_path,artifacts,no_external,capsys):
    # Only a fake filesystem/ACL factory is injected; real CLI cannot accept these paths.
    base = tmp_path/'base'; parent = base/'synthetic'; parent.mkdir(parents=True)
    locators = tmp_path/'locators'
    monkeypatch.setattr(h,'BASE',base); monkeypatch.setattr(h,'LOCATORS',locators)
    monkeypatch.setattr(h,'acl_observation',lambda _:True)
    calls=[]
    def build(spec): calls.append(spec); return deepcopy(artifacts)
    monkeypatch.setattr(h,'build_artifacts',build)
    def factory(*args,**kwargs):
        target=parent/'T512H2-20260927T120000000000Z-TEST000'; target.mkdir(); return target
    prepared=h.prepare_cache(h.SPEC.read_bytes(),'T512HARNESS',private_factory=factory,acl_observer=lambda _:True)
    cache=parent/prepared['locator_id']
    assert len(calls)==1
    verified=h.verify_cache(cache,h.SPEC_SHA)
    assert verified['manifest_sha256']==prepared['manifest_sha256'] and len(calls)==1
    manifest=h.strict_json((cache/'manifest.json').read_bytes())
    assert 'manifest_sha256' not in manifest
    assert h.strict_json((cache/'source.json').read_bytes())['input_spec_sha256']==h.SPEC_SHA
    public=tmp_path/'public.json'
    assert h.main(['preflight','--locator',prepared['locator_id'],'--out',str(public)]) == 2
    assert h.main(['report','--preflight',str(public)]) == 2
    assert len(calls)==1
    output=capsys.readouterr().out
    assert 'PRIVATE_MARKER' not in output and 'source_pointer' not in output and 'canonical_input' not in output
    assert h.main(['preflight','--locator','../private','--out',str(public)]) == 5
    (cache/'a000.json').write_bytes(b'{}')
    with pytest.raises(h.IntegrityError,match='CACHE_ARTIFACT_DRIFT'):
        h.verify_cache(cache,h.SPEC_SHA)


def test_builder_exact_projection_call_domain(monkeypatch,no_external):
    from scripts import phase6_recovery_runner as r
    original=r.base.project; calls=[]
    def counted(case,variant): calls.append(case.case_id); return original(case,variant)
    monkeypatch.setattr(r.base,'project',counted)
    built=h.build_artifacts(h.expected_spec())
    # 192 eligible product/GB1 entries, plus the existing cost-only toy fixture helper.
    assert len(built)==199 and len(calls)==193
    assert calls.count('G01-1')==7 and all(calls.count(case)==6 for case in h.CASE_IDS[1:])


def test_write_exclusive_no_overwrite(tmp_path):
    output=tmp_path/'report.json'
    h.write_exclusive(output,b'first')
    with pytest.raises(h.IntegrityError): h.write_exclusive(output,b'second')
    assert output.read_bytes()==b'first'


def test_frozen_source_detects_config_change(monkeypatch,tmp_path):
    config=tmp_path/'config.toml'; config.write_bytes(b'one')
    monkeypatch.setattr(h,'CONFIG',config)
    before=h.freeze_sources(h.SPEC_SHA)
    config.write_bytes(b'two')
    after=h.freeze_sources(h.SPEC_SHA)
    assert before['config_sha256'] != after['config_sha256']


@pytest.mark.parametrize('reparse', [0x400,0])
def test_plain_path_reparse_and_missing(monkeypatch,tmp_path,reparse):
    target=tmp_path/'item'; target.write_bytes(b'public')
    if reparse:
        original=h.os.lstat
        def fake(path,*args,**kwargs):
            if Path(path)==target:
                from types import SimpleNamespace
                return SimpleNamespace(st_mode=original(path).st_mode,st_file_attributes=0x400)
            return original(path,*args,**kwargs)
        monkeypatch.setattr(h.os,'lstat',fake)
        with pytest.raises(h.IntegrityError,match='REPARSE_PATH'): h.plain_path(target)
    else:
        with pytest.raises(h.IntegrityError,match='PATH_MISSING'): h.plain_path(tmp_path/'missing')


@pytest.mark.parametrize('task_id',['','x','T512H2','lowercase','T'*33,'../../OTHER'])
def test_invalid_task_has_no_factory_side_effect(task_id,monkeypatch):
    def forbidden(*args,**kwargs): raise AssertionError('FACTORY_CALLED')
    with pytest.raises(h.IntegrityError):
        h.prepare_cache(h.SPEC.read_bytes(),task_id,private_factory=forbidden)


@pytest.mark.parametrize('relative',list(h.PRIVATE_HELPERS))
def test_helper_drift_is_rejected_before_factory(relative,monkeypatch):
    changed=dict(h.PRIVATE_HELPERS); size,sha=changed[relative]; changed[relative]=(size,'0'*64)
    monkeypatch.setattr(h,'PRIVATE_HELPERS',changed)
    def forbidden(*args,**kwargs): raise AssertionError('FACTORY_CALLED')
    with pytest.raises(h.IntegrityError,match='PRIVATE_HELPER_SOURCE_DRIFT'):
        h.prepare_cache(h.SPEC.read_bytes(),'T512HARNESS',private_factory=forbidden,acl_observer=forbidden)
    assert h.acl_observation(h.BASE/'synthetic') is False
