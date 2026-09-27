from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_client.discussion.context import canonical_json_bytes
from scripts import phase6_local_staged_probe as probe
from scripts import phase6_local_staged_runner as runner
from tests.test_phase6_two_call_probe import legacy, projection
from tests.test_phase6_quality_grounding import request_with


@pytest.fixture
def chat_scene():
    p = projection()
    old = legacy(p, "chat")
    plan, text = probe.from_legacy(old, p)
    case = SimpleNamespace(case_id="G01-1", request=request_with())
    return case, p, json.dumps(plan), json.dumps(text), old


def sender(replies, calls):
    def dispatch(stage, attempt, body):
        calls.append((stage, attempt, deepcopy(body)))
        raw, status = next(replies)
        return raw, {
            "status": status,
            "wire_sha256": runner.r.sha(runner.r.wire_bytes(body)),
            "raw_sha256": None if raw is None else runner.r.sha(raw.encode("utf-8")),
            "consumed": True,
        }
    return dispatch


def test_contract_and_exact_seed_schedule_are_finite():
    value = runner.contract()
    assert value["seeds"] == [4242027, 4242028, 4242029]
    assert (value["t_tokens"], value["p_tokens"]) == (384, 128)
    assert (value["maximum_completion_tokens_per_t_p_pair"],
            value["maximum_completion_tokens_per_row"],
            value["maximum_completion_tokens_per_program"]) == (512, 1536, 147456)
    assert (value["maximum_calls_per_row"], value["maximum_calls_per_seed"],
            value["maximum_calls"]) == (6, 192, 576)
    assert (value["block_seconds"], value["outer_seconds"],
            value["program_seconds"]) == (1200, 1320, 21600)
    assert value["transport_retry"] == value["repair"] == value["fallback"] == 0
    assert [runner.derived_seed(4242027, 2, stage, attempt)
            for stage in ("T", "P") for attempt in range(3)] == [
                4242027 + 1009 * offset for offset in range(13, 19)]


def test_t_and_p_each_use_three_attempts_and_keep_rejections(chat_scene, tmp_path):
    case, p, plan_raw, text_raw, old = chat_scene
    calls = []
    replies = iter([("{}", "GENERATED"), ("{}", "GENERATED"),
                    (plan_raw, "GENERATED"),
                    ("{}", "GENERATED"), ("{}", "GENERATED"),
                    (text_raw, "GENERATED")])
    row, final = runner.process_case(
        case, p, runner.SEEDS[0], 2, sender(replies, calls), tmp_path)
    assert final == old and row["status"] == row["comparison_status"] == "ACCEPTED"
    assert row["provider_calls"] == len(calls) == 6
    assert [item["status"] for item in row["attempts"]] == [
        "T_INVALID", "T_INVALID", "T_ACCEPTED", "P_INVALID", "P_INVALID", "P_ACCEPTED"]
    assert [body["seed"] for _, _, body in calls] == [
        runner.derived_seed(runner.SEEDS[0], 2, stage, attempt)
        for stage, attempt in (("T", 0), ("T", 1), ("T", 2),
                               ("P", 0), ("P", 1), ("P", 2))]
    assert all(body["max_tokens"] == (384 if stage == "T" else 128)
               for stage, _, body in calls)
    for stage in ("T", "P"):
        stage_bodies = []
        for observed_stage, _, body in calls:
            if observed_stage == stage:
                body.pop("seed")
                stage_bodies.append(body)
        assert stage_bodies[1:] == stage_bodies[:-1]


def test_t_exhaustion_is_choice_invalid_not_legal_none(chat_scene, tmp_path):
    case, p, _, _, _ = chat_scene
    calls = []
    row, final = runner.process_case(case, p, runner.SEEDS[0], 0,
        sender(iter([("{}", "GENERATED")] * 3), calls), tmp_path)
    assert final is None and row["status"] == "T_K_EXHAUSTED"
    assert row["comparison_status"] == "CHOICE_INVALID"
    assert [item["status"] for item in row["attempts"]] == ["T_INVALID"] * 3
    assert row["final_output_sha256"] is None and row["content_status"] == "UNKNOWN"


