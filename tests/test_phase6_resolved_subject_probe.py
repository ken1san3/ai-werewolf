from __future__ import annotations

from copy import deepcopy
import os

import pytest

from scripts import phase6_quality_probe_v2 as q
from scripts.phase6_quality_runner_v2 import derived_seed
from scripts.phase6_resolved_subject_probe import (
    BUDGET, CONTEXT, RequestCache, ResolvedSubjectError, build_main_pin, chosen_intent,
    digest, new_case_aliases, parse, run_offline, transform_request, validate_preflight, observation_digest,
    validate_source_root, wire, write_once, main, run_owned, validate_chosen_intent, generation_gate,
)
from scripts import phase6_resolved_subject_probe as probe
from scripts.phase6_resolved_subject_custody import reconstruct_expected_pin


@pytest.mark.parametrize("which", ("root", "ancestor", "sources"))
def test_source_root_rejects_reparse_at_every_directory_component(monkeypatch, tmp_path, which):
    root = tmp_path / "owned" / "source"
    root.mkdir(parents=True)
    manifest = _manifest(root)
    blocked = {"root": root, "ancestor": root.parent, "sources": root / "sources"}[which]
    original = os.lstat
    def marked(path, *args, **kwargs):
        metadata = original(path, *args, **kwargs)
        if probe.Path(path).absolute() == blocked.absolute():
            from types import SimpleNamespace
            return SimpleNamespace(st_file_attributes=0x400)
        return metadata
    monkeypatch.setattr(os, "lstat", marked)
    with pytest.raises(ResolvedSubjectError):
        validate_source_root(root, manifest, manifest["manifest_sha256"])


def test_owned_layout_rejects_exact_copy_outside_fixed_base(monkeypatch, tmp_path):
    base = tmp_path / "owned-base"
    base.mkdir()
    monkeypatch.setattr(probe, "OWNED_EVIDENCE_BASE", base)
    owned = base / "T568-public-fixture"
    outside = tmp_path / "T568-public-fixture"
    for root in (owned, outside):
        root.mkdir()
        (root / "source").mkdir()
        (root / "input.json").write_text("{}")
        (root / "profile-freeze.json").write_text("{}")
    probe.validate_owned_layout(owned / "input.json", owned / "source", owned / "profile-freeze.json", owned / "measurement")
    with pytest.raises(ResolvedSubjectError):
        probe.validate_owned_layout(outside / "input.json", outside / "source", outside / "profile-freeze.json", outside / "measurement")
    with pytest.raises(ResolvedSubjectError):
        probe.validate_owned_layout(owned / "input.json", outside / "source", owned / "profile-freeze.json", owned / "measurement")


def _fixture_plan():
    fixture = next(f for f in q.prepare()[0] if f.stage == "chat_plan")
    for raw in q.witnesses("chat_plan", fixture):
        candidate = q.product.parse_and_validate_generation_v2_candidate_structure("chat_plan", raw, fixture.catalog)
        if candidate.value["subject_player_id"] is not None:
            return fixture, candidate
    raise AssertionError("native subject plan missing")


def _control(fixture, plan, seed=4242027, ordinal=1):
    return q.wire(q.body("message", fixture, derived_seed(seed, "message", ordinal), BUDGET, plan))


def _manifest(root):
    source_dir = root / "sources"; source_dir.mkdir(parents=True, exist_ok=True); files = []
    for index, role in enumerate(sorted(probe.SOURCE_ROLES)):
        source_id = f"source_{index:02d}"; raw = ("frozen:" + role).encode()
        (source_dir / source_id).write_bytes(raw)
        files.append({"source_id": source_id, "role": role, "original_name": role + ".json",
                      "sha256": digest(raw), "size": len(raw)})
    payload = {"contract": probe.CONTRACT, "files": files}
    return {**payload, "manifest_sha256": digest(payload)}


def test_native_fixture_strict_transform_only_subject_and_seed():
    fixture, plan = _fixture_plan(); control1 = _control(fixture, plan)
    result1 = transform_request(control1, fixture, 4242027, 1)
    result2 = transform_request(control1, fixture, 4242027, 2,
                                saved_ordinal2=_control(fixture, plan, ordinal=2))
    assert result1["changed_pointers"] == ["/messages/1/content"]
    assert result2["changed_pointers"] == ["/messages/1/content", "/seed"]
    outer = parse(result1["wire"]); inner = parse(outer["messages"][1]["content"].encode())
    assert inner["plan"]["subject_player_id"] != plan.value["subject_player_id"]
    assert outer["max_tokens"] == 237


