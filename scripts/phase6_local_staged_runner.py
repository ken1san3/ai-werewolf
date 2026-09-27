"""Finite T510 local staged T/P probe. It never sends game actions."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_client.discussion.context import canonical_json_bytes
from scripts import phase6_local_staged_offline as offline
from scripts import phase6_local_staged_probe as probe
from scripts import phase6_recovery_runner as r


base, gb = r.base, r.gb
SEEDS = (4242027, 4242028, 4242029)
MODEL = "qw9"
K_T = K_P = 3
T_TOKENS, P_TOKENS = 384, 128
MAX_ROW_CALLS, MAX_SEED_CALLS, MAX_CALLS = 6, 192, 576
DESIGN = ROOT / "Docs/ai/design/PHASE6_LOCAL_STAGED_A_PROBE_DESIGN.md"
DESIGN_SHA = "4cedce90fecb3c81e33815341db71a0d8c79f6c7b8887800fd252914837e9e64"
DESIGN_REVIEW = ROOT / "Docs/ai/handoffs/tasks/T510_LOCAL_STAGED_A_DESIGN_REVIEW.md"
DESIGN_REVIEW_SHA = "c0f35730219db72e34f5a316f1cc96ad18395ada8e5ef4e5f2cc935f9fe774b7"
TOOL_REVIEW = ROOT / "Docs/ai/handoffs/tasks/T510_LOCAL_STAGED_TOOL_REVIEW.md"
CONFIG = r.CONFIG
PUBLIC_STOP_CODES = frozenset({
    "ATTEMPT_IDENTITY", "BLOCK_ALREADY_CLAIMED", "BLOCK_STOPPED", "BUDGET_IDENTITY",
    "CALL_ACCOUNTING", "CALL_CAP", "CALL_IDENTITY", "CLEANUP_FAILED", "DEADLINE",
    "DESIGN_BINDING", "DESIGN_REVIEW_BINDING", "DUPLICATE_ATTEMPT",
    "DUPLICATE_DISPATCH", "FROZEN_SOURCE_CHANGED", "LOAD_EXIT", "LOAD_TIMEOUT",
    "MODEL_IDENTITY", "MODEL_RUNTIME_DRIFT", "NATIVE_CONTEXT", "NATIVE_EVIDENCE_BINDING",
    "NATIVE_EVIDENCE_FREEZE", "NATIVE_EVIDENCE_GATE", "NON_OWNED_LISTENER", "OWNERSHIP",
    "PREPARE_BINDING_REQUIRED", "RESPONSE_CONTRACT", "RUN_ALREADY_CLAIMED",
    "RUNTIME_OWNERSHIP_DRIFT", "SEED_IDENTITY", "TOKEN_BUDGET", "TOKEN_MISMATCH",
    "TOOL_REVIEW_BINDING",
})


def contract():
    return {
        "task": "T510",
        "experiment": "local_staged_a_v1",
        "model": MODEL,
        "seeds": list(SEEDS),
        "k_t": K_T,
        "k_p": K_P,
        "t_tokens": T_TOKENS,
        "p_tokens": P_TOKENS,
        "maximum_completion_tokens_per_t_p_pair": T_TOKENS + P_TOKENS,
        "maximum_completion_tokens_per_row": K_T * T_TOKENS + K_P * P_TOKENS,
        "maximum_completion_tokens_per_program": len(SEEDS) * 32 *
                                                 (K_T * T_TOKENS + K_P * P_TOKENS),
        "context": 8192,
        "maximum_calls_per_row": MAX_ROW_CALLS,
        "maximum_calls_per_seed": MAX_SEED_CALLS,
        "maximum_calls": MAX_CALLS,
        "request_seconds": 60,
        "load_seconds": 180,
        "block_seconds": 1200,
        "outer_seconds": 1320,
        "program_seconds": 21600,
        "transport_retry": 0,
        "repair": 0,
        "fallback": 0,
        "seed_stride": 1009,
        "sampling": {k: v for k, v in base.SAMPLING.items()
                     if k not in ("seed", "max_tokens")},
    }


def _body_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def public_stop_code(error):
    value = str(error)
    return value if type(error) is r.Stop and value in PUBLIC_STOP_CODES else "EXECUTION_ERROR"


def sources():
    # Match the existing finite recovery runner's transitive source freeze.
    # The runner, helper, offline witness, statistics and their tests are all
    # included without relying on an experiment-delta registry.
    return r.sources()


def entries():
    return [(case, base.project(case, "baseline")) for case in base.cases()]


def input_identity():
    result = []
    for case, p in entries():
        capture = p.discussion_capture
        cat = probe.catalog(p)
        result.append({
            "case_id": case.case_id,
            "projection_sha256": p.prompt_sha256,
            "canonical_input_sha256": r.sha(canonical_json_bytes(p.canonical_input)),
            "decision_schema_sha256": r.sha(canonical_json_bytes(p.decision_schema)),
            "catalog_sha256": r.sha(canonical_json_bytes(cat)),
            "capture_id": capture.capture_id,
            "context_sha256": capture.context_sha256,
            "state_sha256": capture.state_sha256,
            "epoch": capture.epoch,
            "base_revision": capture.base_revision,
            "fact_revision": capture.fact_revision,
            "world_version": capture.world_version,
            "last_applied_seq": capture.last_applied_seq,
            "action_generation": capture.trigger.action_generation,
            "connection_generation": capture.trigger.connection_generation,
        })
    return result


def _bound_file(path: Path, digest: str, code: str):
    if not digest or not path.is_file() or base.file_hash(path) != digest:
        raise r.Stop(code)


def _approved(path: Path, digest: str, code: str):
    _bound_file(path, digest, code)
    statuses = [line for line in path.read_text(encoding="utf-8").splitlines()
                if line.startswith("Status:")]
    if statuses != ["Status: APPROVED"]:
        raise r.Stop(code)


def _native_evidence(path: Path, digest: str):
    if not digest or not path.is_file() or base.file_hash(path) != digest:
        raise r.Stop("NATIVE_EVIDENCE_BINDING")
    value = r.read(path)
    verdict = value.get("gate") if type(value) is dict else None
    freeze_path = path.parent / "freeze.json"
    if (verdict != "OFFLINE_WITNESS_PASS" or value.get("provider_calls") != 0
            or value.get("runtime_prompt_gate") != "NOT_RUN"
            or value.get("source_unchanged") is not True
            or value.get("witness_count") != 216
            or value.get("maxima") != {"T": 298, "P": 17}
            or type(value.get("rows")) is not list or len(value["rows"]) != 216
            or any(row.get("status") != "KNOWN" or row.get("fits") is not True
                   for row in value["rows"])
            or not freeze_path.is_file()
            or base.file_hash(freeze_path) != value.get("freeze_sha256")):
        raise r.Stop("NATIVE_EVIDENCE_GATE")
    frozen = r.read(freeze_path)
    identities = frozen.get("identities")
    if (frozen.get("design_sha256") != DESIGN_SHA
            or frozen.get("review_sha256") != DESIGN_REVIEW_SHA
            or frozen.get("source") != offline.witness_sources()
            or frozen.get("cases") != r.input_identity()
            or type(identities) is not dict
            or set(identities) != {"model", "tokenizer", "config"}
            or identities["model"] != base.file_identity(base.PROFILES[MODEL][0])
            or identities["config"] != base.file_identity(CONFIG)
            or any(base.file_identity(item["path"]) != item for item in identities.values())):
        raise r.Stop("NATIVE_EVIDENCE_FREEZE")
    value = dict(value)
    value["verified_freeze_sha256"] = base.file_hash(freeze_path)
    return value


def empty_row(case_id: str, seed: int):
    return {
        "case_id": case_id,
        "seed": seed,
        "status": "NOT_RUN",
        "comparison_status": "NOT_RUN",
        "structural_pass": False,
        "provider_calls": 0,
        "attempts": [],
        "accepted_t_attempt": None,
        "accepted_p_attempt": None,
        "final_output_sha256": None,
        "content_status": "UNKNOWN",
        "content_only_sha256": None,
        "semantic_outcome": 0,
    }


def prepare(out: Path, tool_review_sha: str, native_path: Path, native_sha: str):
    _bound_file(DESIGN, DESIGN_SHA, "DESIGN_BINDING")
    # This exact independently reviewed artifact contains its approved Round 2
    # decision as well as historical CHANGES_REQUIRED text. Its reviewed SHA is
    # the approval identity; substring parsing would be ambiguous.
    _bound_file(DESIGN_REVIEW, DESIGN_REVIEW_SHA, "DESIGN_REVIEW_BINDING")
    _approved(TOOL_REVIEW, tool_review_sha, "TOOL_REVIEW_BINDING")
    native = _native_evidence(native_path, native_sha)
    if not base.port_free():
        raise r.Stop("NON_OWNED_LISTENER")
    model, quant, server, _ = base.PROFILES[MODEL]
    runtime = [base.file_identity(p) for p in [server, *sorted(server.parent.glob("*.dll"))]]
    now = datetime.now(timezone.utc)
    cases = input_identity()
    plan = {
        "contract": contract(),
        "source": sources(),
        "cases": cases,
        "profile": {
            "model": base.file_identity(model),
            "quantization": quant,
            "argv": base.launch_args(MODEL),
            "runtime_files": runtime,
        },
        "config": base.file_identity(CONFIG),
        "bindings": {
            "design": {"path": str(DESIGN.relative_to(ROOT)), "sha256": DESIGN_SHA},
            "design_review": {"path": str(DESIGN_REVIEW.relative_to(ROOT)),
                              "sha256": DESIGN_REVIEW_SHA},
            "tool_review": {"path": str(TOOL_REVIEW.relative_to(ROOT)),
                            "sha256": tool_review_sha},
            "native_evidence": {"path": str(native_path.resolve()), "sha256": native_sha,
                                "verdict": native["gate"],
                                "freeze_sha256": native["verified_freeze_sha256"],
                                "witness_count": native["witness_count"],
                                "maxima": native["maxima"]},
        },
        "started_at_utc": now.isoformat(),
        "deadline_utc": (now + timedelta(seconds=21600)).isoformat(),
        "rows": [empty_row(case["case_id"], seed) for seed in SEEDS for case in cases],
    }
    out.mkdir(parents=True, exist_ok=False)
    r.write(out / "plan.json", plan, exclusive=True)
    r.write(out / "freeze.json", {"plan_sha256": base.file_hash(out / "plan.json")},
            exclusive=True)
    (out / "calls").mkdir()
    r.write(out / "result.json", {
        "status": "NOT_RUN",
        "integrity": True,
        "plan_sha256": base.file_hash(out / "plan.json"),
        "rows": deepcopy(plan["rows"]),
        "durable_call_count": 0,
        "completed_seeds": [],
    }, exclusive=True)
    print("T510_PLAN_FROZEN", len(plan["rows"]), flush=True)


def verify(out: Path, *, files: bool = True):
    plan = r.read(out / "plan.json")
    frozen = r.read(out / "freeze.json")
    bindings = plan["bindings"]
    native_path = Path(bindings["native_evidence"]["path"])
    if (base.file_hash(out / "plan.json") != frozen["plan_sha256"]
            or plan["contract"] != contract()
            or plan["source"] != sources()
            or plan["cases"] != input_identity()
            or plan["profile"]["argv"] != base.launch_args(MODEL)
            or plan["config"] != base.file_identity(CONFIG)):
        raise r.Stop("FROZEN_SOURCE_CHANGED")
    _bound_file(DESIGN, bindings["design"]["sha256"], "DESIGN_BINDING")
    _bound_file(DESIGN_REVIEW, bindings["design_review"]["sha256"],
                "DESIGN_REVIEW_BINDING")
    _approved(TOOL_REVIEW, bindings["tool_review"]["sha256"], "TOOL_REVIEW_BINDING")
    if files:
        native = _native_evidence(native_path, bindings["native_evidence"]["sha256"])
        if (native["verified_freeze_sha256"] != bindings["native_evidence"]["freeze_sha256"]
                or native["witness_count"] != bindings["native_evidence"]["witness_count"]
                or native["maxima"] != bindings["native_evidence"]["maxima"]):
            raise r.Stop("NATIVE_EVIDENCE_BINDING")
    elif (not native_path.is_file() or not (native_path.parent / "freeze.json").is_file()
          or base.file_hash(native_path) != bindings["native_evidence"]["sha256"]
          or base.file_hash(native_path.parent / "freeze.json")
          != bindings["native_evidence"]["freeze_sha256"]):
        raise r.Stop("NATIVE_EVIDENCE_BINDING")
    if files and any(base.file_identity(item["path"]) != item for item in
                     [plan["profile"]["model"], *plan["profile"]["runtime_files"]]):
        raise r.Stop("MODEL_RUNTIME_DRIFT")
    return plan


def derived_seed(base_seed: int, case_index: int, stage: str, attempt: int):
    stage_index = 0 if stage == "T" else 1
    if stage not in ("T", "P") or not 0 <= attempt < 3:
        raise r.Stop("ATTEMPT_IDENTITY")
    return base_seed + 1009 * (6 * case_index + 3 * stage_index + attempt + 1)


def _markers(out: Path):
    return [r.read(path) for path in sorted((out / "calls").glob("*.json"))]


def reserve_call(out: Path, *, run_identity: str, case_id: str, base_seed: int,
                 stage: str, attempt: int, seed: int, wire_sha256: str,
                 messages_sha256: str | None = None, schema_sha256: str | None = None,
                 body_sha256: str | None = None):
    digests = (run_identity, wire_sha256, messages_sha256, schema_sha256, body_sha256)
    if any(type(value) is not str or len(value) != 64
           or any(char not in "0123456789abcdef" for char in value) for value in digests):
        raise r.Stop("CALL_IDENTITY")
    markers = _markers(out)
    row = [item for item in markers if item["base_seed"] == base_seed
           and item["case_id"] == case_id]
    seed_calls = [item for item in markers if item["base_seed"] == base_seed]
    if len(markers) >= MAX_CALLS or len(seed_calls) >= MAX_SEED_CALLS or len(row) >= MAX_ROW_CALLS:
        raise r.Stop("CALL_CAP")
    if any(item["run_identity"] == run_identity and item["base_seed"] == base_seed
           and item["case_id"] == case_id and item["stage"] == stage
           and item["attempt"] == attempt for item in markers):
        raise r.Stop("DUPLICATE_ATTEMPT")
    if any(item["run_identity"] == run_identity and item["derived_seed"] == seed
           and item["wire_sha256"] == wire_sha256 for item in markers):
        raise r.Stop("DUPLICATE_DISPATCH")
    key = f"{run_identity}-{base_seed}-{case_id}-{stage.lower()}{attempt}"
    marker = {
        "run_identity": run_identity,
        "case_id": case_id,
        "base_seed": base_seed,
        "stage": stage,
        "attempt": attempt,
        "derived_seed": seed,
        "wire_sha256": wire_sha256,
        "messages_sha256": messages_sha256,
        "schema_sha256": schema_sha256,
        "body_sha256": body_sha256,
        "started_at_utc": r.stamp(),
    }
    r.write(out / "calls" / (key + ".json"), marker, exclusive=True)
    return marker


def _reject_reuse(out: Path, *, run_identity: str, case_id: str, base_seed: int,
                  stage: str, attempt: int, seed: int, wire_sha256: str):
    markers = _markers(out)
    if any(item["run_identity"] == run_identity and item["base_seed"] == base_seed
           and item["case_id"] == case_id and item["stage"] == stage
           and item["attempt"] == attempt for item in markers):
        raise r.Stop("DUPLICATE_ATTEMPT")
    if any(item["run_identity"] == run_identity and item["derived_seed"] == seed
           and item["wire_sha256"] == wire_sha256 for item in markers):
        raise r.Stop("DUPLICATE_DISPATCH")


def _safe_attempt(meta, stage: str, attempt: int, status: str):
    keys = ("wire_sha256", "messages_sha256", "schema_sha256", "body_sha256",
            "rendered_sha256", "prompt_tokens_actual", "max_tokens",
            "provider_prompt_tokens", "completion_tokens", "finish_reason", "raw_sha256",
            "response_sha256", "started_at_utc", "ended_at_utc", "latency_real_sec")
    return {"stage": stage, "attempt": attempt, "status": status, "consumed": True,
            **{key: meta[key] for key in keys if key in meta}}


def execute_call(out: Path, private: Path, *, run_identity: str, case_id: str,
                 base_seed: int, case_index: int, stage: str, attempt: int, body,
                 remaining, verify_now):
    expected = T_TOKENS if stage == "T" else P_TOKENS
    seed = derived_seed(base_seed, case_index, stage, attempt)
    if body.get("max_tokens") != expected or body.get("seed") != seed:
        raise r.Stop("BUDGET_IDENTITY")
    if remaining() < 60:
        raise r.Stop("DEADLINE")
    verify_now()
    payload = r.wire_bytes(body)
    wire_sha = r.sha(payload)
    messages_sha = r.sha(canonical_json_bytes(body["messages"]))
    schema_sha = r.sha(canonical_json_bytes(body["response_format"]["json_schema"]["schema"]))
    body_sha = r.sha(_body_bytes(body))
    key = f"{case_id}.{stage.lower()}{attempt}"
    _reject_reuse(out, run_identity=run_identity, case_id=case_id,
                  base_seed=base_seed, stage=stage, attempt=attempt, seed=seed,
                  wire_sha256=wire_sha)
    r.write_bytes(private / (key + ".request.bin"), payload)
    rendered_capture = {}

    def sink(rendered):
        rendered_bytes = rendered.encode("utf-8")
        rendered_capture["rendered_sha256"] = r.sha(rendered_bytes)
        r.write_bytes(private / (key + ".rendered.bin"), rendered_bytes)

    measured = base.count_prompt(body, wire_payload=payload, private_sink=sink)
    prompt_tokens = measured.get("prompt_tokens_actual")
    if type(prompt_tokens) is not int or prompt_tokens + expected + 1 > 8192:
        raise r.Stop("NATIVE_CONTEXT")
    if remaining() < 60:
        raise r.Stop("DEADLINE")
    marker = reserve_call(out, run_identity=run_identity, case_id=case_id,
                          base_seed=base_seed, stage=stage, attempt=attempt, seed=seed,
                          wire_sha256=wire_sha, messages_sha256=messages_sha,
                          schema_sha256=schema_sha, body_sha256=body_sha)
    meta = {
        **marker,
        **measured,
        **rendered_capture,
        "max_tokens": expected,
        "consumed": True,
        "status": "STARTED",
    }
    # The public and private durable markers both precede generation. A failure
    # after the public marker is consumed and is never eligible for resend.
    r.write(private / (key + ".consumed.json"), meta, exclusive=True)
    started = time.monotonic()
    text = None
    try:
        response = base.request("/v1/chat/completions", body, timeout=60, wire_payload=payload)
        response_path = private / (key + ".response.json")
        r.write(response_path, response, exclusive=True)
        meta["response_sha256"] = base.file_hash(response_path)
        item = response["choices"][0]
        content = item["message"].get("content")
        reasoning = item["message"].get("reasoning_content")
        usage = response["usage"]
        if reasoning not in (None, "") or type(content) is not str:
            raise r.Stop("RESPONSE_CONTRACT")
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        if type(prompt) is not int or prompt != prompt_tokens:
            raise r.Stop("TOKEN_MISMATCH")
        if type(completion) is not int or not 0 <= completion <= expected:
            raise r.Stop("TOKEN_BUDGET")
        finish = base.fixed_value(item.get("finish_reason"), {"stop", "length", "content_filter"})
        meta.update(provider_prompt_tokens=prompt, completion_tokens=completion,
                    finish_reason=finish, raw_sha256=r.sha(content.encode("utf-8")))
        meta["status"] = ("GENERATED" if finish == "stop" else
                          "LENGTH" if finish == "length" else "CONTENT_FILTER")
        # A length-limited response cannot be accepted, but a unique strict text
        # remains available for private content-only annotation.
        text = content if finish in ("stop", "length") else None
    except (base.httpx.TimeoutException, TimeoutError):
        meta["status"] = "TIMEOUT"
    except base.httpx.HTTPError:
        meta["status"] = "TRANSPORT"
    except Exception:
        meta["status"] = "ERROR"
        raise
    finally:
        meta.update(ended_at_utc=r.stamp(), latency_real_sec=time.monotonic() - started)
        r.write(private / (key + ".outcome.json"), meta, exclusive=True)
    return text, meta


def _content_only(raw, body):
    if type(raw) is not str:
        return None
    try:
        value = gb.strict_json(raw)
    except ValueError:
        return None
    schema = body["response_format"]["json_schema"]["schema"]
    names = list(schema.get("properties", {}))
    if type(value) is not dict or len(names) != 1 or type(value.get(names[0])) is not str:
        return None
    text = value[names[0]]
    return {"field": names[0], "text": text, "sha256": r.sha(text.encode("utf-8"))}


def _mechanical(case, p, legacy):
    raw = canonical_json_bytes(legacy).decode("utf-8")
    checked = r.mechanical(case, p, raw)
    return raw, checked


def comparison_status(status: str):
    if status in ("ACCEPTED", "T_ACCEPTED", "P_ACCEPTED"):
        return "ACCEPTED"
    if status in ("T_K_EXHAUSTED", "T_INVALID", "T_LENGTH", "T_PROVIDER_ERROR"):
        return "CHOICE_INVALID"
    if status in ("P_K_EXHAUSTED",):
        return "FILTER_EXHAUSTED"
    if status in ("P_INVALID", "P_LENGTH", "P_PROVIDER_ERROR", "TEXT_GUARD_REJECT"):
        return "OUTPUT_INVALID"
    if status in ("T_TIMEOUT", "T_TRANSPORT_ERROR"):
        return "CHOICE_ERROR"
    if status in ("P_TIMEOUT", "P_TRANSPORT_ERROR"):
        return "OUTPUT_ERROR"
    return status if status == "NOT_RUN" else "ERROR"


def _finish(row, final):
    row["comparison_status"] = comparison_status(row["status"])
    for attempt in row["attempts"]:
        attempt["comparison_status"] = comparison_status(attempt["status"])
    return row, final


def process_case(case, p, base_seed: int, case_index: int, dispatch, private: Path):
    row = empty_row(case.case_id, base_seed)
    baseline = r.baseline_body(p, MODEL, base_seed)

    def call(stage, attempt, body):
        raw, meta = dispatch(stage, attempt, body)
        row["provider_calls"] += 1
        row["attempts"].append(_safe_attempt(meta, stage, attempt, meta["status"]))
        return raw, meta

    for t_attempt in range(K_T):
        t_body = deepcopy(probe.plan_body(baseline, p))
        t_body.update(seed=derived_seed(base_seed, case_index, "T", t_attempt),
                      max_tokens=T_TOKENS)
        raw, meta = call("T", t_attempt, t_body)
        if meta["status"] in ("TIMEOUT", "TRANSPORT"):
            status = "T_TIMEOUT" if meta["status"] == "TIMEOUT" else "T_TRANSPORT_ERROR"
            row["attempts"][-1]["status"] = status
            row["status"] = status
            return _finish(row, None)
        if meta["status"] != "GENERATED":
            row["attempts"][-1]["status"] = "T_LENGTH" if meta["status"] == "LENGTH" else "T_PROVIDER_ERROR"
            continue
        try:
            plan = probe.validate_plan(raw, p)
        except (TypeError, ValueError, KeyError):
            row["attempts"][-1]["status"] = "T_INVALID"
            continue
        row["attempts"][-1]["status"] = "T_ACCEPTED"
        row["accepted_t_attempt"] = t_attempt
        try:
            p_template = probe.message_body(baseline, plan, p)
        except ValueError as error:
            if str(error) == "NO_MESSAGE_ACTION":
                p_template = None
            else:
                row["attempts"][-1]["status"] = "T_INVALID"
                row["accepted_t_attempt"] = None
                continue
        except (TypeError, KeyError):
            row["attempts"][-1]["status"] = "T_INVALID"
            row["accepted_t_attempt"] = None
            continue

        if p_template is None:
            try:
                legacy = probe.validate_final(plan, None, p)
                final_raw, checked = _mechanical(case, p, legacy)
            except (TypeError, ValueError, KeyError):
                row["attempts"][-1]["status"] = "T_INVALID"
                row["accepted_t_attempt"] = None
                continue
            if not checked["filter_pass"]:
                row["attempts"][-1].update(status="TEXT_GUARD_REJECT", **checked)
                row["accepted_t_attempt"] = None
                continue
            row.update(status="ACCEPTED", structural_pass=True,
                       final_output_sha256=r.sha(final_raw.encode("utf-8")),
                       content_status="NOT_APPLICABLE")
            return _finish(row, legacy)

        for p_attempt in range(K_P):
            p_body = deepcopy(p_template)
            p_body.update(seed=derived_seed(base_seed, case_index, "P", p_attempt),
                          max_tokens=P_TOKENS)
            p_raw, p_meta = call("P", p_attempt, p_body)
            if p_meta["status"] in ("TIMEOUT", "TRANSPORT"):
                status = "P_TIMEOUT" if p_meta["status"] == "TIMEOUT" else "P_TRANSPORT_ERROR"
                row["attempts"][-1]["status"] = status
                row["status"] = status
                return _finish(row, None)
            candidate = _content_only(p_raw, p_body)
            if candidate is not None:
                r.write(private / f"{case.case_id}.p{p_attempt}.content-only.json",
                        candidate, exclusive=True)
                row["content_status"] = "CONTENT_ONLY"
                row["content_only_sha256"] = candidate["sha256"]
                row["attempts"][-1]["content_only_sha256"] = candidate["sha256"]
            if p_meta["status"] != "GENERATED":
                row["attempts"][-1]["status"] = "P_LENGTH" if p_meta["status"] == "LENGTH" else "P_PROVIDER_ERROR"
                continue
            try:
                legacy = probe.validate_final(plan, p_raw, p)
                final_raw, checked = _mechanical(case, p, legacy)
            except (TypeError, ValueError, KeyError):
                row["attempts"][-1]["status"] = "P_INVALID"
                continue
            if not checked["filter_pass"]:
                row["attempts"][-1].update(status="TEXT_GUARD_REJECT", **checked)
                continue
            row["attempts"][-1].update(status="P_ACCEPTED", **checked)
            row.update(status="ACCEPTED", structural_pass=True,
                       accepted_p_attempt=p_attempt,
                       final_output_sha256=r.sha(final_raw.encode("utf-8")),
                       content_status="GENERATED")
            return _finish(row, legacy)
        row["status"] = "P_K_EXHAUSTED"
        return _finish(row, None)
    row["status"] = "T_K_EXHAUSTED"
    return _finish(row, None)


def _row_markers(markers, seed, case_id):
    return [item for item in markers if item["base_seed"] == seed and item["case_id"] == case_id]


def _reconcile(result, out: Path, private: Path | None):
    markers = _markers(out)
    seed = result["seed"]
    for row in result["rows"]:
        owned = _row_markers(markers, seed, row["case_id"])
        seen = {(item["stage"], item["attempt"]) for item in row["attempts"]}
        for marker in owned:
            identity = (marker["stage"], marker["attempt"])
            if identity in seen:
                continue
            safe = {"stage": marker["stage"], "attempt": marker["attempt"],
                    "status": "UNKNOWN_CONSUMED", "consumed": True,
                    "wire_sha256": marker["wire_sha256"],
                    "started_at_utc": marker["started_at_utc"]}
            if private is not None:
                key = f"{row['case_id']}.{marker['stage'].lower()}{marker['attempt']}"
                outcome = private / (key + ".outcome.json")
                consumed = private / (key + ".consumed.json")
                if outcome.exists() or consumed.exists():
                    value = r.read(outcome if outcome.exists() else consumed)
                    safe = _safe_attempt(value, marker["stage"], marker["attempt"],
                                         value.get("status", "UNKNOWN_CONSUMED"))
            row["attempts"].append(safe)
        row["attempts"].sort(key=lambda item: (0 if item["stage"] == "T" else 1, item["attempt"]))
        row["provider_calls"] = len(owned)
        if owned and row["status"] == "NOT_RUN":
            row["status"] = "ERROR"
            row["comparison_status"] = "ERROR"
        for attempt in row["attempts"]:
            attempt["comparison_status"] = comparison_status(attempt["status"])
    result["durable_call_count"] = sum(1 for item in markers if item["base_seed"] == seed)
    accounted = sum(row["provider_calls"] for row in result["rows"])
    if accounted != result["durable_call_count"]:
        result.update(status="STOPPED", stop_reason="CALL_ACCOUNTING", integrity=False)


def run_block(out: Path, seed: int):
    if seed not in SEEDS:
        raise r.Stop("SEED_IDENTITY")
    plan = verify(out)
    target = out / f"seed-{seed}"
    target.mkdir(exist_ok=True)
    deadline = datetime.fromisoformat(plan["deadline_utc"]).timestamp()
    if deadline - time.time() <= 60:
        raise r.Stop("DEADLINE")
    if not base.port_free():
        raise r.Stop("NON_OWNED_LISTENER")
    run_identity = base.file_hash(out / "plan.json")
    result = {
        "seed": seed,
        "status": "STARTING",
        "integrity": False,
        "plan_sha256": run_identity,
        "rows": [empty_row(case.case_id, seed) for case in base.cases()],
    }
    proc = monitor = private = None
    started = time.monotonic()
    end = min(started + 1200, started + deadline - time.time())

    def save():
        result.update(real_duration_sec=time.monotonic() - started,
                      provider_calls=sum(row["provider_calls"] for row in result["rows"]))
        r.write(target / "result.json", result)

    with r.lease(out):
        r.write(target / "claim.json", {"started_at_utc": r.stamp()}, exclusive=True)
        base._RUN_DEADLINE = end
        try:
            save()
            private = base.create_private_evidence_container(
                ROOT / "logs/phase6-private-evidence", evidence_kind="synthetic",
                task_id=f"T510QW9{seed}", created_at_utc=datetime.now(timezone.utc))
            r.write(target / "locator.json", {"path": str(private)}, exclusive=True)
            with (private / "server.log").open("xb") as log:
                proc = subprocess.Popen(plan["profile"]["argv"],
                    cwd=Path(plan["profile"]["argv"][0]).parent,
                    stdout=log, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            while True:
                if proc.poll() is not None:
                    raise r.Stop("LOAD_EXIT")
                if time.monotonic() - started > 180:
                    raise r.Stop("LOAD_TIMEOUT")
                try:
                    if base.request("/health", timeout=2).get("status") == "ok":
                        break
                except (base.httpx.HTTPError, TimeoutError, base.StopComparison):
                    pass
                time.sleep(0.25)
            if proc.poll() is not None or not base.owned_listener(proc) or proc.poll() is not None:
                raise r.Stop("OWNERSHIP")
            identity = base.runtime()
            if Path(identity["model_path"]).resolve() != Path(plan["profile"]["model"]["path"]).resolve():
                raise r.Stop("MODEL_IDENTITY")
            r.write(private / "runtime.json", identity, exclusive=True)
            result["runtime"] = base.safe_runtime(identity)
            with (private / "monitor.log").open("xb") as log:
                monitor = subprocess.Popen([
                    sys.executable, str(ROOT / "scripts/monitor_phase6_gpu.py"),
                    "--output", str(private / "gpu.jsonl"), "--max-seconds", "1200",
                    "--watch-pid", str(os.getpid())], stdout=log, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

            def verify_now():
                verify(out, files=False)
                if (proc.poll() is not None or not base.owned_listener(proc)
                        or proc.poll() is not None or base.runtime() != identity):
                    raise r.Stop("RUNTIME_OWNERSHIP_DRIFT")

            for index, (case, p) in enumerate(entries()):
                if end - time.monotonic() < 60:
                    result["status"] = "DEADLINE"
                    break
                row_started = time.monotonic()
                r.write_bytes(private / f"{case.case_id}.projection.json",
                              canonical_json_bytes(p.canonical_input))

                def dispatch(stage, attempt, body):
                    return execute_call(out, private, run_identity=run_identity,
                        case_id=case.case_id, base_seed=seed, case_index=index,
                        stage=stage, attempt=attempt, body=body,
                        remaining=lambda: end - time.monotonic(), verify_now=verify_now)

                row, final = process_case(case, p, seed, index, dispatch, private)
                row["row_latency_real_sec"] = time.monotonic() - row_started
                r.write(private / f"{case.case_id}.final.json", {
                    "final": final,
                    "final_output_sha256": row["final_output_sha256"],
                }, exclusive=True)
                result["rows"][index] = row
                save()
                print("T510", seed, case.case_id, row["status"], flush=True)
            else:
                result["status"] = "COMPLETE"
            verify(out)
            result["integrity"] = True
        except Exception as error:
            result.update(status="STOPPED",
                          stop_reason=public_stop_code(error),
                          integrity=False)
            if private is not None:
                import traceback
                r.write(private / "failure.json", {
                    "exception": type(error).__name__,
                    "traceback": traceback.format_exc(),
                }, exclusive=True)
        finally:
            base._RUN_DEADLINE = None
            result.update(base.cleanup_owned(monitor, proc))
            if result["owned_processes_remaining"] or any(key.endswith("_cleanup_error") for key in result):
                result.update(status="STOPPED", stop_reason="CLEANUP_FAILED", integrity=False)
            _reconcile(result, out, private)
            if private is not None:
                if (private / "server.log").exists():
                    base.attach_performance(result, private)
                result["private_artifacts"] = {
                    path.name: base.file_hash(path) for path in private.iterdir() if path.is_file()
                }
            save()
            r.write(target / "seal.json", {"result_sha256": base.file_hash(target / "result.json")},
                    exclusive=True)
    return 0 if result["integrity"] else 2


def supervised_block(out: Path, seed: int):
    plan = verify(out)
    target = out / f"seed-{seed}"
    if (target / "claim.json").exists() or (target / "outer").exists():
        raise r.Stop("BLOCK_ALREADY_CLAIMED")
    remaining = datetime.fromisoformat(plan["deadline_utc"]).timestamp() - time.time()
    if remaining <= 60:
        raise r.Stop("DEADLINE")
    target.mkdir(exist_ok=True)
    private = base.create_private_evidence_container(
        ROOT / "logs/phase6-private-evidence", evidence_kind="synthetic",
        task_id=f"T510OUTER{seed}", created_at_utc=datetime.now(timezone.utc))
    r.write(target / "outer-locator.json", {"path": str(private)}, exclusive=True)
    result = r.supervise([
        sys.executable, str(Path(__file__).resolve()), "--output", str(out.resolve()),
        "--block", str(seed)], target / "outer", raw_directory=private,
        limit_seconds=min(1320, remaining))
    if (result.get("error_kind") or result["exit_code"] != 0 or result["outer_timeout"]
            or not result["ownership_complete"] or result["owned_alive_after"] != 0):
        raise r.Stop("BLOCK_STOPPED")


def _merge_seed(program, block):
    by_key = {(row["seed"], row["case_id"]): row for row in block["rows"]}
    program["rows"] = [by_key.get((row["seed"], row["case_id"]), row)
                       for row in program["rows"]]


def run_group(out: Path):
    verify(out)
    if (out / "run-claim.json").exists() or (out / "program-seal.json").exists():
        raise r.Stop("RUN_ALREADY_CLAIMED")
    r.write(out / "run-claim.json", {"started_at_utc": r.stamp()}, exclusive=True)
    program = r.read(out / "result.json")
    program.update(status="RUNNING", integrity=True)
    try:
        for seed in SEEDS:
            try:
                supervised_block(out, seed)
            finally:
                block_path = out / f"seed-{seed}" / "result.json"
                if block_path.exists():
                    block = r.read(block_path)
                    _merge_seed(program, block)
                    if block["integrity"] and block["status"] == "COMPLETE":
                        program["completed_seeds"].append(seed)
                    else:
                        program["integrity"] = False
                    program["status"] = "RUNNING"
                    program["durable_call_count"] = len(_markers(out))
                    r.write(out / "result.json", program)
        program["status"] = ("COMPLETE" if all(row["status"] != "NOT_RUN" for row in program["rows"])
                             else "PARTIAL")
    except Exception as error:
        program.update(status="STOPPED",
                       stop_reason=public_stop_code(error),
                       integrity=False)
    finally:
        program["durable_call_count"] = len(_markers(out))
        if sum(row["provider_calls"] for row in program["rows"]) != program["durable_call_count"]:
            program.update(status="STOPPED", stop_reason="CALL_ACCOUNTING", integrity=False)
        r.write(out / "result.json", program)
        r.write(out / "program-seal.json", {"result_sha256": base.file_hash(out / "result.json")},
                exclusive=True)
    return 0 if program["integrity"] else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--block", type=int, choices=SEEDS)
    parser.add_argument("--tool-review-sha")
    parser.add_argument("--native-evidence", type=Path)
    parser.add_argument("--native-evidence-sha")
    args = parser.parse_args()
    if args.block is not None and (args.prepare or args.run):
        parser.error("--block cannot be combined with --prepare/--run")
    if args.block is None and not (args.prepare or args.run):
        parser.error("choose --prepare, --run, or both")
    try:
        if args.block is not None:
            return run_block(args.output, args.block)
        if args.prepare:
            if not (args.tool_review_sha and args.native_evidence and args.native_evidence_sha):
                raise r.Stop("PREPARE_BINDING_REQUIRED")
            prepare(args.output, args.tool_review_sha, args.native_evidence,
                    args.native_evidence_sha)
        if args.run:
            return run_group(args.output)
    except Exception as error:
        code = public_stop_code(error)
        print("T510_STOPPED", code if code != "EXECUTION_ERROR" else "COMMAND_ERROR", flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
