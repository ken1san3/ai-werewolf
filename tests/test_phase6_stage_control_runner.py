import copy
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from scripts import phase6_stage_control_runner as runner
from scripts import phase6_two_stage_probe_runtime as runtime
from tests.test_phase6_intent_choice_probe import projection
from tests.test_phase6_two_call_probe import legacy


def test_fixed_identity_and_contract():
    value = runner.fixed_contract()
    assert value["experiment"] == "stage_control_v1"
    assert value["task_id"] == "T471"
    assert value["runner"] == "scripts/phase6_stage_control_runner.py"
    assert value["stage_contract"] == runtime.asdict(runtime.SC2_CONTRACT)
    assert value["max_provider_calls"] == 64
    assert value["choice_tokens"] == 32 and value["output_tokens"] == 480


def test_all_32_entries_freeze_seven_choices_and_four_c5_cases(monkeypatch):
    monkeypatch.setattr(runner, "prior_schema_hashes", lambda: {})
    rows = [runner.entry(case, runner.base.project(case, "baseline"))
            for case in runner.base.cases()]
    assert len(rows) == 32
    assert all(len(row["locked_outputs"]) == 7 for row in rows)
    assert {row["case_id"] for row in rows if row["c5_grammar_compat_changed"]} == {
        "G04-1", "G04-2", "G14-1", "G14-2"
    }
    assert any(any(value.get("grounding_status") == "NO_LEGAL_GROUNDING"
                   for value in row["locked_outputs"].values()) for row in rows)


def test_source_allowlist_contains_native_and_shared_files():
    assert "tests/test_phase6_stage_control_native.py" in runner.EXTRA
    assert "scripts/phase6_model_comparison.py" in runner.EXTRA
    assert "scripts/phase6_two_stage_probe_runtime.py" in runner.EXTRA


def ready(monkeypatch, path, *, rows=1, failure=None, kind="chat", choice_kind="NONE"):
    p = projection(kind)
    cases = [(SimpleNamespace(case_id=f"G{i:02d}-1", category="fixed"), p)
             for i in range(1, rows + 1)]
    identity = {"build": "fixed", "model_path": runner.base.PROFILES["qw9"][0],
                "template_sha256": "a" * 64, "generation_settings": {}}
    profile = {"model": {"path": identity["model_path"]},
               "argv": runner.base.launch_args("qw9")}
    (path / "plan.json").write_text(json.dumps({**runner.fixed_contract(), "source": {},
        "config_sha256": runner.CONFIG_SHA,
        "baseline": {"artifacts": runner.base.BASELINE_FILES,
                     "runtime": runner.base.safe_runtime(identity)}}))
    monkeypatch.setattr(runner, "verify", lambda _plan: (profile, cases))
    monkeypatch.setattr(runner, "sources", lambda: {})
    monkeypatch.setattr(runner, "prior_schema_hashes", lambda: {})
    contract=runner.fixed_contract()
    bound={runner.ROOT/item['path']:item['sha256'] for item in
        [contract['c5_helper'],contract['c5_review'],*contract['prior_evidence'].values()]}
    old_hash = runner.base.file_hash
    monkeypatch.setattr(runner.base, "file_hash", lambda value:
        runner.CONFIG_SHA if value == runner.CONFIG else bound.get(value) or old_hash(value))
    private = path / "private"; private.mkdir()
    monkeypatch.setattr(runner.base, "create_private_evidence_container",
                        lambda *_a, **_k: private)
    monkeypatch.setattr(runner.base, "runtime", lambda: identity)
    monkeypatch.setattr(runner.base, "owned_listener", lambda _proc: True)
    monkeypatch.setattr(runner.base, "screen", lambda *_a: {
        "structural_pass": True, "speech_act": "NONE"})
    calls = []
    class Child:
        pid = 123; returncode = None
        def poll(self): return self.returncode
        def terminate(self): self.returncode = 0
        def kill(self): self.returncode = -1
        def wait(self, **_kwargs): return self.returncode
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *_a, **_k: Child())
    def handle(request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        body = json.loads(request.content)
        if request.url.path == "/apply-template":
            messages = body["messages"]
            rendered = "".join(item["content"] for item in messages)
            if failure == "native": rendered = messages[0]["content"]
            return httpx.Response(200, json={"prompt": rendered})
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": [1] * 100})
        stage = "choice" if body["max_tokens"] == 32 else "output"
        calls.append(stage)
        value = ({"speech_act_kind": choice_kind} if stage == "choice"
                 else legacy(p, kind))
        if failure == "choice": value = {}
        if failure == "reserve" and stage == "choice":
            monkeypatch.setattr(runner.base, "_RUN_DEADLINE",
                                runtime.time.monotonic() + 119)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(value)},
            "finish_reason": "stop"}], "usage": {"prompt_tokens": 100,
            "completion_tokens": 5}})
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(runner.base, "request", lambda endpoint, body=None,
        timeout=20, **kwargs: asyncio.run(runner.base._request(
            endpoint, body, timeout, transport=transport, **kwargs)))
    return calls, private