def test_saved_ordinal2_mismatch_is_rejected():
    fixture, plan = _fixture_plan(); control = _control(fixture, plan)
    with pytest.raises(ResolvedSubjectError):
        transform_request(control, fixture, 4242027, 2, saved_ordinal2=control)


def test_input_not_changed_has_no_wire():
    fixture = next(f for f in q.prepare()[0] if f.stage == "chat_plan")
    plan = next(q.product.parse_and_validate_generation_v2_candidate_structure("chat_plan", raw, fixture.catalog)
                for raw in q.witnesses("chat_plan", fixture) if parse(raw)["subject_player_id"] is None)
    result = transform_request(_control(fixture, plan), fixture, 4242027, 1)
    assert result["status"] == "INPUT_NOT_CHANGED" and result["wire"] is None


def test_all_native_chat_fixtures_have_closed_chosen_observation():
    aliases = new_case_aliases(32); seen = 0
    for fixture, alias in zip(q.prepare()[0], aliases):
        if fixture.stage != "chat_plan": continue
        plan = {"act": "NONE", "subject_player_id": None, "reply_to": None}
        observation = chosen_intent(fixture, plan, alias)
        assert set(observation) == {"case_alias", "act", "subject", "reply", "public_trigger", "current_public_state", "observation_sha256"}
        assert observation["observation_sha256"] == observation_digest({k: v for k, v in observation.items() if k != "observation_sha256"})
        assert set(observation["current_public_state"]) == {"alive_player_ids", "day", "phase", "players", "vote_candidate_player_ids"}
        seen += 1
    assert seen == 28


def test_chosen_intent_rejects_nested_type_visibility_and_hash_drift():
    fixture = next(f for f in q.prepare()[0] if f.stage == "chat_plan")
    value = chosen_intent(fixture, {"act": "NONE", "subject_player_id": None, "reply_to": None}, "case_" + "a"*32)
    assert validate_chosen_intent(value) == value
    for mutate in (
        lambda v: v.update(extra=None),
        lambda v: v["current_public_state"].update(day=True),
        lambda v: v.update(observation_sha256="0"*64),
    ):
        bad = deepcopy(value); mutate(bad)
        with pytest.raises(ResolvedSubjectError): validate_chosen_intent(bad)


@pytest.mark.parametrize("bad", [b'{"a":1,"a":2}', b'{"a":NaN}', b'[]'])
def test_strict_json_negatives(bad):
    if bad == b'[]': assert parse(bad) == []
    else:
        with pytest.raises(ResolvedSubjectError): parse(bad)


def test_main_pin_requires_exact_96_partition_input():
    rows = ([{"row_id": f"eligible-{i}", "status": "READY"} for i in range(77)]
            + [{"row_id": f"missing-{i}", "status": "PLAN_NOT_OBSERVED"} for i in range(7)]
            + [{"row_id": f"nonchat-{i}", "status": "NOT_MESSAGE_DOMAIN"} for i in range(12)])
    pin = build_main_pin(rows, "1" * 64, "2" * 64)
    assert pin["counts"] == {"rows": 96, "eligible": 77, "plan_missing": 7, "non_chat": 12, "saved_requests": 0}
    with pytest.raises(ResolvedSubjectError): build_main_pin(rows[:-1], "1" * 64, "2" * 64)


