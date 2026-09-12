from __future__ import annotations

import asyncio
from contextlib import contextmanager
from functools import wraps
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from typing import BinaryIO, Iterator
from unittest.mock import patch

import pytest

from ai_client.llm.audit import JsonlAiAuditSink
from ai_client.llm.types import (
    AiAuditDecision,
    AiAuditError,
    AiAuditErrorCode,
    AiAuditRecord,
    AiAuditStatus,
    AiAuditWriterConfig,
    BackendIdentity,
    serialize_ai_audit,
)


def async_test(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return wrapper


@contextmanager
def _local_directory() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as value:
        yield Path(value)


def _record(request_id: str = "request-A") -> AiAuditRecord:
    prompt = '{"messages":[{"content":"日本語","role":"user"}],"output_schema":{}}'
    response = '{"kind":"chat"}'
    prompt_bytes = prompt.encode("utf-8")
    response_bytes = response.encode("utf-8")
    return AiAuditRecord(
        schema_version="aiwolf.ai-log.v1",
        recorded_at_utc="2026-09-11T01:02:03.123456Z",
        game_id="game-A",
        player_id="player-A",
        request_id=request_id,
        phase="DAY",
        day=1,
        world_version=9,
        backend=BackendIdentity(
            backend_type="openai_compatible_http",
            endpoint_origin="http://127.0.0.1:8080",
            endpoint_path="/v1/chat/completions",
            model="configured-model",
            config_fingerprint="a" * 64,
        ),
        attempt_ordinal=1,
        prompt_sha256=hashlib.sha256(prompt_bytes).hexdigest(),
        prompt_bytes=len(prompt_bytes),
        prompt_json=prompt,
        response_sha256=hashlib.sha256(response_bytes).hexdigest(),
        response_bytes=len(response_bytes),
        response_text=response,
        latency_microseconds=100,
        provider_model="provider-model",
        finish_reason="stop",
        prompt_tokens=8,
        completion_tokens=4,
        status=AiAuditStatus.DECISION,
        backend_error_code=None,
        validation_code=None,
        decision=AiAuditDecision(kind="chat", option_id="opaque-O", text="hello"),
    )


def _assert_error(
    caught: pytest.ExceptionInfo[AiAuditError],
    code: AiAuditErrorCode,
    terminal_code: AiAuditErrorCode | None = None,
) -> None:
    assert caught.value.code is code
    assert caught.value.terminal_code is terminal_code
    assert str(caught.value) == code.value
    assert caught.value.args == (code.value,)
    assert caught.value.__cause__ is None


async def _wait_thread_event(event: threading.Event) -> None:
    assert await asyncio.to_thread(event.wait, 2.0)


async def _wait_queue_size(sink: JsonlAiAuditSink, expected: int) -> None:
    async def wait() -> None:
        while sink._queue.qsize() != expected:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), 2.0)


def _assert_no_owned_work(sink: JsonlAiAuditSink) -> None:
    assert sink._executor_shutdown
    assert sink._file_owner._handle is None
    assert sink._writer_task is None or sink._writer_task.done()
    assert sink._start_task is None or sink._start_task.done()
    assert sink._close_task is None or sink._close_task.done()
    assert not any(
        thread.name.startswith(sink._thread_name_prefix)
        for thread in threading.enumerate()
    )


@async_test
async def test_fifo_durable_sequences_append_and_lifecycle_are_exact() -> None:
    with _local_directory() as directory:
        path = directory / "ai.jsonl"
        existing = b'{"existing":true}\n'
        path.write_bytes(existing)
        sink = JsonlAiAuditSink(path, config=AiAuditWriterConfig(queue_capacity=2))
        with pytest.raises(AiAuditError) as before:
            await sink.write(_record())
        _assert_error(before, AiAuditErrorCode.NOT_STARTED)

        await sink.start()
        with pytest.raises(AiAuditError) as restarted:
            await sink.start()
        _assert_error(restarted, AiAuditErrorCode.CLOSED)
        acknowledgements = await asyncio.gather(
            *(sink.write(_record(f"request-{index}")) for index in range(1, 5))
        )
        assert [ack.sequence for ack in acknowledgements] == [1, 2, 3, 4]
        assert all(ack.durable is True for ack in acknowledgements)

        await sink.aclose()
        await sink.aclose()
        with pytest.raises(AiAuditError) as closed:
            await sink.write(_record("late"))
        _assert_error(closed, AiAuditErrorCode.CLOSED)
        assert path.read_bytes() == existing + b"".join(
            serialize_ai_audit(_record(f"request-{index}"))
            for index in range(1, 5)
        )
        _assert_no_owned_work(sink)


