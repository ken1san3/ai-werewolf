"""Offline test-only plan-locked resolved-subject probe tools."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import secrets
import random
import time
import re
import os
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from scripts import phase6_quality_probe_v2 as q
from scripts.phase6_quality_runner_v2 import derived_seed
from scripts import phase6_quality_runner_v2 as runner

CONTRACT = "PHASE6_RESOLVED_SUBJECT_PLAN_LOCKED_V1"
RUBRIC = "RESOLVED_SUBJECT_PLAN_LOCKED_V1"
BUDGET = 237
CONTEXT = 8192
OWNED_EVIDENCE_BASE = runner.ROOT / "logs" / "phase6-private-evidence" / "synthetic"
ACTS = ("ANSWER", "REBUTTAL", "QUESTION", "CLAIM", "OPINION_CHANGE", "NONE")
SOURCE_ROLES = ("t550_manifest", "t550_seal", "saved_rows", "saved_requests", "saved_outcomes",
                "saved_plans", "fixture_inputs", "runner_source", "probe_source", "product_source",
                "profile", "config", "model_metadata", "design_approval", "task_packet")


class ResolvedSubjectError(ValueError):
    def __init__(self, detail="INPUT_INTEGRITY"):
        self.detail = detail if detail in {"INPUT_INTEGRITY", "JSON", "SOURCE", "WIRE", "PIN", "CONTEXT", "RUNTIME", "CREATE_ONLY"} else "INPUT_INTEGRITY"
        super().__init__("RESOLVED_SUBJECT_INPUT_INTEGRITY")


def _fail(detail="INPUT_INTEGRITY"):
    raise ResolvedSubjectError(detail)


def _walk(value):
    if value is None or type(value) in (str, bool, int): return
    if type(value) is float and math.isfinite(value): return
    if type(value) is list:
        for item in value: _walk(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str: _fail("JSON")
            _walk(item)
        return
    _fail("JSON")


def wire(value):
    _walk(value)
    try: return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    except (UnicodeEncodeError, ValueError): raise ResolvedSubjectError("JSON") from None


def digest(value):
    return hashlib.sha256(value if type(value) is bytes else wire(value)).hexdigest()


def observation_digest(value):
    _walk(value)
    try: raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (UnicodeEncodeError, ValueError): raise ResolvedSubjectError("JSON") from None
    return hashlib.sha256(raw).hexdigest()


def parse(raw: bytes):
    if type(raw) is not bytes: _fail("JSON")
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out: _fail("JSON")
            out[key] = value
        return out
    try: value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: _fail("JSON"))
    except (json.JSONDecodeError, UnicodeDecodeError): raise ResolvedSubjectError("JSON") from None
    _walk(value)
    return value


def _resolve(source, pointer):
    value = source
    if type(pointer) is not str or not pointer.startswith("/"): _fail("SOURCE")
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if type(value) is dict and token in value: value = value[token]
        elif type(value) is list and token.isdigit() and str(int(token)) == token and int(token) < len(value): value = value[int(token)]
        else: _fail("SOURCE")
    return value


def resolved_subject(fixture, opaque_id):
    if opaque_id is None: return None
    q.verify_bindings(fixture)
    binding = fixture.bindings.get(opaque_id)
    if (type(binding) is not dict or binding.get("source_kind") != "PLAYER"
            or binding.get("projection_sha256") != q.digest(fixture.source_bytes)):
        _fail("SOURCE")
    value = _resolve(fixture.source, binding.get("pointer"))
    if type(value) is not str or not value or binding.get("value_sha256") != q.digest(value): _fail("SOURCE")
    return value


def transform_request(control_ordinal1: bytes, fixture, seed: int, ordinal: int, *, saved_ordinal2: bytes | None = None):
    outer = parse(control_ordinal1)
    if type(outer) is not dict or ordinal not in (1, 2): _fail("WIRE")
    try: inner = parse(outer["messages"][1]["content"].encode())
    except (KeyError, IndexError, AttributeError, UnicodeEncodeError): _fail("WIRE")
    if inner.get("schema_version") != "t550.p-input.v2" or type(inner.get("plan")) is not dict: _fail("WIRE")
    plan = inner["plan"]
    if "subject_player_id" not in plan: _fail("WIRE")
    canonical = resolved_subject(fixture, plan["subject_player_id"])
    if canonical == plan["subject_player_id"]:
        return {"status": "INPUT_NOT_CHANGED", "reason": "INPUT_NOT_CHANGED", "wire": None,
                "binding_sha256": digest({"plan": digest(plan), "source": q.digest(fixture.source_bytes), "subject": canonical})}
    before = deepcopy(outer); plan["subject_player_id"] = canonical
    outer["messages"][1]["content"] = wire(inner).decode()
    outer["seed"] = derived_seed(seed, "message", ordinal)
    if ordinal == 1:
        permitted = {("messages", 1, "content")}
    else:
        permitted = {("messages", 1, "content"), ("seed",)}
        if saved_ordinal2 is not None:
            control2 = deepcopy(before); control2["seed"] = derived_seed(seed, "message", 2)
            if wire(control2) != saved_ordinal2: _fail("WIRE")
    # Exact outer equality after restoring the two permitted leaves.
    check = deepcopy(outer); check["messages"][1]["content"] = before["messages"][1]["content"]; check["seed"] = before["seed"]
    if check != before or outer.get("max_tokens") != BUDGET: _fail("WIRE")
    raw = wire(outer)
    return {"status": "READY", "reason": "SUBJECT_RESOLVED", "wire": raw, "wire_sha256": digest(raw),
            "changed_pointers": sorted("/" + "/".join(map(str, p)) for p in permitted)}


def chosen_intent(fixture, plan: dict, case_alias: str):
    if type(case_alias) is not str or not case_alias.startswith("case_") or len(case_alias) != 37: _fail("SOURCE")
    act = plan.get("act")
    if act not in ACTS: _fail("SOURCE")
    subject = resolved_subject(fixture, plan.get("subject_player_id"))
    reply = None
    if plan.get("reply_to") is not None:
        selected = q.selected(fixture, plan["reply_to"])
        value = selected["value"]
        if value["source"]["record_kind"] != "chat" or value["source"]["visibility"] != "PUBLIC": _fail("SOURCE")
        reply = {k: deepcopy(value[k]) for k in ("source", "actor_player_ids", "channel_id", "day", "phase", "text_excerpt", "text_truncated")}
    trigger = fixture.source["capture"]["trigger"]
    public_trigger = {k: deepcopy(trigger[k]) for k in ("kind", "day", "phase", "source")}
    current = fixture.source["grounding"]["current"]
    public_state = {k: deepcopy(current[k]) for k in ("alive_player_ids", "day", "phase", "players", "vote_candidate_player_ids")}
    payload = {"case_alias": case_alias, "act": act, "subject": subject, "reply": reply,
               "public_trigger": public_trigger, "current_public_state": public_state}
    return validate_chosen_intent({**payload, "observation_sha256": observation_digest(payload)})


def _strict_int(value):
    return type(value) is int and value >= 0


def _ids(value):
    return (type(value) is list and all(type(item) is str and item for item in value)
            and len(value) == len(set(value)))


def validate_chosen_intent(value):
    outer = {"case_alias", "act", "subject", "reply", "public_trigger", "current_public_state", "observation_sha256"}
    if type(value) is not dict or set(value) != outer: _fail("WIRE")
    alias = value["case_alias"]
    if (type(alias) is not str or len(alias) != 37 or not alias.startswith("case_")
            or any(c not in "0123456789abcdef" for c in alias[5:])): _fail("WIRE")
    if value["act"] not in ACTS or not (value["subject"] is None or type(value["subject"]) is str and value["subject"]): _fail("WIRE")
    reply = value["reply"]
    if reply is not None:
        if type(reply) is not dict or set(reply) != {"source", "actor_player_ids", "channel_id", "day", "phase", "text_excerpt", "text_truncated"}: _fail("WIRE")
        if not _ids(reply["actor_player_ids"]) or type(reply["channel_id"]) is not str or not reply["channel_id"]: _fail("WIRE")
        if not _strict_int(reply["day"]) or type(reply["phase"]) is not str or not reply["phase"] or type(reply["text_excerpt"]) is not str or type(reply["text_truncated"]) is not bool: _fail("WIRE")
        source = reply["source"]
        if type(source) is not dict or set(source) != {"record_kind", "order", "visibility"} or source["record_kind"] != "chat" or source["visibility"] != "PUBLIC" or not _strict_int(source["order"]): _fail("WIRE")
    trigger = value["public_trigger"]
    if type(trigger) is not dict or set(trigger) != {"kind", "day", "phase", "source"}: _fail("WIRE")
    if trigger["kind"] not in ("INITIAL_CHAT", "PEER_CHAT") or not _strict_int(trigger["day"]) or type(trigger["phase"]) is not str or not trigger["phase"]: _fail("WIRE")
    if trigger["source"] is not None:
        source = trigger["source"]
        if type(source) is not dict or set(source) != {"record_kind", "order", "visibility"} or source["record_kind"] != "chat" or source["visibility"] != "PUBLIC" or not _strict_int(source["order"]): _fail("WIRE")
    state = value["current_public_state"]
    if type(state) is not dict or set(state) != {"alive_player_ids", "day", "phase", "players", "vote_candidate_player_ids"}: _fail("WIRE")
    if not _ids(state["alive_player_ids"]) or not _ids(state["vote_candidate_player_ids"]) or not _strict_int(state["day"]) or type(state["phase"]) is not str or not state["phase"] or type(state["players"]) is not list: _fail("WIRE")
    player_ids = []
    for player in state["players"]:
        if type(player) is not dict or set(player) != {"player_id", "alive", "death"} or type(player["player_id"]) is not str or not player["player_id"] or type(player["alive"]) is not bool: _fail("WIRE")
        player_ids.append(player["player_id"]); death = player["death"]
        if death is not None and (type(death) is not dict or set(death) != {"day", "public_cause"} or not _strict_int(death["day"]) or type(death["public_cause"]) is not str or not death["public_cause"]): _fail("WIRE")
    if len(player_ids) != len(set(player_ids)): _fail("WIRE")
    payload = {k: value[k] for k in ("case_alias", "act", "subject", "reply", "public_trigger", "current_public_state")}
    if value["observation_sha256"] != observation_digest(payload): _fail("WIRE")
    return deepcopy(value)


def build_main_pin(rows, expected_source_sha256, expected_code_sha256):
    if type(rows) is not list or len(rows) != 96: _fail("PIN")
    ids = [row.get("row_id") for row in rows]
    if any(type(x) is not str or not x for x in ids) or len(ids) != len(set(ids)): _fail("PIN")
    counts = {"eligible": sum(r.get("status") in ("READY", "INPUT_NOT_CHANGED") for r in rows),
              "plan_missing": sum(r.get("status") == "PLAN_NOT_OBSERVED" for r in rows),
              "non_chat": sum(r.get("status") == "NOT_MESSAGE_DOMAIN" for r in rows)}
    if counts != {"eligible": 77, "plan_missing": 7, "non_chat": 12}: _fail("PIN")
    for value in (expected_source_sha256, expected_code_sha256):
        if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value): _fail("PIN")
    requests = []
    controls = []
    for row in rows:
        if row.get("status") in ("READY", "INPUT_NOT_CHANGED") and "control_ordinal1" in row:
            requests.append(digest(row["control_ordinal1"].encode()))
            if row.get("saved_ordinal2") is not None: requests.append(digest(row["saved_ordinal2"].encode()))
            if type(row.get("control_text")) is str and row["control_text"]:
                controls.append(digest(row["control_text"].encode()))
    payload = {"contract": CONTRACT, "source_sha256": expected_source_sha256, "code_sha256": expected_code_sha256,
               "rows_sha256": digest(rows), "saved_request_sha256": digest(requests),
               "control_observation_sha256": digest(controls),
               "counts": {"rows": 96, "eligible": 77, "plan_missing": 7, "non_chat": 12,
                          "saved_requests": len(requests)}}
    return {**payload, "pin_sha256": digest(payload)}


def _plain_directory(path):
    path = Path(path).absolute()
    current = Path(path.anchor)
    try:
        for component in path.parts[1:]:
            if component in (".", ".."): _fail("SOURCE")
            current /= component
            metadata = os.lstat(current)
            if (current.is_symlink() or getattr(metadata, "st_file_attributes", 0) & 0x400
                    or not current.is_dir()): _fail("SOURCE")
        if path.resolve(strict=True) != path: _fail("SOURCE")
    except OSError: raise ResolvedSubjectError("SOURCE") from None
    return path


def validate_owned_layout(input_path, source_root, profile_path, private_dir):
    root = _plain_directory(Path(input_path).absolute().parent)
    base = _plain_directory(OWNED_EVIDENCE_BASE)
    if root.parent != base or not root.name.startswith("T568-"): _fail("SOURCE")
    if (Path(source_root).absolute() != root / "source"
            or Path(profile_path).absolute() != root / "profile-freeze.json"
            or Path(private_dir).absolute() != root / "measurement"): _fail("SOURCE")
    for path in (Path(input_path), Path(profile_path)):
        try: metadata = os.lstat(path)
        except OSError: raise ResolvedSubjectError("SOURCE") from None
        if path.is_symlink() or getattr(metadata, "st_file_attributes", 0) & 0x400: _fail("SOURCE")


def validate_source_root(root: Path, manifest: dict, expected_manifest_sha256: str):
    root = _plain_directory(root)
    if type(manifest) is not dict or set(manifest) != {"contract", "files", "manifest_sha256"} or manifest["contract"] != CONTRACT:
        _fail("SOURCE")
    if manifest["manifest_sha256"] != expected_manifest_sha256 or digest({"contract": CONTRACT, "files": manifest["files"]}) != expected_manifest_sha256:
        _fail("SOURCE")
    if type(manifest["files"]) is not list: _fail("SOURCE")
    loaded = {}; previous = None; roles = []; physical = set(); resolved_names = set()
    root_sources = _plain_directory(root / "sources")
    for entry in manifest["files"]:
        if type(entry) is not dict or set(entry) != {"source_id", "role", "original_name", "sha256", "size"}: _fail("SOURCE")
        sid = entry["source_id"]
        original = entry["original_name"]; original_path = PurePosixPath(original) if type(original) is str else None
        if (type(sid) is not str or re.fullmatch(r"[a-z0-9_]{8,80}", sid) is None
                or original_path is None or not original or "\\" in original or original_path.is_absolute()
                or any(part in ("", ".", "..") for part in original_path.parts)
                or type(entry["role"]) is not str or (previous is not None and sid <= previous) or sid in loaded): _fail("SOURCE")
        previous = sid; path = Path(root) / "sources" / sid
        try:
            resolved = path.resolve(strict=True)
        except OSError: raise ResolvedSubjectError("SOURCE") from None
        try: stat = resolved.stat()
        except OSError: raise ResolvedSubjectError("SOURCE") from None
        identity = (stat.st_dev, stat.st_ino); folded = str(resolved).casefold()
        if (resolved.parent != root_sources or path.is_symlink() or getattr(path.stat(), "st_file_attributes", 0) & 0x400
                or not resolved.is_file() or identity in physical or folded in resolved_names): _fail("SOURCE")
        physical.add(identity); resolved_names.add(folded)
        try: raw = path.read_bytes()
        except OSError: raise ResolvedSubjectError("SOURCE") from None
        if len(raw) != entry["size"] or digest(raw) != entry["sha256"]: _fail("SOURCE")
        loaded[sid] = raw; roles.append(entry["role"])
    if tuple(sorted(roles)) != tuple(sorted(SOURCE_ROLES)) or len(roles) != len(SOURCE_ROLES): _fail("SOURCE")
    return loaded


def validate_identity_roles(root, manifest, expected_manifest, preflight, current_sources=None):
    loaded = validate_source_root(root, manifest, expected_manifest)
    by_role = {entry["role"]: entry["source_id"] for entry in manifest["files"]}
    for role, key in (("profile", "profile_sha256"), ("config", "config_sha256"),
                      ("model_metadata", "model_sha256"), ("design_approval", "design_sha256")):
        if digest(loaded[by_role[role]]) != preflight[key]: _fail("PIN")
    if digest(loaded[by_role["t550_seal"]]) != preflight["old_seal_sha256"]: _fail("PIN")
    approval = digest({"design_sha256": digest(loaded[by_role["design_approval"]]),
                       "task_packet_sha256": digest(loaded[by_role["task_packet"]])})
    if approval != preflight["approval_bundle_sha256"]: _fail("PIN")
    if current_sources is not None:
        for role, path in current_sources.items():
            if role not in ("runner_source", "probe_source", "product_source") or digest(Path(path).read_bytes()) != digest(loaded[by_role[role]]): _fail("SOURCE")
    return loaded


def validate_preflight(value: dict, expected_sha256: str):
    keys = {"contract", "rows", "eligible", "plan_missing", "non_chat", "saved_requests", "ordinal1", "ordinal2",
            "max_generation_calls", "budget", "context", "source_manifest_sha256", "code_sha256",
            "profile_sha256", "config_sha256", "model_sha256", "runtime_identity_sha256",
            "old_run_id", "old_seal_sha256", "new_run_id", "design_sha256", "approval_bundle_sha256",
            "preflight_sha256"}
    if type(value) is not dict or set(value) != keys or value["contract"] != CONTRACT: _fail("PIN")
    expected = (96, 77, 7, 12, 88, 77, 11, 154, 237, 8192)
    if tuple(value[k] for k in ("rows", "eligible", "plan_missing", "non_chat", "saved_requests", "ordinal1", "ordinal2",
                                "max_generation_calls", "budget", "context")) != expected: _fail("PIN")
    if value["preflight_sha256"] != expected_sha256 or digest({k: value[k] for k in value if k != "preflight_sha256"}) != expected_sha256:
        _fail("PIN")
    for key in ("source_manifest_sha256", "code_sha256", "profile_sha256", "config_sha256", "model_sha256",
                "runtime_identity_sha256", "old_seal_sha256", "design_sha256", "approval_bundle_sha256"):
        item = value[key]
        if type(item) is not str or len(item) != 64 or any(c not in "0123456789abcdef" for c in item): _fail("PIN")
    if any(type(value[key]) is not str or not value[key] for key in ("old_run_id", "new_run_id")): _fail("PIN")
    return deepcopy(value)


class RequestCache:
    def __init__(self, entries):
        self.entries = deepcopy(entries); self.root = digest(self.entries); self.sent = set()
    def get(self, key):
        if digest(self.entries) != self.root or key not in self.entries: _fail("PIN")
        return deepcopy(self.entries[key])
    def mark_sent(self, key):
        if key in self.sent: _fail("RUNTIME")
        self.sent.add(key)


def run_offline(rows, runtime, cache, *, deadline_calls=154):
    results, calls = [], 0
    try:
        for row in rows:
            if row["status"] != "READY":
                results.append({"row_id": row["row_id"], "status": row["status"], "attempts": []}); continue
            attempts = []; accepted = False
            for ordinal in (1, 2):
                key = f"{row['row_id']}:{ordinal}"; request = cache.get(key)
                if request["input_tokens"] + BUDGET + 1 > CONTEXT: _fail("CONTEXT")
                if calls >= deadline_calls: _fail("RUNTIME")
                cache.mark_sent(key); response = runtime.generate(request["body"]); calls += 1
                status = response["status"]; attempts.append({"ordinal": ordinal, "status": status})
                if status == "ACCEPTED": accepted = True; break
            results.append({"row_id": row["row_id"], "status": "ACCEPTED" if accepted else attempts[-1]["status"], "attempts": attempts})
        if len(results) != 96: _fail("RUNTIME")
        return {"contract": CONTRACT, "results": results, "generation_calls": calls}
    finally:
        runtime.close()
        if not runtime.closed: _fail("RUNTIME")


def write_once(path: Path, value):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream: stream.write(wire(value))
    except (OSError, FileExistsError): raise ResolvedSubjectError("CREATE_ONLY") from None


def new_case_aliases(count=96):
    aliases = ["case_" + secrets.token_hex(16) for _ in range(count)]
    if len(set(aliases)) != count: _fail("SOURCE")
    return aliases


def offline_summary():
    fixtures, rows = q.prepare()
    for fixture in fixtures: q.verify_bindings(fixture)
    chat = sum(f.stage == "chat_plan" for f in fixtures)
    if len(fixtures) != 32 or len(rows) != 96 or chat != 28: _fail("SOURCE")
    return {"contract": CONTRACT, "status": "OFFLINE_PASS", "fixtures": 32, "rows": 96,
            "chat_fixtures": 28, "budget": BUDGET, "context": CONTEXT, "provider_calls": 0}


def _run_bundle(value, expected_preflight, expected_manifest, expected_pin):
    keys = {"contract", "preflight", "source_manifest", "main_pin", "rows"}
    if type(value) is not dict or set(value) != keys or value["contract"] != CONTRACT: _fail("PIN")
    validate_preflight(value["preflight"], expected_preflight)
    if value["source_manifest"].get("manifest_sha256") != expected_manifest: _fail("PIN")
    pin = value["main_pin"]
    if type(pin) is not dict or pin.get("pin_sha256") != expected_pin or digest({k: pin[k] for k in pin if k != "pin_sha256"}) != expected_pin:
        _fail("PIN")
    if pin.get("source_sha256") != expected_manifest or pin.get("rows_sha256") != digest(value["rows"]): _fail("PIN")
    if pin.get("counts", {}).get("saved_requests") != 88: _fail("PIN")
    current_code = digest(Path(__file__).read_bytes())
    if value["preflight"].get("source_manifest_sha256") != expected_manifest:
        _fail("PIN")
    if value["preflight"].get("code_sha256") != current_code or pin.get("code_sha256") != current_code:
        _fail("PIN")
    if type(value["rows"]) is not list or len(value["rows"]) != 96: _fail("PIN")
    return value


def _prepare_owned_rows(bundle, fixtures, runtime, private, *, before_utility=None):
    by_case = {fixture.case.case_id: fixture for fixture in fixtures}; prepared = []; counted_requests = 0
    verified_cases = set()
    identities = [(row.get("case_id"), row.get("seed")) for row in bundle["rows"]]
    if any(type(case_id) is not str or type(seed) is not int for case_id, seed in identities) or len(set(identities)) != 96:
        _fail("PIN")
    if sum(row.get("saved_ordinal2") is not None for row in bundle["rows"]) != 11:
        _fail("PIN")
    for row in bundle["rows"]:
        if type(row) is not dict or set(row) != {"row_id", "case_id", "seed", "status", "plan", "control_ordinal1", "saved_ordinal2", "control_text"}:
            _fail("PIN")
        status = row["status"]
        if status in ("PLAN_NOT_OBSERVED", "NOT_MESSAGE_DOMAIN"):
            if any(row[k] is not None for k in ("plan", "control_ordinal1", "saved_ordinal2", "control_text")): _fail("PIN")
            prepared.append({"row_id": row["row_id"], "status": status, "attempts": []}); continue
        if status != "READY": _fail("PIN")
        if not (row["control_text"] is None or type(row["control_text"]) is str and row["control_text"]): _fail("PIN")
        fixture = by_case.get(row["case_id"])
        if fixture is None or fixture.stage != "chat_plan": _fail("SOURCE")
        if fixture.case.case_id not in verified_cases:
            q.verify_bindings(fixture); verified_cases.add(fixture.case.case_id)
        plan_raw = wire(row["plan"])
        plan = q.product.parse_and_validate_generation_v2_candidate_structure("chat_plan", plan_raw, fixture.catalog)
        control1 = row["control_ordinal1"].encode()
        if control1 != q.wire(q.body("message", fixture, derived_seed(row["seed"], "message", 1), BUDGET, plan)):
            _fail("WIRE")
        if row["saved_ordinal2"] is not None and row["saved_ordinal2"].encode() != q.wire(
                q.body("message", fixture, derived_seed(row["seed"], "message", 2), BUDGET, plan)):
            _fail("WIRE")
        requests = []
        for ordinal in (1, 2):
            transformed = transform_request(row["control_ordinal1"].encode(), fixture, row["seed"], ordinal,
                saved_ordinal2=row["saved_ordinal2"].encode() if row["saved_ordinal2"] is not None else None)
            if transformed["status"] == "INPUT_NOT_CHANGED":
                requests = []; status = "INPUT_NOT_CHANGED"; break
            body = parse(transformed["wire"])
            if before_utility is not None:
                # The check belongs immediately before native utility; keep it adjacent.
                before_utility()
                tokens, rendered = runtime.input_count(body)
            else:
                tokens, rendered = runtime.input_count(body)
            counted_requests += 1
            if tokens + BUDGET + 1 > CONTEXT: _fail("CONTEXT")
            item = {"ordinal": ordinal, "body": body, "wire_sha256": transformed["wire_sha256"],
                    "input_tokens": tokens, "rendered_sha256": rendered}
            requests.append(item)
            write_once(private / f"request-{row['row_id']}-{ordinal}.json", item)
        prepared.append({"row_id": row["row_id"], "status": status, "fixture": fixture, "plan": plan,
                         "control_text": row["control_text"], "requests": requests})
    if counted_requests != sum(len(row.get("requests", [])) for row in prepared): _fail("RUNTIME")
    return prepared


def _generate_owned(runtime, row, request):
    try:
        response = runtime.request("/v1/chat/completions", request["body"], generation=True,
                                   wire_payload=wire(request["body"]))
    except runner.GenerationDisconnected:
        return "TRANSPORT_GENERATION_LOST", None, None
    try:
        choice, usage = response["choices"][0], response["usage"]
        if (usage["prompt_tokens"] != request["input_tokens"] or type(usage["completion_tokens"]) is not int
                or usage["completion_tokens"] < 0 or choice["finish_reason"] not in ("stop", "length")):
            _fail("RUNTIME")
        if choice["finish_reason"] == "length" or usage["completion_tokens"] > BUDGET: return "LENGTH", None, response
        if choice["message"].get("reasoning_content"): return "MISSING_RESPONSE", None, response
        if runtime.count(choice["message"]["content"]) > BUDGET: return "LENGTH", None, response
        try:
            parsed = q.product.parse_and_validate_generation_v2_candidate_structure("message", choice["message"]["content"], row["fixture"].catalog)
        except Exception:
            return "STRUCTURE_INVALID", None, response
        try:
            q.text_guard(parsed.value["message"], row["fixture"])
        except Exception:
            return "GUARD_REJECT", None, response
        return "ACCEPTED", parsed.value["message"], response
    except ResolvedSubjectError: raise
    except (KeyError, IndexError, TypeError, ValueError):
        return "GUARD_REJECT", None, response


def generation_gate(*, source_root, manifest, expected_manifest, code_path, expected_code,
                    profile_path, expected_profile, owner, expected_identity, calls, deadline=None,
                    preflight=None, current_sources=None):
    if preflight is None or current_sources is None: _fail("PIN")
    validate_identity_roles(source_root, manifest, expected_manifest, preflight, current_sources)
    if (digest(Path(code_path).read_bytes()) != expected_code
            or digest(Path(profile_path).read_bytes()) != expected_profile
            or owner.runtime_identity != expected_identity or not owner.integrity): _fail("RUNTIME")
    owner.light()
    if (not runner.listener_owners(runner.existing.PORT) or calls >= 154
            or deadline is not None and time.monotonic() + 60 > deadline): _fail("RUNTIME")


def run_owned(*, input_path: Path, source_root: Path, profile_path: Path, private_dir: Path, public_path: Path,
              expected_preflight: str, expected_manifest: str, expected_pin: str, expected_input: str):
    validate_owned_layout(input_path, source_root, profile_path, private_dir)
    input_raw = Path(input_path).read_bytes()
    if digest(input_raw) != expected_input: _fail("PIN")
    bundle = _run_bundle(parse(input_raw), expected_preflight, expected_manifest, expected_pin)
    current_sources = {"runner_source": Path(runner.__file__), "probe_source": Path(q.__file__),
                       "product_source": Path(q.product.__file__)}
    validate_identity_roles(source_root, bundle["source_manifest"], expected_manifest, bundle["preflight"], current_sources)
    profile = runner.load_profile(profile_path)
    if digest(Path(profile_path).read_bytes()) != bundle["preflight"]["profile_sha256"]: _fail("PIN")
    private_dir.mkdir(parents=True, exist_ok=False)
    write_once(private_dir / "input.json", bundle)
    write_once(private_dir / "preflight.json", bundle["preflight"])
    write_once(private_dir / "source-manifest.json", bundle["source_manifest"])
    write_once(private_dir / "main-pin.json", bundle["main_pin"])
    write_once(private_dir / "profile.json", profile)
    write_once(private_dir / "claim.json", {"contract": CONTRACT, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "preflight_sha256": expected_preflight, "manifest_sha256": expected_manifest, "pin_sha256": expected_pin,
        "profile_sha256": profile["freeze_sha256"], "max_generation_calls": 154})
    process = owner = runtime = None; calls = 0; active_attempt = None
    results = [{"row_id": row["row_id"], "status": "MEASUREMENT_NOT_OBSERVED", "attempts": [], "candidate_text": None}
               for row in bundle["rows"]]
    result_index = {item["row_id"]: item for item in results}
    summary = {"contract": CONTRACT, "status": "UNKNOWN", "provider_calls": 0}
    initial_code = digest(Path(__file__).read_bytes()); initial_profile = digest(Path(profile_path).read_bytes())
    failure_stage = "LAUNCH"
    try:
        process, owner, runtime = runner.launch(profile, private_dir, "resolved-subject")
        identity = deepcopy(owner.runtime_identity); write_once(private_dir / "runtime-identity.json", identity)
        if digest(identity) != bundle["preflight"]["runtime_identity_sha256"]: _fail("RUNTIME")
        fixtures = q.prepare()[0]
        failure_stage = "PREPARE"
        deadline = time.monotonic() + 5400
        def pre_utility():
            generation_gate(source_root=source_root, manifest=bundle["source_manifest"], expected_manifest=expected_manifest,
                code_path=Path(__file__), expected_code=initial_code, profile_path=profile_path,
                expected_profile=initial_profile, owner=owner, expected_identity=identity, calls=0, deadline=deadline,
                preflight=bundle["preflight"], current_sources=current_sources)
        prepared = _prepare_owned_rows(bundle, fixtures, runtime, private_dir, before_utility=pre_utility)
        failure_stage = "GENERATE"
        for row in prepared:
            if row["status"] != "READY":
                result_index[row["row_id"]].update(status=row["status"]); continue
            attempts = []; text = None
            for request in row["requests"]:
                # Full physical custody and ownership are rechecked immediately before every body send.
                generation_gate(source_root=source_root, manifest=bundle["source_manifest"], expected_manifest=expected_manifest,
                    code_path=Path(__file__), expected_code=initial_code, profile_path=profile_path,
                    expected_profile=initial_profile, owner=owner, expected_identity=identity, calls=calls, deadline=deadline,
                    preflight=bundle["preflight"], current_sources=current_sources)
                active_attempt = (row["row_id"], request["ordinal"])
                status, text, response = _generate_owned(runtime, row, request); calls += 1
                attempt = {"ordinal": request["ordinal"], "status": status}
                attempts.append(attempt); result_index[row["row_id"]].update(status=status, attempts=deepcopy(attempts), candidate_text=text)
                write_once(private_dir / f"attempt-{row['row_id']}-{request['ordinal']}.json", attempt)
                active_attempt = None
                if response is not None:
                    write_once(private_dir / f"response-{row['row_id']}-{request['ordinal']}.json", response)
                if status == "ACCEPTED": break
            result_index[row["row_id"]].update(status=attempts[-1]["status"], attempts=attempts, candidate_text=text)
        failure_stage = "BLIND"
        aliases = new_case_aliases(96)
        blind_rows = []
        associations = []; condition_mapping = []
        for i, result in enumerate(results):
            source_row = bundle["rows"][i]
            if result["status"] == "ACCEPTED" and type(source_row["control_text"]) is str and source_row["control_text"]:
                fixture = next(f for f in fixtures if f.case.case_id == source_row["case_id"])
                observation = chosen_intent(fixture, source_row["plan"], aliases[i])
                associations.append({"case_alias": aliases[i],
                    "row_identity_sha256": digest({k: source_row[k] for k in ("row_id", "case_id", "seed")}),
                    "plan_sha256": digest(source_row["plan"]), "source_sha256": q.digest(fixture.source_bytes),
                    "subject_binding_sha256": digest({"subject": observation["subject"]}),
                    "reply_binding_sha256": None if observation["reply"] is None else digest(observation["reply"]),
                    "observation_sha256": observation["observation_sha256"]})
                pair = [("CONTROL", source_row["control_text"]), ("CANDIDATE", result["candidate_text"])]
                for condition, text_value in pair:
                    evaluation_id = "eval_" + secrets.token_hex(16)
                    blind_rows.append({"blind_id": evaluation_id, "case_alias": aliases[i], "text": text_value,
                                       "chosen_intent": observation})
                    condition_mapping.append({"blind_id": evaluation_id, "condition": condition, "case_alias": aliases[i]})
        random.SystemRandom().shuffle(blind_rows)
        packet = {"contract": RUBRIC, "rows": blind_rows}
        write_once(private_dir / "results.json", {"contract": CONTRACT, "rows": results})
        write_once(private_dir / "blind-packet.json", packet)
        write_once(private_dir / "blind-associations.json", {"contract": RUBRIC, "associations": associations})
        write_once(private_dir / "blind-condition-mapping.json", {"contract": RUBRIC, "mapping": condition_mapping})
        summary.update(status="COMPLETE", rows=96, generation_calls=calls, provider_calls=calls,
                       blind_packet_sha256=digest(packet), run_integrity=True)
    except Exception as error:
        if active_attempt is not None:
            row_id, ordinal = active_attempt
            attempt = {"ordinal": ordinal, "status": "TRANSPORT_OR_OWNERSHIP"}
            result_index[row_id]["attempts"].append(attempt); result_index[row_id]["status"] = attempt["status"]
            try: write_once(private_dir / f"attempt-{row_id}-{ordinal}.json", attempt)
            except Exception: pass
        failure_code = ("RESOLVED_SUBJECT_" + error.detail
                        if isinstance(error, ResolvedSubjectError) else "RESOLVED_SUBJECT_RUNTIME")
        summary.update(status="UNKNOWN", run_integrity=False, failure_reason=failure_code,
                       failure_stage=failure_stage)
    finally:
        if runtime is not None:
            calls = max(calls, getattr(runtime, "generation_calls", calls))
        summary["provider_calls"] = calls
        summary["generation_calls"] = calls
        if runtime is not None:
            try: runtime.close()
            except Exception: summary["run_integrity"] = False
        cleanup = runner.existing.cleanup_owned(None, process)
        summary["cleanup_confirmed"] = cleanup["owned_processes_remaining"] == 0 and not runner.listener_owners(runner.existing.PORT)
        if not summary["cleanup_confirmed"]: summary["status"] = "UNKNOWN"; summary["run_integrity"] = False
        if digest(Path(__file__).read_bytes()) != initial_code: summary["status"] = "UNKNOWN"; summary["run_integrity"] = False
        try:
            if digest(Path(input_path).read_bytes()) != expected_input or digest(Path(profile_path).read_bytes()) != initial_profile:
                summary["status"] = "UNKNOWN"; summary["run_integrity"] = False
        except OSError:
            summary["status"] = "UNKNOWN"; summary["run_integrity"] = False
        try: validate_identity_roles(source_root, bundle["source_manifest"], expected_manifest, bundle["preflight"], current_sources)
        except Exception: summary["status"] = "UNKNOWN"; summary["run_integrity"] = False
        write_once(private_dir / "cleanup.json", cleanup)
        if not (private_dir / "results.json").exists():
            try: write_once(private_dir / "results.json", {"contract": CONTRACT, "rows": results})
            except Exception: summary["run_integrity"] = False
        seal = {p.relative_to(private_dir).as_posix(): digest(p.read_bytes()) for p in sorted(private_dir.rglob("*")) if p.is_file()}
        write_once(private_dir / "seal.json", seal); summary["private_seal_sha256"] = digest(seal)
        write_once(public_path, summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--offline", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--input", type=Path); parser.add_argument("--source-root", type=Path)
    parser.add_argument("--profile-freeze", type=Path); parser.add_argument("--private-dir", type=Path)
    parser.add_argument("--expected-preflight"); parser.add_argument("--expected-manifest"); parser.add_argument("--expected-pin")
    parser.add_argument("--expected-input")
    args = parser.parse_args(argv)
    try:
        if args.offline: summary = offline_summary(); write_once(args.output, summary)
        else:
            if any(value is None for value in (args.input, args.source_root, args.profile_freeze, args.private_dir,
                                                args.expected_preflight, args.expected_manifest, args.expected_pin,
                                                args.expected_input)): _fail("PIN")
            summary = run_owned(input_path=args.input, source_root=args.source_root, profile_path=args.profile_freeze,
                private_dir=args.private_dir, public_path=args.output, expected_preflight=args.expected_preflight,
                expected_manifest=args.expected_manifest, expected_pin=args.expected_pin, expected_input=args.expected_input)
        print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception:
        print("RESOLVED_SUBJECT_INPUT_INTEGRITY")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
