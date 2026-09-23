from copy import deepcopy
import json
from types import SimpleNamespace as NS

import pytest

from scripts import phase6_derived_grounding_runner as derived
from scripts import phase6_derived_grounding_snapshot as snapshot
from scripts import phase6_minimal_suite_adapter as adapter
from scripts import phase6_minimal_suite_runner as legacy
from tests.test_phase6_derived_grounding_probe import fixture


def setup_stage(monkeypatch, tmp_path):
    value, binding = fixture(4)
    body = dict(model="Qwen.gguf", max_tokens=512,
                messages=[{"role":"system", "content":"PREFIX\n\n" +
                    adapter.MINIMAL_V1_INSTRUCTION.replace(adapter.GROUNDING_INSTRUCTION, "")},
                    {"role":"user", "content":"USER"}],
                response_format={"json_schema":{"schema":{"properties":{}}}})
    legacy_body = deepcopy(body)
    legacy_body["messages"][0]["content"] = "PREFIX\n\n" + adapter.MINIMAL_V1_INSTRUCTION
    legacy_body["response_format"]["json_schema"]["schema"] = {
        "properties":{"grounding":{}},
        "required":["schema_version", "decision", "speech_act", "grounding", "utterance", "trigger_detail"]}
    body["response_format"]["json_schema"]["schema"] = {
        "properties":{},
        "required":["schema_version", "decision", "speech_act", "utterance", "trigger_detail"]}
    suite = NS(case=NS(case_id="G01-1", category="direct_question", request=None), binding=binding)
    monkeypatch.setattr(adapter, "candidate_body_without_grounding", lambda *args: deepcopy(body))
    monkeypatch.setattr(adapter, "candidate_body", lambda *args: deepcopy(legacy_body))
    monkeypatch.setattr(adapter, "candidate_wire", legacy.wire_bytes)
    monkeypatch.setattr(legacy.base, "_RUN_DEADLINE", legacy.time.monotonic() + 1200)
    monkeypatch.setattr(legacy, "measure_prompt", lambda *args: {
        "prompt_tokens_actual": 10, "rendered_bytes_sha256": "e"*64,
        "schema_changes_rendered_prompt": False})
    monkeypatch.setattr(legacy.base, "request", lambda *args, **kwargs: {
        "choices":[{"message":{"content":json.dumps(value)}, "finish_reason":"stop"}],
        "usage":{"prompt_tokens":10, "completion_tokens":20}})
    row = {**legacy.frozen_case(suite, derived.PROFILE), "call_consumed":0, "status":"NOT_STARTED",
           "raw_sha256":None, "mechanical_status":"UNKNOWN", "applicability":"UNRESOLVED"}
    budget = {"calls":0}
    def save(): pass
    return suite, row, budget, save


def test_stage_writes_verified_private_snapshot_and_public_safe_counts(monkeypatch, tmp_path):
    suite, row, budget, save = setup_stage(monkeypatch, tmp_path)
    derived.stage(suite, row, tmp_path, {"run_id":"run"}, save, budget)
    assert row["status"] == "COMPLETE" and row["mechanical_status"] == "PASS"
    assert row["derived_grounding_item_count"] == sum(row["derived_grounding_purpose_counts"].values())
    encoded = legacy.wire_bytes(row)
    assert b"canonical_ref_value" not in encoded and b"ref_key" not in encoded
    assert b"snapshot_sha256" not in encoded and b"derived_grounding_sha256" not in encoded
    assert (tmp_path / "G01-1.validation-snapshot.json").exists()
    assert (tmp_path / "G01-1.validation-snapshot.locator.json").exists()


def test_snapshot_failure_is_fixed_private_error_and_never_success(monkeypatch, tmp_path):
    suite, row, budget, save = setup_stage(monkeypatch, tmp_path)
    monkeypatch.setattr(snapshot, "write_validation_snapshot", lambda *args, **kwargs: (_ for _ in ()).throw(snapshot.SnapshotError()))
    derived.stage(suite, row, tmp_path, {"run_id":"run"}, save, budget)
    assert row["status"] == "ERROR" and row["error"] == "PRIVATE_EVIDENCE_ERROR"
    assert row["mechanical_status"] == "UNKNOWN" and budget["calls"] == 1


@pytest.mark.parametrize("invalid", ["null", "list", "missing_utterance"])
def test_derived_shape_validation_precedes_candidate_field_access(monkeypatch, tmp_path, invalid):
    suite, row, budget, save = setup_stage(monkeypatch, tmp_path)
    value, _ = fixture(4)
    if invalid == "null": candidate = None
    elif invalid == "list": candidate = []
    else:
        candidate = deepcopy(value)
        del candidate["utterance"]
    monkeypatch.setattr(legacy.base, "request", lambda *args, **kwargs: {
        "choices":[{"message":{"content":json.dumps(candidate)}, "finish_reason":"stop"}],
        "usage":{"prompt_tokens":10, "completion_tokens":20}})
    derived.stage(suite, row, tmp_path, {"run_id":"run"}, save, budget)
    assert row["status"] == "OUTPUT_INVALID" and row["error"] == "OUTPUT_INVALID"
    assert row["validation_code"] == "SHAPE_INVALID" and row["mechanical_status"] == "FAIL"


def test_new_profile_keeps_fixed_identity_budgets_and_single_command_cli(monkeypatch, tmp_path):
    monkeypatch.setattr(legacy, "git_head", lambda **kwargs: "head")
    monkeypatch.setattr(legacy, "source_identity", lambda profile: {"source":"hash"})
    monkeypatch.setattr(legacy, "approval_identity", lambda profile: {"approval":"hash"})
    monkeypatch.setattr(legacy, "external_identity", lambda: {"external":"hash"})
    monkeypatch.setattr(legacy.base, "launch_args", lambda key: ["launch"])
    monkeypatch.setattr(legacy, "suite_cases", lambda: ())
    plan = legacy.prepare(tmp_path / "out", profile=derived.PROFILE)
    assert plan["experiment"] == "minimal_derived_grounding_v1" and plan["task_id"] == "T504"
    assert plan["context"] == 8192 and plan["max_provider_calls"] == 32
    assert plan["retry"] == plan["repair"] == plan["fallback"] == 0
    assert plan["legacy_instruction_present"] is True
    assert plan["derived_delta_contract"] == legacy.derived_delta_contract()
    assert plan["derived_delta_contract"]["removed_schema_json_pointers"] == [
        "/properties/grounding", "/required/3"]
    assert "--prepare-and-supervise" in __import__("subprocess").run(
        [__import__("sys").executable, derived.__file__, "--help"], capture_output=True,
        text=True, check=True).stdout
