"""Shared helpers for multi-process completion tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import time


class _CompletionClock:
    """One integer clock shared by core, sessions, and ticker in a scenario."""

    def __init__(self) -> None:
        self.released_at_ns: int | None = None

    def __call__(self) -> int:
        if self.released_at_ns is None:
            return 0
        return (time.monotonic_ns() - self.released_at_ns) // 1_000_000_000

    def release(self) -> None:
        if self.released_at_ns is not None:
            raise RuntimeError("completion clock was released twice")
        self.released_at_ns = time.monotonic_ns()


@dataclass
class _ProcessOutput:
    stdout_tail: bytearray
    stderr_tail: bytearray
    stdout_buffer: bytearray
    observations: list[dict[str, object]]
    progress_seq: int | None = None
    checkpoint_seq: int | None = None
    parse_error: str | None = None
    readers: tuple[asyncio.Task[None], ...] = ()


async def drain_process_stream(
    stream: asyncio.StreamReader,
    output: _ProcessOutput,
    stream_name: str,
) -> None:
    """Continuously drain a child stream while retaining a bounded tail."""

    tail = output.stdout_tail if stream_name == "stdout" else output.stderr_tail
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            return
        tail.extend(chunk)
        del tail[:-65536]


def load_process_output(
    output: _ProcessOutput,
    paths: tuple[Path, Path] | None,
) -> None:
    """Load bounded stdout/stderr tails when a child used file redirection."""

    if paths is None:
        return
    stdout_path, stderr_path = paths
    for path, tail in (
        (stdout_path, output.stdout_tail),
        (stderr_path, output.stderr_tail),
    ):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        tail[:] = data[-65536:]


async def finish_process(
    process: asyncio.subprocess.Process,
    *,
    output: _ProcessOutput | None,
    label: str,
    status_path: Path | None = None,
    ignored: bool = False,
    output_paths: tuple[Path, Path] | None = None,
) -> None:
    """Wait for a child, terminating only when it fails to self-exit."""

    terminated = False
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        terminated = True
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()

    if output is not None:
        load_process_output(output, output_paths)

    if output is not None and output.readers:
        try:
            await asyncio.wait_for(
                asyncio.gather(*output.readers, return_exceptions=True),
                timeout=5,
            )
        except TimeoutError:
            for reader in output.readers:
                reader.cancel()
            await asyncio.gather(*output.readers, return_exceptions=True)

    if output is not None and output.parse_error is not None:
        raise AssertionError(
            f"{label} emitted invalid observation: {output.parse_error}"
        )
    if ignored:
        return

    allowed_returncodes = {0} if not terminated else {0, -15, -9}
    if process.returncode in allowed_returncodes:
        return

    diagnostics = [
        f"stdout={bytes(output.stdout_tail).decode(errors='replace')!r}"
        if output is not None
        else "stdout=None",
        f"stderr={bytes(output.stderr_tail).decode(errors='replace')!r}"
        if output is not None
        else "stderr=None",
    ]
    if output is not None and output.parse_error is not None:
        diagnostics.append(f"observation_error={output.parse_error!r}")
    if status_path is not None:
        try:
            status = status_path.read_text(encoding="utf-8")
        except OSError as error:
            diagnostics.append(
                f"status_read_error={type(error).__name__}: {error}"
            )
        else:
            diagnostics.append(f"status={status}")
    raise AssertionError(
        f"{label} exited {process.returncode}: " + " ".join(diagnostics)
    )
