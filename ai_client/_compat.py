"""Compatibility shims required by the declared Python runtime range."""

from __future__ import annotations

import asyncio
from types import TracebackType
from typing import Optional, Type


if not hasattr(asyncio, "timeout"):  # Python 3.10
    class _Timeout:
        """Minimal Python-3.10 equivalent of asyncio.timeout for this project."""

        def __init__(self, delay: float | None) -> None:
            self._delay = delay
            self._task: asyncio.Task[object] | None = None
            self._handle: asyncio.TimerHandle | None = None
            self._expired = False

        async def __aenter__(self) -> "_Timeout":
            task = asyncio.current_task()
            if task is None:
                raise RuntimeError("timeout requires an active asyncio task")
            self._task = task
            if self._delay is not None:
                loop = asyncio.get_running_loop()
                delay = max(0.0, float(self._delay))
                self._handle = loop.call_later(delay, self._cancel)
            return self

        def _cancel(self) -> None:
            if self._task is not None and not self._task.done():
                self._expired = True
                self._task.cancel()

        async def __aexit__(
            self,
            exc_type: Type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> bool:
            if self._handle is not None:
                self._handle.cancel()
            if self._expired and exc_type is asyncio.CancelledError:
                raise TimeoutError from exc
            return False

    def _timeout(delay: float | None) -> _Timeout:
        return _Timeout(delay)

    # Call sites remain on asyncio.timeout so existing instrumentation and
    # tests observe the same boundary on every supported Python version.
    asyncio.timeout = _timeout  # type: ignore[attr-defined]
    # Python 3.11 made asyncio.TimeoutError an alias of built-in TimeoutError.
    # Normalize 3.10 so shared product code/tests observe the declared runtime contract.
    asyncio.TimeoutError = TimeoutError  # type: ignore[attr-defined]
    asyncio.exceptions.TimeoutError = TimeoutError  # type: ignore[attr-defined]


__all__: list[str] = []
