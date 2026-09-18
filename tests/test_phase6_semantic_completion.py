from __future__ import annotations
import asyncio
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
from random import Random
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest
from scripts import run_phase5_local_smoke as runner
from tests.fixtures.phase6_semantic_backend import Phase6SemanticBackend, semantic_response
from tests.fixtures.phase6_evidence import create_private_evidence_container
from ai_client.discussion.context import canonical_json_bytes, validate_discussion_bootstrap
from ai_client.llm import GenerationSettings, LocalLLMSettings, LLMMessage, StructuredGenerationRequest
from server.aiwolf_core import GameState, InMemoryEventSink, PlayerConfig, load_content, load_preset


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class P6FSemanticGameEndFailure(AssertionError):
    pass


class P6FSemanticCleanupFailure(AssertionError):
    pass


class P6FSemanticResponsiveFailure(AssertionError):
    pass


class P6FSemanticPreVoteFailure(AssertionError):
    pass


class P6FSemanticChatCapFailure(AssertionError):
    pass


class P6FSemanticAggregateFailure(AssertionError):
    pass


@pytest.fixture
def objects():
    content = load_content(runner.PROJECT_ROOT / "content")
    preset = load_preset(runner.PROJECT_ROOT / "content/presets/standard_9.yaml", content)
    preset = replace(preset, rules=replace(preset.rules, vote_seconds=60, night_seconds=60, silence_after_dawn_seconds=0))
    game = GameState.create_from_preset(content, preset, tuple(PlayerConfig(f"opaque-{i}", f"seat-{i}") for i in range(9)), game_id="opaque-game", event_sink=InMemoryEventSink(), rng=Random(19), started_at=0)
    return content, preset, game


def test_p6f_same_object_canonical_context_and_exact_roots(objects):
    content, preset, game = objects
    envelopes = runner._phase6_envelopes(content, preset, game)
    assert len(envelopes) == 9
    assert canonical_json_bytes({"z": frozenset({"b", "a"}), "a": True}) == b'{"a":true,"z":["a","b"]}'
    assert set(envelopes) == set(game.players)
    for player_id, envelope in envelopes.items():
        material = envelope["manifest_material"]
        assert set(material["content_pack"]) == {"teams", "roles", "effects", "passives", "selectors", "restriction_types", "action_timings", "chat_channels", "death_causes", "modifiers"}
        assert material["effective_preset"]["rules"]["day_seconds"] == 180
        assert len(canonical(material)) <= 65536
        assert len(canonical(envelope)) <= 81920
        context = envelope["context_payload"]
        assert len(canonical(context)) <= 8192
        assert envelope["manifest_sha256"] == hashlib.sha256(canonical(material)).hexdigest()
        assert envelope["context_sha256"] == hashlib.sha256(canonical(context)).hexdigest()
        assert context["authorized_known_player_ids"] == [] and context["known_players_complete"] is False
        expected = [{"channel_id": c, "is_public": content.chat_channels[c].is_public} for c in sorted(game.players[player_id].role.chat_channels)]
        assert context["chat_channels"] == expected
        assert all(other not in canonical(context).decode() for other in game.players if other != player_id)
        pending = validate_discussion_bootstrap(envelope, network_game_id="opaque-game", player_id=player_id)
        pending.discard_manifest()
        assert pending.manifest_is_retained is False
    with pytest.raises(ValueError, match="source objects"):
        runner._phase6_envelopes(replace(content), preset, game)


def test_p6f_relay_removed_only_after_complete_validation(objects):
    envelopes = runner._phase6_envelopes(*objects)
    path = Path("private-relay")
    events = []
    with patch.object(Path, "open", side_effect=lambda *a, **k: io.BytesIO(canonical(envelopes))), patch.object(Path, "unlink", side_effect=lambda: events.append("removed")):
        result = runner._consume_phase6_relay(path, "opaque-game", list(envelopes))
    assert events == ["removed"] and result == envelopes


@pytest.mark.parametrize("mutation", ["missing", "extra", "seat", "game", "digest", "bool", "missing_channel", "cross_manifest", "duplicate_key", "oversize", "unreadable", "removal"])
def test_p6f_relay_failure_prevents_delivery(objects, mutation):
    envelopes = runner._phase6_envelopes(*objects)
    ids = list(envelopes)
    if mutation == "missing": envelopes.pop(ids[0])
    elif mutation == "extra": envelopes["extra"] = envelopes[ids[0]]
    elif mutation in {"seat", "game", "bool", "missing_channel"}:
        e = envelopes[ids[0]]
        c = e["context_payload"]
        if mutation == "seat": c["player_id"] = ids[1]
        elif mutation == "game": c["game_id"] = "wrong"
        elif mutation == "bool": c["chat_channels"][0]["is_public"] = 1
        else: c["chat_channels"] = []
        e["context_sha256"] = hashlib.sha256(canonical(c)).hexdigest()
    elif mutation == "digest": envelopes[ids[0]]["manifest_sha256"] = "0" * 64
    elif mutation == "cross_manifest":
        e = deepcopy(envelopes[ids[0]])
        e["manifest_material"]["effective_preset"]["rules"]["day_seconds"] = 179
        e["manifest_sha256"] = hashlib.sha256(canonical(e["manifest_material"])).hexdigest()
        e["context_payload"]["content_manifest_sha256"] = e["manifest_sha256"]
        e["context_sha256"] = hashlib.sha256(canonical(e["context_payload"])).hexdigest()
        envelopes[ids[0]] = e
    payload = canonical(envelopes)
    if mutation == "duplicate_key": payload = b'{"opaque-0":{},"opaque-0":{}}'
    if mutation == "oversize": payload = b" " * 745473
    with patch.object(Path, "open", side_effect=PermissionError("private") if mutation == "unreadable" else lambda *a, **k: io.BytesIO(payload)), patch.object(Path, "unlink", side_effect=PermissionError("private") if mutation == "removal" else None) as remove:
        with pytest.raises((ValueError, OSError)):
            runner._consume_phase6_relay(Path("private"), "opaque-game", ids)
        assert remove.call_count == (1 if mutation == "removal" else 0)


def projected(kind="chat", text="support this proposal?"):
    source = {"record_kind": "chat", "order": 7, "visibility": "PUBLIC"}
    context = {
        "game_id": "g",
        "player_id": "self",
        "chat_channels": [{"channel_id": "opaque-channel", "is_public": True}],
    }
    trigger = {"owner": "reaction_chat" if kind == "chat" else "vote_ability",
        "kind": "PEER_CHAT" if kind == "chat" else "PRE_VOTE", "day": 1, "phase": "day",
        "connection_generation": 1, "action_generation": 1, "mapping_order": 7 if kind == "chat" else 0,
        "source": source if kind == "chat" else None}
    option = {"action_kind": kind, "option_id": "action:0", "channel": "opaque-channel", "valid_targets": ["peer", "third"], "target_count": 1,
        "day": 1, "phase": "day", "connection_generation": 1, "action_generation": 1}
    return {"schema_version": "aiwolf.discussion-prompt.v1",
        "capture": {"base_revision": 0, "capture_id": "a" * 64, "current_player_ids": ["peer", "self", "third"], "trigger": trigger,
            "context_sha256": hashlib.sha256(canonical(context)).hexdigest(), "state_sha256": "b" * 64,
            "epoch": 0, "fact_revision": 1, "world_version": 8, "last_applied_seq": 7, "capture_ordinal": 1},
        "state": {"identity": {"revision": 0, "epoch": 0, "fact_revision": 1, "world_version": 8, "last_applied_seq": 7, "day": 1, "phase": "day"}},
        "context": context, "action_context": {"options": [option], "world_version": 8, "world_last_applied_seq": 7, "network_last_seq": 7},
        "memory": {"records": [{"source": source, "actor_player_ids": ["peer"], "channel_id": "opaque-channel", "text_excerpt": text}]}}


