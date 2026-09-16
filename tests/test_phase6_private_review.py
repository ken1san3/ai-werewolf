from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.windows_private
from ai_client.brain import BrainActionContext, BrainActionOption, BrainInput
from ai_client.discussion.context import (
    AuthorizedChatChannelContext,
    AuthorizedDiscussionContext,
    BoundDiscussionContext,
    CountParityWinCondition,
    canonical_sha256,
)
from ai_client.discussion.model import DiscussionTrigger
from ai_client.discussion.state import (
    DiscussionStateStore,
    DiscussionViews,
    evidence_ref_for_record,
)
from ai_client.llm import LLMBrainConfig, ShortChatConfig
from ai_client.llm.decision import parse_llm_output
from ai_client.llm.prompt import project_brain_input, canonical_prompt_json
from ai_client.network import AbilityAction, ChatAction, CoDeclareAction, VoteAction
from ai_client.world import (
    AbilityResultView,
    ChatRecord,
    CoView,
    Freshness,
    HistoryRetention,
    HistoryView,
    PhaseView,
    PlayerView,
    SelfView,
    TransportObservationView,
    WorldSnapshot,
)
from tests.fixtures.phase6_semantic_backend import Phase6SemanticBackend, semantic_response
from tests.fixtures.phase6_evidence import create_private_evidence_container


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "phase6_private_review.py"
VECTORS = ROOT / "tests" / "fixtures" / "phase6_private_review_vectors.json"
SPEC = importlib.util.spec_from_file_location("phase6_private_review", SCRIPT)


# 実装前のserializerから取得した固定bytes。実行時のlogsや現実装から期待値を生成しない。
_OLD_HTTP_FAILURE_GENERATIONS = (
    (
        'v1',
        (
            (b'{"after_state_sha256":null,"attempt_ordinal":1,"backend":{"backend_type":"synthe'
             b'tic","config_fingerprint":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
             b'bbbbbbbbbbb","endpoint_origin":"http://127.0.0.1:1","endpoint_path":"/unused","m'
             b'odel":"fixture"},"backend_error_code":"HTTP_STATUS","base_revision":0,"before_st'
             b'ate_sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","'
             b'capture_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","'
             b'completion_tokens":null,"context_sha256":"cccccccccccccccccccccccccccccccccccccc'
             b'cccccccccccccccccccccccccc","day":1,"decision":null,"finish_reason":null,"game_i'
             b'd":"synthetic","latency_microseconds":1,"phase":"day","player_id":"p","prompt_by'
             b'tes":2,"prompt_json":"{}","prompt_sha256":"44136fa355b3678a1146ad16f7e8649e94fb4'
             b'fc21fe77e8310c060f61caaff8a","prompt_tokens":null,"proposal":null,"proposal_sha2'
             b'56":null,"provider_model":null,"recorded_at_utc":"2026-09-15T00:00:00Z","request'
             b'_id":"phase6:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","'
             b'response_bytes":null,"response_sha256":null,"response_text":null,"schema_version'
             b'":"aiwolf.ai-discussion-generation.v1","status":"BACKEND_FAILED","validation_cod'
             b'e":null,"world_version":1}\n')
        ),
        'a40d8912fbfa1f705fc776d64494daca53893d5b95647bbba18870e17eced831',
    ),
    (
        'v2',
        (
            (b'{"after_state_sha256":null,"attempt_ordinal":1,"backend":{"backend_type":"synthe'
             b'tic","config_fingerprint":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
             b'bbbbbbbbbbb","endpoint_origin":"http://127.0.0.1:1","endpoint_path":"/unused","m'
             b'odel":"fixture"},"backend_error_code":"HTTP_STATUS","base_revision":0,"before_st'
             b'ate_sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","'
             b'capture_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","'
             b'completion_tokens":null,"context_sha256":"cccccccccccccccccccccccccccccccccccccc'
             b'cccccccccccccccccccccccccc","day":1,"decision":null,"finish_reason":null,"game_i'
             b'd":"synthetic","latency_microseconds":1,"phase":"day","player_id":"p","prompt_by'
             b'tes":2,"prompt_json":"{}","prompt_rejection_code":null,"prompt_sha256":"44136fa3'
             b'55b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a","prompt_tokens":null,"'
             b'proposal":null,"proposal_sha256":null,"provider_model":null,"recorded_at_utc":"2'
             b'026-09-15T00:00:00Z","request_id":"phase6:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
             b'aaaaaaaaaaaaaaaaaaaaaaaaaa","response_bytes":null,"response_sha256":null,"respon'
             b'se_text":null,"schema_version":"aiwolf.ai-discussion-generation.v2","status":"BA'
             b'CKEND_FAILED","validation_code":null,"world_version":1}\n')
        ),
        'ba0cb00eb637a1e2c0b57997379dad3bf75ed3c6a4bb633081a9eb75886e8374',
    ),
)