@async_test
async def test_record_rejection_is_pre_enqueue_and_consumes_no_sequence() -> None:
    with _local_directory() as directory:
        sink = JsonlAiAuditSink(directory / "ai.jsonl")
        await sink.start()
        with pytest.raises(AiAuditError) as invalid:
            await sink.write(object())  # type: ignore[arg-type]
        _assert_error(invalid, AiAuditErrorCode.RECORD_INVALID)
        acknowledgement = await sink.write(_record())
        assert acknowledgement.sequence == 1
        await sink.aclose()


@async_test
async def test_queue_backpressure_and_cancellation_before_enqueue_are_bounded() -> None:
    entered = threading.Event()
    release = threading.Event()
    original = None

    with _local_directory() as directory:
        sink = JsonlAiAuditSink(
            directory / "ai.jsonl", config=AiAuditWriterConfig(queue_capacity=1)
        )
        original = sink._file_owner.durable_append

        def blocked(payload: bytes) -> None:
            entered.set()
            assert release.wait(2.0)
            original(payload)

        sink._file_owner.durable_append = blocked  # type: ignore[method-assign]
        await sink.start()
        first = asyncio.create_task(sink.write(_record("first")))
        await _wait_thread_event(entered)
        second = asyncio.create_task(sink.write(_record("second")))
        await _wait_queue_size(sink, 1)
        before_enqueue = asyncio.create_task(sink.write(_record("cancel-before")))
        await asyncio.sleep(0)
        before_enqueue.cancel()
        with pytest.raises(asyncio.CancelledError):
            await before_enqueue
        assert sink._queue.qsize() == 1
        release.set()
        assert [ack.sequence for ack in await asyncio.gather(first, second)] == [1, 2]
        await sink.aclose()
        lines = (directory / "ai.jsonl").read_text(encoding="utf-8").splitlines()
        assert [json.loads(line)["request_id"] for line in lines] == ["first", "second"]


@async_test
async def test_cancellation_after_enqueue_retains_owned_item_and_close_waiter_is_shielded() -> None:
    entered = threading.Event()
    release = threading.Event()
    with _local_directory() as directory:
        sink = JsonlAiAuditSink(
            directory / "ai.jsonl", config=AiAuditWriterConfig(queue_capacity=1)
        )
        original = sink._file_owner.durable_append

        def blocked(payload: bytes) -> None:
            entered.set()
            assert release.wait(2.0)
            original(payload)

        sink._file_owner.durable_append = blocked  # type: ignore[method-assign]
        await sink.start()
        first = asyncio.create_task(sink.write(_record("first")))
        await _wait_thread_event(entered)
        cancelled = asyncio.create_task(sink.write(_record("cancel-after")))
        await _wait_queue_size(sink, 1)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        close_waiter = asyncio.create_task(sink.aclose())
        await asyncio.sleep(0)
        close_waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await close_waiter
        release.set()
        assert (await first).sequence == 1
        await sink.aclose()
        lines = (directory / "ai.jsonl").read_text(encoding="utf-8").splitlines()
        assert [json.loads(line)["request_id"] for line in lines] == [
            "first",
            "cancel-after",
        ]
        assert sink._sequence == 2
        _assert_no_owned_work(sink)


class _FailingHandle:
    def __init__(self, phase: str, entered: threading.Event, release: threading.Event):
        self.phase = phase
        self.entered = entered
        self.release = release
        self.closed = False

    def _fail(self, phase: str) -> None:
        if self.phase == phase:
            self.entered.set()
            assert self.release.wait(2.0)
            raise OSError("C:/secret/path and bearer-secret")

    def write(self, payload: bytes) -> int:
        self._fail("write")
        return len(payload)

    def flush(self) -> None:
        self._fail("flush")

    def fileno(self) -> int:
        self._fail("fsync")
        return -1

    def close(self) -> None:
        self.closed = True