def test_p6f_fixture_responds_to_prior_authorized_speech_and_pre_vote():
    first = semantic_response(projected())
    second = semantic_response(projected(text="oppose this proposal?"))
    assert first["discussion"]["speech_act"]["kind"] == "ANSWER"
    assert first["discussion"]["speech_act"]["stance"] == "SUPPORT"
    assert second["discussion"]["speech_act"]["stance"] == "OPPOSE"
    assert first["discussion"]["speech_act"]["in_reply_to"] == {"record_kind": "chat", "order": 7, "visibility": "PUBLIC"}
    assert semantic_response(projected("vote"))["discussion"]["pre_vote_reassessment"]["preferred_target_player_id"] == "peer"
    assert semantic_response(projected("vote", "oppose"))["discussion"]["pre_vote_reassessment"]["preferred_target_player_id"] == "third"
    request = StructuredGenerationRequest("r", (LLMMessage("system", "fixed"), LLMMessage("user", json.dumps(projected()))), {"type": "object"})
    result = asyncio.run(Phase6SemanticBackend().generate(request))
    assert json.loads(result.text) == first
    assert result.usage.prompt_tokens is None


@pytest.mark.completion
@pytest.mark.windows_private
def test_p6f_nine_client_semantic_completion(pytestconfig, record_property):
    """Explicit finite process/network fixture; never starts a provider or GPU."""
    evidence_base = runner.PROJECT_ROOT / "logs" / "phase6-private-evidence"
    evidence_base.mkdir(parents=True, exist_ok=True, mode=0o700)
    configured_basetemp = pytestconfig.getoption("basetemp")
    evidence_container = create_private_evidence_container(
        evidence_base, pytest_basetemp=Path(configured_basetemp)
        if configured_basetemp is not None else None,
        evidence_kind="synthetic",
        task_id="T293",
        created_at_utc=datetime.now(timezone.utc),
    )
    config = runner.RunConfig(LocalLLMSettings(endpoint="http://127.0.0.1:1/v1/chat/completions", model="Qwen3.5-9B-Q4_K_M.gguf", generation=GenerationSettings(max_output_tokens=512), llama_cpp_structured_output=runner.LlamaCppStructuredOutputConfig()), evidence_container / "run", False, False, None, 8625, 1200.0, (), phase6=True)
    result = asyncio.run(runner._run_game(config, config.output_dir, label="P6-F", environ=os.environ, phase6_fixture=True))
    runner._write_private_json_atomic(evidence_container / "completion-result.json", result)
    from scripts.ci_private_summary import private_semantic_codes, private_result_codes
    record_property("ci_failure_codes", ",".join(filter(None, (
        private_result_codes(result.get("errors")),
        private_semantic_codes(result.get("semantic")),
    ))))
    # Classify only public acceptance predicates; private result/error material stays on disk.
    if result["server"]["game_end"] is not True:
        raise P6FSemanticGameEndFailure
    if len(result["cleanup"]) != 11 or not all(
        not child["alive"] for child in result["cleanup"]
    ):
        raise P6FSemanticCleanupFailure
    if result["semantic"]["responsive_accepted_count"] < 1:
        raise P6FSemanticResponsiveFailure
    if result["semantic"]["pre_vote_reassessment_count"] < 1:
        raise P6FSemanticPreVoteFailure
    if result["semantic"]["maximum_chat_starts_per_player_phase"] > 2:
        raise P6FSemanticChatCapFailure
    if result["machine_semantic_pass"] is not True:
        raise P6FSemanticAggregateFailure