@pytest.mark.parametrize("kind", ["chat", "none", "vote", "ability", "co_declare"])
def test_mock_lifecycle_five_actions(monkeypatch, tmp_path, kind):
    calls, private = ready(monkeypatch, tmp_path, kind=kind)
    assert runner.run(tmp_path) == 0
    assert calls == ["choice", "output"]
    result = json.loads((tmp_path / "qw9-results.json").read_text())
    assert result["owned_processes_remaining"] == 0
    row = result['rows'][0]
    assert all(row[key] is True for key in ('choice_schema_pass','candidate_schema_pass',
        'kind_match_pass','legacy_validator_pass'))
    assert row['status']=='COMPLETE'
    raw=[json.loads(line) for line in (private/'raw.jsonl').read_text(encoding='utf-8').splitlines()]
    assert raw[-1]['final_content']==raw[-2]['final_content']
    expected=legacy(projection(kind),kind)
    assert json.loads(raw[-1]['final_content'])==expected
    assert raw[-1]['final_content'] not in json.dumps(result)
    assert len(list(private.glob("*.rendered.bin"))) == 2


def test_choice_invalid_skips_output_and_keeps_denominator(monkeypatch, tmp_path):
    calls, _ = ready(monkeypatch, tmp_path, rows=32, failure="choice")
    assert runner.run(tmp_path) == 0
    result = json.loads((tmp_path / "qw9-results.json").read_text())
    assert calls == ["choice"] * 32 and len(result["rows"]) == 32
    assert all(row["output_provider_calls"] == 0 for row in result["rows"])


def test_native_failure_consumes_zero_and_keeps_all_rows(monkeypatch, tmp_path):
    calls, private = ready(monkeypatch, tmp_path, rows=32, failure="native")
    assert runner.run(tmp_path) == 2
    result = json.loads((tmp_path / "qw9-results.json").read_text())
    assert calls == [] and len(result["rows"]) == 32
    assert result["rows"][0]["error_kind"] == "TEMPLATE_INVALID"
    assert "Return exactly" not in json.dumps(result)
    assert not list(private.glob("*.consumed.json"))


def test_32_valid_choices_use_exact_maximum_64_calls(monkeypatch, tmp_path):
    calls, _ = ready(monkeypatch, tmp_path, rows=32)
    assert runner.run(tmp_path) == 0
    assert calls == ["choice", "output"] * 32


def test_no_legal_grounding_skips_output(monkeypatch, tmp_path):
    calls, _ = ready(monkeypatch, tmp_path, choice_kind="ANSWER")
    assert runner.run(tmp_path) == 0
    result = json.loads((tmp_path / "qw9-results.json").read_text())
    assert calls == ["choice"]
    assert result["rows"][0]["status"] == "NO_LEGAL_GROUNDING"
    assert result["rows"][0]["output_provider_calls"] == 0


def test_output_reserve_stops_before_output_consumption(monkeypatch, tmp_path):
    calls, private = ready(monkeypatch, tmp_path, rows=32, failure="reserve")
    assert runner.run(tmp_path) == 2
    result = json.loads((tmp_path / "qw9-results.json").read_text())
    assert calls == ["choice"] and len(result["rows"]) == 32
    assert result["rows"][0]["output_provider_calls"] == 0
    assert not list(private.glob("*.output.consumed.json"))


@pytest.mark.parametrize('field', list(runner.fixed_contract()))
@pytest.mark.parametrize('mutation', ['missing','changed','wrong_type'])
def test_each_fixed_contract_field_rejects_before_claim(monkeypatch,tmp_path,field,mutation):
    plan=copy.deepcopy(runner.fixed_contract())
    if mutation=='missing': del plan[field]
    elif mutation=='wrong_type': plan[field]=None
    else:
        value=plan[field]
        if type(value) is int: plan[field]=value+1
        elif type(value) is str: plan[field]=value+'-changed'
        elif type(value) is list: plan[field]=value+['changed']
        else: plan[field]['unexpected']=True
    (tmp_path/'plan.json').write_text(json.dumps(plan),encoding='utf-8')
    monkeypatch.setattr(runner.base,'claim_run',lambda *a:pytest.fail('no claim'))
    monkeypatch.setattr(runtime.subprocess,'Popen',lambda *a,**k:pytest.fail('no provider'))
    with pytest.raises(runner.base.StopComparison,match='PLAN_CONTRACT_CHANGED'):
        runner.run(tmp_path)


@pytest.mark.parametrize('field',['case_id','projection_sha256','baseline_input_sha256',
    'baseline_messages_sha256','choice_wire_sha256','choice_raw_sha256','canonical_choice_sha256',
    'selected_kind','branch_sha256','schema_sha256','input_sha256','messages_sha256','wire_sha256'])
