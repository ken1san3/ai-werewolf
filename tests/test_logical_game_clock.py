from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from ai_client.game_time import GameTime
from ai_client.network import NetworkClient, NetworkClientConfig
from ai_client.llm.backend import OpenAICompatibleBackend
from scripts import run_phase5_local_smoke as runner
from tests.test_phase4_llm_backend import _config, _request, _success_payload
from tests.test_phase5_local_smoke import _args


@pytest.mark.parametrize("scale", [0, -1, True, float("inf"), float("nan"), 5e-324])
def test_invalid_numeric_scale(scale):
    with pytest.raises(ValueError):
        GameTime(scale)


@pytest.mark.parametrize("scale", ["", "nan", "inf", "0", "-0.1", "bad"])
def test_invalid_environment_rejected_before_run(tmp_path, scale):
    with pytest.raises(ValueError, match="AIWOLF_TIME_SCALE"):
        runner._prepare_run(_args(tmp_path / "unused"), {"AIWOLF_TIME_SCALE": scale})
    assert not (tmp_path / "unused").exists()


def test_default_and_scaled_duration_evidence(tmp_path):
    assert GameTime.from_env({}) == GameTime(1)
    assert GameTime(1).real_budget(44) == 44
    clock = GameTime.from_env({"AIWOLF_TIME_SCALE": "0.1"})
    assert clock.real_budget(44) == 440
    assert clock.logical_elapsed(10) == 1
    evidence = clock.evidence(123.5)
    path = tmp_path / "clock.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "time_scale": 0.1, "real_duration_sec": 123.5, "logical_duration_sec": 12.350000000000001,
    }
    assert clock.evidence(None)["logical_duration_sec"] is None


@pytest.mark.parametrize("scale,expected", [(1, 10), (0.1, 1)])
def test_authoritative_clock_only_scales_elapsed_after_start(tmp_path, scale, expected):
    start = tmp_path / "start"
    with patch.object(runner.time, "monotonic", side_effect=[100, 105, 110, 120]):
        clock = runner._StartGateClock(start, integer=True, game_time=GameTime(scale))
        assert clock() == 0
        start.write_text("start", encoding="utf-8")
        assert clock() == 0
        assert clock() == expected


@pytest.mark.parametrize("scale,remaining", [("1", 20), ("0.1", 200)])
def test_network_maps_logical_deadline_to_real_without_scaling_safety(monkeypatch, scale, remaining):
    monkeypatch.setenv("AIWOLF_TIME_SCALE", scale)
    async def scenario():
        sleep = AsyncMock(side_effect=asyncio.CancelledError)
        config = NetworkClientConfig("ws://localhost:1234", "game", "token")
        client = NetworkClient(config, SimpleNamespace(), clock=lambda: 1000, sleep=sleep)
        client._publish = AsyncMock()
        await client._replace_deadline_mapping(
            {"phase": "day", "day": 1, "phase_ends_at": 50},
            source_seq=1, server_timestamp=30, mapped_at_monotonic=1000,
        )
        mapped = client._publish.call_args.args[0]
        assert mapped.local_deadline_monotonic == 1000 + remaining
        await asyncio.sleep(0)
        sleep.assert_awaited_once_with(remaining)
        await client._cancel_deadline()
        assert client._clock() == 1000
        assert config.connect_timeout_seconds == config.shutdown_timeout_seconds == 5
        assert client.reconnect_policy.max_disconnected_seconds == 60
    asyncio.run(scenario())


@pytest.mark.parametrize("scale,factor", [("1", 1), ("0.1", 10)])
def test_admission_cutoff_budget_and_real_measurement_and_drain(monkeypatch, scale, factor):
    monkeypatch.setenv("AIWOLF_TIME_SCALE", scale)
    from ai_client.llm.admission_broker import GenerationAdmissionBroker, _Slot, _SlotState
    from ai_client.llm import GenerationBrokerConfig
    from ai_client.llm.types import ProviderQuiescence
    from tests.test_phase5_generation_admission import _FakeBackend, _generation, _request as admission_request
    async def scenario():
        now = [110.0]
        config = GenerationBrokerConfig(provider_drain_grace_seconds=5)
        broker = GenerationAdmissionBroker({"client": "a" * 64}, _FakeBackend(),
            fairness_seed="clock", config=config, clock=lambda: now[0], backend_request_timeout_seconds=4)
        slot = _Slot("client", admission_request("request", deadline=100 + 20 * factor),
                     _SlotState.ENQUEUED, enqueued_at=100, request_ids=set())
        broker._slots["client"] = slot
        broker._emit = Mock()
        broker._record = Mock()
        broker._offer_next_locked()
        assert slot.state is _SlotState.OFFERED
        assert broker._record.call_args.kwargs["queue_wait_microseconds"] == 10000000
        slot.state = _SlotState.CLAIMED
        broker._require_invocation_owner = Mock()
        broker._require_slot_owner = Mock(return_value=slot)
        broker._run_backend_call = AsyncMock()
        broker._generate_locked(SimpleNamespace(client_id="client"), "request", 1, _generation())
        await slot.provider_task
        assert broker._run_backend_call.call_args.args[-1] == 4 * factor
        now[0] = 115
        broker._metrics.record_nowait = Mock()
        broker._record_call(slot, 1, None, None, None, ProviderQuiescence.PROVEN_TERMINAL,
                            response=None, poison_transition=False)
        record = broker._metrics.record_nowait.call_args.args[0]
        assert record.generation_latency_microseconds == 5000000
        assert broker._config.provider_drain_grace_seconds == 5
        assert broker._config.cancellation_grace_seconds == 0.25
        assert broker._config.shutdown_grace_seconds == 6
        # The logical deadline has already become a REAL absolute cutoff.
        assert slot.request.not_after_monotonic == 100 + 20 * factor
    asyncio.run(scenario())