def semantic_shard():
    records, server = [], []
    for index, kind in enumerate(("chat", "vote"), 1):
        value = projected(kind)
        capture = str(index) * 64
        value["capture"]["capture_id"] = capture
        response = semantic_response(value)
        prompt = canonical({"messages": [{"role": "system", "content": "fixed"}, {"role": "user", "content": canonical(value).decode()}], "output_schema": {"type": "object", "required": ["decision", "discussion"], "additionalProperties": False, "properties": {"decision": {"type": "object"}, "discussion": {"type": "object"}}}}).decode()
        response_text = canonical(response).decode()
        decision = {"kind": kind, "option_id": "action:0", "text": response["decision"].get("message"), "vote_target_player_id": response["decision"].get("target_player_id"), "ability_id": None, "ability_target_player_ids": [], "claimed_role_id": None, "report_kind": None, "report_target_player_id": None, "claimed_result": None}
        generation = {"schema_version": "aiwolf.ai-discussion-generation.v1", "recorded_at_utc": "2026-09-13T00:00:00Z", "game_id": "g", "player_id": "self", "request_id": "phase6:" + capture, "capture_id": capture, "phase": "day", "day": 1, "world_version": 8, "backend": asdict(Phase6SemanticBackend.identity), "attempt_ordinal": 1, "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "prompt_bytes": len(prompt.encode()), "prompt_json": prompt, "response_sha256": hashlib.sha256(response_text.encode()).hexdigest(), "response_bytes": len(response_text.encode()), "response_text": response_text, "latency_microseconds": 100, "provider_model": "fixture", "finish_reason": "stop", "prompt_tokens": None, "completion_tokens": None, "status": "DECISION", "backend_error_code": None, "validation_code": None, "decision": decision, "context_sha256": hashlib.sha256(canonical(value["context"])).hexdigest(), "before_state_sha256": "b" * 64, "after_state_sha256": None, "base_revision": 0, "proposal": response["discussion"], "proposal_sha256": hashlib.sha256(canonical(response["discussion"])).hexdigest()}
        records.append(generation)
        terminal = {"schema_version": "aiwolf.ai-discussion-terminal.v1", "recorded_at_utc": "2026-09-13T00:00:01Z", "game_id": "g", "player_id": "self", "request_id": "phase6:" + capture, "capture_id": capture, "phase": "day", "day": 1, "final_attempt_ordinal": 1, "generation_audit_sequence": len(records), "generation_record_sha256": hashlib.sha256(canonical(generation)).hexdigest(), "context_sha256": generation["context_sha256"], "before_state_sha256": "b" * 64, "after_state_sha256": "c" * 64, "proposal_sha256": generation["proposal_sha256"], "base_revision": 0, "committed_revision": 1, "decision_kind": kind, "option_id": "action:0", "status": "ACCEPTED", "reason": "AUTHORITATIVE_ACCEPTED", "request_event_id": f"event-{index}", "send_connection_generation": 1, "authoritative_evidence": {"record_kind": "chat" if kind == "chat" else "action_accepted", "order": 9, "visibility": "PUBLIC" if kind == "chat" else "AUTHORIZED_PRIVATE"}}
        records.append(terminal)
        if kind == "chat":
            server.append({"server_record_order": 1, "player_id": "self", "request_event_id": "event-1", "decision_kind": "chat", "day": 1, "phase": "day", "text_sha256": hashlib.sha256(decision["text"].encode()).hexdigest()})
    return {"self": records}, server


def test_phase6_population_limit_and_summary_stop(tmp_path):
    template, receipts = semantic_shard()
    records, accepted = [], []
    for index in range(513):
        generation, terminal = deepcopy(template["self"][:2])
        capture_id = f"{index + 1:064x}"
        prompt = json.loads(generation["prompt_json"])
        value = json.loads(prompt["messages"][1]["content"])
        value["capture"]["capture_id"] = capture_id
        value["capture"]["capture_ordinal"] = index + 1
        value["capture"]["trigger"]["day"] = index + 1
        value["state"]["identity"]["day"] = index + 1
        value["action_context"]["options"][0]["day"] = index + 1
        prompt["messages"][1]["content"] = canonical(value).decode()
        prompt_json = canonical(prompt).decode()
        generation.update(
            capture_id=capture_id, request_id="phase6:" + capture_id, day=index + 1,
            prompt_json=prompt_json, prompt_bytes=len(prompt_json.encode()),
            prompt_sha256=hashlib.sha256(prompt_json.encode()).hexdigest(),
        )
        terminal.update(
            capture_id=capture_id, request_id=generation["request_id"], day=index + 1,
            generation_audit_sequence=len(records) + 1,
            generation_record_sha256=hashlib.sha256(canonical(generation)).hexdigest(),
            request_event_id=f"event-{index + 1}",
        )
        records.extend((generation, terminal))
        accepted.append({**receipts[0], "server_record_order": index + 1,
                         "request_event_id": terminal["request_event_id"], "day": index + 1})
    for count in (511, 512):
        population, measured = runner._phase6_semantic_population(
            {"self": records[:count * 2]}, accepted[:count]
        )
        assert len(population) == measured["accepted_text_count"] == count
        assert measured["chat_caps_respected"] is True
    with pytest.raises(runner.Phase6PopulationExceeded) as captured:
        runner._phase6_semantic_population({"self": records}, accepted)
    assert captured.value.accepted_text_count == 513
    assert captured.value.reason == "ACCEPTED_TEXT_POPULATION_EXCEEDED"
    ai_dir = tmp_path / "g" / "ai"
    ai_dir.mkdir(parents=True)
    metrics = ai_dir / "admission.jsonl"
    metrics.write_bytes(b"")
    with patch.object(
        runner, "_manifest_value", return_value={"shards": {}}
    ), patch.object(
        runner, "_phase6_semantic_population", side_effect=captured.value
    ), patch.object(runner, "_write_private_json_atomic") as write_manifest, patch.object(
        runner, "_write_private_jsonl_atomic"
    ) as write_ledger:
        with pytest.raises(runner.Phase6PopulationExceeded):
            runner._write_phase6_evidence(
                ai_dir, {}, metrics, {}, {"accepted_text": []}
            )
    write_manifest.assert_not_called()
    write_ledger.assert_not_called()

    settings = LocalLLMSettings(
        endpoint="http://127.0.0.1:1/v1/chat/completions",
        model="Qwen3.5-9B-Q4_K_M.gguf",
        generation=GenerationSettings(max_output_tokens=512),
    )
    config = runner.RunConfig(
        settings, tmp_path / "summary-run", False, False, None, 1, 1200.0, (), phase6=True
    )
    summaries = []
    events = []
    players = [f"player-{index}" for index in range(9)]
    ready_identity = {"config_fingerprint": settings.backend_config().config_fingerprint}
    ready_config = asdict(runner.GenerationBrokerConfig())
    ready_fingerprint = runner.GenerationBrokerConfig().config_fingerprint

    class Sampler:
        def __init__(self, *_args): pass
        def start(self): pass
        async def stop(self): return {"samples": []}

    async def inventory(*_args): return []

    async def spawn(owned, *, label, **_kwargs):
        child = SimpleNamespace(label=label)
        owned.append(child)
        return child

    async def cleanup(owned, _model_pid):
        events.append("cleanup")
        return [{"label": child.label, "alive": False} for child in owned]

    def read_json(path):
        if path.name == "server.ready.json":
            return {"player_ids": players, "game_id": "g", "uri": "ws://unused"}
        if path.name == "broker.ready.json":
            return {"host": "unused", "port": 1, "config": ready_config,
                    "config_fingerprint": ready_fingerprint, "backend_identity": ready_identity}
        if path.name == "server.result.json":
            return {"game_end": True, "accepted_text": []}
        return {}

    def write_summary(_path, value):
        events.append("summary")
        summaries.append(value)

    with ExitStack() as stack:
        for name, replacement in {
            "_private_directory": lambda *_: None,
            "_new_private_subdirectory": lambda parent, name: parent / name,
            "_external_model_inventory": inventory,
            "_GpuSampler": Sampler,
            "_spawn_owned": spawn,
            "_wait_for_paths": AsyncMock(),
            "_read_json": read_json,
            "_read_jsonl": lambda *_: [],
            "_consume_phase6_relay": lambda *_: {player: {} for player in players},
            "_cleanup_owned_shielded": cleanup,
            "_touch_stop": lambda *_: events.append("stop"),
            "_artifact_hashes": lambda *_: [],
            "_validate_external_model_evidence": lambda *_: [],
            "_write_private_jsonl_atomic": lambda *_: None,
            "_write_private_json_atomic": write_summary,
        }.items():
            stack.enter_context(patch.object(runner, name, replacement))
        stack.enter_context(patch.object(Path, "write_text"))
        stack.enter_context(patch.object(runner.os, "chmod"))
        publish = stack.enter_context(patch.object(runner, "_write_phase6_evidence", side_effect=captured.value))
        assert asyncio.run(runner._execute(config, {})) != 0
        assert publish.call_count == 1
        assert events == ["stop", "cleanup", "summary"]
        for mutation in ("backend", "broker_hash", "broker_config"):
            ready_identity["config_fingerprint"] = (
                "0" * 64 if mutation == "backend" else settings.backend_config().config_fingerprint
            )
            ready_fingerprint = "0" * 64 if mutation == "broker_hash" else runner.GenerationBrokerConfig().config_fingerprint
            ready_config = asdict(runner.GenerationBrokerConfig())
            if mutation == "broker_config":
                ready_config["shutdown_grace_seconds"] = 7.0
            rejected = asyncio.run(runner._run_game(
                config, config.output_dir / ("bad-ready-" + mutation), label="SMOKE", environ={}
            ))
            assert rejected["success"] is False
            assert len(rejected["cleanup"]) == 2
            assert [child["label"] for child in rejected["cleanup"]] == ["server", "broker"]
            assert all(child["alive"] is False for child in rejected["cleanup"])
            assert publish.call_count == 1
    summary = summaries[-1]
    assert summary["success"] is False
    assert events == ["stop", "cleanup", "summary"] + ["stop", "cleanup"] * 3
    assert summary["rows"][0]["semantic"] == {
        "accepted_text_count": 513, "failure_reason": "ACCEPTED_TEXT_POPULATION_EXCEEDED"}
    assert summary["rows"][0]["errors"] == ["ACCEPTED_TEXT_POPULATION_EXCEEDED"]
    assert summary["rows"][0]["machine_semantic_pass"] is False
    assert len(summary["rows"][0]["cleanup"]) == 11
    assert all(child["alive"] is False for child in summary["rows"][0]["cleanup"])


def test_phase6_zero_pre_vote_is_diagnostic():
    shards, server = semantic_shard()
    shards["self"] = shards["self"][:2]
    _, summary = runner._phase6_semantic_population(shards, server)
    assert summary["pre_vote_reassessment_count"] == 0
    assert summary["responsive_accepted_count"] == 1
    assert summary["semantic_requirements_met"] is True


def test_p6f_final_manifest_is_published_once_and_failures_remain_closed(tmp_path):
    shards, accepted = semantic_shard()

    def evidence_inputs(name):
        root = tmp_path / name / "g"
        ai_dir = root / "ai"
        shard_dir = ai_dir / "opaque-self"
        shard_dir.mkdir(parents=True)
        audit = shard_dir / "ai.jsonl"
        runner._write_private_jsonl_atomic(audit, shards["self"])
        metrics = ai_dir / "admission.jsonl"
        runner._write_private_jsonl_atomic(metrics, ({"metric": 1},))
        statuses = {"self": {"audit": {"path": "opaque-self/ai.jsonl"}}}
        return ai_dir, statuses, metrics

    ai_dir, statuses, metrics = evidence_inputs("success")
    published = []
    writer = runner._write_private_json_atomic

    def observe(path, value):
        published.append(path)
        writer(path, value)

    with patch.object(runner, "_write_private_json_atomic", side_effect=observe):
        manifest, summary = runner._write_phase6_evidence(
            ai_dir, statuses, metrics, {"self": "opaque-self"},
            {"accepted_text": accepted},
        )
    assert published.count(manifest) == 1
    assert published[-1] == manifest
    assert summary["semantic_requirements_met"] is True
    value = json.loads(manifest.read_text(encoding="utf-8"))
    assert value["schema"] == "aiwolf.phase6-private-manifest.v1"
    assert value["game_id"] == "g"
    assert runner._validate_manifest(manifest, ai_dir) == []
    accepted_path = ai_dir / value["accepted_text"]["path"]
    assert value["accepted_text"]["sha256"] == hashlib.sha256(accepted_path.read_bytes()).hexdigest()

    existing_dir, existing_statuses, existing_metrics = evidence_inputs("existing")
    existing_manifest = existing_dir / "manifest.json"
    existing_manifest.write_bytes(b"original-private-manifest\n")
    original = existing_manifest.read_bytes()
    with pytest.raises(FileExistsError):
        runner._write_phase6_evidence(
            existing_dir, existing_statuses, existing_metrics,
            {"self": "opaque-self"}, {"accepted_text": accepted},
        )
    assert existing_manifest.read_bytes() == original
    assert not (existing_dir / "accepted-text.jsonl").exists()

    failed_dir, failed_statuses, failed_metrics = evidence_inputs("collection-failure")
    corrupt = deepcopy(accepted)
    corrupt[0]["text_sha256"] = "f" * 64
    with pytest.raises(ValueError):
        runner._write_phase6_evidence(
            failed_dir, failed_statuses, failed_metrics,
            {"self": "opaque-self"}, {"accepted_text": corrupt},
        )
    assert not (failed_dir / "accepted-text.jsonl").exists()
    assert not (failed_dir / "manifest.json").exists()

    phase5_dir, phase5_statuses, phase5_metrics = evidence_inputs("phase5")
    phase5_manifest = runner._write_manifest(
        phase5_dir, phase5_statuses, phase5_metrics,
        {"self": "opaque-self"},
    )
    phase5 = json.loads(phase5_manifest.read_text(encoding="utf-8"))
    assert phase5 == runner._manifest_value(
        phase5_dir, phase5_statuses, phase5_metrics,
        {"self": "opaque-self"},
    )
    assert phase5["schema"] == "aiwolf.phase5-private-manifest.v1"
    assert "accepted_text" not in phase5 and "semantic_counts" not in phase5


def test_p6f_machine_semantic_authority_links_exact_private_population():
    shards, server = semantic_shard()
    population, summary = runner._phase6_semantic_population(shards, server)
    assert summary["semantic_requirements_met"] is True
    assert summary["responsive_accepted_count"] == 1
    assert summary["pre_vote_reassessment_count"] == 1
    assert summary["generation_count"] == 2
    assert population[0]["generation_sequence"] == 1
    assert population[0]["terminal_sequence"] == 2
    assert population[0]["capture_id"] == "1" * 64
    assert population[0]["source_relevant_applicable"] is True
    assert all(key not in population[0] for key in ("text", "prompt_json", "context"))
    assert all(sentinel not in json.dumps(summary) for sentinel in ("self", "peer", "opaque-channel", "1" * 64, "Please", "proposal"))


@pytest.mark.parametrize("mutation", ["no_population", "missing_terminal", "duplicate_terminal", "duplicate_server", "missing_generation", "generation_hash", "terminal_sequence", "wrong_order", "wrong_text", "bad_prompt", "cross_player", "extra_field", "bad_schema", "same_order_source", "terminal_status", "terminal_reason", "server_extra_private", "server_day", "server_phase", "server_bool_order", "generation_bool_ordinal", "generation_backend_code", "generation_missing_response"])
def test_p6f_machine_semantic_authority_rejects_corrupt_or_ambiguous_evidence(mutation):
    shards, server = semantic_shard()
    records = shards["self"]
    if mutation == "no_population": server.clear()
    elif mutation == "missing_terminal": records.pop()
    elif mutation == "duplicate_terminal": records.append(deepcopy(records[1]))
    elif mutation == "duplicate_server": server.append(deepcopy(server[0]))
    elif mutation == "missing_generation": records.pop(0)
    elif mutation == "generation_hash": records[1]["generation_record_sha256"] = "f" * 64
    elif mutation == "terminal_sequence": records[1]["generation_audit_sequence"] = 2
    elif mutation == "wrong_order": server[0]["server_record_order"] = 2
    elif mutation == "wrong_text": server[0]["text_sha256"] = "f" * 64
    elif mutation == "bad_prompt": records[0]["prompt_json"] += " "
    elif mutation == "cross_player": records[0]["player_id"] = "peer"
    elif mutation == "extra_field": records[0]["secret"] = "owner-only"
    elif mutation == "bad_schema": records[0]["schema_version"] = "unknown"
    elif mutation == "same_order_source": records[1]["authoritative_evidence"]["order"] = 7
    elif mutation == "terminal_status": records[1]["status"] = "OTHER"
    elif mutation == "terminal_reason": records[1]["reason"] = "EXPLICIT_NO_DECISION"
    elif mutation == "server_extra_private": server[0]["private"] = "owner-only-sentinel"
    elif mutation == "server_day": server[0]["day"] = 2
    elif mutation == "server_phase": server[0]["phase"] = "night"
    elif mutation == "server_bool_order": server[0]["server_record_order"] = True
    elif mutation == "generation_bool_ordinal": records[0]["attempt_ordinal"] = True
    elif mutation == "generation_backend_code": records[0]["backend_error_code"] = "FAILED"
    elif mutation == "generation_missing_response": records[0]["response_text"] = None
    with pytest.raises(ValueError):
        runner._phase6_semantic_population(shards, server)


def test_p6f_no_empty_population_can_qualify():
    population, summary = runner._phase6_semantic_population({}, [])
    assert population == []
    assert summary["semantic_requirements_met"] is False


def _failed_generation_shard(status, *, version="v2", marker=False):
    shards, receipts = semantic_shard()
    generation, terminal = deepcopy(shards["self"][2:4])
    capture = "3" * 64
    prompt = json.loads(generation["prompt_json"])
    projected_value = json.loads(prompt["messages"][1]["content"])
    projected_value["capture"]["capture_id"] = capture
    prompt["messages"][1]["content"] = canonical(projected_value).decode()
    if marker:
        prompt["messages"][1]["content"] = canonical({
            "schema_version": "aiwolf.discussion-prompt-rejected.v1",
            "projection_rejected": "PROMPT_TOO_LARGE",
        }).decode()
        prompt["output_schema"] = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    raw_prompt = canonical(prompt)
    generation.update(
        schema_version="aiwolf.ai-discussion-generation." + version,
        capture_id=capture, request_id="phase6:" + capture,
        prompt_json=raw_prompt.decode(), prompt_bytes=len(raw_prompt),
        prompt_sha256=hashlib.sha256(raw_prompt).hexdigest(),
        status=status, proposal=None, proposal_sha256=None, decision=None,
        backend_error_code="ADMISSION_PROTOCOL" if status == "BACKEND_FAILED" else None,
        validation_code="SCHEMA" if status in {"OUTPUT_INVALID", "REPAIR_FAILED"} else None,
    )
    if version == "v2":
        generation["prompt_rejection_code"] = "PROMPT_TOO_LARGE" if status == "PROMPT_REJECTED" else None
    if status in {"OUTPUT_INVALID", "REPAIR_FAILED"}:
        response = b"not-json-private-sentinel"
        generation.update(response_text=response.decode(), response_bytes=len(response), response_sha256=hashlib.sha256(response).hexdigest())
    else:
        for name in ("response_text", "response_bytes", "response_sha256", "provider_model", "finish_reason", "prompt_tokens", "completion_tokens"):
            generation[name] = None
    if status == "REPAIR_FAILED":
        first = deepcopy(generation)
        first["status"] = "OUTPUT_INVALID"
        shards["self"].append(first)
        generation["attempt_ordinal"] = 2
    shards["self"].append(generation)
    terminal.update(
        capture_id=capture, request_id=generation["request_id"],
        generation_audit_sequence=len(shards["self"]),
        generation_record_sha256=hashlib.sha256(canonical(generation)).hexdigest(),
        final_attempt_ordinal=generation["attempt_ordinal"],
        status="ABORTED", reason={"OUTPUT_INVALID": "FINAL_OUTPUT_INVALID", "REPAIR_FAILED": "FINAL_OUTPUT_INVALID", "CANCELLED": "CANCELLED_BEFORE_RESULT"}.get(status, status),
        after_state_sha256=None, committed_revision=None, proposal_sha256=None,
        decision_kind=None, option_id=None, request_event_id=None,
        send_connection_generation=None, authoritative_evidence=None,
    )
    shards["self"].append(terminal)
    return shards, receipts


@pytest.mark.parametrize("version", ["v1", "v2"])
@pytest.mark.parametrize("status", ["OUTPUT_INVALID", "REPAIR_FAILED", "BACKEND_FAILED", "CANCELLED", "PROMPT_REJECTED"])
def test_p6f_machine_semantic_authority_accounts_failed_generations_without_accepting_them(status, version, tmp_path):
    shards, receipts = _failed_generation_shard(status, version=version)
    population, summary = runner._phase6_semantic_population(shards, receipts)
    assert len(population) == summary["accepted_text_count"] == 1
    assert summary["generation_count"] == (4 if status == "REPAIR_FAILED" else 3)
    if version == "v2" or status != "CANCELLED":
        assert summary["generation_status_counts"][status] == 1
        assert summary["legacy_prompt_rejection_reason_missing"] == int(version == "v1" and status == "PROMPT_REJECTED")
    assert "not-json-private-sentinel" not in json.dumps(population) + json.dumps(summary)
    ai_dir = tmp_path / "g" / "ai"
    shard = ai_dir / "opaque" / "ai.jsonl"
    shard.parent.mkdir(parents=True)
    runner._write_private_jsonl_atomic(shard, shards["self"])
    metrics = ai_dir / "admission.jsonl"
    runner._write_private_jsonl_atomic(metrics, [{"metric": 1}])
    manifest, _ = runner._write_phase6_evidence(ai_dir, {"self": {"audit": {"path": "opaque/ai.jsonl"}}}, metrics, {"self": "opaque"}, {"game_end": False, "accepted_text": receipts})
    assert json.loads(manifest.read_text(encoding="utf-8"))["semantic_counts"] == summary
    assert runner._validate_manifest(manifest, ai_dir) == []
    for mutation in ("terminal_missing", "digest", "accepted_failure", "attempt_missing", "unknown_status", "unknown_field", "nullability"):
        damaged = deepcopy(shards)
        if mutation == "terminal_missing":
            damaged["self"].pop()
        elif mutation == "digest":
            damaged["self"][-1]["generation_record_sha256"] = "f" * 64
        elif mutation == "accepted_failure":
            damaged["self"][-1].update(status="ACCEPTED", reason="AUTHORITATIVE_ACCEPTED")
        elif mutation == "attempt_missing":
            damaged["self"][-2]["attempt_ordinal"] = 2
            damaged["self"][-1]["final_attempt_ordinal"] = 2
            if status == "REPAIR_FAILED":
                del damaged["self"][-3]
        elif mutation == "unknown_status":
            damaged["self"][-2]["status"] = "OTHER"
        elif mutation == "unknown_field":
            damaged["self"][-2]["private_extra"] = "sentinel"
        else:
            damaged["self"][-2]["proposal"] = {}
        with pytest.raises(ValueError):
            runner._phase6_semantic_population(damaged, receipts)
    if status == "OUTPUT_INVALID" and version == "v2":
        repaired = deepcopy(shards)
        repaired["self"].pop()  # A final repair replaces the initial abort, not its generation.
        valid, accepted_terminal = deepcopy(repaired["self"][2:4])
        first = repaired["self"][-1]
        for name in ("capture_id", "request_id", "prompt_json", "prompt_sha256", "prompt_bytes"):
            valid[name] = first[name]
        valid.update(schema_version="aiwolf.ai-discussion-generation.v2", prompt_rejection_code=None,
                     status="REPAIR_SUCCEEDED", attempt_ordinal=2)
        repaired["self"].append(valid)
        accepted_terminal.update(capture_id=valid["capture_id"], request_id=valid["request_id"],
                                 final_attempt_ordinal=2, generation_audit_sequence=6,
                                 generation_record_sha256=hashlib.sha256(canonical(valid)).hexdigest())
        repaired["self"].append(accepted_terminal)
        repaired_population, repaired_counts = runner._phase6_semantic_population(repaired, receipts)
        assert len(repaired_population) == 1
        assert repaired_counts["generation_count"] == 4
        assert repaired_counts["generation_status_counts"] == {"DECISION": 2, "OUTPUT_INVALID": 1, "REPAIR_SUCCEEDED": 1}


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_p6f_prompt_rejection_marker_is_closed_and_reason_bound(version):
    shards, receipts = _failed_generation_shard("PROMPT_REJECTED", version=version, marker=True)
    population, summary = runner._phase6_semantic_population(shards, receipts)
    assert len(population) == 1
    assert summary["prompt_rejection_marker_count"] == 1
    assert summary["chat_caps_respected"] is False  # Trigger information is unavailable.
    assert summary["legacy_prompt_rejection_reason_missing"] == int(version == "v1")
    for mutation in ("unknown_reason", "extra", "missing", "null", "status", "marker_mismatch"):
        damaged = deepcopy(shards)
        row = damaged["self"][-2]
        if mutation == "unknown_reason": row["prompt_rejection_code"] = "private-sentinel"
        elif mutation == "extra": row["extra"] = None
        elif mutation == "missing":
            row["schema_version"] = "aiwolf.ai-discussion-generation.v2"
            row.pop("prompt_rejection_code", None)
        elif mutation == "null": row["prompt_rejection_code"] = None
        elif mutation == "status": row["status"] = "CANCELLED"
        else:
            prompt = json.loads(row["prompt_json"])
            marker = json.loads(prompt["messages"][1]["content"])
            marker["projection_rejected"] = "OTHER"
            prompt["messages"][1]["content"] = canonical(marker).decode()
            raw = canonical(prompt)
            row.update(prompt_json=raw.decode(), prompt_bytes=len(raw), prompt_sha256=hashlib.sha256(raw).hexdigest())
        damaged["self"][-1]["generation_record_sha256"] = hashlib.sha256(canonical(row)).hexdigest()
        with pytest.raises(ValueError):
            runner._phase6_semantic_population(damaged, receipts)


def test_p6f_parent_validation_failure_launches_no_client_and_cleans_owned_server():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    config = runner.RunConfig(LocalLLMSettings(endpoint="http://127.0.0.1:1/v1/chat/completions", model="Qwen3.5-9B-Q4_K_M.gguf", generation=GenerationSettings(max_output_tokens=512), llama_cpp_structured_output=runner.LlamaCppStructuredOutputConfig()), Path("private-root"), False, False, None, 1, 1200.0, (), phase6=True)
    launched = []
    async def spawn(owned, **kwargs):
        launched.append(kwargs)
        item = SimpleNamespace(label=kwargs["label"])
        owned.append(item)
        return item
    cleanup = AsyncMock(return_value=[{"alive": False}])
    ready = {"player_ids": [f"player-{i}" for i in range(9)], "game_id": "g", "uri": "ws://local"}
    with patch.object(runner, "_private_directory"), patch.object(runner, "_new_private_subdirectory", side_effect=lambda p,n: p/n), patch.object(runner, "_spawn_owned", side_effect=spawn), patch.object(runner, "_wait_for_paths", new=AsyncMock()), patch.object(runner, "_read_json", return_value=ready), patch.object(runner, "_consume_phase6_relay", side_effect=PermissionError("owner-sentinel")), patch.object(runner, "_touch_stop"), patch.object(runner, "_cleanup_owned_shielded", new=cleanup), patch.object(runner, "_artifact_hashes", return_value=[]):
        result = asyncio.run(runner._run_game(config, config.output_dir, label="P6-F", environ={}))
    assert [item["label"] for item in launched] == ["server"]
    assert result["machine_semantic_pass"] is False
    assert result["errors"] == ["PermissionError"]
    assert "owner-sentinel" not in json.dumps(result)
    assert cleanup.await_count == 1
    assert launched[0]["bootstrap"]["discussion_relay"].endswith("discussion.relay.json")
    assert all("discussion" not in arg for arg in launched[0]["arguments"])

def test_p6f_parent_delivers_only_owner_envelope_after_relay_removal(objects):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    content, preset, old_game = objects
    game = GameState.create_from_preset(content, preset, tuple(PlayerConfig(f"player-{i}", f"seat-{i}") for i in range(9)), game_id="g", event_sink=InMemoryEventSink(), rng=Random(19), started_at=0)
    envelopes = runner._phase6_envelopes(content, preset, game)
    for index, envelope in enumerate(envelopes.values()):
        # Test-double sentinels track transport partitioning independently of parsing.
        envelope["private_transport_sentinel"] = f"owner-only-sentinel-{index}"
    originals = deepcopy(envelopes)
    events, launched = [], []
    config = runner.RunConfig(LocalLLMSettings(endpoint="http://127.0.0.1:1/v1/chat/completions", model="Qwen3.5-9B-Q4_K_M.gguf", generation=GenerationSettings(max_output_tokens=512), llama_cpp_structured_output=runner.LlamaCppStructuredOutputConfig(), read_timeout_seconds=12.0, request_timeout_seconds=20.0), Path("private-root"), False, False, None, 1, 1200.0, (), phase6=True)
    expected_broker = runner.GenerationBrokerConfig(provider_drain_grace_seconds=20.0, shutdown_grace_seconds=20.25)
    async def spawn(owned, **kwargs):
        events.append(kwargs["label"])
        launched.append(deepcopy(kwargs))
        item = SimpleNamespace(label=kwargs["label"])
        owned.append(item)
        return item
    def consume(*args):
        events.append("relay-removed")
        return envelopes
    def read(path):
        if path.name == "server.ready.json": return {"player_ids": list(originals), "game_id": "g", "uri": "ws://local"}
        if path.name == "broker.ready.json": return {
            "host": "127.0.0.1",
            "port": 1,
            "config": asdict(expected_broker),
            "config_fingerprint": expected_broker.config_fingerprint,
            "backend_identity": {
                "config_fingerprint": config.settings.backend_config().config_fingerprint,
            },
        }
        if path.name.endswith(".status.json"): return {"reaction": {"chat_brain_invocations": 0}}
        return {}
    semantic = {"semantic_requirements_met": True, "chat_start_count": 0}
    wait_paths = AsyncMock()
    with patch.object(runner, "_private_directory"), patch.object(runner, "_new_private_subdirectory", side_effect=lambda p,n: p/n), patch.object(runner, "_spawn_owned", side_effect=spawn), patch.object(runner, "_wait_for_paths", new=wait_paths), patch.object(runner, "_read_json", side_effect=read), patch.object(runner, "_read_jsonl", return_value=[]), patch.object(runner, "_consume_phase6_relay", side_effect=consume), patch.object(runner, "_touch_stop"), patch.object(runner, "_cleanup_owned_shielded", new=AsyncMock(return_value=[{"alive": False}] * 11)), patch.object(runner, "_artifact_hashes", return_value=[]), patch.object(runner, "_write_phase6_evidence", return_value=(Path("manifest"), semantic)), patch.object(runner, "_validate_game_evidence", return_value=[]), patch.object(Path, "write_text"), patch.object(os, "chmod"):
        result = asyncio.run(runner._run_game(config, config.output_dir, label="P6-F", environ={}))
    assert wait_paths.await_args_list[-1].args[2] == 50.25
    assert launched[1]["bootstrap"]["read_timeout_seconds"] == 12
    assert launched[1]["bootstrap"]["request_timeout_seconds"] == 20
    assert all(item["bootstrap"]["broker_config"] == asdict(expected_broker) for item in launched[2:])
    assert events[:3] == ["server", "relay-removed", "broker"]
    assert envelopes == {}
    assert len(launched) == 11
    for item in launched[2:]:
        owner = item["label"]
        bootstrap = item["bootstrap"]
        assert bootstrap["discussion_envelope"] == originals[owner]
        encoded = json.dumps(bootstrap)
        assert all(other["private_transport_sentinel"] not in encoded for player,other in originals.items() if player != owner)
        assert "discussion" not in json.dumps(item["arguments"])
        assert "owner-only-sentinel" not in json.dumps(item["environ"])
    assert "owner-only-sentinel" not in json.dumps(result)
    assert "context_payload" not in json.dumps(result)


def test_p6f_child_discards_raw_private_stdin_before_long_execution():
    import inspect
    from types import SimpleNamespace
    own = {"phase6": True, "discussion_envelope": {"private": "owner-only-sentinel"}}
    async def child(args, bootstrap):
        caller = inspect.currentframe().f_back
        assert caller.f_code.co_name == "_run_child"
        assert "raw" not in caller.f_locals
        assert bootstrap == own
        bootstrap.pop("discussion_envelope")
        return 0
    args = SimpleNamespace(_child_mode="client", ready=Path("r"), status=Path("s"), audit=Path("a"), start=Path("g"), stop=Path("t"))
    with patch.object(runner.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(canonical(own) + b"\n"))), patch.object(runner, "_client_child", new=child):
        assert asyncio.run(runner._run_child(args)) == 0


