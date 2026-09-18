"""One real Phase 5 generation-broker subprocess for offline completion."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import stat
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_client._compat import await_with_timeout
from ai_client.llm import (
    AdmissionMetrics,
    GenerationAdmissionBroker,
    GenerationBrokerConfig,
)
from tests.fixtures.phase5_deterministic_backend import (
    Phase5DeterministicBrokerBackend,
)


from tests.fixtures.completion_clock import CompletionClock as _BarrierClock


def _private_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


async def run_broker(
    registry: dict[str, str],
    ready_path: Path,
    result_path: Path,
    stop_path: Path,
    metrics_path: Path,
    fairness_seed: str,
    clock_start: Path,
    day_one_release: Path,
) -> int:
    config = GenerationBrokerConfig(
        authentication_timeout_seconds=2.0,
        cancellation_grace_seconds=0.5,
        provider_drain_grace_seconds=2.0,
        shutdown_grace_seconds=3.0,
        metrics_queue_capacity=64,
    )
    metrics = AdmissionMetrics(config.metrics_queue_capacity, path=metrics_path)
    backend = Phase5DeterministicBrokerBackend()
    clock = _BarrierClock(clock_start, day_one_release)
    broker = GenerationAdmissionBroker(
        registry,
        backend,
        fairness_seed=fairness_seed,
        config=config,
        metrics=metrics,
        clock=clock,
        backend_request_timeout_seconds=1.0,
    )
    maximum_pending = 0
    ready = await broker.start()
    _private_json(
        ready_path,
        {
            "pid": os.getpid(),
            "host": ready.host,
            "port": ready.port,
            "protocol": ready.protocol,
            "backend_identity": asdict(ready.backend_identity),
            "config_fingerprint": ready.config_fingerprint,
            "config": asdict(config),
            "model_pid": None,
        },
    )
    failure: str | None = None
    try:
        async def _watch_broker():
            nonlocal maximum_pending, failure
            while not stop_path.exists():
                snapshot = broker.snapshot
                maximum_pending = max(maximum_pending, snapshot.pending_total)
                if snapshot.poisoned:
                    failure = snapshot.poison_reason or "ADMISSION_POISONED"
                    break
                await asyncio.sleep(0.001)
        await await_with_timeout(175.0, _watch_broker)
    except TimeoutError:
        failure = "BROKER_STOP_TIMEOUT"
    finally:
        before_close = broker.snapshot
        maximum_pending = max(maximum_pending, before_close.pending_total)
        await broker.aclose()

    metric_values = [
        json.loads(line)
        for line in metrics_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    terminal = {
        item["invocation_id"]: item["terminal_status"]
        for item in metric_values
        if item["event"] == "TERMINAL" and item["invocation_id"] is not None
    }
    calls = [
        item
        for item in metric_values
        if item["event"] == "PROVIDER_CALL_TERMINAL"
    ]
    _private_json(
        result_path,
        {
            "pid": os.getpid(),
            "success": failure is None and broker.shutdown_clean,
            "failure": failure,
            "shutdown_clean": broker.shutdown_clean,
            "snapshot_before_close": asdict(before_close),
            "snapshot_after_close": asdict(broker.snapshot),
            "maximum_pending": maximum_pending,
            "metrics_count": len(metric_values),
            "metrics_dropped": metrics.dropped_records,
            "terminal": terminal,
            "provider_call_terminals": len(calls),
            "backend": {
                "call_count": len(backend.calls),
                "peak_active": backend.peak_active,
                "active_after_close": backend.active,
                "closed": backend.closed,
                "calls": backend.calls,
            },
            "model_pid": None,
        },
    )
    return 0 if failure is None and broker.shutdown_clean else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--stop", type=Path, required=True)
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--fairness-seed", required=True)
    parser.add_argument("--clock-start", type=Path, required=True)
    parser.add_argument("--day-one-release", type=Path, required=True)
    arguments = parser.parse_args()
    raw_bootstrap = sys.stdin.readline()
    if not raw_bootstrap:
        parser.error("private bootstrap pipe is required")
    registry_value = json.loads(raw_bootstrap)
    if not isinstance(registry_value, dict):
        parser.error("private registry must be an object")
    registry = {str(key): str(value) for key, value in registry_value.items()}
    raise SystemExit(
        asyncio.run(
            run_broker(
                registry,
                arguments.ready,
                arguments.result,
                arguments.stop,
                arguments.metrics,
                arguments.fairness_seed,
                arguments.clock_start,
                arguments.day_one_release,
            )
        )
    )


if __name__ == "__main__":
    main()