@pytest.mark.parametrize("version,expected,expected_sha", _OLD_HTTP_FAILURE_GENERATIONS)
def test_http_detail_preserves_old_v1_v2_bytes_and_private_processing_boundary(
    tmp_path: Path, version: str, expected: bytes, expected_sha: str,
) -> None:
    from ai_client.llm.types import (
        AiDiscussionGenerationRecord, AiDiscussionGenerationRecordV2,
        AiDiscussionGenerationStatus, BackendIdentity, LLMBackendErrorCode,
        serialize_ai_audit,
    )

    fields = json.loads(expected)
    fields["backend"] = BackendIdentity(**fields["backend"])
    fields["status"] = AiDiscussionGenerationStatus(fields["status"])
    fields["backend_error_code"] = LLMBackendErrorCode(fields["backend_error_code"])
    record_type = AiDiscussionGenerationRecord if version == "v1" else AiDiscussionGenerationRecordV2
    actual = serialize_ai_audit(record_type(**fields))
    assert actual == expected
    assert hashlib.sha256(actual).hexdigest() == expected_sha
    assert b"backend_error_detail" not in actual

    # 既存collectorを通した旧形式の受理記録に、private admission detailを併置する。
    run_dir, owner, checklist, checklist_value, manifest = _case(
        tmp_path, full_matrix=True, generation_versions=(version,),
    )
    ai_dir = run_dir / "g" / "ai"
    old_shards = {entry["path"]: (ai_dir / entry["path"]).read_bytes()
                  for entry in manifest["shards"].values()}
    detail = "synthetic-provider-secret-do-not-publish"
    admission_path = ai_dir / manifest["metadata"]["path"]
    manifest["metadata"] = _entry(admission_path, ai_dir, [{
        "event": "PROVIDER_CALL_TERMINAL", "backend_code": "HTTP_STATUS",
        "http_status": 400, "backend_error_detail": detail,
    }])
    manifest_bytes = _write_json(ai_dir / "manifest.json", manifest)
    checklist_value["evidence_manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    _write_json(checklist, checklist_value)
    output = owner / "aggregate-with-private-detail.json"
    result = _invoke(run_dir.resolve(), checklist.resolve(), output.resolve())
    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    public_text = output.read_text(encoding="utf-8")
    public = json.loads(public_text)
    assert public["human_quality_pass"] is True
    assert public["population_count"] == 2
    assert "backend_error_detail" not in public_text and detail not in public_text
    assert json.loads(admission_path.read_bytes())["backend_error_detail"] == detail
    assert all((ai_dir / path).read_bytes() == payload for path, payload in old_shards.items())

    # metadataにdetailがあっても、未知generation versionを受理する理由にはしない。
    shard = ai_dir / manifest["shards"]["self"]["path"]
    records = [json.loads(line) for line in shard.read_bytes().splitlines()]
    records[0]["schema_version"] = "aiwolf.ai-discussion-generation.v99"
    manifest["shards"]["self"] = _entry(shard, ai_dir, records)
    manifest_bytes = _write_json(ai_dir / "manifest.json", manifest)
    checklist_value["evidence_manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    _write_json(checklist, checklist_value)
    rejected_path = owner / "unknown-version.json"
    rejected = _invoke(run_dir.resolve(), checklist.resolve(), rejected_path.resolve())
    assert rejected.returncode != 0
    rejected_text = rejected_path.read_text(encoding="utf-8")
    assert json.loads(rejected_text)["human_quality_pass"] is False
    assert "backend_error_detail" not in rejected_text and detail not in rejected_text
    assert detail not in rejected.stdout + rejected.stderr


assert SPEC is not None and SPEC.loader is not None
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


@pytest.fixture(scope="session")
def private_case_root(tmp_path_factory):
    base = ROOT / "logs" / "phase6-private-evidence"
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    return create_private_evidence_container(
        base, pytest_basetemp=tmp_path_factory.getbasetemp(),
        evidence_kind="synthetic",
        task_id="T293",
        created_at_utc=datetime.now(timezone.utc),
    )


@pytest.fixture
def tmp_path(private_case_root: Path, request) -> Path:
    # Synthetic acceptance inputs, too, are never owned by pytest retention.
    path = private_case_root / hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:24]
    path.mkdir(mode=0o700)
    return path


def _canonical_sha(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _capture_id(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _write_json(path: Path, value: object) -> bytes:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    path.write_bytes(payload)
    path.chmod(0o600)
    return payload


def _write_jsonl(path: Path, values: list[dict[str, object]]) -> bytes:
    payload = b"".join(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        for value in values
    )
    path.write_bytes(payload)
    path.chmod(0o600)
    return payload


def _entry(path: Path, run_dir: Path, records: list[dict[str, object]]) -> dict[str, object]:
    payload = _write_jsonl(path, records)
    return {
        "path": path.relative_to(run_dir).as_posix(), "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(), "record_count": len(records), "terminal": True,
    }


def _projection(player: str, capture_label: str, *, kind: str = "chat", responsive: bool = True, day: int = 1, channel_public: bool = True):
    """Build one synthetic audit input through the public Phase 6 API."""
    players = ("self", "p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8")
    peer = next(candidate for candidate in players if candidate != player)
    context = AuthorizedDiscussionContext(
        "aiwolf.discussion-context.v1", "g", player, "opaque-role", (), "a" * 64,
        "opaque-team", "opaque-count", "opaque-attack", "opaque-inspect", "opaque-medium",
        (CountParityWinCondition("count_parity", "opaque-count", "other", "gte"),), (), (),
        (AuthorizedChatChannelContext("opaque-channel", channel_public),), False, (), False,
    )
    bound = BoundDiscussionContext("a" * 64, canonical_sha256(context), context)
    world_version = 8 + int(_capture_id(capture_label)[:8], 16)
    records = (
        ChatRecord(7, day, "day", "opaque-channel", peer, peer, f"synthetic-source-{capture_label}"),
    ) if responsive else ()
    retention = HistoryRetention(len(records), len(records), 0, 0, None, 7 if records else None,
                               7 if records else None, 128, 65536, True)
    snapshot = WorldSnapshot(
        world_version, Freshness.CURRENT, True, 7, tuple(PlayerView(value, value) for value in players), players,
        (), PhaseView("day", day), SelfView(player, "opaque-role", ()), (), retention,
    )
    history = HistoryView(records, True, retention)
    co = CoView((), (), True, retention)
    ability = AbilityResultView((), True, retention)
    views = DiscussionViews(snapshot, history, co, ability,
                            TransportObservationView(world_version, None, None, False, (), None))
    source = evidence_ref_for_record(records[0], bound_context=bound) if records and kind == "chat" else None
    trigger_kind = {
        "chat": "PEER_CHAT" if responsive else "INITIAL_CHAT",
        "vote": "PRE_VOTE", "ability": "ABILITY", "co_declare": "CO_OPPORTUNITY",
    }[kind]
    trigger = DiscussionTrigger("reaction_chat" if kind in {"chat", "co_declare"} else "vote_ability",
                              trigger_kind, day, "day", 1, 1, 7 if source else 0, source)
    capture = DiscussionStateStore(bound).capture(views, trigger)
    common = dict(connection_generation=1, action_generation=1, phase="day", day=day, type=kind)
    if kind == "chat":
        handle = ChatAction(**common, channel="opaque-channel")
    elif kind == "vote":
        handle = VoteAction(**common, valid_targets=(peer,), target_count=1, allows_abstain=False)
    elif kind == "ability":
        handle = AbilityAction(**common, ability_id="opaque-ability", description=None,
                               valid_targets=(peer,), target_count=1, uses_remaining=1)
    else:
        handle = CoDeclareAction(**common, claimed_role_ids=("opaque-role",))
    request = BrainInput(snapshot, BrainActionContext(world_version, 7, 7, True,
                         (BrainActionOption("action:0", handle),)), history, co, ability, capture)
    return project_brain_input(request, config=LLMBrainConfig(short_chat=ShortChatConfig()))


def _generation(player: str, capture: str, text: str, event: str, *, kind: str = "chat", responsive: bool = True, day: int = 1, channel_public: bool = True) -> dict[str, object]:
    projection = _projection(player, capture, kind=kind, responsive=responsive, day=day, channel_public=channel_public)
    projected = json.loads(projection.messages[1].content)
    response = semantic_response(projected)
    if kind == "chat":
        response["decision"]["message"] = text
    prompt = canonical_prompt_json(projection.messages, projection.decision_schema)
    response_text = json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    parse_llm_output(response_text, projection=projection)
    decision = {"kind": kind, "option_id": "action:0", "text": response["decision"].get("message", response["decision"].get("comment")),
        "vote_target_player_id": response["decision"].get("target_player_id"), "ability_id": None,
        "ability_target_player_ids": response["decision"].get("target_player_ids", []),
        "claimed_role_id": response["decision"].get("claimed_role_id"), "report_kind": None,
        "report_target_player_id": None, "claimed_result": None}
    if kind == "ability":
        decision["ability_id"] = "opaque-ability"
    proposal = response["discussion"]
    return {"schema_version": review.GENERATION_SCHEMA, "recorded_at_utc": "2026-09-13T00:00:00Z",
        "game_id": "g", "player_id": player,
        "request_id": "phase6:" + str(projected["capture"]["capture_id"]),
        "capture_id": projected["capture"]["capture_id"], "phase": projected["capture"]["trigger"]["phase"],
        "day": projected["capture"]["trigger"]["day"], "world_version": projected["capture"]["world_version"],
        "backend": asdict(Phase6SemanticBackend.identity), "attempt_ordinal": 1,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "prompt_bytes": len(prompt.encode()),
        "prompt_json": prompt, "response_sha256": hashlib.sha256(response_text.encode()).hexdigest(),
        "response_bytes": len(response_text.encode()), "response_text": response_text,
        "latency_microseconds": 100, "provider_model": "fixture", "finish_reason": "stop",
        "prompt_tokens": None, "completion_tokens": None, "status": "DECISION",
        "backend_error_code": None, "validation_code": None, "decision": decision,
        "context_sha256": projected["capture"]["context_sha256"],
        "before_state_sha256": projected["capture"]["state_sha256"],
        "after_state_sha256": None, "base_revision": projected["capture"]["base_revision"], "proposal": proposal,
        "proposal_sha256": _canonical_sha(proposal)}


def _terminal(player: str, capture: str, generation: dict[str, object], event: str, *, visibility: str) -> dict[str, object]:
    return {
        "schema_version": review.TERMINAL_SCHEMA, "recorded_at_utc": "2026-09-13T00:00:01Z",
        "player_id": player,
        "request_id": generation["request_id"], "final_attempt_ordinal": 1, "capture_id": generation["capture_id"],
        "generation_audit_sequence": 1, "generation_record_sha256": _canonical_sha(generation),
        "context_sha256": generation["context_sha256"], "before_state_sha256": generation["before_state_sha256"],
        "after_state_sha256": "c" * 64, "proposal_sha256": generation["proposal_sha256"],
        "base_revision": generation["base_revision"], "committed_revision": 1,
        "day": generation["day"], "phase": generation["phase"], "game_id": "g",
        "status": "ACCEPTED", "reason": "AUTHORITATIVE_ACCEPTED",
        "decision_kind": generation["decision"]["kind"], "option_id": "action:0", "request_event_id": event,
        "send_connection_generation": 1,
        "authoritative_evidence": {"record_kind": ({"chat": "chat", "co_declare": "co_declaration"}.get(generation["decision"]["kind"], "action_accepted")),
            "order": 9, "visibility": visibility},
    }


def _private_chat_population(visibility: str, *, include_receipt: bool = True):
    from scripts.run_phase5_local_smoke import _phase6_semantic_population

    capture = _capture_id("private-chat")
    event = "private-chat-event"
    generation = _generation(
        "self", capture, "opaque synthetic private chat", event,
        channel_public=False,
    )
    terminal = _terminal(
        "self", capture, generation, event, visibility=visibility
    )
    terminal["generation_record_sha256"] = _canonical_sha(generation)
    shards = {"self": [generation, terminal]}
    receipts = [{
        "server_record_order": 1,
        "player_id": "self",
        "request_event_id": event,
        "decision_kind": "chat",
        "day": generation["day"],
        "phase": generation["phase"],
        "text_sha256": hashlib.sha256(
            str(generation["decision"]["text"]).encode()
        ).hexdigest(),
    }] if include_receipt else []
    return _phase6_semantic_population(shards, receipts)


def test_private_chat_literal_visibility_matches_sealed_descriptor():
    vectors = json.loads(VECTORS.read_text(encoding="utf-8"))
    expected = vectors["fixture_contract"]["private_chat_visibility"]
    population, _ = _private_chat_population(expected)
    assert expected == "AUTHORIZED_PRIVATE"
    assert len(population) == 1


def test_private_chat_public_terminal_visibility_is_rejected():
    with pytest.raises(ValueError, match="chat terminal visibility mismatch"):
        _private_chat_population("PUBLIC")


def test_private_chat_missing_population_is_rejected_separately():
    with pytest.raises(ValueError, match="accepted text population incomplete"):
        _private_chat_population("AUTHORIZED_PRIVATE", include_receipt=False)


def _case(tmp_path: Path, rows: list[tuple[str, str, str, bool, bool]] | None = None, *, full_matrix: bool = False, generation_versions: tuple[str, ...] | None = None):
    tmp_path.mkdir(parents=True, exist_ok=True, mode=0o700)
    run_dir = tmp_path / "run"
    owner = tmp_path / "owner"
    run_dir.mkdir(mode=0o700); owner.mkdir(mode=0o700)
    run_dir.chmod(0o700); owner.chmod(0o700)
    game_dir = run_dir / "g"; game_dir.mkdir(mode=0o700); game_dir.chmod(0o700)
    ai_dir = game_dir / "ai"; ai_dir.mkdir(mode=0o700); ai_dir.chmod(0o700)
    rows = rows if rows is not None else [("self", "capture-0", "synthetic alpha", True, True)]
    players = ["self", *[f"p{i}" for i in range(1, 9)]]
    mapping = {player: f"opaque-{i}" for i, player in enumerate(players)}
    shard_records: dict[str, list[dict[str, object]]] = {player: [] for player in mapping}
    receipts = []
    checklist_captures = []
    for order, (player, capture_label, text, responsive, applicable) in enumerate(rows, 1):
        capture = _capture_id(capture_label)
        event = f"event-{order}"
        generation = _generation(player, capture, text, event, responsive=responsive, day=1 + (order - 1) // 18)
        checklist_captures.append(str(generation["capture_id"]))
        terminal = _terminal(player, capture, generation, event, visibility="PUBLIC")
        generation_sequence = len(shard_records[player]) + 1
        terminal["generation_audit_sequence"] = generation_sequence
        terminal["generation_record_sha256"] = _canonical_sha(generation)
        terminal_sequence = generation_sequence + 1
        shard_records[player].extend([generation, terminal])
        receipts.append({
            "server_record_order": order, "player_id": player, "request_event_id": event,
            "decision_kind": "chat", "day": generation["day"], "phase": generation["phase"],
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        })
    # A normal accepted vote shares the shard but is not part of the text-review population.
    vote_capture = _capture_id("vote")
    vote_generation = _generation("self", vote_capture, "", "vote-event", kind="vote")
    vote_terminal = _terminal("self", vote_capture, vote_generation, "vote-event", visibility="AUTHORIZED_PRIVATE")
    vote_terminal["generation_audit_sequence"] = len(shard_records["self"]) + 1
    vote_terminal["generation_record_sha256"] = _canonical_sha(vote_generation)
    shard_records["self"].extend([vote_generation, vote_terminal])
    if full_matrix:
        for label, kind in (("ability", "ability"), ("co", "co_declare")):
            capture = _capture_id(label)
            event = label + "-event"
            generation = _generation("p1", capture, "", event, kind=kind, responsive=False)
            terminal = _terminal(
                "p1", capture, generation, event,
                visibility="PUBLIC" if kind == "co_declare" else "AUTHORIZED_PRIVATE",
            )
            terminal["generation_audit_sequence"] = len(shard_records["p1"]) + 1
            terminal["generation_record_sha256"] = _canonical_sha(generation)
            shard_records["p1"].extend([generation, terminal])
            if kind == "co_declare":
                text = str(generation["decision"]["text"])
                receipts.append({"server_record_order": len(receipts) + 1, "player_id": "p1",
                    "request_event_id": event, "decision_kind": kind, "day": generation["day"], "phase": generation["phase"],
                    "text_sha256": hashlib.sha256(text.encode()).hexdigest()})
                checklist_captures.append(str(generation["capture_id"]))
                rows.append(("p1", label, text, False, False))
        capture = _capture_id("cancelled")
        generation = _generation("p2", capture, "", "cancelled-event", kind="vote", responsive=False)
        generation.update(response_sha256=None, response_bytes=None, response_text=None,
            provider_model=None, finish_reason=None, status="CANCELLED", decision=None,
            proposal=None, proposal_sha256=None)
        terminal = _terminal("p2", capture, {**generation, "decision": {"kind": "vote"}, "proposal_sha256": "0" * 64}, "cancelled-event", visibility="AUTHORIZED_PRIVATE")
        terminal.update(generation_audit_sequence=1, generation_record_sha256=_canonical_sha(generation),
            after_state_sha256=None, proposal_sha256=None, committed_revision=None,
            decision_kind=None, option_id=None, status="ABORTED", reason="CANCELLED_BEFORE_RESULT",
            request_event_id=None, send_connection_generation=None, authoritative_evidence=None)
        shard_records["p2"].extend([generation, terminal])
    shards = {}
    generation_index = 0
    for player, records in shard_records.items():
        if generation_versions is not None:
            for record in records:
                if record["schema_version"] == review.GENERATION_SCHEMA:
                    version = generation_versions[generation_index % len(generation_versions)]
                    generation_index += 1
                    record["schema_version"] = "aiwolf.ai-discussion-generation." + version
                    if version == "v2":
                        record["prompt_rejection_code"] = None
                else:
                    generation = records[record["generation_audit_sequence"] - 1]
                    record["generation_record_sha256"] = _canonical_sha(generation)
        shard_dir = ai_dir / mapping[player]; shard_dir.mkdir(mode=0o700); shard_dir.chmod(0o700)
        shards[player] = _entry(shard_dir / "ai.jsonl", ai_dir, records)
    from scripts.run_phase5_local_smoke import _phase6_semantic_population
    accepted, semantic_counts = _phase6_semantic_population(shard_records, receipts)
    accepted_entry = _entry(ai_dir / "accepted-text.jsonl", ai_dir, accepted)
    metadata_entry = _entry(ai_dir / "admission.jsonl", ai_dir, [{"synthetic": True}])
    manifest = {
        "schema": review.MANIFEST_SCHEMA, "game_id": "g", "terminal": True,
        "player_to_opaque_client_id": mapping, "shards": shards,
        "accepted_text": accepted_entry, "metadata": metadata_entry, "semantic_counts": semantic_counts,
    }
    manifest_payload = _write_json(ai_dir / "manifest.json", manifest)
    _write_json(run_dir / "server.result.json", {"success": True, "game_end": True, "accepted_text": receipts})
    records = [{
        "capture_id": capture, "coherent": "PASS", "source_relevant": "PASS" if row[4] else "NOT_APPLICABLE",
        "objective_consistent": "PASS", "privacy_safe": "PASS", "non_repetitive": "PASS",
    } for capture, row in zip(checklist_captures, rows, strict=True)]
    checklist = {
        "schema_version": review.CHECKLIST_SCHEMA, "reviewer_task_id": "T-J-SYNTHETIC",
        "evidence_manifest_sha256": hashlib.sha256(manifest_payload).hexdigest(), "records": records,
    }
    checklist_path = owner / "checklist.json"
    _write_json(checklist_path, checklist)
    return run_dir, owner, checklist_path, checklist, manifest


def _invoke(run_dir: Path, checklist: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--run-dir", str(run_dir), "--checklist", str(checklist), "--output", str(output)],
        cwd=ROOT, text=True, capture_output=True, check=False, timeout=30,
    )


def test_private_review_all_pass_cli_and_exact_public_schema(tmp_path: Path) -> None:
    run_dir, owner, checklist, _, _ = _case(tmp_path)
    output = owner / "aggregate.json"
    result = _invoke(run_dir.resolve(), checklist.resolve(), output.resolve())
    assert result.returncode == 0 and result.stdout == "" and result.stderr == ""
    assert sorted(path.name for path in owner.iterdir()) == ["aggregate.json", "checklist.json"]
    aggregate = json.loads(output.read_text(encoding="utf-8"))
    assert set(aggregate) == {
        "schema_version", "reviewer_task_id", "evidence_manifest_sha256", "private_checklist_sha256",
        "population_count", "responsive_population_count", "source_relevant_applicable_count", "dimension_counts",
        "linkage_failure_count", "missing_count", "corrupt_count", "duplicate_count", "normalization_duplicate_count",
        "human_quality_pass",
    }
    assert aggregate["human_quality_pass"] is True
    assert aggregate["population_count"] == aggregate["responsive_population_count"] == 1
    assert aggregate["source_relevant_applicable_count"] == 1
    assert all(set(value) == {"pass", "fail", "not_applicable"} for value in aggregate["dimension_counts"].values())


@pytest.mark.parametrize("versions", [("v1",), ("v2",), ("v1", "v2")])
def test_private_review_accepts_exact_v1_v2_generation_linkage_and_rejects_unknown_version(tmp_path: Path, versions):
    run_dir, owner, checklist, checklist_value, manifest = _case(tmp_path, full_matrix=True, generation_versions=versions)
    result = _invoke(run_dir.resolve(), checklist.resolve(), (owner / "aggregate.json").resolve())
    assert result.returncode == 0, result.stderr
    aggregate = json.loads((owner / "aggregate.json").read_text(encoding="utf-8"))
    assert aggregate["population_count"] == 2
    assert aggregate["human_quality_pass"] is True
    ai_dir = run_dir / "g" / "ai"
    entry = manifest["shards"]["self"]
    shard_path = ai_dir / entry["path"]
    records = [json.loads(line) for line in shard_path.read_text(encoding="utf-8").splitlines()]
    records[0]["schema_version"] = "aiwolf.ai-discussion-generation.v99"
    manifest["shards"]["self"] = _entry(shard_path, ai_dir, records)
    payload = _write_json(ai_dir / "manifest.json", manifest)
    checklist_value["evidence_manifest_sha256"] = hashlib.sha256(payload).hexdigest()
    _write_json(checklist, checklist_value)
    rejected = _invoke(run_dir.resolve(), checklist.resolve(), (owner / "rejected.json").resolve())
    assert rejected.returncode != 0
    rejected_text = (owner / "rejected.json").read_text(encoding="utf-8")
    rejected_aggregate = json.loads(rejected_text)
    assert rejected_aggregate["human_quality_pass"] is False
    assert rejected_aggregate["linkage_failure_count"] == 1
    assert "synthetic alpha" not in rejected_text
    assert "synthetic alpha" not in rejected.stdout + rejected.stderr


def test_private_review_real_collector_matrix_has_separate_receipts_and_ledger(tmp_path: Path) -> None:
    run_dir, owner, checklist, _, manifest = _case(tmp_path, full_matrix=True)
    vectors = json.loads(VECTORS.read_text(encoding="utf-8"))["fixture_contract"]
    receipts = json.loads((run_dir / "server.result.json").read_text())["accepted_text"]
    ledger = [json.loads(line) for line in (run_dir / "g" / "ai" / "accepted-text.jsonl").read_text().splitlines()]
    assert set(manifest["player_to_opaque_client_id"]) == set(vectors["shard_player_ids"])
    assert all(set(row) == set(vectors["server_receipt_fields"]) for row in receipts)
    assert all(set(row) == set(vectors["accepted_ledger_fields"]) for row in ledger)
    assert {row["decision_kind"] for row in ledger} == {"chat", "co_declare"}
    terminals = [record for shard in manifest["shards"].values() for record in
                 (json.loads(line) for line in (run_dir / "g" / "ai" / shard["path"]).read_text().splitlines())
                 if record["schema_version"] == review.TERMINAL_SCHEMA]
    assert {record["decision_kind"] for record in terminals if record["decision_kind"] is not None} == set(vectors["required_terminal_kinds"])
    assert {record["status"] for record in terminals} == set(vectors["required_terminal_statuses"])
    assert any(record["reason"] == vectors["cancelled_reason"] for record in terminals)
    assert manifest["semantic_counts"]["pre_vote_reassessment_count"] == 1
    output = owner / "aggregate.json"
    assert _invoke(run_dir.resolve(), checklist.resolve(), output.resolve()).returncode == 0


@pytest.mark.parametrize("dimension", review.DIMENSIONS)
def test_private_review_each_dimension_isolated_fail(tmp_path: Path, dimension: str) -> None:
    run_dir, owner, checklist_path, checklist, _ = _case(tmp_path)
    checklist["records"][0][dimension] = "FAIL"
    _write_json(checklist_path, checklist)
    output = owner / "aggregate.json"
    result = _invoke(run_dir.resolve(), checklist_path.resolve(), output.resolve())
    closure_dimension = dimension in {"privacy_safe", "non_repetitive"}
    assert result.returncode == (1 if closure_dimension else 0)
    aggregate = json.loads(output.read_text())
    assert aggregate["human_quality_pass"] is (not closure_dimension)
    assert aggregate["dimension_counts"][dimension]["fail"] == 1


def test_private_review_normalization_literal_oracle_and_two_three_boundary(tmp_path: Path) -> None:
    vectors = json.loads(VECTORS.read_text(encoding="utf-8"))
    assert [(review._normalize_text(v["input"]), v["expected"]) for v in vectors["normalization"]] == [
        ("A B", "A B"), ("x y", "x y")
    ]
    rows = [
        ("self", "c0", "  Ａ\u3000B  ", True, True),
        ("p1", "c1", "A B", False, False),
        ("p2", "c2", "A\nB", False, False),
    ]
    run_dir, owner, checklist_path, checklist, _ = _case(tmp_path, rows)
    for record in checklist["records"]:
        record["non_repetitive"] = "FAIL"
    _write_json(checklist_path, checklist)
    output = owner / "aggregate.json"
    result = _invoke(run_dir.resolve(), checklist_path.resolve(), output.resolve())
    assert result.returncode == 1
    aggregate = json.loads(output.read_text())
    assert aggregate["normalization_duplicate_count"] == 3
    assert aggregate["dimension_counts"]["non_repetitive"] == {"pass": 0, "fail": 3, "not_applicable": 0}


def test_private_review_immediate_previous_same_player_is_repetitive(tmp_path: Path) -> None:
    rows = [("self", "c0", "same", True, True), ("self", "c1", "same", False, False)]
    run_dir, owner, checklist_path, checklist, _ = _case(tmp_path, rows)
    checklist["records"][1]["non_repetitive"] = "FAIL"
    _write_json(checklist_path, checklist)
    output = owner / "aggregate.json"
    assert _invoke(run_dir.resolve(), checklist_path.resolve(), output.resolve()).returncode == 1
    assert json.loads(output.read_text())["normalization_duplicate_count"] == 1


@pytest.mark.parametrize("mutation,expected", [
    ("missing_shard", "missing_count"), ("corrupt_shard", "corrupt_count"),
    ("unmatched", "missing_count"), ("duplicate", "duplicate_count"),
])
def test_private_review_population_failures_are_redacted(tmp_path: Path, mutation: str, expected: str) -> None:
    sentinel = "PRIVATE-SENTINEL-MUST-NOT-LEAK"
    run_dir, owner, checklist_path, checklist, manifest = _case(tmp_path)
    if mutation == "missing_shard":
        (run_dir / "g" / "ai" / "opaque-8" / "ai.jsonl").unlink()
    elif mutation == "corrupt_shard":
        (run_dir / "g" / "ai" / "opaque-8" / "ai.jsonl").write_text(sentinel, encoding="utf-8")
    elif mutation == "unmatched":
        accepted_path = run_dir / "g" / "ai" / "accepted-text.jsonl"
        accepted = json.loads(accepted_path.read_text().splitlines()[0])
        accepted["request_event_id"] = sentinel
        manifest["accepted_text"] = _entry(accepted_path, run_dir / "g" / "ai", [accepted])
        manifest_payload = _write_json(run_dir / "g" / "ai" / "manifest.json", manifest)
        receipt = json.loads((run_dir / "server.result.json").read_text())["accepted_text"][0]
        receipt["request_event_id"] = sentinel
        _write_json(run_dir / "server.result.json", {"success": True, "game_end": True, "accepted_text": [receipt]})
        checklist["evidence_manifest_sha256"] = hashlib.sha256(manifest_payload).hexdigest()
        _write_json(checklist_path, checklist)
    else:
        checklist["records"].append(dict(checklist["records"][0]))
        _write_json(checklist_path, checklist)
    output = owner / "aggregate.json"
    result = _invoke(run_dir.resolve(), checklist_path.resolve(), output.resolve())
    assert result.returncode == 1 and sentinel not in result.stdout + result.stderr
    aggregate = json.loads(output.read_text())
    assert aggregate[expected] == 1 and sentinel not in output.read_text()


def test_private_review_zero_population_fails_but_responsive_is_machine_gate(tmp_path: Path) -> None:
    run_dir, owner, checklist_path, _, _ = _case(tmp_path, [])
    output = owner / "aggregate.json"
    assert _invoke(run_dir.resolve(), checklist_path.resolve(), output.resolve()).returncode == 1
    assert json.loads(output.read_text())["missing_count"] == 1
    run_dir, owner, checklist_path, _, _ = _case(tmp_path / "second", [("self", "c", "text", False, False)])
    output = owner / "aggregate.json"
    assert _invoke(run_dir.resolve(), checklist_path.resolve(), output.resolve()).returncode == 0
    assert json.loads(output.read_text())["human_quality_pass"] is True


@pytest.mark.parametrize("bad", [None, True, 1, "UNKNOWN", "NOT_APPLICABLE"])
def test_private_review_closed_checklist_types_and_enums(tmp_path: Path, bad: object) -> None:
    run_dir, owner, checklist_path, checklist, _ = _case(tmp_path)
    checklist["records"][0]["coherent"] = bad
    _write_json(checklist_path, checklist)
    result = _invoke(run_dir.resolve(), checklist_path.resolve(), (owner / "aggregate.json").resolve())
    assert result.returncode == 1 and result.stdout == "" and result.stderr == "phase6_private_review_failed category=input code=checklist_invalid\n"


def test_private_review_literal_closed_schema_boundaries() -> None:
    record = {"capture_id": "x", "coherent": "PASS", "source_relevant": "NOT_APPLICABLE",
        "objective_consistent": "PASS", "privacy_safe": "PASS", "non_repetitive": "PASS"}
    checklist = {"schema_version": review.CHECKLIST_SCHEMA, "reviewer_task_id": "😀" * 128,
        "evidence_manifest_sha256": "a" * 64, "records": [record]}
    review._validate_checklist(checklist)
    for mutation in (
        {**checklist, "reviewer_task_id": "😀" * 129},
        {**checklist, "evidence_manifest_sha256": "A" * 64},
        {**checklist, "records": [{**record, "unknown": 0}]},
        {**checklist, "records": [{**record, "coherent": True}]},
    ):
        with pytest.raises(review.ReviewFailure):
            review._validate_checklist(mutation)
    aggregate = review._aggregate(checklist, "b" * 64, [], "missing")
    assert review.Draft202012Validator(review.AGGREGATE_JSON_SCHEMA).is_valid(aggregate)
    assert not review.Draft202012Validator(review.AGGREGATE_JSON_SCHEMA).is_valid({**aggregate, "missing_count": True})
    assert not review.Draft202012Validator(review.AGGREGATE_JSON_SCHEMA).is_valid({**aggregate, "unknown": 0})


def test_private_review_duplicate_json_key_is_corrupt(tmp_path: Path) -> None:
    run_dir, owner, checklist_path, _, _ = _case(tmp_path)
    checklist_path.write_text('{"schema_version":"x","schema_version":"y"}', encoding="utf-8")
    result = _invoke(run_dir.resolve(), checklist_path.resolve(), (owner / "aggregate.json").resolve())
    assert result.returncode == 1 and result.stderr == "phase6_private_review_failed category=input code=json_invalid\n"


@pytest.mark.parametrize("mutation", ["semantic_counts", "game_id", "server_incomplete"])
def test_private_review_binds_manifest_game_counts_and_server_completion(tmp_path: Path, mutation: str) -> None:
    run_dir, owner, checklist_path, checklist, manifest = _case(tmp_path)
    manifest_path = run_dir / "g" / "ai" / "manifest.json"
    if mutation == "semantic_counts":
        manifest["semantic_counts"]["accepted_text_count"] += 1
    elif mutation == "game_id":
        manifest["game_id"] = "other"
    else:
        server = json.loads((run_dir / "server.result.json").read_text())
        server["success"] = False
        _write_json(run_dir / "server.result.json", server)
    if mutation != "server_incomplete":
        payload = _write_json(manifest_path, manifest)
        checklist["evidence_manifest_sha256"] = hashlib.sha256(payload).hexdigest()
        _write_json(checklist_path, checklist)
    output = owner / "aggregate.json"
    assert _invoke(run_dir.resolve(), checklist_path.resolve(), output.resolve()).returncode == 1
    assert json.loads(output.read_text())["linkage_failure_count"] == 1


def test_private_review_rejects_unknown_fields_relative_paths_and_overwrite(tmp_path: Path) -> None:
    run_dir, owner, checklist_path, checklist, _ = _case(tmp_path)
    checklist["records"][0]["text"] = "PRIVATE-SENTINEL"
    _write_json(checklist_path, checklist)
    assert _invoke(run_dir.resolve(), checklist_path.resolve(), (owner / "aggregate.json").resolve()).returncode == 1
    relative_run_dir = Path(os.path.relpath(run_dir, ROOT))
    assert _invoke(relative_run_dir, checklist_path.resolve(), (owner / "other.json").resolve()).returncode == 1
    existing = owner / "existing.json"; existing.write_text("preserve", encoding="utf-8")
    assert _invoke(run_dir.resolve(), checklist_path.resolve(), existing.resolve()).returncode == 1
    assert existing.read_text() == "preserve"


def test_private_review_exact_512_boundary(tmp_path: Path) -> None:
    players = ["self", *[f"p{i}" for i in range(1, 9)]]
    rows = [(players[i % 9], f"capture-{i}", f"unique-{i}", i == 0, i == 0) for i in range(512)]
    run_dir, owner, checklist_path, _, _ = _case(tmp_path, rows)
    output = owner / "aggregate.json"
    assert _invoke(run_dir.resolve(), checklist_path.resolve(), output.resolve()).returncode == 0
    assert json.loads(output.read_text())["population_count"] == 512
    value = json.loads(checklist_path.read_text())
    value["records"].append(dict(value["records"][0], capture_id="overflow"))
    overflow = owner / "overflow-checklist.json"; _write_json(overflow, value)
    assert _invoke(run_dir.resolve(), overflow.resolve(), (owner / "overflow.json").resolve()).returncode == 1


def test_private_review_two_equal_texts_different_players_pass(tmp_path: Path) -> None:
    run_dir, owner, checklist, _, _ = _case(tmp_path, [
        ("self", "two-a", "same", True, True), ("p1", "two-b", "same", False, False),
    ])
    output = owner / "aggregate.json"
    assert _invoke(run_dir, checklist, output).returncode == 0
    assert json.loads(output.read_text(encoding="utf-8"))["normalization_duplicate_count"] == 0


@pytest.mark.parametrize("mutation", ["order", "extra", "missing", "normalization_mismatch"])
def test_private_review_checklist_population_is_exact(tmp_path: Path, mutation: str) -> None:
    run_dir, owner, checklist_path, checklist, _ = _case(tmp_path, [
        ("self", "a", "first", True, True), ("p1", "b", "second", False, False),
    ])
    if mutation == "order":
        checklist["records"].reverse()
    elif mutation == "extra":
        checklist["records"].append({**checklist["records"][0], "capture_id": "unknown"})
    elif mutation == "missing":
        checklist["records"].pop()
    else:
        checklist["records"][0]["non_repetitive"] = "FAIL"
    _write_json(checklist_path, checklist)
    output = owner / "aggregate.json"
    assert _invoke(run_dir, checklist_path, output).returncode == 1
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["human_quality_pass"] is False
    assert result["missing_count" if mutation == "missing" else "linkage_failure_count"] == 1


def test_private_review_existing_output_and_temporary_are_preserved(tmp_path: Path) -> None:
    run_dir, owner, checklist, _, _ = _case(tmp_path)
    output = owner / "aggregate.json"
    output.write_bytes(b"original")
    assert _invoke(run_dir, checklist, output).returncode == 1
    assert output.read_bytes() == b"original"
    temporary = owner / "another.json.tmp"
    temporary.write_bytes(b"keep temporary")
    assert _invoke(run_dir, checklist, owner / "another.json").returncode == 1
    assert temporary.read_bytes() == b"keep temporary"
    assert not (owner / "another.json").exists()


@pytest.mark.parametrize("relative", ["../outside.jsonl", "C:/outside.jsonl", "opaque-0/ai.jsonl:stream"])
def test_private_review_rejects_manifest_path_escape(tmp_path: Path, relative: str) -> None:
    run_dir, owner, checklist_path, checklist, manifest = _case(tmp_path)
    manifest["shards"]["self"]["path"] = relative
    raw = _write_json(run_dir / "g" / "ai" / "manifest.json", manifest)
    checklist["evidence_manifest_sha256"] = hashlib.sha256(raw).hexdigest()
    _write_json(checklist_path, checklist)
    output = owner / "aggregate.json"
    assert _invoke(run_dir, checklist_path, output).returncode == 1
    assert json.loads(output.read_text(encoding="utf-8"))["human_quality_pass"] is False


def test_private_review_rejects_oversize_before_read_and_non_scalar_id(tmp_path: Path) -> None:
    run_dir, owner, checklist_path, _, _ = _case(tmp_path)
    with checklist_path.open("wb") as stream:
        stream.truncate(16 * 1024 * 1024 + 1)
    assert _invoke(run_dir, checklist_path, owner / "aggregate.json").returncode == 1
    assert review._bounded_id("\ud800") is False


def test_private_review_literal_aggregate_nested_types() -> None:
    record = {"capture_id": "x", "coherent": "PASS", "source_relevant": "PASS",
        "objective_consistent": "PASS", "privacy_safe": "PASS", "non_repetitive": "PASS"}
    for key, value in [("reviewer_task_id", ""), ("reviewer_task_id", "a" * 129),
                       ("evidence_manifest_sha256", "a" * 64 + "\n")]:
        checklist = {"schema_version": "aiwolf.phase6-private-review-checklist.v1", "reviewer_task_id": "T-X",
            "evidence_manifest_sha256": "a" * 64, "records": [record], key: value}
        with pytest.raises(review.ReviewFailure):
            review._validate_checklist(checklist)
    aggregate = {"schema_version": "aiwolf.phase6-private-review-aggregate.v1", "reviewer_task_id": "T-X",
        "evidence_manifest_sha256": "a" * 64, "private_checklist_sha256": "b" * 64,
        "population_count": 1, "responsive_population_count": 1, "source_relevant_applicable_count": 1,
        "dimension_counts": {k: {"pass": 1, "fail": 0, "not_applicable": 0} for k in
            ("coherent", "source_relevant", "objective_consistent", "privacy_safe", "non_repetitive")},
        "linkage_failure_count": 0, "missing_count": 0, "corrupt_count": 0, "duplicate_count": 0,
        "normalization_duplicate_count": 0, "human_quality_pass": True}
    validator = review.Draft202012Validator(review.AGGREGATE_JSON_SCHEMA)
    assert validator.is_valid(aggregate)
    for key, value in [("pass", True), ("not_applicable", 1), ("fail", -1), ("private", "sentinel")]:
        altered = json.loads(json.dumps(aggregate))
        altered["dimension_counts"]["coherent"][key] = value
        assert not validator.is_valid(altered)


@pytest.mark.parametrize("platform_name,version", [("posix", (3, 13)), ("nt", (3, 12))])
def test_private_review_unsupported_platform_stops_before_filesystem(monkeypatch, capsys, platform_name, version):
    monkeypatch.setattr(review, "os", SimpleNamespace(name=platform_name))
    monkeypatch.setattr(review, "sys", SimpleNamespace(version_info=version, stderr=sys.stderr))

    def forbidden(*args, **kwargs):
        raise AssertionError("unsupported platform reached filesystem handling")

    monkeypatch.setattr(review, "_plain_absolute", forbidden)
    monkeypatch.setattr(review, "_read_bytes", forbidden)
    monkeypatch.setattr(review, "_write_aggregate", forbidden)
    assert review.main(["--run-dir", "unread-run", "--checklist", "unread-checklist", "--output", "unwritten-output"]) == 1
    output = capsys.readouterr()
    assert output.out == "" and output.err == "phase6_private_review_failed category=platform code=unsupported_platform\n"


@pytest.mark.parametrize(
    "category,code",
    sorted((category, code) for code, category in {
        "checklist_invalid": "input", "path_invalid": "input",
        "path_missing": "input", "path_not_private": "input",
        "unsupported_platform": "platform", "json_invalid": "input",
        "schema_closed": "input", "manifest_missing": "evidence",
        "manifest_hash_mismatch": "input", "artifact_missing": "evidence",
        "artifact_hash_mismatch": "evidence", "visibility_mismatch": "evidence",
        "population_missing": "evidence", "population_exceeded": "evidence",
        "correlation_missing": "linkage", "correlation_duplicate": "linkage",
        "correlation_mismatch": "linkage", "checklist_population_mismatch": "linkage",
        "legacy_quality_dimension_failed": "review", "aggregate_write_failed": "output",
        "unexpected_failure": "internal",
    }.items()),
)
def test_private_review_closed_diagnostic_is_one_line(monkeypatch, capsys, category, code):
    sentinel = "PRIVATE-SENTINEL-MUST-NOT-APPEAR"
    def fail(*args, **kwargs):
        raise review.ReviewFailure(category, code) from ValueError(sentinel)

    monkeypatch.setattr(review, "process", fail)
    assert review.main(["--run-dir", "x", "--checklist", "y", "--output", "z"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"phase6_private_review_failed category={category} code={code}\n"
    assert sentinel not in captured.err
    monkeypatch.setattr(
        review, "process", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(sentinel))
    )
    assert review.main(["--run-dir", "x", "--checklist", "y", "--output", "z"]) == 1
    unexpected = capsys.readouterr()
    assert unexpected.out == ""
    assert unexpected.err == "phase6_private_review_failed category=internal code=unexpected_failure\n"
    assert sentinel not in unexpected.err