def test_p6f_chat_cap_three_fails_machine_requirements():
    shards, server = semantic_shard()
    base = deepcopy(shards["self"][:2])
    for index in (3, 4):
        generation, terminal = deepcopy(base)
        capture = str(index) * 64
        prompt = json.loads(generation["prompt_json"])
        value = json.loads(prompt["messages"][1]["content"])
        value["capture"]["capture_id"] = capture
        prompt["messages"][1]["content"] = canonical(value).decode()
        generation["prompt_json"] = canonical(prompt).decode()
        generation["prompt_sha256"] = hashlib.sha256(generation["prompt_json"].encode()).hexdigest()
        generation["prompt_bytes"] = len(generation["prompt_json"].encode())
        generation["capture_id"] = terminal["capture_id"] = capture
        generation["request_id"] = terminal["request_id"] = "phase6:" + capture
        terminal["generation_record_sha256"] = hashlib.sha256(canonical(generation)).hexdigest()
        terminal["generation_audit_sequence"] = len(shards["self"]) + 1
        terminal["request_event_id"] = f"event-{index}"
        shards["self"].extend((generation, terminal))
        server.append({**server[0], "server_record_order": len(server) + 1, "request_event_id": f"event-{index}"})
    _, summary = runner._phase6_semantic_population(shards, server)
    assert summary["maximum_chat_starts_per_player_phase"] == 3
    assert summary["chat_caps_respected"] is False
    assert summary["semantic_requirements_met"] is False

