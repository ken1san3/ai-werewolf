"""Bounded, durable, single-writer JSONL audit sink for one AI client process."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum, auto
import os
from pathlib import Path
import threading
from typing import BinaryIO, Final

from .types import (
    AiAuditError,
    AiAuditErrorCode,
    AiAuditRecord,
    AiAuditWriterConfig,
    AuditWriteAck,
    serialize_ai_audit,
)


class _SinkState(Enum):
    NEW = auto()
    STARTING = auto()
    RUNNING = auto()
    CLOSING = auto()
    CLOSED = auto()
    FAILED = auto()


class _AuditIoFailure(Exception):
    def __init__(self, code: AiAuditErrorCode) -> None:
        self.code = code


def _open_binary_append(path: Path) -> BinaryIO:
    return open(path, "ab")


class _FileOwner:
    """Own the handle and touch it only from the sink's dedicated executor."""

    def __init__(self) -> None:
        self._handle: BinaryIO | None = None
        self._thread_id: int | None = None

    def open(self, path: Path) -> None:
        try:
            handle = _open_binary_append(path)
        except Exception:
            raise _AuditIoFailure(AiAuditErrorCode.OPEN_FAILED) from None
        self._handle = handle
        self._thread_id = threading.get_ident()

    def _require_handle(self) -> BinaryIO:
        if self._handle is None or self._thread_id != threading.get_ident():
            raise RuntimeError("audit file ownership violation")
        return self._handle

    def durable_append(self, payload: bytes) -> None:
        handle = self._require_handle()
        try:
            written = handle.write(payload)
            if written != len(payload):
                raise OSError("partial audit write")
        except Exception:
            raise _AuditIoFailure(AiAuditErrorCode.WRITE_FAILED) from None
        try:
            handle.flush()
        except Exception:
            raise _AuditIoFailure(AiAuditErrorCode.FLUSH_FAILED) from None
        try:
            os.fsync(handle.fileno())
        except Exception:
            raise _AuditIoFailure(AiAuditErrorCode.FSYNC_FAILED) from None

    def close(self) -> None:
        handle = self._require_handle()
        try:
            handle.close()
        except Exception:
            # Preserve the stable CLOSE_FAILED result while making one bounded
            # best-effort release attempt for handles whose first close was
            # interrupted after partially completing.
            try:
                handle.close()
            except Exception:
                pass
            raise _AuditIoFailure(AiAuditErrorCode.CLOSE_FAILED) from None
        finally:
            self._handle = None


@dataclass(frozen=True)
class _WriteItem:
    payload: bytes
    acknowledgement: asyncio.Future[AuditWriteAck]


_STOP: Final = object()


def _consume_future_exception(future: asyncio.Future[AuditWriteAck]) -> None:
    """Prevent an enqueue-then-cancel caller from leaving an unobserved failure."""

    if not future.cancelled():
        future.exception()


