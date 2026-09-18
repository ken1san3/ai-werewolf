"""Small compatibility shims for supported Python runtimes."""

from __future__ import annotations

import asyncio

if hasattr(asyncio, "timeout"):
    timeout = asyncio.timeout
else:  # Python 3.10
    from async_timeout import timeout


__all__ = ["timeout"]