@pytest.mark.parametrize("kind", ["chat", "vote", "ability", "co_declare"])
def test_p6f_fixture_passes_real_projection_and_semantic_parser(objects, kind):
    from ai_client.brain import BrainInput, BrainActionContext, BrainActionOption
    from ai_client.discussion.model import DiscussionTrigger
    from ai_client.discussion.state import DiscussionStateStore, DiscussionViews, evidence_ref_for_record
    from ai_client.llm import LLMBrainConfig
    from ai_client.llm.prompt import project_brain_input
    from ai_client.llm.decision import parse_llm_output
    from ai_client.network import ChatAction, VoteAction, AbilityAction, CoDeclareAction
    from ai_client.world import WorldSnapshot, Freshness, PlayerView, PhaseView, SelfView, HistoryRetention, HistoryView, CoView, AbilityResultView, TransportObservationView, ChatRecord
    content, preset, game = objects
    owner, peer = tuple(game.players)[:2]
    envelope = runner._phase6_envelopes(content, preset, game)[owner]
    context = envelope["context_payload"]
    channel = next(c["channel_id"] for c in context["chat_channels"] if c["is_public"])
    record = ChatRecord(7, 1, "day", channel, peer, "peer", "Which evidence should guide our vote?")
    retention = HistoryRetention(1, 1, 0, 0, None, 7, 7, 128, 65536, True)
    snapshot = WorldSnapshot(1, Freshness.CURRENT, True, 7, tuple(PlayerView(p,p) for p in game.players), tuple(game.players), (), PhaseView("day",1), SelfView(owner, context["role_id"], ()), (), retention)
    bound = validate_discussion_bootstrap(envelope, network_game_id="opaque-game", player_id=owner).bind(snapshot)
    history, co, ability = HistoryView((record,), True, retention), CoView((),(),True,retention), AbilityResultView((),True,retention)
    views = DiscussionViews(snapshot, history, co, ability, TransportObservationView(1,None,None,False,(),None))
    source = evidence_ref_for_record(record, bound_context=bound) if kind == "chat" else None
    trigger = DiscussionTrigger("reaction_chat" if kind in {"chat","co_declare"} else "vote_ability", {"chat":"PEER_CHAT", "vote":"PRE_VOTE", "ability":"ABILITY", "co_declare":"CO_OPPORTUNITY"}[kind], 1,"day",1,1,7 if source else 0,source)
    capture = DiscussionStateStore(bound).capture(views, trigger)
    common = dict(connection_generation=1, action_generation=1, phase="day", day=1, type=kind)
    if kind == "chat": handle = ChatAction(**common, channel=channel)
    elif kind == "vote": handle = VoteAction(**common, valid_targets=(peer,), target_count=1, allows_abstain=False)
    elif kind == "ability": handle = AbilityAction(**common, ability_id="offered-capability", description=None, valid_targets=(peer,), target_count=1, uses_remaining=1)
    else: handle = CoDeclareAction(**common, claimed_role_ids=(context["role_id"],))
    request = BrainInput(snapshot, BrainActionContext(1,7,7,True,(BrainActionOption("action:0",handle),)),history,co,ability,capture)
    projection = project_brain_input(request, config=LLMBrainConfig())
    value = json.loads(projection.messages[1].content)
    output = semantic_response(value)
    parsed = parse_llm_output(json.dumps(output), projection=projection)
    assert parsed.proposal.decision_kind == kind
    if kind == "chat":
        assert parsed.proposal.speech_act.in_reply_to.order == 7
        assert parsed.proposal.speech_act.addressee_player_id == peer
    if kind == "vote":
        assert parsed.proposal.pre_vote_reassessment.preferred_target_player_id == peer
    assert "manifest_material" not in projection.messages[1].content
    assert all(other not in canonical(value["context"]).decode() for other in game.players if other != owner)