@pytest.mark.parametrize("scale,expected", [("1", 4), ("0.1", 40)])
def test_provider_total_budget_scaled_socket_timeouts_and_telemetry_real(monkeypatch, scale, expected):
    monkeypatch.setenv("AIWOLF_TIME_SCALE", scale)
    async def scenario():
        captured = {}
        def response(request):
            captured.update(request.extensions["timeout"])
            return httpx.Response(200, content=_success_payload(), headers={"Content-Type": "application/json"})
        config = _config(request_timeout_seconds=4, connect_timeout_seconds=1.1,
                         read_timeout_seconds=2.2, write_timeout_seconds=3.3, pool_timeout_seconds=4.4)
        backend = OpenAICompatibleBackend(config, transport=httpx.MockTransport(response))
        from ai_client._compat import await_with_timeout
        original = await_with_timeout
        with patch("ai_client.llm.backend.await_with_timeout", wraps=original) as timeout:
            result = await backend.generate(_request())
            assert timeout.call_count == 1
            assert timeout.call_args.args[0] == expected
            assert callable(timeout.call_args.args[1])
        assert captured == {"connect": 1.1, "read": 2.2, "write": 3.3, "pool": 4.4}
        assert result.usage.prompt_tokens == 8
        assert result.usage.completion_tokens == 4
        await backend.aclose()
    asyncio.run(scenario())


def test_supervision_is_explicit_real_and_never_derived_from_scale(tmp_path):
    args = _args(tmp_path / "run")
    base = runner._prepare_run(args, {})
    scaled = runner._prepare_run(args, {"AIWOLF_TIME_SCALE": "0.1"})
    assert base.max_seconds == scaled.max_seconds
    assert base.real_supervision_seconds is scaled.real_supervision_seconds is None
    args.real_supervision_seconds = 14400
    explicit = runner._prepare_run(args, {"AIWOLF_TIME_SCALE": "0.1"})
    assert explicit.real_supervision_seconds == 14400
    assert runner._run_metadata(explicit)["time_scale"] == 0.1
    assert runner._STOP_GRACE_SECONDS == 5
    assert runner._READY_SECONDS == 30


def test_failure_evidence_keeps_both_times_and_real_queue_metrics(tmp_path):
    row = runner._phase6_wait_failure_row(
        root=tmp_path, label="clock", errors=[], cleanup=[], game_time=GameTime(0.1),
        recovered={"server": {"total_game_wall_microseconds": 10000000,
                    "phase_wall_durations": [{"phase": "day", "day": 1, "wall_microseconds": 10000000}]}}
    )
    assert row["real_duration_sec"] == 10
    assert row["logical_duration_sec"] == 1
    assert row["time_scale"] == 0.1
    assert row["server"]["phase_wall_durations"][0]["wall_microseconds"] == 10000000


@pytest.mark.parametrize("scale,factor", [("1", 1), ("0.1", 10)])
def test_client_composition_decision_reaction_vote_and_real_lifecycle(monkeypatch, tmp_path, scale, factor):
    monkeypatch.setenv("AIWOLF_TIME_SCALE", scale)
    from ai_client import Phase5ClientRuntime
    captured = {}
    class Captured(Exception):
        pass
    async def connect(config, store, **kwargs):
        captured.update(config=config, kwargs=kwargs)
        raise Captured
    async def scenario():
        with patch.object(Phase5ClientRuntime, "connect_phase6", side_effect=connect), patch(
            "ai_client.discussion.context.validate_discussion_bootstrap", return_value=object()
        ):
            with pytest.raises(Captured):
                await runner._client_child(
                    SimpleNamespace(start=tmp_path / "start", audit=tmp_path / "audit", seed=8625),
                    {"phase6": True, "player_id": "player-0", "uri": "ws://localhost:1234",
                     "game_id": "test", "entry_token": "token", "admission_host": "127.0.0.1",
                     "admission_port": 1235, "admission_client_id": "client", "admission_token": "a" * 64},
                )
        config = captured["config"]
        assert config.brain.max_decision_seconds == 44 * factor
        for feature in (config.reaction, config.vote_ability):
            assert feature.brain_timeout_seconds == 44 * factor
            assert feature.deadline_guard_seconds == factor
            assert feature.minimum_start_budget_seconds == 0.1 * factor
        assert config.network.shutdown_timeout_seconds == 10
        assert config.reconnect.max_disconnected_seconds == 10
        assert config.brain.cancellation_grace_seconds == 0.25
        assert config.speaking.cooldown_seconds == 0.2
        if factor == 10:
            assert captured["kwargs"]["clock"] is runner.time.monotonic
            assert captured["kwargs"]["sleep"] is asyncio.sleep
    asyncio.run(scenario())
