import json
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts import monitor_phase6_gpu as monitor


SAMPLE = "0, GPU-test, 75, 20, 7000, 8192, 68, 210.5\n"


def test_fields_and_missing_are_distinct():
    row = monitor.parse_sample(SAMPLE)[0]
    assert row["gpu_util_pct"] == 75
    assert row["vram_used_mib"] == 7000
    assert row["temperature_c"] == 68
    assert row["power_w"] == 210.5
    assert row["missing_fields"] == []
    missing = monitor.parse_sample(SAMPLE.replace("75", "N/A"))[0]
    assert missing["gpu_util_pct"] is None
    assert missing["missing_fields"] == ["gpu_util_pct"]


@pytest.mark.parametrize("bad", ["", "x", SAMPLE.replace("75", "nan"), SAMPLE.replace("75", "101"), SAMPLE.replace("75", "-1")])
def test_bad_samples_rejected(bad):
    with pytest.raises(ValueError):
        monitor.parse_sample(bad)


@pytest.mark.parametrize("error,code", [(OSError(), "NVIDIA_SMI_UNAVAILABLE"),
    (subprocess.TimeoutExpired("nvidia-smi", 2), "NVIDIA_SMI_TIMEOUT")])
def test_query_failures_are_not_zero_usage(error, code):
    with patch.object(monitor.subprocess, "run", side_effect=error):
        assert monitor.sample_gpu(2) == {"status": "ERROR", "error": code, "gpus": []}


def test_query_uses_bounded_argv_without_shell():
    with patch.object(monitor.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=SAMPLE.encode())) as run:
        assert monitor.sample_gpu(1.25)["status"] == "OK"
    assert run.call_args.kwargs["timeout"] == 1.25
    assert "shell" not in run.call_args.kwargs
    assert run.call_args.args[0][0] == "nvidia-smi"


@pytest.mark.parametrize("scale", ["1", "0.1"])
def test_real_limit_and_poll_unaffected_by_scale(tmp_path, monkeypatch, scale):
    monkeypatch.setenv("AIWOLF_TIME_SCALE", scale)
    now = [0.0]
    waits, timeouts = [], []
    def sleep(delay):
        waits.append(delay)
        now[0] += delay
    def sample(timeout):
        timeouts.append(timeout)
        return {"status": "OK", "gpus": monitor.parse_sample(SAMPLE)}
    output = tmp_path / "samples.jsonl"
    summary = monitor.monitor(output, max_seconds=3, sampler=sample, clock=lambda: now[0], sleep=sleep)
    assert waits == [1, 1, 1]
    assert timeouts == [2, 2, 1]
    assert summary["real_duration_sec"] == 3
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert rows[0]["clock_domain"] == "REAL"
    assert rows[-1]["reason"] == "REAL_LIMIT"
    assert rows[-1]["samples"] == 3
    with pytest.raises(FileExistsError):
        monitor.monitor(output, max_seconds=1)


def test_watched_exit_stops_without_query(tmp_path):
    watch = SimpleNamespace(alive=lambda: False)
    result = monitor.monitor(tmp_path / "exit.jsonl", max_seconds=100, watch=watch,
                             sampler=lambda _: pytest.fail("query after process exit"))
    assert result["reason"] == "WATCH_PROCESS_EXITED"
    assert result["samples"] == 0


@pytest.mark.parametrize("duration", [0, -1, float("nan"), float("inf"), 86401])
def test_invalid_real_bound_rejected(tmp_path, duration):
    output = tmp_path / "bad.jsonl"
    with pytest.raises(ValueError):
        monitor.monitor(output, max_seconds=duration)
    assert not output.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows process identity handle")
def test_windows_watch_keeps_exact_process_object():
    with subprocess.Popen(["python", "-c", "pass"]) as process:
        watch = monitor.ProcessWatch(process.pid)
        process.wait(timeout=5)
        try:
            assert watch.alive() is False
        finally:
            watch.close()