def test_p6f_client_requires_validated_pending_and_connect_phase6(objects):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from ai_client import Phase5ClientRuntime
    envelope = runner._phase6_envelopes(*objects)["opaque-0"]
    bootstrap = {"phase6": True, "discussion_envelope": envelope, "player_id": "opaque-0", "game_id": "opaque-game", "uri": "ws://127.0.0.1:1", "entry_token": "owner-entry", "admission_host": "127.0.0.1", "admission_port": 1, "admission_client_id": "opaque", "admission_token": "a" * 64, "broker_config": {}}
    args = SimpleNamespace(start=Path("start"), audit=Path("audit.jsonl"), seed=1)
    observed = []
    async def connect(config, store, **kwargs):
        pending = kwargs["pending_discussion_context"]
        assert pending.manifest_is_retained
        assert pending.context.player_id == config.player_id == "opaque-0"
        assert pending.context.game_id == config.network.game_id == "opaque-game"
        pending.discard_manifest()
        observed.append(pending)
        raise RuntimeError("bounded test stop before resources")
    with patch.object(Phase5ClientRuntime, "connect_phase6", new=connect), patch.object(Phase5ClientRuntime, "connect", new=AsyncMock()) as old:
        with pytest.raises(RuntimeError, match="bounded test stop"):
            asyncio.run(runner._client_child(args, bootstrap))
        old.assert_not_awaited()
    assert "discussion_envelope" not in bootstrap
    assert observed and not observed[0].manifest_is_retained
    with patch.object(Phase5ClientRuntime, "connect_phase6", new=AsyncMock()) as connect_new:
        with pytest.raises(ValueError):
            asyncio.run(runner._client_child(args, bootstrap))
        connect_new.assert_not_awaited()