def test_each_output_binding_field_rejects(field):
    p=projection();case=SimpleNamespace(case_id='G01-1')
    original=runner.base.body_for(p,runner.base.PROFILES['qw9'][0])
    body=runner.probe.choice_body(original);value={'speech_act_kind':'NONE'};raw=json.dumps(value)
    binding=runner.choice_binding(case,p,original,body,raw,value)
    binding[field]+='-changed'
    with pytest.raises(runner.base.StopComparison,match='CHOICE_BINDING_MISMATCH'):
        runner.locked_output(case,p,original,body,raw,value,binding)


@pytest.fixture(scope='module')
def frozen_fixture():
    p=projection();cases=[SimpleNamespace(case_id=f'G{i:02d}-1',category='fixed') for i in range(1,33)]
    identity={'path':'fixture','sha256':'a'}
    profile={'model':identity,'runtime_files':[],'argv':runner.base.launch_args('qw9'),'available':True}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(runner,'prior_schema_hashes',lambda:{})
        entries=[runner.entry(c,p) for c in cases]
    plan={**copy.deepcopy(runner.fixed_contract()),'source':{'stable':'a'},'baseline':{'directory':'baseline'},
        'profiles':{'qw9':profile},'tokenizer':identity,'cases':entries,
        'prior_stage_metadata':[{'case_id':c.case_id,'stages':{}} for c in cases]}
    return p,cases,identity,plan


@pytest.mark.parametrize('change',['source','c5_source','c5_review','prior_file','prior_metadata',
    'case','projection','user','wire','locked_schema','port'])
def test_frozen_input_and_evidence_drift_prevents_claim(monkeypatch,tmp_path,frozen_fixture,change):
    p,cases,identity,original=frozen_fixture;plan=copy.deepcopy(original)
    monkeypatch.setattr(runner,'sources',lambda:{'stable':'a'})
    monkeypatch.setattr(runner,'prior_schema_hashes',lambda:{})
    monkeypatch.setattr(runner,'prior_stage_metadata',lambda:original['prior_stage_metadata'])
    contract=runner.fixed_contract()
    identities={runner.ROOT/i['path']:i['sha256'] for i in
        [contract['c5_helper'],contract['c5_review'],*contract['prior_evidence'].values()]}
    identities[runner.CONFIG]=runner.CONFIG_SHA
    if change=='c5_source': identities[runner.ROOT/contract['c5_helper']['path']]='changed'
    if change=='c5_review': identities[runner.ROOT/contract['c5_review']['path']]='changed'
    if change=='prior_file': identities[runner.ROOT/contract['prior_evidence']['gc2_result']['path']]='changed'
    monkeypatch.setattr(runner.base,'file_hash',lambda path:identities[path])
    monkeypatch.setattr(runner.base,'file_identity',lambda path:identity)
    monkeypatch.setattr(runner.base,'baseline_binding',lambda *a:original['baseline'])
    monkeypatch.setattr(runner.base,'cases',lambda:cases)
    monkeypatch.setattr(runner.base,'project',lambda *a:p)
    monkeypatch.setattr(runner.base,'port_free',lambda:change!='port')
    monkeypatch.setattr(runner.base,'claim_run',lambda *a:pytest.fail('no claim'))
    monkeypatch.setattr(runtime.subprocess,'Popen',lambda *a,**k:pytest.fail('no provider'))
    if change=='source': plan['source']['stable']='changed'
    if change=='prior_metadata': plan['prior_stage_metadata'][0]['case_id']='changed'
    if change=='case': plan['cases'][0]['case_id']='changed'
    if change=='projection': plan['cases'][0]['projection_sha256']='changed'
    if change=='user': plan['cases'][0]['baseline_messages_sha256']='changed'
    if change=='wire': plan['cases'][0]['choice_wire_sha256']='changed'
    if change=='locked_schema': plan['cases'][0]['locked_outputs']['NONE']['schema_sha256']='changed'
    # Call verify directly: only its returned capability may enter the ownership lifecycle.
    with pytest.raises(runner.base.StopComparison):
        runner.verify(plan)


@pytest.mark.parametrize('field',['c5_helper','c5_review','gc2_result','ic2_result','gc2_schema_diagnostics'])
def test_final_source_includes_bound_source_and_prior_evidence(monkeypatch,field):
    plan={**runner.fixed_contract(),'source':{}}
    bound=[plan['c5_helper'],plan['c5_review'],*plan['prior_evidence'].values()]
    hashes={runner.ROOT/item['path']:item['sha256'] for item in bound}
    hashes[runner.CONFIG]=runner.CONFIG_SHA
    monkeypatch.setattr(runner,'sources',lambda:{})
    monkeypatch.setattr(runner.base,'file_hash',lambda path:hashes[path])
    assert runner.final_source(plan)=={'source_unchanged':True,'config_unchanged':True}
    item=plan[field] if field in plan else plan['prior_evidence'][field]
    hashes[runner.ROOT/item['path']]='changed'
    assert runner.final_source(plan)=={'source_unchanged':False,'config_unchanged':True}
