"""Bounded, prompt-free metadata records for generation admission."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field, replace
import json
import os
from pathlib import Path
from typing import BinaryIO, Final

from .admission_types import GenerationPriority
from .types import (
    ProviderQuiescence,
    ProviderTimingDiagnostic,
    ProviderTimingFieldState,
    ProviderTimingShapeStatus,
)


@dataclass(frozen=True)
class AdmissionMetric:
    event: str
    invocation_id: str | None = None
    client_id: str | None = None
    priority: GenerationPriority | None = None
    queue_wait_microseconds: int | None = None
    call_ordinal: int | None = None
    backend_code: str | None = None
    backend_error_detail: str | None = field(default=None, repr=False)
    http_status: int | None = None
    retryable: bool | None = None
    provider_quiescence: ProviderQuiescence | None = None
    consumer_state: str | None = None
    terminal_status: str | None = None
    generation_latency_microseconds: int | None = None
    request_bytes: int | None = None
    response_bytes: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    provider_prompt_microseconds: int | None = None
    provider_completion_microseconds: int | None = None
    provider_timing_diagnostic_status: ProviderTimingShapeStatus | None = None
    provider_timing_prompt_n_state: ProviderTimingFieldState | None = None
    provider_timing_prompt_ms_state: ProviderTimingFieldState | None = None
    provider_timing_predicted_n_state: ProviderTimingFieldState | None = None
    provider_timing_predicted_ms_state: ProviderTimingFieldState | None = None
    provider_timing_extra_key_count: int | None = None
    poison_transition: bool = False
    monotonic_microseconds: int = 0
    sequence: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.event, str) or not self.event or len(self.event) > 64:
            raise ValueError("event must be a non-empty bounded string")
        for name in (
            "invocation_id",
            "client_id",
            "backend_code",
            "consumer_state",
            "terminal_status",
        ):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str) or not value or len(value) > 128
            ):
                raise ValueError(f"{name} must be a bounded non-empty string or None")
        if self.priority is not None and not isinstance(
            self.priority, GenerationPriority
        ):
            raise TypeError("priority must be GenerationPriority or None")
        if self.provider_quiescence is not None and not isinstance(
            self.provider_quiescence, ProviderQuiescence
        ):
            raise TypeError("provider_quiescence must be ProviderQuiescence or None")
        diagnostic_fields = (
            self.provider_timing_diagnostic_status,
            self.provider_timing_prompt_n_state,
            self.provider_timing_prompt_ms_state,
            self.provider_timing_predicted_n_state,
            self.provider_timing_predicted_ms_state,
            self.provider_timing_extra_key_count,
        )
        if any(value is not None for value in diagnostic_fields):
            if self.event != "PROVIDER_CALL_TERMINAL" or any(
                value is None for value in diagnostic_fields
            ):
                raise ValueError(
                    "provider timing diagnostic requires one complete provider terminal projection"
                )
            if not isinstance(
                self.provider_timing_diagnostic_status, ProviderTimingShapeStatus
            ):
                raise TypeError(
                    "provider_timing_diagnostic_status must be ProviderTimingShapeStatus or None"
                )
            for name in (
                "provider_timing_prompt_n_state",
                "provider_timing_prompt_ms_state",
                "provider_timing_predicted_n_state",
                "provider_timing_predicted_ms_state",
            ):
                if not isinstance(getattr(self, name), ProviderTimingFieldState):
                    raise TypeError(f"{name} must be ProviderTimingFieldState or None")
            ProviderTimingDiagnostic(
                status=self.provider_timing_diagnostic_status,
                prompt_n=self.provider_timing_prompt_n_state,
                prompt_ms=self.provider_timing_prompt_ms_state,
                predicted_n=self.provider_timing_predicted_n_state,
                predicted_ms=self.provider_timing_predicted_ms_state,
                extra_key_count=self.provider_timing_extra_key_count,
            )
        if self.http_status is not None and (
            type(self.http_status) is not int or not 100 <= self.http_status <= 599
        ):
            raise ValueError("http_status must be an HTTP status int or None")
        if self.retryable is not None and type(self.retryable) is not bool:
            raise TypeError("retryable must be bool or None")
        if self.backend_code == "HTTP_STATUS":
            if self.http_status is None or self.retryable is None:
                raise ValueError("HTTP_STATUS metrics require status and retryable")
        elif self.backend_code is not None:
            if self.http_status is not None or self.retryable is None:
                raise ValueError(
                    "non-HTTP error metrics require retryable and forbid status"
                )
        elif self.http_status is not None or self.retryable is not None:
            raise ValueError("successful metrics cannot carry error attributes")
        if self.backend_error_detail is not None and type(self.backend_error_detail) is not str:
            raise TypeError("backend_error_detail must be str or None")
        if self.backend_error_detail is not None and (
            self.event != "PROVIDER_CALL_TERMINAL"
            or self.backend_code != "HTTP_STATUS"
            or not self.backend_error_detail
            or len(self.backend_error_detail) > 256
            or not all(character.isprintable() for character in self.backend_error_detail)
        ):
            raise ValueError("backend_error_detail requires an HTTP provider terminal")
        for name in (
            "queue_wait_microseconds",
            "call_ordinal",
            "generation_latency_microseconds",
            "request_bytes",
            "response_bytes",
            "prompt_tokens",
            "completion_tokens",
            "provider_prompt_microseconds",
            "provider_completion_microseconds",
            "provider_timing_extra_key_count",
            "monotonic_microseconds",
            "sequence",
        ):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} must be a non-negative int or None")
        if type(self.poison_transition) is not bool:
            raise TypeError("poison_transition must be bool")


def serialize_admission_metric(metric: AdmissionMetric) -> bytes:
    return _serialize_admission_metric(metric, include_private_detail=False)


def _serialize_admission_metric(
    metric: AdmissionMetric, *, include_private_detail: bool
) -> bytes:
    """Serialize only the fixed metadata allowlist; no arbitrary details exist."""

    value = {
        "backend_code": metric.backend_code,
        "call_ordinal": metric.call_ordinal,
        "client_id": metric.client_id,
        "consumer_state": metric.consumer_state,
        "event": metric.event,
        "generation_latency_microseconds": metric.generation_latency_microseconds,
        "http_status": metric.http_status,
        "invocation_id": metric.invocation_id,
        "monotonic_microseconds": metric.monotonic_microseconds,
        "poison_transition": metric.poison_transition,
        "priority": int(metric.priority) if metric.priority is not None else None,
        "provider_quiescence": (
            metric.provider_quiescence.value
            if metric.provider_quiescence is not None
            else None
        ),
        "provider_completion_microseconds": metric.provider_completion_microseconds,
        "provider_prompt_microseconds": metric.provider_prompt_microseconds,
        "provider_timing_diagnostic_status": (
            metric.provider_timing_diagnostic_status.value
            if metric.provider_timing_diagnostic_status is not None
            else None
        ),
        "provider_timing_prompt_n_state": (
            metric.provider_timing_prompt_n_state.value
            if metric.provider_timing_prompt_n_state is not None
            else None
        ),
        "provider_timing_prompt_ms_state": (
            metric.provider_timing_prompt_ms_state.value
            if metric.provider_timing_prompt_ms_state is not None
            else None
        ),
        "provider_timing_predicted_n_state": (
            metric.provider_timing_predicted_n_state.value
            if metric.provider_timing_predicted_n_state is not None
            else None
        ),
        "provider_timing_predicted_ms_state": (
            metric.provider_timing_predicted_ms_state.value
            if metric.provider_timing_predicted_ms_state is not None
            else None
        ),
        "provider_timing_extra_key_count": metric.provider_timing_extra_key_count,
        "prompt_tokens": metric.prompt_tokens,
        "completion_tokens": metric.completion_tokens,
        "queue_wait_microseconds": metric.queue_wait_microseconds,
        "request_bytes": metric.request_bytes,
        "response_bytes": metric.response_bytes,
        "retryable": metric.retryable,
        "sequence": metric.sequence,
        "terminal_status": metric.terminal_status,
    }
    if include_private_detail:
        value["backend_error_detail"] = metric.backend_error_detail
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


_STOP: Final = object()


class AdmissionMetricsWriteError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("ADMISSION_METRICS_WRITE_FAILED")


class AdmissionMetrics:
    """One bounded writer for admission-only metadata.

    When ``path`` is absent this remains an in-memory bounded evidence sink.  A
    full writer queue never blocks provider state transitions; it increments a
    bounded counter and drops that metrics record rather than retaining prompts
    or unbounded diagnostic state.
    """

    def __init__(self, capacity: int, *, path: Path | None = None, handle: BinaryIO | None = None) -> None:
        if type(capacity) is not int or not 1 <= capacity <= 64:
            raise ValueError("capacity must be an int in [1, 64]")
        if path is not None and not isinstance(path, Path):
            raise TypeError("path must be pathlib.Path or None")
        if path is not None and not path.parent.is_dir():
            raise ValueError("metrics parent directory must already exist")
        if path is not None and handle is not None:
            raise ValueError("path and handle are mutually exclusive")
        self._capacity = capacity
        self._path = path
        self._queue: asyncio.Queue[AdmissionMetric | object] = asyncio.Queue(
            maxsize=capacity
        )
        self._records: deque[AdmissionMetric] = deque(maxlen=capacity)
        self._sequence = 0
        self._dropped = 0
        self._writer_task: asyncio.Task[None] | None = None
        self._closed = False
        self._closing = False
        self._handle: BinaryIO | None = handle
        self._owns_handle = False
        self._private_detail_enabled = handle is not None
        self._writer_error: AdmissionMetricsWriteError | None = None

    @property
    def records(self) -> tuple[AdmissionMetric, ...]:
        return tuple(self._records)

    @property
    def dropped_records(self) -> int:
        return self._dropped

    async def start(self) -> None:
        if self._writer_task is not None or self._closed:
            raise RuntimeError("admission metrics already started or closed")
        if self._path is not None:
            self._handle = await asyncio.to_thread(open, self._path, "ab")
            self._owns_handle = True
        self._writer_task = asyncio.create_task(
            self._writer_loop(), name="aiwolf-admission-metrics-writer"
        )

    def record_nowait(self, metric: AdmissionMetric, *, critical: bool = False) -> bool:
        if not isinstance(metric, AdmissionMetric):
            raise TypeError("metric must be AdmissionMetric")
        if self._closed or self._closing or self._writer_task is None or self._writer_task.done() or self._writer_error is not None:
            return False
        if not self._private_detail_enabled and metric.backend_error_detail is not None:
            metric = replace(metric, backend_error_detail=None)
        self._sequence += 1
        stamped = replace(metric, sequence=self._sequence)
        try:
            self._queue.put_nowait(stamped)
        except asyncio.QueueFull:
            self._dropped = min(self._dropped + 1, 2**63 - 1)
            if not critical:
                return False
            # Permanent poison evidence must itself enter the bounded stream.
            # Evict exactly one older not-yet-written metadata item; never grow
            # the queue or retain arbitrary diagnostic payloads.
            try:
                evicted = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return False
            self._queue.task_done()
            if evicted is _STOP:
                self._queue.put_nowait(_STOP)
                return False
            self._queue.put_nowait(stamped)
        return True

    async def flush(self) -> None:
        if self._writer_error is not None:
            raise self._writer_error
        if self._writer_task is not None and self._writer_task.done():
            await self._writer_task
        if self._writer_task is not None:
            await self._queue.join()
        if self._writer_error is not None:
            raise self._writer_error

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closing = True
        task = self._writer_task
        if task is None:
            self._closed = True
            return
        if self._writer_error is None and not task.done():
            await self._queue.join()
            if self._writer_error is None:
                self._queue.put_nowait(_STOP)
        await task
        self._closed = True
        if self._writer_error is not None:
            raise self._writer_error

    async def _writer_loop(self) -> None:
        try:
            while True:
                queued = await self._queue.get()
                try:
                    if queued is _STOP:
                        break
                    assert isinstance(queued, AdmissionMetric)
                    self._records.append(queued)
                    if self._handle is not None:
                        payload = _serialize_admission_metric(
                            queued, include_private_detail=self._private_detail_enabled
                        )
                        await asyncio.to_thread(self._durable_write, payload)
                except Exception:
                    self._writer_error = AdmissionMetricsWriteError()
                    while True:
                        try:
                            self._queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        else:
                            self._queue.task_done()
                    break
                finally:
                    self._queue.task_done()
        finally:
            if self._handle is not None and self._owns_handle:
                await asyncio.to_thread(self._handle.close)
                self._handle = None

    def _durable_write(self, payload: bytes) -> None:
        assert self._handle is not None
        self._handle.write(payload)
        self._handle.flush()
        os.fsync(self._handle.fileno())