class JsonlAiAuditSink:
    """Append records in FIFO order and acknowledge only durable writes.

    The sink is deliberately one-shot and bound to the event loop on which ``start``
    is called.  Its only filesystem owner is a dedicated one-thread executor.
    """

    def __init__(
        self,
        path: Path,
        *,
        config: AiAuditWriterConfig = AiAuditWriterConfig(),
    ) -> None:
        if not isinstance(path, Path):
            raise TypeError("path must be pathlib.Path")
        if not isinstance(config, AiAuditWriterConfig):
            raise TypeError("config must be AiAuditWriterConfig")
        self._path = path
        self._config = config
        self._queue: asyncio.Queue[_WriteItem | object] = asyncio.Queue(
            maxsize=config.queue_capacity
        )
        self._admission_lock = asyncio.Lock()
        self._state = _SinkState.NEW
        self._terminal_code: AiAuditErrorCode | None = None
        self._sequence = 0
        self._file_owner = _FileOwner()
        self._thread_name_prefix = f"aiwolf-ai-audit-{id(self):x}"
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=self._thread_name_prefix
        )
        self._executor_shutdown = False
        self._start_task: asyncio.Task[None] | None = None
        self._writer_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Open the append-only file and start the sole writer task once."""

        if self._state is not _SinkState.NEW:
            if self._state is _SinkState.FAILED:
                raise self._terminal_error()
            raise AiAuditError(AiAuditErrorCode.CLOSED)
        self._state = _SinkState.STARTING
        self._start_task = asyncio.create_task(
            self._start_owned(), name="aiwolf-ai-audit-start"
        )
        try:
            await asyncio.shield(self._start_task)
        except asyncio.CancelledError:
            raise
        if self._terminal_code is not None:
            raise self._terminal_error()

    async def write(self, record: AiAuditRecord) -> AuditWriteAck:
        """Admit one bounded serialized record and await its durable acknowledgement."""

        self._raise_if_not_writable()
        loop = asyncio.get_running_loop()
        acknowledgement: asyncio.Future[AuditWriteAck] = loop.create_future()
        acknowledgement.add_done_callback(_consume_future_exception)

        async with self._admission_lock:
            self._raise_if_not_writable()
            payload = serialize_ai_audit(record, self._config.max_record_bytes)
            item = _WriteItem(payload=payload, acknowledgement=acknowledgement)
            await self._queue.put(item)

        return await asyncio.shield(acknowledgement)

    async def aclose(self) -> None:
        """Stop admission and await complete drain, file close, and executor shutdown."""

        if self._close_task is None:
            if self._state in {_SinkState.STARTING, _SinkState.RUNNING}:
                self._state = _SinkState.CLOSING
            elif self._state is _SinkState.NEW:
                self._state = _SinkState.CLOSED
            self._close_task = asyncio.create_task(
                self._close_owned(), name="aiwolf-ai-audit-close"
            )
        try:
            await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            raise
        if self._terminal_code is not None:
            raise self._terminal_error()

    async def _start_owned(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                self._executor, self._file_owner.open, self._path
            )
        except _AuditIoFailure:
            self._set_terminal(AiAuditErrorCode.OPEN_FAILED)
            await self._shutdown_executor()
            return
        except Exception:
            self._set_terminal(AiAuditErrorCode.OPEN_FAILED)
            await self._shutdown_executor()
            return

        self._writer_task = asyncio.create_task(
            self._writer_loop(), name="aiwolf-ai-audit-writer"
        )
        if self._state is _SinkState.STARTING:
            self._state = _SinkState.RUNNING

    async def _writer_loop(self) -> None:
        try:
            while True:
                queued = await self._queue.get()
                if queued is _STOP:
                    self._queue.task_done()
                    break
                assert isinstance(queued, _WriteItem)
                try:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        self._executor,
                        self._file_owner.durable_append,
                        queued.payload,
                    )
                except _AuditIoFailure as failure:
                    self._queue.task_done()
                    self._resolve_failure(queued, failure.code)
                    await self._fail_remaining(failure.code)
                    break
                except Exception:
                    self._queue.task_done()
                    self._resolve_failure(queued, AiAuditErrorCode.WRITE_FAILED)
                    await self._fail_remaining(AiAuditErrorCode.WRITE_FAILED)
                    break
                self._sequence += 1
                if not queued.acknowledgement.done():
                    queued.acknowledgement.set_result(
                        AuditWriteAck(sequence=self._sequence, durable=True)
                    )
                self._queue.task_done()
        finally:
            await self._finish_writer()

    async def _fail_remaining(self, terminal_code: AiAuditErrorCode) -> None:
        self._set_terminal(terminal_code)
        self._drain_failed_items(terminal_code)
        # A writer may already hold the admission lock while waiting for capacity.
        # The first drain releases it; acquiring the lock proves that admission has
        # either completed or observed FAILED.  Drain once more for that final item.
        async with self._admission_lock:
            self._drain_failed_items(terminal_code)

    def _drain_failed_items(self, terminal_code: AiAuditErrorCode) -> None:
        while True:
            try:
                queued = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if isinstance(queued, _WriteItem) and not queued.acknowledgement.done():
                queued.acknowledgement.set_exception(
                    AiAuditError(
                        AiAuditErrorCode.SINK_FAILED,
                        terminal_code=terminal_code,
                    )
                )
            self._queue.task_done()

    def _resolve_failure(
        self, item: _WriteItem, terminal_code: AiAuditErrorCode
    ) -> None:
        if not item.acknowledgement.done():
            item.acknowledgement.set_exception(AiAuditError(terminal_code))

    async def _finish_writer(self) -> None:
        close_code: AiAuditErrorCode | None = None
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self._file_owner.close)
        except _AuditIoFailure:
            close_code = AiAuditErrorCode.CLOSE_FAILED
        except Exception:
            close_code = AiAuditErrorCode.CLOSE_FAILED
        if self._terminal_code is None and close_code is not None:
            self._set_terminal(close_code)
        try:
            await self._shutdown_executor()
        except Exception:
            if self._terminal_code is None:
                self._set_terminal(AiAuditErrorCode.CLOSE_FAILED)
        if self._terminal_code is None:
            self._state = _SinkState.CLOSED

    async def _close_owned(self) -> None:
        if self._start_task is not None:
            await self._start_task
        writer = self._writer_task
        if writer is not None and not writer.done() and self._terminal_code is None:
            async with self._admission_lock:
                if not writer.done() and self._terminal_code is None:
                    await self._queue.put(_STOP)
        if writer is not None:
            await writer
        else:
            await self._shutdown_executor()

    async def _shutdown_executor(self) -> None:
        if self._executor_shutdown:
            return
        self._executor_shutdown = True
        # Every submitted operation has already completed when this is called,
        # so shutdown only joins the now-idle owned thread.  No second executor
        # and no event-loop filesystem operation is introduced.
        self._executor.shutdown(wait=True)

    def _raise_if_not_writable(self) -> None:
        if self._state is _SinkState.RUNNING:
            return
        if self._state is _SinkState.FAILED:
            assert self._terminal_code is not None
            raise AiAuditError(
                AiAuditErrorCode.SINK_FAILED,
                terminal_code=self._terminal_code,
            )
        if self._state in {_SinkState.NEW, _SinkState.STARTING}:
            raise AiAuditError(AiAuditErrorCode.NOT_STARTED)
        raise AiAuditError(AiAuditErrorCode.CLOSED)

    def _set_terminal(self, code: AiAuditErrorCode) -> None:
        if self._terminal_code is None:
            self._terminal_code = code
        self._state = _SinkState.FAILED

    def _terminal_error(self) -> AiAuditError:
        assert self._terminal_code is not None
        return AiAuditError(self._terminal_code)
