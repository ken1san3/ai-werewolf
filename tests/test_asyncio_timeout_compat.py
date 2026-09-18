"""Supported runtimes must preserve timeout ownership and external cancellation."""
import asyncio
import inspect
import sys
from unittest.mock import patch

import pytest

from ai_client._compat import await_with_timeout


@pytest.mark.parametrize("mode", ["own", "external", "own_then_external", "external_then_own", "nested_inner", "nested_outer"])
@pytest.mark.parametrize("child_wait", [False, True])
def test_timeout_cancellation_attribution(mode, child_wait):
    async def exercise():
        parent = asyncio.current_task()
        saved_cancel = parent.cancel
        baseline = asyncio.all_tasks()
        async def operation():
            if mode in ("external", "own_then_external"):
                asyncio.get_running_loop().call_soon(saved_cancel)
            if child_wait:
                await asyncio.create_task(asyncio.Event().wait())
            else:
                await asyncio.Event().wait()
        async def nested():
            return await await_with_timeout(0 if mode == "nested_inner" else None, operation)
        if mode == "external_then_own":
            asyncio.get_running_loop().call_soon(saved_cancel)
        expected = TimeoutError if mode in ("own", "nested_inner", "nested_outer") else asyncio.CancelledError
        delay = 0 if mode in ("own", "own_then_external", "external_then_own", "nested_outer") else None
        with pytest.raises(expected):
            await await_with_timeout(delay, nested if mode.startswith("nested") else operation)
        assert parent.cancel == saved_cancel
        assert asyncio.all_tasks() == baseline
    asyncio.run(exercise())


@pytest.mark.parametrize("outcome", ["return", "raise", "cancel"])
def test_external_cancel_wins_when_child_is_already_terminal(outcome):
    async def exercise():
        parent = asyncio.current_task()
        async def child_body():
            if outcome == "raise": raise ValueError("child failure")
            if outcome == "cancel": raise asyncio.CancelledError
            return 42
        child = asyncio.create_task(child_body())
        child.add_done_callback(lambda _: parent.cancel())
        async def operation():
            return await child
        with pytest.raises(asyncio.CancelledError):
            await await_with_timeout(None, operation)
        assert child.done()
        if not child.cancelled(): child.exception()
    asyncio.run(exercise())


def test_repeated_external_cancel_waits_for_child_cleanup():
    async def exercise():
        started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def operation():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleaning.set()
                # Cooperative cleanup can be interrupted again on native 3.11+.
                while not release.is_set():
                    try: await release.wait()
                    except asyncio.CancelledError: continue
                raise
        parent = asyncio.create_task(await_with_timeout(None, operation))
        await started.wait()
        parent.cancel()
        await cleaning.wait()
        parent.cancel()
        await asyncio.sleep(0)
        assert not parent.done()
        release.set()
        with pytest.raises(asyncio.CancelledError): await parent
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(exercise())


@pytest.mark.parametrize("delay", [None, 1])
def test_return_exception_factory_and_task_identity(delay):
    async def exercise():
        loop = asyncio.get_running_loop()
        parent = asyncio.current_task()
        original = loop.get_task_factory()
        made = []
        def factory(loop, coroutine, **kwargs):
            task = asyncio.Task(coroutine, loop=loop, **kwargs)
            made.append(task)
            return task
        async def operation():
            assert (asyncio.current_task() is parent) == (sys.version_info >= (3, 11))
            return 42
        loop.set_task_factory(factory)
        try:
            assert await await_with_timeout(delay, operation) == 42
            assert loop.get_task_factory() is factory
            assert len(made) == (0 if sys.version_info >= (3, 11) else 1)
            assert all(task.done() for task in made)
        finally:
            loop.set_task_factory(original)
        async def failure(): raise ValueError("body failure")
        with pytest.raises(ValueError, match="body failure"):
            await await_with_timeout(delay, failure)
        future = loop.create_future()
        with pytest.raises(TypeError): await await_with_timeout(delay, lambda: future)
        assert not future.cancelled()  # rejected foreign resource is not ours
        future.cancel()
    asyncio.run(exercise())


@pytest.mark.parametrize("delay", [0, -1])
def test_immediate_timeout(delay):
    async def exercise():
        with pytest.raises(TimeoutError):
            await await_with_timeout(delay, asyncio.Event().wait)
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(exercise())


def test_native_timeout_instrumentation_and_import_boundary():
    async def exercise():
        async def operation(): return 42
        if sys.version_info >= (3, 11):
            native = asyncio.timeout
            assert native.__module__ == "asyncio.timeouts"
            with patch("asyncio.timeout", wraps=native) as observed:
                assert await await_with_timeout(7, operation) == 42
            observed.assert_called_once_with(7)
        else:
            assert not hasattr(asyncio, "timeout")
            assert await await_with_timeout(7, operation) == 42
    asyncio.run(exercise())


@pytest.mark.parametrize("outcome", ["return", "raise"])
def test_simultaneous_timer_and_child_completion_prefers_child(outcome):
    async def exercise():
        observed = []
        real_wait = asyncio.wait
        async def record_wait(futures, **kwargs):
            result = await real_wait(futures, **kwargs)
            observed.append((len(futures), len(result[0]), len(result[1])))
            return result
        async def operation():
            if outcome == "raise":
                raise ValueError("completed body")
            return 42
        with patch("asyncio.wait", side_effect=record_wait):
            if outcome == "raise":
                with pytest.raises(ValueError, match="completed body"):
                    await await_with_timeout(0, operation)
            else:
                assert await await_with_timeout(0, operation) == 42
        # On 3.10 the zero timer and immediate child are both complete before
        # the supervisor resumes. Native 3.11 retains its direct-body behavior.
        assert observed == ([(2, 2, 0)] if sys.version_info < (3, 11) else [])
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(exercise())


@pytest.mark.parametrize("failed_setup", ["create_future", "create_task"])
def test_setup_failure_closes_fresh_coroutine(failed_setup):
    async def exercise():
        loop = asyncio.get_running_loop()
        async def operation(): return 42
        coroutine = operation()
        target = loop if failed_setup == "create_future" else asyncio
        with patch.object(target, failed_setup, side_effect=ValueError("setup failure")):
            if sys.version_info < (3, 11):
                with pytest.raises(ValueError, match="setup failure"):
                    await await_with_timeout(1, lambda: coroutine)
            else:
                # Native branch neither creates a child nor changes its loop.
                assert await await_with_timeout(1, lambda: coroutine) == 42
        assert inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(exercise())


def test_owned_timeout_waits_for_cancel_suppressing_child():
    async def exercise():
        async def operation():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(0)
                return 42
        if sys.version_info < (3, 11):
            with pytest.raises(TimeoutError):
                await await_with_timeout(0, operation)
        else:
            assert await await_with_timeout(0, operation) == 42
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(exercise())