async def _assert_terminal_fanout(
    phase: str, terminal_code: AiAuditErrorCode
) -> None:
    entered = threading.Event()
    release = threading.Event()
    handle = _FailingHandle(phase, entered, release)
    with _local_directory() as directory:
        with patch("ai_client.llm.audit._open_binary_append", return_value=handle):
            sink = JsonlAiAuditSink(
                directory / "ai.jsonl",
                config=AiAuditWriterConfig(queue_capacity=2),
            )
            await sink.start()
            current = asyncio.create_task(sink.write(_record("current")))
            await _wait_thread_event(entered)
            queued = [
                asyncio.create_task(sink.write(_record("queued-1"))),
                asyncio.create_task(sink.write(_record("queued-2"))),
            ]
            await _wait_queue_size(sink, 2)
            release.set()
            with pytest.raises(AiAuditError) as current_error:
                await current
            _assert_error(current_error, terminal_code)
            for task in queued:
                with pytest.raises(AiAuditError) as queued_error:
                    await task
                _assert_error(
                    queued_error, AiAuditErrorCode.SINK_FAILED, terminal_code
                )
            with pytest.raises(AiAuditError) as future_error:
                await sink.write(_record("future"))
            _assert_error(future_error, AiAuditErrorCode.SINK_FAILED, terminal_code)
            for _ in range(2):
                with pytest.raises(AiAuditError) as close_error:
                    await sink.aclose()
                _assert_error(close_error, terminal_code)
            assert handle.closed
            assert sink._sequence == 0
            _assert_no_owned_work(sink)


@async_test
async def test_each_writer_failure_is_sanitized_terminal_and_fans_out() -> None:
    await _assert_terminal_fanout("write", AiAuditErrorCode.WRITE_FAILED)
    await _assert_terminal_fanout("flush", AiAuditErrorCode.FLUSH_FAILED)
    await _assert_terminal_fanout("fsync", AiAuditErrorCode.FSYNC_FAILED)


@async_test
async def test_open_failure_is_terminal_sanitized_and_fully_shut_down() -> None:
    with _local_directory() as directory:
        with patch(
            "ai_client.llm.audit._open_binary_append",
            side_effect=OSError("C:/secret/path and bearer-secret"),
        ):
            sink = JsonlAiAuditSink(directory / "ai.jsonl")
            with pytest.raises(AiAuditError) as opened:
                await sink.start()
            _assert_error(opened, AiAuditErrorCode.OPEN_FAILED)
            with pytest.raises(AiAuditError) as future:
                await sink.write(_record())
            _assert_error(
                future, AiAuditErrorCode.SINK_FAILED, AiAuditErrorCode.OPEN_FAILED
            )
            for _ in range(2):
                with pytest.raises(AiAuditError) as closed:
                    await sink.aclose()
                _assert_error(closed, AiAuditErrorCode.OPEN_FAILED)
            _assert_no_owned_work(sink)


class _CloseFailingWrapper:
    def __init__(self, handle: BinaryIO) -> None:
        self._handle = handle

    def write(self, payload: bytes) -> int:
        return self._handle.write(payload)

    def flush(self) -> None:
        self._handle.flush()

    def fileno(self) -> int:
        return self._handle.fileno()

    def close(self) -> None:
        self._handle.close()
        raise OSError("C:/secret/path and bearer-secret")


@async_test
async def test_close_failure_is_retained_and_repeated_close_re_raises_it() -> None:
    with _local_directory() as directory:
        path = directory / "ai.jsonl"
        real_open = open

        def wrapped_open(open_path: Path) -> BinaryIO:
            return _CloseFailingWrapper(real_open(open_path, "ab"))  # type: ignore[return-value]

        with patch("ai_client.llm.audit._open_binary_append", side_effect=wrapped_open):
            sink = JsonlAiAuditSink(path)
            await sink.start()
            assert (await sink.write(_record())).sequence == 1
            for _ in range(2):
                with pytest.raises(AiAuditError) as closed:
                    await sink.aclose()
                _assert_error(closed, AiAuditErrorCode.CLOSE_FAILED)
            with pytest.raises(AiAuditError) as future:
                await sink.write(_record("late"))
            _assert_error(
                future, AiAuditErrorCode.SINK_FAILED, AiAuditErrorCode.CLOSE_FAILED
            )
            assert path.read_bytes() == serialize_ai_audit(_record())
            _assert_no_owned_work(sink)


@async_test
async def test_close_before_start_is_idempotent_and_creates_no_file() -> None:
    with _local_directory() as directory:
        path = directory / "ai.jsonl"
        sink = JsonlAiAuditSink(path)
        await sink.aclose()
        await sink.aclose()
        assert not path.exists()
        with pytest.raises(AiAuditError) as closed:
            await sink.write(_record())
        _assert_error(closed, AiAuditErrorCode.CLOSED)
        _assert_no_owned_work(sink)