def test_p6f_child_validation_errors_redact_private_stdout_stderr(capsys):
    from unittest.mock import AsyncMock
    args = runner._parser().parse_args(["--_child-mode", "client"])
    with patch.object(runner, "_parser") as parser, patch.object(runner, "_run_child", new=AsyncMock(side_effect=ValueError("owner-private-context-channel-sentinel"))):
        parser.return_value.parse_args.return_value = args
        with pytest.raises(SystemExit) as error:
            runner.main()
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Runner child configuration invalid: ValueError\n"


def test_p6f_machine_population_includes_every_accepted_co_comment():
    shards, server = semantic_shard()
    generation, terminal = deepcopy(shards["self"][:2])
    value = projected()
    capture = "3" * 64
    value["capture"]["capture_id"] = capture
    value["capture"]["trigger"].update(kind="CO_OPPORTUNITY", source=None, mapping_order=0)
    value["action_context"]["options"] = [{"action_kind": "co_declare", "option_id": "action:0", "claimed_role_ids": ["opaque-claim"], "day": 1, "phase": "day", "connection_generation": 1, "action_generation": 1}]
    response = semantic_response(value)
    prompt = json.loads(generation["prompt_json"])
    prompt["messages"][1]["content"] = canonical(value).decode()
    generation.update(capture_id=capture, request_id="phase6:" + capture, prompt_json=canonical(prompt).decode(), response_text=canonical(response).decode(), proposal=response["discussion"])
    for field in ("prompt", "response"):
        text = generation[field + ("_json" if field == "prompt" else "_text")].encode()
        generation[field + "_bytes"] = len(text)
        generation[field + "_sha256"] = hashlib.sha256(text).hexdigest()
    generation["proposal_sha256"] = hashlib.sha256(canonical(response["discussion"])).hexdigest()
    generation["decision"].update(kind="co_declare", text=response["decision"]["comment"], claimed_role_id="opaque-claim")
    terminal.update(capture_id=capture, request_id="phase6:" + capture, decision_kind="co_declare", request_event_id="event-3", generation_audit_sequence=5, generation_record_sha256=hashlib.sha256(canonical(generation)).hexdigest(), proposal_sha256=generation["proposal_sha256"], authoritative_evidence={"record_kind":"co_declaration", "order":10, "visibility":"PUBLIC"})
    shards["self"].extend((generation,terminal))
    server.append({"server_record_order":2,"player_id":"self","request_event_id":"event-3","decision_kind":"co_declare","day":1,"phase":"day","text_sha256":hashlib.sha256(generation["decision"]["text"].encode()).hexdigest()})
    population, summary = runner._phase6_semantic_population(shards, server)
    assert summary["accepted_text_count"] == 2
    assert [row["capture_id"] for row in population] == ["1" * 64, "3" * 64]
    assert population[1]["decision_kind"] == "co_declare"
    assert population[1]["generation_sequence"] == 5 and population[1]["terminal_sequence"] == 6
    assert population[1]["source_relevant_applicable"] is False
    assert population[1]["responsive"] is False


async def _public_audit_material():
    """Input only: real projection/parser/generation serialization, typed terminal.

    The in-memory audit sink and authoritative accepted metadata are test doubles.
    No collector, oracle, provider, filesystem, or child-process execution here.
    """
    from ai_client.brain import BrainInput, BrainActionContext, BrainActionOption
    from ai_client.discussion.context import validate_discussion_bootstrap, canonical_json_bytes
    from ai_client.discussion.model import DiscussionTrigger, DiscussionTerminalStatus, DiscussionTerminalReason, EvidenceRef, EvidenceRecordKind, EvidenceVisibility
    from ai_client.discussion.state import DiscussionStateStore, DiscussionViews, evidence_ref_for_record
    from ai_client.discussion.transaction import AiDiscussionTerminalRecord
    from ai_client.llm import LLMBrain, LLMBrainConfig, LLMClientIdentity
    from ai_client.llm.types import AuditWriteAck, serialize_ai_audit
    from ai_client.network import ChatAction, VoteAction, CoDeclareAction
    from ai_client.world import WorldSnapshot, Freshness, PlayerView, PhaseView, SelfView, HistoryRetention, HistoryView, CoView, AbilityResultView, TransportObservationView, ChatRecord

    class Audit:
        def __init__(self):
            self.records = []

        async def write(self, record):
            self.records.append(json.loads(serialize_ai_audit(record)))
            return AuditWriteAck(len(self.records))

    content=load_content(runner.PROJECT_ROOT/'content')
    preset=load_preset(runner.PROJECT_ROOT/'content/presets/standard_9.yaml',content)
    preset=replace(preset,rules=replace(preset.rules,day_seconds=180,vote_seconds=60,night_seconds=60,silence_after_dawn_seconds=0))
    players=tuple(PlayerConfig(f'opaque-{i}',f'seat-{i}') for i in range(9))
    game=GameState.create_from_preset(content,preset,players,game_id='opaque-game',event_sink=InMemoryEventSink(),rng=Random(19),started_at=0)
    envelopes=runner._phase6_envelopes(content,preset,game)
    material=envelopes['opaque-0']['manifest_material']
    owner,peer='opaque-0','opaque-1'
    context=envelopes[owner]['context_payload']
    channel=next(c['channel_id'] for c in context['chat_channels'] if c['is_public'])
    audit=Audit(); server=[]
    for index,kind in enumerate(('chat','vote','co_declare'),1):
        phase='vote' if kind=='vote' else 'day'
        rec=ChatRecord(7,1,'day',channel,peer,peer,'Which evidence should guide our vote?')
        retention=HistoryRetention(1,1,0,0,None,7,7,128,65536,True)
        snap=WorldSnapshot(index,Freshness.CURRENT,True,7,tuple(PlayerView(p.player_id,p.display_name) for p in players),tuple(game.players),(),PhaseView(phase,1),SelfView(owner,context['role_id'],()),(),retention)
        pending=validate_discussion_bootstrap(envelopes[owner],network_game_id='opaque-game',player_id=owner)
        bound=pending.bind(snap)
        history=HistoryView((rec,),True,retention); co=CoView((),(),True,retention); ability=AbilityResultView((),True,retention)
        views=DiscussionViews(snap,history,co,ability,TransportObservationView(index,None,None,False,(),None))
        source=evidence_ref_for_record(rec,bound_context=bound) if kind=='chat' else None
        trigger=DiscussionTrigger('vote_ability' if kind=='vote' else 'reaction_chat',{'chat':'PEER_CHAT','vote':'PRE_VOTE','co_declare':'CO_OPPORTUNITY'}[kind],1,phase,1,1,7 if source else 0,source)
        store=DiscussionStateStore(bound); capture=store.capture(views,trigger)
        common=dict(connection_generation=1,action_generation=1,phase=phase,day=1,type=kind)
        handle=ChatAction(**common,channel=channel) if kind=='chat' else VoteAction(**common,valid_targets=(peer,),target_count=1,allows_abstain=False) if kind=='vote' else CoDeclareAction(**common,claimed_role_ids=(context['role_id'],))
        request=BrainInput(snap,BrainActionContext(index,7,7,True,(BrainActionOption('action:0',handle),)),history,co,ability,capture)
        brain=LLMBrain(backend=Phase6SemanticBackend(),audit=audit,identity=LLMClientIdentity('opaque-game',owner),config=LLMBrainConfig())
        output=await asyncio.wait_for(brain.decide(request),2)
        generation=audit.records[-1]; ack=output.audit_ack
        terminal=AiDiscussionTerminalRecord(schema_version='aiwolf.ai-discussion-terminal.v1',recorded_at_utc='2026-09-13T00:00:01Z',game_id='opaque-game',player_id=owner,request_id=generation['request_id'],capture_id=capture.capture_id,phase=phase,day=1,final_attempt_ordinal=1,generation_audit_sequence=ack.audit_sequence,generation_record_sha256=ack.generation_record_sha256,context_sha256=capture.context_sha256,before_state_sha256=capture.state_sha256,after_state_sha256='c'*64,proposal_sha256=generation['proposal_sha256'],base_revision=0,committed_revision=1,decision_kind=kind,option_id='action:0',status=DiscussionTerminalStatus.ACCEPTED,reason=DiscussionTerminalReason.AUTHORITATIVE_ACCEPTED,request_event_id=f'event-{index}',send_connection_generation=1,authoritative_evidence=EvidenceRef(EvidenceRecordKind.CHAT if kind=='chat' else EvidenceRecordKind.CO_DECLARATION if kind=='co_declare' else EvidenceRecordKind.ACTION_ACCEPTED,9+index,EvidenceVisibility.AUTHORIZED_PRIVATE if kind=='vote' else EvidenceVisibility.PUBLIC))
        await audit.write(terminal)
        if kind!='vote': server.append(dict(server_record_order=len(server)+1,player_id=owner,request_event_id=f'event-{index}',decision_kind=kind,day=1,phase=phase,text_sha256=hashlib.sha256(generation['decision']['text'].encode()).hexdigest()))
        store.close()
    shards={owner:audit.records}
    return shards, server