def test_p_exhaustion_keeps_unique_text_only_in_private_evidence(chat_scene, tmp_path):
    case, p, plan_raw, _, _ = chat_scene
    secret = "private-content-candidate-" + "x" * 220
    invalid = json.dumps({"message": secret})
    row, final = runner.process_case(case, p, runner.SEEDS[0], 0,
        sender(iter([(plan_raw, "GENERATED"), *[(invalid, "GENERATED")] * 3]), []), tmp_path)
    assert final is None and row["status"] == "P_K_EXHAUSTED"
    assert row["comparison_status"] == "FILTER_EXHAUSTED"
    assert row["content_status"] == "CONTENT_ONLY"
    assert row["content_only_sha256"] == runner.r.sha(secret.encode("utf-8"))
    assert all(item["status"] == "P_INVALID" for item in row["attempts"][1:])
    assert secret not in json.dumps(row)
    saved = runner.r.read(tmp_path / f"{case.case_id}.p2.content-only.json")
    assert saved["text"] == secret


@pytest.mark.parametrize("stage,status,terminal,comparison", [
    ("T", "TIMEOUT", "T_TIMEOUT", "CHOICE_ERROR"),
    ("T", "TRANSPORT", "T_TRANSPORT_ERROR", "CHOICE_ERROR"),
    ("P", "TIMEOUT", "P_TIMEOUT", "OUTPUT_ERROR"),
    ("P", "TRANSPORT", "P_TRANSPORT_ERROR", "OUTPUT_ERROR"),
])
def test_stage_transport_or_timeout_is_terminal_without_retry(
        chat_scene, tmp_path, stage, status, terminal, comparison):
    case, p, plan_raw, _, _ = chat_scene
    replies = [(None, status)] if stage == "T" else [(plan_raw, "GENERATED"), (None, status)]
    calls = []
    row, final = runner.process_case(case, p, runner.SEEDS[0], 0,
        sender(iter(replies), calls), tmp_path)
    assert final is None and row["status"] == terminal
    assert row["comparison_status"] == comparison and len(calls) == len(replies)


def test_nontext_action_skips_p_but_still_uses_legacy_validator_and_guard(tmp_path):
    p = projection("vote")
    old = legacy(p, "vote")
    plan, text = probe.from_legacy(old, p)
    assert text is None
    case = SimpleNamespace(case_id="G01-1", request=request_with())
    calls = []
    row, final = runner.process_case(case, p, runner.SEEDS[0], 0,
        sender(iter([(json.dumps(plan), "GENERATED")]), calls), tmp_path)
    assert final == old and row["status"] == "ACCEPTED"
    assert row["content_status"] == "NOT_APPLICABLE"
    assert [stage for stage, _, _ in calls] == ["T"]


def test_reservation_enforces_duplicate_row_seed_and_global_caps(tmp_path, monkeypatch):
    (tmp_path / "calls").mkdir()
    common = dict(run_identity="a" * 64, base_seed=1,
                  messages_sha256="b" * 64, schema_sha256="c" * 64,
                  body_sha256="d" * 64)
    runner.reserve_call(tmp_path, case_id="c1", stage="T", attempt=0, seed=10,
                        wire_sha256="1" * 64, **common)
    with pytest.raises(runner.r.Stop, match="DUPLICATE_ATTEMPT"):
        runner.reserve_call(tmp_path, case_id="c1", stage="T", attempt=0, seed=11,
                            wire_sha256="2" * 64, **common)
    with pytest.raises(runner.r.Stop, match="DUPLICATE_DISPATCH"):
        runner.reserve_call(tmp_path, case_id="c2", stage="T", attempt=0, seed=10,
                            wire_sha256="1" * 64, **common)
    monkeypatch.setattr(runner, "MAX_ROW_CALLS", 2)
    runner.reserve_call(tmp_path, case_id="c1", stage="P", attempt=0, seed=12,
                        wire_sha256="3" * 64, **common)
    with pytest.raises(runner.r.Stop, match="CALL_CAP"):
        runner.reserve_call(tmp_path, case_id="c1", stage="P", attempt=1, seed=13,
                            wire_sha256="4" * 64, **common)
    monkeypatch.setattr(runner, "MAX_SEED_CALLS", 2)
    with pytest.raises(runner.r.Stop, match="CALL_CAP"):
        runner.reserve_call(tmp_path, case_id="c3", stage="T", attempt=0, seed=14,
                            wire_sha256="5" * 64, **common)
    monkeypatch.setattr(runner, "MAX_SEED_CALLS", 192)
    monkeypatch.setattr(runner, "MAX_CALLS", 2)
    with pytest.raises(runner.r.Stop, match="CALL_CAP"):
        runner.reserve_call(tmp_path, case_id="c3", stage="T", attempt=0, seed=14,
                            wire_sha256="5" * 64, **{**common, "base_seed": 2})