def test_main_pin_is_reconstructed_from_separately_frozen_saved_rows(tmp_path):
    rows = []; eligible=missing=nonchat=0; fixture_indexes={}; prepared_fixtures=q.prepare()[0]
    for fixture_index, fixture in enumerate(prepared_fixtures):
        for seed in q.SEEDS:
            if fixture.stage != "chat_plan":
                row={"row_id":f"n{nonchat}","case_id":fixture.case.case_id,"seed":seed,"status":"NOT_MESSAGE_DOMAIN"}; nonchat+=1
            elif missing < 7:
                row={"row_id":f"m{missing}","case_id":fixture.case.case_id,"seed":seed,"status":"PLAN_NOT_OBSERVED"}; missing+=1
            else:
                row={"row_id":f"e{eligible}","case_id":fixture.case.case_id,"seed":seed,"status":"READY","plan":{"x":eligible},
                     "control_ordinal1":"{}","saved_ordinal2":"{}" if eligible<11 else None,"control_text":f"text{eligible}"}; eligible+=1
            rows.append(row); fixture_indexes[row["row_id"]]=fixture_index
    assert (eligible,missing,nonchat)==(77,7,12)
    manifest = _manifest(tmp_path)
    members = {}; requests = []; plans = []; outcomes = []; fixtures = []; saved_row_records=[]
    for row in rows:
        rid = row["row_id"]
        native_projection=q.digest(prepared_fixtures[fixture_indexes[rid]].source_bytes)
        fixtures.append({"row_id": rid, "case_id": row["case_id"], "seed": row["seed"],"fixture_index":fixture_indexes[rid],
            "projection_sha256":native_projection,"source_sha256":"1"*64,"bindings_sha256":"2"*64,"catalog_sha256":"3"*64})
        native_value={"case_id":row["case_id"],"elapsed":1.0,"final":None if row.get("control_text") is None else {"message":row["control_text"]},
            "plan":row.get("plan"),"projection_sha256":native_projection,"sample_exhausted":False,"seed":row["seed"],"silence":False,"terminal_stage":"message"}
        if row["status"] == "NOT_MESSAGE_DOMAIN": native_value["final"]={"decision":"DECLARE","co_option_id":"co1"}
        native_name=f"actual/row-{row['case_id']}-{row['seed']}.json"; native_sha=digest(native_value); members[native_name]=native_sha
        saved_row_records.append({"run_row":row,"native":{"original_name":native_name,"member_sha256":native_sha,"value":native_value}})
        if row["status"] == "READY":
            plans.append({"row_id":rid,"plan":row["plan"],"plan_sha256":digest(row["plan"]),"original_name":native_name,"member_sha256":native_sha})
            for ordinal, field in ((1,"control_ordinal1"),(2,"saved_ordinal2")):
                if row[field] is None: continue
                name=f"actual/{rid}-message-{ordinal}-request.json"; sha=digest(row[field].encode()); members[name]=sha
                requests.append({"row_id":rid,"ordinal":ordinal,"original_name":name,"member_sha256":sha,"wire":row[field]})
                outcome_name=f"actual/{rid}-message-{ordinal}-outcome.json"; outcome_value={"status":"ACCEPTED"}; outcome_sha=digest(outcome_value); members[outcome_name]=outcome_sha
                outcomes.append({"row_id":rid,"ordinal":ordinal,"status":"ACCEPTED","original_name":outcome_name,"member_sha256":outcome_sha,"value":outcome_value})
    def put(role, value):
        entry=next(item for item in manifest["files"] if item["role"]==role); raw=wire(value)
        (tmp_path/"sources"/entry["source_id"]).write_bytes(raw); entry.update(sha256=digest(raw),size=len(raw))
    put("saved_rows", {"contract":probe.CONTRACT,"rows":saved_row_records}); put("saved_requests",{"contract":probe.CONTRACT,"requests":requests})
    put("saved_plans",{"contract":probe.CONTRACT,"plans":plans}); put("saved_outcomes",{"contract":probe.CONTRACT,"outcomes":outcomes})
    native_fixtures=parse(q.wire([dict(case_id=f.case.case_id,source=f.source,projection_sha256=q.digest(f.source_bytes),
        catalog=probe.asdict(f.catalog),bindings=f.bindings) for f in prepared_fixtures]))
    for item in fixtures:
        native=native_fixtures[item["fixture_index"]]; item.update(projection_sha256=native["projection_sha256"],
            source_sha256=digest(native["source"]),bindings_sha256=digest(native["bindings"]),catalog_sha256=digest(native["catalog"]))
    fixture_wire=wire(native_fixtures).decode(); fixture_name="fixture-inputs.json"; members[fixture_name]=digest(fixture_wire.encode())
    put("fixture_inputs",{"contract":probe.CONTRACT,"source":{"original_name":fixture_name,"member_sha256":members[fixture_name],"wire":fixture_wire},"fixtures":fixtures})
    tman={"contract":probe.CONTRACT,"members":[{"original_name":k,"sha256":members[k]} for k in sorted(members)]}
    put("t550_manifest",tman); put("t550_seal",dict(sorted(members.items())))
    payload = {"contract": probe.CONTRACT, "files": manifest["files"]}
    manifest["manifest_sha256"] = digest(payload)
    assert reconstruct_expected_pin(tmp_path, manifest, manifest["manifest_sha256"], "2"*64) == build_main_pin(rows, manifest["manifest_sha256"], "2"*64)
    requests[0]["wire"] = '{"changed":true}'; put("saved_requests",{"contract":probe.CONTRACT,"requests":requests})
    manifest["manifest_sha256"] = digest({"contract":probe.CONTRACT,"files":manifest["files"]})
    with pytest.raises(ResolvedSubjectError): reconstruct_expected_pin(tmp_path, manifest, manifest["manifest_sha256"], "2"*64)


