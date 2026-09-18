import json
from tests.fixtures.completion_diagnostics import controller_error_kind
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import pytest
from tests.fixtures.completion_diagnostics import client_progress, safe_progress, timeout_summary, write_progress, ProgressSampler


def test_progress_excludes_payloads_and_reallowlists_fields(tmp_path):
    value = {"day": 1, "phase": "day", "lifecycle": "SECRET", "payload": "SECRET",
             "outcomes": {"timed_out": 2, "SECRET": 9, "accepted": "SECRET"}}
    path = tmp_path / "server.result.progress.json"
    previous = write_progress(path, value, None)
    assert previous == {"day": 1, "phase": "day", "lifecycle": "UNKNOWN", "outcomes": {"timed_out": 2}}
    assert write_progress(path, value, previous) == previous
    # Reader also refuses raw/private fields even if an input file is malformed.
    path.write_text(json.dumps(value))
    result = timeout_summary(tmp_path)
    assert result["server.result"] == previous
    assert "SECRET" not in json.dumps(result)
    assert safe_progress(None) == {"status": "UNKNOWN"}


def test_progress_distinguishes_stalled_future_from_exhausted_attempts():
    state = NS(lifecycle=NS(value="running"), accepted_count=0, send_count=0,
               outcomes=[NS(status=NS(value="timed_out"), brain_outcome="STALE")])
    controller = NS(snapshot=lambda: state, _phase_chat_invocations=2,
                    _chat_deadline_closed=False, _chat_rejected_closed=False,
                    _history_gap_closed=False, _transport_gap_closed=False,
                    _pending_initial=NS(due=6.1), _pending_reaction=NS(due=6), _pending_co=None)
    runtime = NS(reaction=controller, world=NS(
        snapshot=lambda: NS(phase=NS(day=1, phase="day"), version=1, last_applied_seq=2, is_caught_up=True),
        current_actions=lambda: NS(is_caught_up=True, world_version=1, world_last_applied_seq=2, network_last_seq=2),
        transport_observations=lambda: NS(current_deadline=NS(local_deadline_monotonic=11))))
    value = client_progress(runtime, lambda: 6, True)
    assert value["initial"] == "FUTURE" and value["reaction"] == "DUE" and value["co"] == "ABSENT"
    assert value["phase_attempts"] == 2 and value["accepted"] == 0
    assert value["deadline"] == "VALID" and value["observed_day1"] is True
    assert value["brain_outcomes"] == {"STALE": 1} and value["actions_current"] is True


def test_sampling_is_throttled_before_snapshot_and_gate_reads(tmp_path):
    sampler = ProgressSampler(tmp_path / "progress.json")
    snapshot = Mock(return_value={"day": 1})
    with patch("tests.fixtures.completion_diagnostics.time.monotonic", return_value=10):
        for _ in range(50):
            sampler.sample(snapshot)
    snapshot.assert_called_once()
    with patch("tests.fixtures.completion_diagnostics.time.monotonic", return_value=10.25):
        sampler.sample(snapshot)
    assert snapshot.call_count == 2


@pytest.mark.parametrize("failure", [OSError, ValueError, RuntimeError])
def test_diagnostic_failure_does_not_stop_measured_process(tmp_path, failure):
    sampler = ProgressSampler(tmp_path / "progress.json")
    with patch("tests.fixtures.completion_diagnostics.write_progress", side_effect=failure("SECRET")):
        sampler.sample(lambda: {"day": 1})
    assert sampler.enabled is False
    sampler.sample(lambda: pytest.fail("disabled sampler must not evaluate snapshot"))
    sampler = ProgressSampler(tmp_path / "snapshot.json")
    sampler.sample(Mock(side_effect=failure("SECRET")))
    assert sampler.enabled is False
    assert json.loads(sampler.path.read_text()) == {"diagnostic": "UNKNOWN"}
@pytest.mark.parametrize("value, expected", [
    (None, "NONE"), ("AssertionError", "AssertionError"),
    ("CancelledError", "CancelledError"), ("RuntimeError", "RuntimeError"),
    ("RuntimeError: SECRET /private/path", "UNKNOWN"),
    ({"token": "SECRET"}, "UNKNOWN"), ("CustomPrivateException", "UNKNOWN"),
])
def test_controller_error_kind_is_fixed_allowlist(value, expected):
    assert controller_error_kind(value) == expected


@pytest.mark.parametrize("kind", ["success", "finalization", "record", "cancel"])
def test_failure_probe_preserves_result_exception_identity_and_cancellation(kind):
    import asyncio
    from tests.fixtures.completion_diagnostics import install_reaction_failure_probe

    error = asyncio.CancelledError("SECRET") if kind == "cancel" else RuntimeError("SECRET")
    result = object()

    async def finalize():
        if kind in {"finalization", "cancel"}:
            raise error
        return result

    def record():
        if kind == "record":
            raise error
        return result

    controller = NS(_await_finalization=finalize, _record_outcome=record)
    observed = install_reaction_failure_probe(controller)

    async def scenario():
        if kind in {"finalization", "cancel"}:
            with pytest.raises(type(error)) as caught:
                await controller._await_finalization()
            assert caught.value is error
            assert observed == {"site": "FINALIZATION"}
        else:
            assert await controller._await_finalization() is result
            if kind == "record":
                with pytest.raises(RuntimeError) as caught:
                    controller._record_outcome()
                assert caught.value is error
                assert observed == {"site": "OUTCOME_RECORD"}
            else:
                assert controller._record_outcome() is result
                assert observed == {"site": "UNKNOWN"}
        assert "SECRET" not in json.dumps(observed)

    asyncio.run(scenario())