@pytest.fixture
def public_audit_material():
    return asyncio.run(_public_audit_material())


def _relink_prompt(generation, terminal, prompt, projected_input):
    prompt["messages"][1]["content"] = canonical(projected_input).decode()
    generation["prompt_json"] = canonical(prompt).decode()
    generation["prompt_sha256"] = hashlib.sha256(generation["prompt_json"].encode()).hexdigest()
    generation["prompt_bytes"] = len(generation["prompt_json"].encode())
    terminal["generation_record_sha256"] = hashlib.sha256(canonical(generation)).hexdigest()


def test_p6f_actual_public_audit_valid_chat_co_vote(public_audit_material):
    population, summary = runner._phase6_semantic_population(*public_audit_material)
    assert summary["semantic_requirements_met"] is True
    assert summary["accepted_text_count"] == 2
    assert summary["responsive_accepted_count"] == 1
    assert summary["pre_vote_reassessment_count"] == 1
    assert summary["generation_count"] == 3
    assert summary["chat_start_count"] == 1
    assert [(row["generation_sequence"], row["terminal_sequence"]) for row in population] == [(1, 2), (5, 6)]
    assert [row["decision_kind"] for row in population] == ["chat", "co_declare"]
    assert [row["responsive"] for row in population] == [True, False]


@pytest.mark.parametrize("mutation", ["day_vs_capture", "phase_vs_capture", "world_version_vs_capture", "before_state_vs_capture", "base_revision_capture"])
def test_p6f_actual_public_audit_rejects_recorded_linkage_mismatch(public_audit_material, mutation):
    shards, server = public_audit_material
    generation, terminal = shards["opaque-0"][:2]
    prompt = json.loads(generation["prompt_json"])
    projected_input = json.loads(prompt["messages"][1]["content"])
    if mutation == "day_vs_capture":
        generation["day"] = terminal["day"] = server[0]["day"] = 2
    elif mutation == "phase_vs_capture":
        generation["phase"] = terminal["phase"] = server[0]["phase"] = "night"
    elif mutation == "world_version_vs_capture":
        generation["world_version"] = 999
    elif mutation == "before_state_vs_capture":
        generation["before_state_sha256"] = terminal["before_state_sha256"] = "e" * 64
    else:
        projected_input["capture"]["base_revision"] = 9
    _relink_prompt(generation, terminal, prompt, projected_input)
    with pytest.raises(ValueError, match="captured input identity"):
        runner._phase6_semantic_population(shards, server)


@pytest.mark.parametrize("path,value", [
    (("context", "game_id"), "other-game"),
    (("context", "player_id"), "opaque-1"),
    (("capture", "context_sha256"), "e" * 64),
    (("capture", "capture_id"), "e" * 64),
    (("capture", "trigger", "day"), 2),
    (("capture", "trigger", "phase"), "night"),
    (("capture", "trigger", "owner"), "vote_ability"),
    (("state", "identity", "day"), 2),
    (("state", "identity", "phase"), "night"),
    (("state", "identity", "revision"), 9),
    (("state", "identity", "epoch"), 1),
    (("state", "identity", "fact_revision"), 99),
    (("state", "identity", "world_version"), 99),
    (("state", "identity", "last_applied_seq"), 99),
    (("action_context", "world_version"), 99),
    (("action_context", "world_last_applied_seq"), 99),
    (("action_context", "network_last_seq"), 99),
    (("action_context", "options", 0, "day"), 2),
    (("action_context", "options", 0, "phase"), "night"),
    (("action_context", "options", 0, "connection_generation"), 2),
    (("action_context", "options", 0, "action_generation"), 2),
    (("capture", "base_revision"), False),
    (("capture", "world_version"), True),
    (("capture", "trigger", "day"), True),
])
def test_p6f_actual_public_audit_adjacent_identity_mismatch(public_audit_material, path, value):
    shards, server = public_audit_material
    generation, terminal = shards["opaque-0"][:2]
    prompt = json.loads(generation["prompt_json"])
    projected_input = json.loads(prompt["messages"][1]["content"])
    target = projected_input
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    if path[0] == "context":
        context_hash = hashlib.sha256(canonical(projected_input["context"])).hexdigest()
        projected_input["capture"]["context_sha256"] = context_hash
        generation["context_sha256"] = terminal["context_sha256"] = context_hash
    _relink_prompt(generation, terminal, prompt, projected_input)
    with pytest.raises(ValueError):
        runner._phase6_semantic_population(shards, server)


def test_p6f_actual_public_audit_missing_identity_rejects(public_audit_material):
    shards, server = public_audit_material
    generation, terminal = shards["opaque-0"][:2]
    prompt = json.loads(generation["prompt_json"])
    projected_input = json.loads(prompt["messages"][1]["content"])
    del projected_input["capture"]["state_sha256"]
    _relink_prompt(generation, terminal, prompt, projected_input)
    with pytest.raises(ValueError, match="captured input identity invalid"):
        runner._phase6_semantic_population(shards, server)


def test_p6f_actual_public_audit_existing_revision_guard(public_audit_material):
    shards, server = public_audit_material
    generation, terminal = shards["opaque-0"][:2]
    generation["base_revision"] = terminal["base_revision"] = 9
    terminal["committed_revision"] = 10
    terminal["generation_record_sha256"] = hashlib.sha256(canonical(generation)).hexdigest()
    with pytest.raises(ValueError):
        runner._phase6_semantic_population(shards, server)