def test_source_root_and_preflight_are_externally_pinned(tmp_path):
    manifest = _manifest(tmp_path)
    assert len(validate_source_root(tmp_path, manifest, manifest["manifest_sha256"])) == len(probe.SOURCE_ROLES)
    preflight = {"contract": probe.CONTRACT, "rows": 96, "eligible": 77, "plan_missing": 7, "non_chat": 12,
                 "saved_requests": 88, "ordinal1": 77, "ordinal2": 11, "max_generation_calls": 154,
                 "budget": 237, "context": 8192, "source_manifest_sha256": manifest["manifest_sha256"], "code_sha256": "0" * 64,
                 "profile_sha256": "1"*64, "config_sha256": "2"*64, "model_sha256": "3"*64,
                 "runtime_identity_sha256": "4"*64, "old_run_id": "OLD", "old_seal_sha256": "5"*64,
                 "new_run_id": "NEW", "design_sha256": "6"*64, "approval_bundle_sha256": "7"*64}
    preflight["preflight_sha256"] = digest(preflight)
    assert validate_preflight(preflight, preflight["preflight_sha256"])["rows"] == 96
    escaped = deepcopy(manifest); escaped["files"][0]["source_id"] = "../escape"
    escaped["manifest_sha256"] = digest({"contract":probe.CONTRACT,"files":escaped["files"]})
    with pytest.raises(ResolvedSubjectError): validate_source_root(tmp_path, escaped, escaped["manifest_sha256"])
    aliased = deepcopy(manifest); first=aliased["files"][0]; second=aliased["files"][1]
    (tmp_path/"sources"/second["source_id"]).unlink(); os.link(tmp_path/"sources"/first["source_id"], tmp_path/"sources"/second["source_id"])
    second.update(sha256=first["sha256"],size=first["size"]); aliased["manifest_sha256"]=digest({"contract":probe.CONTRACT,"files":aliased["files"]})
    with pytest.raises(ResolvedSubjectError): validate_source_root(tmp_path, aliased, aliased["manifest_sha256"])
    (tmp_path/"sources"/second["source_id"]).unlink(); (tmp_path/"sources"/second["source_id"]).write_bytes(("frozen:"+second["role"]).encode())
    (tmp_path / "sources" / "source_01").write_bytes(b"changed")
    with pytest.raises(ResolvedSubjectError): validate_source_root(tmp_path, manifest, manifest["manifest_sha256"])


