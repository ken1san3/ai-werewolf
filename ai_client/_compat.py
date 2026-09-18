"""Compatibility shims required by the declared Python runtime range."""

from __future__ import annotations

import asyncio

if not hasattr(asyncio, "timeout"):  # Python 3.10
    from async_timeout import timeout as _timeout

    # Keep call sites on asyncio.timeout so instrumentation/tests observe the
    # same boundary on every supported Python version.
    asyncio.timeout = _timeout  # type: ignore[attr-defined]


__all__: list[str] = []