def test_exact_native_wire_private_save_and_marker_precede_dispatch(chat_scene, tmp_path, monkeypatch):
    _, p, plan_raw, _, _ = chat_scene
    (tmp_path / "calls").mkdir()
    private = tmp_path / "private"
    private.mkdir()
    body = probe.plan_body(runner.r.baseline_body(p, runner.MODEL, runner.SEEDS[0]), p)
    body.update(seed=runner.derived_seed(runner.SEEDS[0], 0, "T", 0), max_tokens=384)
    payload = runner.r.wire_bytes(body)

    def count(request, *, wire_payload, private_sink):
        assert wire_payload == payload
        private_sink("exact rendered prompt")
        return {"prompt_tokens_actual": 23}

    def request(path, sent, *, timeout, wire_payload):
        assert path == "/v1/chat/completions" and sent == body and wire_payload == payload
        assert len(list((tmp_path / "calls").glob("*.json"))) == 1
        assert (private / "G01-1.t0.consumed.json").exists()
        return {"choices": [{"message": {"content": plan_raw}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 23, "completion_tokens": 12}}

    monkeypatch.setattr(runner.base, "count_prompt", count)
    monkeypatch.setattr(runner.base, "request", request)
    raw, meta = runner.execute_call(tmp_path, private, run_identity="a" * 64,
        case_id="G01-1", base_seed=runner.SEEDS[0], case_index=0,
        stage="T", attempt=0, body=body, remaining=lambda: 61,
        verify_now=lambda: None)
    assert raw == plan_raw and meta["status"] == "GENERATED"
    marker = runner.r.read(next((tmp_path / "calls").glob("*.json")))
    assert marker["wire_sha256"] == runner.r.sha(payload)
    assert marker["messages_sha256"] == runner.r.sha(canonical_json_bytes(body["messages"]))
    assert marker["schema_sha256"] == runner.r.sha(canonical_json_bytes(
        body["response_format"]["json_schema"]["schema"]))
    assert marker["body_sha256"] == runner.r.sha(runner._body_bytes(body))


def test_native_or_deadline_failure_is_zero_call(chat_scene, tmp_path, monkeypatch):
    _, p, _, _, _ = chat_scene
    (tmp_path / "calls").mkdir()
    private = tmp_path / "private"
    private.mkdir()
    body = probe.plan_body(runner.r.baseline_body(p, runner.MODEL, runner.SEEDS[0]), p)
    body.update(seed=runner.derived_seed(runner.SEEDS[0], 0, "T", 0), max_tokens=384)
    monkeypatch.setattr(runner.base, "request", lambda *a, **k: pytest.fail("provider called"))
    with pytest.raises(runner.r.Stop, match="DEADLINE"):
        runner.execute_call(tmp_path, private, run_identity="a" * 64,
            case_id="G01-1", base_seed=runner.SEEDS[0], case_index=0,
            stage="T", attempt=0, body=body, remaining=lambda: 59,
            verify_now=lambda: None)
    monkeypatch.setattr(runner.base, "count_prompt",
                        lambda *a, **k: {"prompt_tokens_actual": 8192})
    with pytest.raises(runner.r.Stop, match="NATIVE_CONTEXT"):
        runner.execute_call(tmp_path, private, run_identity="a" * 64,
            case_id="G01-1", base_seed=runner.SEEDS[0], case_index=0,
            stage="T", attempt=0, body=body, remaining=lambda: 61,
            verify_now=lambda: None)
    assert not list((tmp_path / "calls").iterdir())


def test_timeout_after_reservation_is_consumed_and_not_resendable(chat_scene, tmp_path, monkeypatch):
    _, p, _, _, _ = chat_scene
    (tmp_path / "calls").mkdir()
    private = tmp_path / "private"
    private.mkdir()
    body = probe.plan_body(runner.r.baseline_body(p, runner.MODEL, runner.SEEDS[0]), p)
    body.update(seed=runner.derived_seed(runner.SEEDS[0], 0, "T", 0), max_tokens=384)
    monkeypatch.setattr(runner.base, "count_prompt",
                        lambda *a, **k: {"prompt_tokens_actual": 3})
    monkeypatch.setattr(runner.base, "request",
                        lambda *a, **k: (_ for _ in ()).throw(runner.base.httpx.ReadTimeout("private")))
    raw, meta = runner.execute_call(tmp_path, private, run_identity="a" * 64,
        case_id="G01-1", base_seed=runner.SEEDS[0], case_index=0,
        stage="T", attempt=0, body=body, remaining=lambda: 61,
        verify_now=lambda: None)
    assert raw is None and meta["status"] == "TIMEOUT"
    assert len(list((tmp_path / "calls").glob("*.json"))) == 1
    with pytest.raises(runner.r.Stop, match="DUPLICATE_ATTEMPT"):
        runner.execute_call(tmp_path, private, run_identity="a" * 64,
            case_id="G01-1", base_seed=runner.SEEDS[0], case_index=0,
            stage="T", attempt=0, body=body, remaining=lambda: 61,
            verify_now=lambda: None)


def test_prepare_freezes_all_96_rows_and_external_gate_bindings(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_bound_file", lambda *a: None)
    monkeypatch.setattr(runner, "_approved", lambda *a: None)
    monkeypatch.setattr(runner, "_native_evidence", lambda *a: {
        "gate": "OFFLINE_WITNESS_PASS", "provider_calls": 0,
        "verified_freeze_sha256": "f" * 64, "witness_count": 216,
        "maxima": {"T": 298, "P": 17}})
    monkeypatch.setattr(runner.base, "port_free", lambda: True)
    monkeypatch.setattr(runner.base, "file_identity",
                        lambda path: {"path": str(Path(path)), "size": 1, "mtime_ns": 1,
                                      "sha256": "a" * 64})
    monkeypatch.setattr(runner.base, "launch_args", lambda model: ["server", model])
    monkeypatch.setattr(runner, "sources", lambda: {"source.py": "b" * 64})
    monkeypatch.setattr(runner, "input_identity",
                        lambda: [{"case_id": f"G{n:02}-1", "projection_sha256": "c" * 64}
                                 for n in range(32)])
    out = tmp_path / "run"
    runner.prepare(out, "d" * 64, tmp_path / "native.json", "e" * 64)
    plan = runner.r.read(out / "plan.json")
    result = runner.r.read(out / "result.json")
    assert len(plan["rows"]) == len(result["rows"]) == 96
    assert all(row["status"] == row["comparison_status"] == "NOT_RUN" for row in result["rows"])
    assert plan["bindings"]["tool_review"]["sha256"] == "d" * 64
    assert plan["bindings"]["native_evidence"]["sha256"] == "e" * 64
    assert (out / "calls").is_dir() and result["durable_call_count"] == 0


@pytest.mark.parametrize("text,approved", [
    ("# Review\n\nStatus: APPROVED\n", True),
    ("# Review\n\nStatus: CHANGES_REQUIRED\nAPPROVEDになるまで禁止\n", False),
    ("# Review\n\nThe result is APPROVED.\n", False),
    ("Status: APPROVED\nStatus: CHANGES_REQUIRED\n", False),
    ("Status: **APPROVED**\n", False),
])
def test_tool_review_requires_one_exact_status_line(tmp_path, text, approved):
    path = tmp_path / "review.md"
    path.write_text(text, encoding="utf-8")
    digest = runner.base.file_hash(path)
    if approved:
        runner._approved(path, digest, "TOOL_REVIEW_BINDING")
    else:
        with pytest.raises(runner.r.Stop, match="TOOL_REVIEW_BINDING"):
            runner._approved(path, digest, "TOOL_REVIEW_BINDING")


def test_real_offline_native_witness_binds_freeze_identities_and_sources():
    path = runner.ROOT / "logs/t510-local-staged/offline-v4/result.json"
    digest = "9f6685a0a691e76160beb7e103162e8cd8ce601bbf6a1a66b4acb0a25590017c"
    value = runner._native_evidence(path, digest)
    assert value["gate"] == "OFFLINE_WITNESS_PASS"
    assert value["provider_calls"] == 0 and value["witness_count"] == 216
    assert value["maxima"] == {"T": 298, "P": 17}


def test_complete_fake_block_has_32_rows_64_calls_and_no_public_text(
        chat_scene, tmp_path, monkeypatch):
    case, p, plan_raw, text_raw, _ = chat_scene
    cases = [SimpleNamespace(case_id=f"G{n + 1:02}-1", request=case.request) for n in range(32)]
    entries = [(item, p) for item in cases]
    model_path = runner.base.PROFILES[runner.MODEL][0]
    plan = {"deadline_utc": "2099-01-01T00:00:00+00:00",
            "profile": {"argv": ["owned-server"], "model": {"path": model_path}}}
    runner.r.write(tmp_path / "plan.json", plan)
    (tmp_path / "calls").mkdir()
    private = tmp_path / "private"
    private.mkdir()
    monkeypatch.setattr(runner, "verify", lambda *a, **k: plan)
    monkeypatch.setattr(runner, "entries", lambda: entries)
    monkeypatch.setattr(runner.base, "cases", lambda: cases)
    monkeypatch.setattr(runner.base, "port_free", lambda: True)
    monkeypatch.setattr(runner.base, "create_private_evidence_container", lambda *a, **k: private)
    monkeypatch.setattr(runner.base, "runtime", lambda: {"model_path": model_path})
    monkeypatch.setattr(runner.base, "safe_runtime", lambda value: {"n_ctx": 8192})
    monkeypatch.setattr(runner.base, "request", lambda *a, **k: {"status": "ok"})
    monkeypatch.setattr(runner.base, "owned_listener", lambda proc: True)
    monkeypatch.setattr(runner.base, "attach_performance", lambda *a: None)
    owned = []

    def launch(*args, **kwargs):
        obj = SimpleNamespace(poll=lambda: None)
        owned.append(obj)
        return obj

    monkeypatch.setattr(runner.subprocess, "Popen", launch)
    monkeypatch.setattr(runner.base, "cleanup_owned",
                        lambda monitor, provider: {"owned_processes_remaining": 0})

    def execute(out, secret, **kwargs):
        body = kwargs["body"]
        marker = runner.reserve_call(out, run_identity=kwargs["run_identity"],
            case_id=kwargs["case_id"], base_seed=kwargs["base_seed"],
            stage=kwargs["stage"], attempt=kwargs["attempt"], seed=body["seed"],
            wire_sha256=runner.r.sha(runner.r.wire_bytes(body)),
            messages_sha256="b" * 64, schema_sha256="c" * 64,
            body_sha256="d" * 64)
        return (plan_raw if kwargs["stage"] == "T" else text_raw), {
            **marker, "status": "GENERATED", "consumed": True,
            "raw_sha256": "f" * 64,
        }

    monkeypatch.setattr(runner, "execute_call", execute)
    assert runner.run_block(tmp_path, runner.SEEDS[0]) == 0
    result = runner.r.read(tmp_path / f"seed-{runner.SEEDS[0]}" / "result.json")
    assert result["status"] == "COMPLETE" and result["integrity"] is True
    assert len(result["rows"]) == 32 and all(row["status"] == "ACCEPTED" for row in result["rows"])
    assert result["provider_calls"] == result["durable_call_count"] == 64
    assert "The public claims" not in json.dumps(result)
    assert result["owned_processes_remaining"] == 0 and len(owned) == 2
    assert runner.base._RUN_DEADLINE is None


@pytest.mark.parametrize("existing", ["run-claim.json", "program-seal.json"])
def test_program_is_one_shot_and_never_implicitly_resumes(tmp_path, monkeypatch, existing):
    monkeypatch.setattr(runner, "verify", lambda *a, **k: {})
    (tmp_path / existing).touch()
    with pytest.raises(runner.r.Stop, match="RUN_ALREADY_CLAIMED"):
        runner.run_group(tmp_path)


def test_public_stop_reason_never_contains_private_exception(tmp_path, monkeypatch):
    rows = [runner.empty_row(f"G{n + 1:02}-1", seed)
            for seed in runner.SEEDS for n in range(32)]
    runner.r.write(tmp_path / "result.json", {"status": "NOT_RUN", "integrity": True,
        "rows": rows, "durable_call_count": 0, "completed_seeds": []})
    (tmp_path / "calls").mkdir()
    monkeypatch.setattr(runner, "verify", lambda *a, **k: {})
    monkeypatch.setattr(runner, "supervised_block",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("PRIVATE_EXCEPTION_TEXT")))
    assert runner.run_group(tmp_path) == 2
    result = runner.r.read(tmp_path / "result.json")
    assert result["status"] == "STOPPED" and result["stop_reason"] == "EXECUTION_ERROR"
    assert "PRIVATE_EXCEPTION_TEXT" not in json.dumps(result)
    assert len(result["rows"]) == 96 and all(row["status"] == "NOT_RUN" for row in result["rows"])
    assert runner.public_stop_code(runner.r.Stop("PRIVATE_STOP_TEXT")) == "EXECUTION_ERROR"