def test_generation_gate_stops_before_second_send_on_source_or_owner_drift(monkeypatch, tmp_path):
    manifest = _manifest(tmp_path); code = tmp_path/"code.py"; profile = tmp_path/"profile.json"
    code.write_bytes(b"code"); profile.write_bytes(b"profile"); owner = OwnedOwner()
    monkeypatch.setattr(probe.runner, "listener_owners", lambda *_: [123])
    paths={e["role"]:tmp_path/"sources"/e["source_id"] for e in manifest["files"]}
    preflight={"profile_sha256":digest(paths["profile"].read_bytes()),"config_sha256":digest(paths["config"].read_bytes()),
        "model_sha256":digest(paths["model_metadata"].read_bytes()),"design_sha256":digest(paths["design_approval"].read_bytes()),
        "old_seal_sha256":digest(paths["t550_seal"].read_bytes()),
        "approval_bundle_sha256":digest({"design_sha256":digest(paths["design_approval"].read_bytes()),"task_packet_sha256":digest(paths["task_packet"].read_bytes())})}
    current={role:paths[role] for role in ("runner_source","probe_source","product_source")}
    args = dict(source_root=tmp_path, manifest=manifest, expected_manifest=manifest["manifest_sha256"],
        code_path=code, expected_code=digest(code.read_bytes()), profile_path=profile,
        expected_profile=digest(profile.read_bytes()), owner=owner,
        expected_identity=deepcopy(owner.runtime_identity), calls=1,preflight=preflight,current_sources=current)
    generation_gate(**args)
    first = manifest["files"][0]["source_id"]; (tmp_path/"sources"/first).write_bytes(b"drift")
    with pytest.raises(ResolvedSubjectError): generation_gate(**args)
    original=next(e for e in manifest["files"] if e["source_id"]==first); (tmp_path/"sources"/first).write_bytes(("frozen:"+original["role"]).encode())
    owner.integrity = False
    with pytest.raises(ResolvedSubjectError): generation_gate(**args)


class MockRuntime:
    def __init__(self, statuses): self.statuses = list(statuses); self.closed = False; self.calls = 0
    def generate(self, body): self.calls += 1; return {"status": self.statuses.pop(0) if self.statuses else "ACCEPTED"}
    def close(self): self.closed = True


def test_full_96_mock_runner_bounded_retry_and_cleanup():
    rows = [{"row_id": "ready", "status": "READY"}] + [{"row_id": f"skip-{i}", "status": "NOT_MESSAGE_DOMAIN"} for i in range(95)]
    entries = {"ready:1": {"body": {"x": 1}, "input_tokens": CONTEXT - BUDGET - 1},
               "ready:2": {"body": {"x": 2}, "input_tokens": 1}}
    runtime = MockRuntime(["GUARD_REJECT", "ACCEPTED"])
    result = run_offline(rows, runtime, RequestCache(entries))
    assert len(result["results"]) == 96 and result["generation_calls"] == 2 and runtime.closed


def test_cache_prevents_resend_and_cleanup_on_context_failure():
    cache = RequestCache({"ready:1": {"body": {}, "input_tokens": CONTEXT}})
    runtime = MockRuntime([])
    with pytest.raises(ResolvedSubjectError): run_offline([{"row_id": "ready", "status": "READY"}] +
        [{"row_id": f"x{i}", "status": "NOT_MESSAGE_DOMAIN"} for i in range(95)], runtime, cache)
    assert runtime.closed and runtime.calls == 0


def test_create_only(tmp_path):
    path = tmp_path / "artifact.json"; write_once(path, {"contract": "x"})
    with pytest.raises(ResolvedSubjectError): write_once(path, {"contract": "y"})


def test_offline_cli_native_gate(tmp_path):
    output = tmp_path / "offline.json"
    assert main(["--offline", "--output", str(output)]) == 0
    assert parse(output.read_bytes())["provider_calls"] == 0
    assert main(["--offline", "--output", str(output)]) == 2


class OwnedRuntime:
    def __init__(self, response, retry_all=False, after_request=None):
        self.response = response; self.retry_all = retry_all; self.after_request = after_request
        self.utility_calls = 0; self.generation_calls = 0; self.closed = False
    def input_count(self, body):
        self.utility_calls += 1
        return 10, digest({"rendered": body})
    def request(self, endpoint, body, *, generation, wire_payload):
        assert endpoint == "/v1/chat/completions" and generation
        self.generation_calls += 1
        response = deepcopy(self.response)
        if self.retry_all and self.generation_calls % 2:
            response["choices"][0]["message"]["content"] = '{"invalid":true}'
        if self.after_request is not None: self.after_request(self.generation_calls)
        return response
    def count(self, raw): return 10
    def close(self): self.closed = True


class OwnedOwner:
    def __init__(self):
        self.runtime_identity = {"build": "synthetic", "n_ctx": 8192}; self.integrity = True
    def light(self): return None


