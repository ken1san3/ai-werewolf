"""Version-local timeout supervision; never replace Task or the loop factory."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
import inspect
import sys
from typing import Any, TypeVar

_T = TypeVar("_T")


async def await_with_timeout(
    delay: float | None, operation: Callable[[], Coroutine[Any, Any, _T]]
) -> _T:
    """Run a fresh operation, preserving external cancellation during cleanup.

    3.11+ keeps native timeout instrumentation and the caller's Task identity.
    3.10 owns one child: a deadline cancels that child, never the supervisor.
    No cancellation messages, Task internals or task factory replacement.
    """
    if sys.version_info >= (3, 11):
        async with asyncio.timeout(delay):
            coroutine = operation()
            if not inspect.iscoroutine(coroutine):
                raise TypeError("timeout operation must return a fresh coroutine")
            return await coroutine

    loop = asyncio.get_running_loop()
    coroutine = operation()
    if not inspect.iscoroutine(coroutine):
        raise TypeError("timeout operation must return a fresh coroutine")
    timer = None
    handle = None
    try:
        timer = loop.create_future()
        if delay is not None:
            handle = loop.call_later(max(0, delay), timer.set_result, None)
        child = asyncio.create_task(coroutine)
    except BaseException:
        if handle is not None:
            handle.cancel()
        if timer is not None:
            timer.cancel()
        coroutine.close()
        raise
    external: asyncio.CancelledError | None = None
    try:
        try:
            done, _ = await asyncio.wait((child, timer), return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError as error:
            external = error
        else:
            if child in done:  # explicit tie: completed operation wins
                return child.result()

        if not child.done():
            child.cancel()
        # wait() does not propagate child exceptions/cancellation. A CancelledError
        # here always belongs to the parent, even when child finishes concurrently.
        while not child.done():
            try:
                await asyncio.wait((child,))
            except asyncio.CancelledError as error:
                if external is None:
                    external = error
                if not child.done():
                    child.cancel()  # preserve subsequent caller cancellation pressure
        if not child.cancelled():
            child.exception()  # retrieve even when external cancellation wins
        if external is not None:
            raise external
        raise TimeoutError
    finally:
        if handle is not None:
            handle.cancel()
        timer.cancel()


if sys.version_info < (3, 11):
    # Preserve the existing branch's exception-name compatibility for wait_for
    # and consumers catching built-in TimeoutError. Native 3.11+ is untouched.
    asyncio.TimeoutError = TimeoutError
    asyncio.exceptions.TimeoutError = TimeoutError


__all__ = ["await_with_timeout"]