def test_owned_runtime_full96_native_fixture_lifecycle(monkeypatch, tmp_path):
    monkeypatch.setattr(probe, "OWNED_EVIDENCE_BASE", tmp_path)
    tmp_path = tmp_path / "T568-public-synthetic"
    tmp_path.mkdir()
    fixtures = q.prepare()[0]
    fixture, nonnull = _fixture_plan()
    nullplan = next(q.product.parse_and_validate_generation_v2_candidate_structure("chat_plan", raw, fixture.catalog)
                    for raw in q.witnesses("chat_plan", fixture) if parse(raw)["subject_player_id"] is None)
    rows = []; eligible_i = missing_i = nonchat_i = 0
    for native_fixture in fixtures:
        candidates = ([q.product.parse_and_validate_generation_v2_candidate_structure("chat_plan", raw, native_fixture.catalog)
                       for raw in q.witnesses("chat_plan", native_fixture)]
                      if native_fixture.stage == "chat_plan" else [])
        for seed in q.SEEDS:
            if native_fixture.stage != "chat_plan":
                rows.append({"row_id": f"nonchat-{nonchat_i}", "case_id": native_fixture.case.case_id, "seed": seed,
                    "status": "NOT_MESSAGE_DOMAIN", "plan": None, "control_ordinal1": None,
                    "saved_ordinal2": None, "control_text": None}); nonchat_i += 1; continue
            if missing_i < 7:
                rows.append({"row_id": f"missing-{missing_i}", "case_id": native_fixture.case.case_id, "seed": seed,
                    "status": "PLAN_NOT_OBSERVED", "plan": None, "control_ordinal1": None,
                    "saved_ordinal2": None, "control_text": None}); missing_i += 1; continue
            plan = next(p for p in candidates if p.value["subject_player_id"] is not None)
            plan_value = {key: list(value) if type(value) is tuple else value for key, value in plan.value.items()}
            rows.append({"row_id": f"eligible-{eligible_i}", "case_id": native_fixture.case.case_id, "seed": seed, "status": "READY",
                "plan": plan_value, "control_ordinal1": _control(native_fixture, plan, seed).decode(),
                "saved_ordinal2": _control(native_fixture, plan, seed, 2).decode() if eligible_i < 11 else None,
                "control_text": "synthetic control" if eligible_i < 69 else None})
            eligible_i += 1
    assert (eligible_i, missing_i, nonchat_i) == (77, 7, 12)
    source_root = tmp_path / "source"; manifest = _manifest(source_root)
    code_sha = digest(probe.Path(probe.__file__).read_bytes())
    profile_path = tmp_path / "profile-freeze.json"; profile_path.write_bytes(b"synthetic-profile")
    role_values = {"profile": profile_path.read_bytes(), "config": b"synthetic-config",
                   "model_metadata": b"synthetic-model", "design_approval": b"synthetic-design",
                   "task_packet": b"synthetic-task", "t550_seal": b"synthetic-old-seal",
                   "runner_source": probe.Path(probe.runner.__file__).read_bytes(),
                   "probe_source": probe.Path(q.__file__).read_bytes(),
                   "product_source": probe.Path(q.product.__file__).read_bytes()}
    for entry in manifest["files"]:
        if entry["role"] in role_values:
            raw = role_values[entry["role"]]; (source_root/"sources"/entry["source_id"]).write_bytes(raw)
            entry.update(sha256=digest(raw), size=len(raw))
    manifest["manifest_sha256"] = digest({"contract": probe.CONTRACT, "files": manifest["files"]})
    preflight = {"contract": probe.CONTRACT, "rows": 96, "eligible": 77, "plan_missing": 7, "non_chat": 12,
        "saved_requests": 88, "ordinal1": 77, "ordinal2": 11, "max_generation_calls": 154,
        "budget": 237, "context": 8192, "source_manifest_sha256": manifest["manifest_sha256"], "code_sha256": code_sha,
        "profile_sha256": digest(role_values["profile"]), "config_sha256": digest(role_values["config"]),
        "model_sha256": digest(role_values["model_metadata"]),
        "runtime_identity_sha256": digest({"build": "synthetic", "n_ctx": 8192}), "old_run_id": "OLD",
        "old_seal_sha256": digest(role_values["t550_seal"]), "new_run_id": "NEW", "design_sha256": digest(role_values["design_approval"]),
        "approval_bundle_sha256": digest({"design_sha256": digest(role_values["design_approval"]),
                                           "task_packet_sha256": digest(role_values["task_packet"])})}
    preflight["preflight_sha256"] = digest(preflight)
    pin = build_main_pin(rows, manifest["manifest_sha256"], code_sha)
    bundle = {"contract": probe.CONTRACT, "preflight": preflight, "source_manifest": manifest, "main_pin": pin, "rows": rows}
    input_path = tmp_path / "input.json"; input_path.write_bytes(wire(bundle))
    response_fixture = next(f for f in fixtures if f.case.case_id == next(r for r in rows if r["status"] == "READY")["case_id"])
    response_text = '{"message":"I will review the public record."}'
    q.text_guard("I will review the public record.", response_fixture)
    runtime = OwnedRuntime({"choices": [{"finish_reason": "stop", "message": {"content": response_text,
        "reasoning_content": ""}}], "usage": {"prompt_tokens": 10, "completion_tokens": 10}}, retry_all=True)
    owner = OwnedOwner()
    monkeypatch.setattr(probe.runner, "load_profile", lambda _: {"freeze_sha256": "f"*64})
    monkeypatch.setattr(probe.runner, "launch", lambda *args: (object(), owner, runtime))
    monkeypatch.setattr(probe.runner.existing, "cleanup_owned", lambda *_: {"owned_processes_remaining": 0})
    monkeypatch.setattr(probe.runner, "listener_owners", lambda *_: [] if runtime.closed else [123])
    summary = run_owned(input_path=input_path, source_root=source_root, profile_path=profile_path,
        private_dir=tmp_path/"measurement", public_path=tmp_path/"public.json",
        expected_preflight=preflight["preflight_sha256"], expected_manifest=manifest["manifest_sha256"],
        expected_pin=pin["pin_sha256"], expected_input=digest(input_path.read_bytes()))
    assert summary["status"] == "COMPLETE" and summary["generation_calls"] == 154, summary
    assert summary["cleanup_confirmed"] and runtime.closed
    packet = parse((tmp_path/"measurement"/"blind-packet.json").read_bytes())
    assert len(packet["rows"]) == 138 and all(r["chosen_intent"] is not None for r in packet["rows"])
    assert not any("case_id" in r or "seed" in r for r in packet["rows"])
    drift_path = source_root/"sources"/manifest["files"][0]["source_id"]; original = drift_path.read_bytes()
    second_root = tmp_path.parent / "T568-public-synthetic-failure"
    import shutil
    shutil.copytree(tmp_path, second_root, ignore=shutil.ignore_patterns("measurement", "public.json"))
    input_path = second_root / "input.json"
    profile_path = second_root / "profile-freeze.json"
    source_root = second_root / "source"
    drift_path = source_root/"sources"/manifest["files"][0]["source_id"]
    runtime = OwnedRuntime({"choices": [{"finish_reason": "stop", "message": {"content": response_text,
        "reasoning_content": ""}}], "usage": {"prompt_tokens": 10, "completion_tokens": 10}}, retry_all=True,
        after_request=lambda count: drift_path.write_bytes(b"drift") if count == 1 else None)
    owner = OwnedOwner()
    failed = run_owned(input_path=input_path, source_root=source_root, profile_path=profile_path,
        private_dir=second_root/"measurement", public_path=second_root/"public-failed.json",
        expected_preflight=preflight["preflight_sha256"], expected_manifest=manifest["manifest_sha256"],
        expected_pin=pin["pin_sha256"], expected_input=digest(input_path.read_bytes()))
    assert failed["status"] == "UNKNOWN" and failed["provider_calls"] == 1 and failed["generation_calls"] == 1
    partial = parse((second_root/"measurement"/"results.json").read_bytes())
    assert len(partial["rows"]) == 96 and partial["rows"][7]["attempts"] == [{"ordinal": 1, "status": "STRUCTURE_INVALID"}]
    assert sum(row["status"] == "MEASUREMENT_NOT_OBSERVED" for row in partial["rows"]) > 0
    drift_path.write_bytes(original)
