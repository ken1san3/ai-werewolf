from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
from tempfile import gettempdir
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import ai_client.runtime as runtime_module
from ai_client import (
    AdmissionCredentials,
    Phase5ClientRuntime,
    Phase5ClientRuntimeConfig,
    Phase5RuntimeExitReason,
    Phase5RuntimeLifecycle,
)
from ai_client.brain import FeatureControllerExit, FeatureControllerExitReason
from ai_client.discussion import (
    AuthorizedChatChannelContext,
    AuthorizedDiscussionContext,
    BoundDiscussionContext,
    DiscussionContextError,
    PendingDiscussionContext,
    canonical_sha256,
)
from ai_client.network import (
    ClientExit,
    ClientExitReason,
    ClientLifecycle,
    ClientSnapshot,
    NetworkClientConfig,
)
from ai_client.world import (
    Freshness,
    SelfView,
    WorldSnapshot,
    WorldStateExit,
    WorldStateExitReason,
)


ENTRY_SECRET = "entry-private-sentinel"
ADMISSION_SECRET = "a" * 64
MANIFEST_SENTINEL = "full-manifest-other-seat-channel-content-sentinel"


def _context(
    manifest_sha256: str,
    *,
    game_id: str = "opaque-game",
    player_id: str = "opaque-player",
    role_id: str = "opaque-role",
    modifier_ids: tuple[str, ...] = (),
) -> AuthorizedDiscussionContext:
    return AuthorizedDiscussionContext(
        schema_version="aiwolf.discussion-context.v1",
        game_id=game_id,
        player_id=player_id,
        role_id=role_id,
        modifier_ids=modifier_ids,
        content_manifest_sha256=manifest_sha256,
        team="opaque-team",
        count_as="opaque-count",
        attack_result="opaque-attack",
        inspect_result="opaque-inspect",
        medium_result="opaque-medium",
        win_conditions=(),
        abilities=(),
        passives=(),
        chat_channels=(AuthorizedChatChannelContext("opaque-channel", False),),
        knows_teammates=False,
        authorized_known_player_ids=(),
        known_players_complete=False,
    )


class _TrackingPending(PendingDiscussionContext):
    __slots__ = ("bind_calls", "bind_entered", "on_bind")

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.bind_calls = 0
        self.bind_entered = asyncio.Event()
        self.on_bind: object | None = None

    def bind(self, snapshot: WorldSnapshot) -> BoundDiscussionContext:
        self.bind_calls += 1
        self.bind_entered.set()
        if callable(self.on_bind):
            self.on_bind()
        return super().bind(snapshot)


def _pending(
    *,
    game_id: str = "opaque-game",
    player_id: str = "opaque-player",
    role_id: str = "opaque-role",
    modifier_ids: tuple[str, ...] = (),
) -> _TrackingPending:
    manifest_bytes = (
        '{"private":"' + MANIFEST_SENTINEL + '"}'
    ).encode("utf-8")
    manifest_hash = sha256(manifest_bytes).hexdigest()
    context = _context(
        manifest_hash,
        game_id=game_id,
        player_id=player_id,
        role_id=role_id,
        modifier_ids=modifier_ids,
    )
    return _TrackingPending(
        manifest_material={"private": MANIFEST_SENTINEL},
        manifest_bytes=manifest_bytes,
        manifest_sha256=manifest_hash,
        context_sha256=canonical_sha256(context),
        context=context,
    )


def _snapshot(
    version: int,
    *,
    freshness: Freshness = Freshness.CURRENT,
    player_id: str = "opaque-player",
    role_id: str = "opaque-role",
    modifier_ids: tuple[str, ...] = (),
    with_self: bool = True,
) -> WorldSnapshot:
    return WorldSnapshot(
        version=version,
        freshness=freshness,
        self_view=(
            SelfView(player_id, role_id, modifier_ids) if with_self else None
        ),
    )


def _config() -> Phase5ClientRuntimeConfig:
    return Phase5ClientRuntimeConfig(
        network=NetworkClientConfig(
            "ws://127.0.0.1:1",
            "opaque-game",
            ENTRY_SECRET,
            shutdown_timeout_seconds=0.2,
        ),
        player_id="opaque-player",
        admission_host="127.0.0.1",
        admission_port=1,
        admission_credentials=AdmissionCredentials("opaque-client", ADMISSION_SECRET),
        audit_path=Path(gettempdir()) / "phase6-runtime-test.ai.jsonl",
        master_seed=61,
    )


class _FakeNetwork:
    _END = object()

    def __init__(self, harness: "_RuntimeHarness") -> None:
        self.harness = harness
        self.result: asyncio.Future[ClientExit] = (
            asyncio.get_running_loop().create_future()
        )
        self.run_entered = asyncio.Event()
        self.action_calls = 0
        self.stopped = False
        self._snapshot = ClientSnapshot(lifecycle=ClientLifecycle.CONNECTED)
        self._events: asyncio.Queue[object] = asyncio.Queue()
        self.event_iterator_calls = 0
        self.event_pull_requests: asyncio.Queue[int] = asyncio.Queue()
        self.event_pull_count = 0
        self.event_yield_count = 0
        self.iterator_close_count = 0
        self._source_ended = False

    def snapshot(self) -> ClientSnapshot:
        return self._snapshot

    async def events(self) -> AsyncIterator[object]:
        self.event_iterator_calls += 1
        if self.event_iterator_calls != 1:
            raise RuntimeError("fake Network event source was consumed twice")
        try:
            while True:
                self.event_pull_count += 1
                self.event_pull_requests.put_nowait(self.event_pull_count)
                item = await self._events.get()
                if item is self._END:
                    return
                if isinstance(item, BaseException):
                    raise item
                self.event_yield_count += 1
                self.harness.order.append(f"network.event.{self.event_yield_count}")
                yield item
        finally:
            self.iterator_close_count += 1

    def publish(self, snapshot: WorldSnapshot) -> None:
        self._events.put_nowait(snapshot)

    def fail_source(self, error: BaseException) -> None:
        self._events.put_nowait(error)

    def end_source(self) -> None:
        if self._source_ended:
            return
        self._source_ended = True
        self._events.put_nowait(self._END)

    def finish(self, result: ClientExit, *, close_events: bool = True) -> None:
        if not self.result.done():
            self.result.set_result(result)
        if close_events:
            self.end_source()

    async def run(self) -> ClientExit:
        self.run_entered.set()
        return await self.result

    async def stop(self) -> None:
        if self.stopped:
            return
        self.stopped = True
        self.harness.order.append("network.stop")
        if not self.result.done():
            self.result.set_result(
                ClientExit(ClientExitReason.STOPPED, True, ClientLifecycle.ENDED)
            )
        self.end_source()


class _FakeWorld:
    def __init__(self, harness: "_RuntimeHarness", source: object) -> None:
        self.harness = harness
        self.source = source
        self._snapshot = WorldSnapshot()
        self.committed_snapshots: list[WorldSnapshot] = []
        self._updated = asyncio.Event()
        self.wait_calls: asyncio.Queue[int] = asyncio.Queue()
        self.run_entered = asyncio.Event()
        self.stopped = False
        self.on_wait: object | None = None
        self._run_task: asyncio.Task[WorldStateExit] | None = None
        self._terminal_reason: WorldStateExitReason | None = None
        self.reducer_failure: BaseException | None = None

    def snapshot(self) -> WorldSnapshot:
        return self._snapshot

    async def wait_for_update(self, after_version: int) -> WorldSnapshot:
        self.wait_calls.put_nowait(after_version)
        on_wait = self.on_wait
        self.on_wait = None
        if callable(on_wait):
            on_wait()
        while self._snapshot.version <= after_version and self._snapshot.freshness not in {
            Freshness.ENDED,
            Freshness.FAILED,
        }:
            updated = self._updated
            await updated.wait()
        return self._snapshot

    def publish(self, snapshot: WorldSnapshot) -> None:
        network = self.harness.network
        assert network is not None
        network.publish(snapshot)

    def _commit(self, snapshot: WorldSnapshot) -> None:
        self._snapshot = snapshot
        self.committed_snapshots.append(snapshot)
        updated = self._updated
        self._updated = asyncio.Event()
        updated.set()

    async def run(self) -> WorldStateExit:
        self.run_entered.set()
        self._run_task = asyncio.current_task()
        try:
            async for snapshot in self.source.events():  # type: ignore[attr-defined]
                if self.reducer_failure is not None:
                    error = self.reducer_failure
                    self.reducer_failure = None
                    raise error
                if not isinstance(snapshot, WorldSnapshot):
                    raise RuntimeError("fake World received an invalid event")
                self._commit(snapshot)
        except asyncio.CancelledError:
            if self._snapshot.freshness not in {Freshness.ENDED, Freshness.FAILED}:
                self._commit(
                    _snapshot(
                        self._snapshot.version + 1,
                        freshness=Freshness.ENDED,
                        with_self=False,
                    )
                )
            return WorldStateExit(WorldStateExitReason.STOPPED, Freshness.ENDED, 0)
        except Exception as error:
            self._commit(
                _snapshot(
                    self._snapshot.version + 1,
                    freshness=Freshness.FAILED,
                    with_self=False,
                )
            )
            return WorldStateExit(
                WorldStateExitReason.FAILED,
                Freshness.FAILED,
                0,
                str(error),
            )
        finally:
            self._run_task = None
        reason = self._terminal_reason or WorldStateExitReason.SOURCE_CLOSED
        freshness = self._snapshot.freshness
        if reason is WorldStateExitReason.CLIENT_ENDED:
            freshness = Freshness.ENDED
        elif reason in {WorldStateExitReason.FAILED, WorldStateExitReason.SOURCE_CLOSED}:
            freshness = Freshness.FAILED
        if self._snapshot.freshness is not freshness:
            self._commit(
                _snapshot(
                    self._snapshot.version + 1,
                    freshness=freshness,
                    with_self=False,
                )
            )
        return WorldStateExit(reason, freshness, 0)

    async def stop(self) -> None:
        if self.stopped:
            return
        self.stopped = True
        self.harness.order.append("world.stop")
        task = self._run_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await task
        elif self._snapshot.freshness not in {Freshness.ENDED, Freshness.FAILED}:
            self._commit(
                _snapshot(
                    self._snapshot.version + 1,
                    freshness=Freshness.ENDED,
                    with_self=False,
                )
            )

    def finish(self, reason: WorldStateExitReason) -> None:
        freshness = (
            Freshness.FAILED
            if reason is WorldStateExitReason.FAILED
            else Freshness.ENDED
        )
        self._terminal_reason = reason
        self.publish(
            _snapshot(
                self._snapshot.version + 1,
                freshness=freshness,
                with_self=False,
            )
        )
        network = self.harness.network
        assert network is not None
        network.end_source()

    def fail_reducer_on_next_event(self, error: BaseException) -> None:
        self.reducer_failure = error


class _FakeAdmission:
    def __init__(self, harness: "_RuntimeHarness") -> None:
        self.harness = harness
        self.closed = False

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.harness.order.append("session.close")


class _FakeBackend:
    def __init__(self, harness: "_RuntimeHarness", admission: _FakeAdmission) -> None:
        self.harness = harness
        self.admission = admission
        self.identity = SimpleNamespace(kind="fake")
        self.generate_calls = 0
        self.closed = False

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.harness.order.append("backend.close")
        if self.harness.close_failure == "backend.close":
            raise RuntimeError("controlled backend close")


class _FakeAudit:
    def __init__(self, harness: "_RuntimeHarness") -> None:
        self.harness = harness
        self.started = False
        self.closed = False
        self.write_calls = 0

    async def start(self) -> None:
        self.harness.order.append("audit.start")
        self.harness.maybe_fail("audit.start")
        self.started = True

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.harness.order.append("audit.close")
        self.harness.audit_close_entered.set()
        if self.harness.block_audit_close:
            await self.harness.release_audit_close.wait()
        if self.harness.close_failure == "audit.close":
            raise RuntimeError("controlled audit close")


class _FakeStore:
    def __init__(self, harness: "_RuntimeHarness", bound: BoundDiscussionContext) -> None:
        self.harness = harness
        self.bound = bound
        self.closed = False

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.harness.order.append("store.close")
        if self.harness.close_failure == "store.close":
            raise RuntimeError("controlled store close")


class _FakeBrain:
    def __init__(self, harness: "_RuntimeHarness", kwargs: dict[str, object]) -> None:
        self.harness = harness
        self.kwargs = kwargs
        self.decide_calls = 0

    async def decide(self, request: object) -> object:
        self.decide_calls += 1
        raise AssertionError("test runtime must not invoke Brain")


class _FakeBrainController:
    def __init__(self, harness: "_RuntimeHarness", kwargs: dict[str, object]) -> None:
        self.harness = harness
        self.kwargs = kwargs
        self.stopped = False

    async def stop(self) -> None:
        if self.stopped:
            return
        self.stopped = True
        self.harness.order.append("brain_controller.stop")


class _FakeArbiter:
    def __init__(
        self,
        harness: "_RuntimeHarness",
        controller: _FakeBrainController,
        admission: _FakeAdmission,
    ) -> None:
        self.harness = harness
        self.controller = controller
        self.admission = admission
        self.stopped = False

    async def stop(self) -> None:
        if self.stopped:
            return
        self.stopped = True
        self.harness.order.append("arbiter.stop")
        await self.controller.stop()


class _FakeFeature:
    def __init__(
        self,
        harness: "_RuntimeHarness",
        name: str,
        kwargs: dict[str, object],
    ) -> None:
        self.harness = harness
        self.name = name
        self.kwargs = kwargs
        self.discussion_context = kwargs.get("discussion_context")
        self.invoker = kwargs["invoker"]
        self.result: asyncio.Future[FeatureControllerExit] = (
            asyncio.get_running_loop().create_future()
        )
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.harness.order.append(f"{self.name}.start")
        self.harness.maybe_fail(f"{self.name}.start")
        self.started = True

    async def wait(self) -> FeatureControllerExit:
        return await self.result

    async def stop(self) -> None:
        if self.stopped:
            return
        self.stopped = True
        self.harness.order.append(f"{self.name}.stop")
        if not self.result.done():
            self.result.set_result(
                FeatureControllerExit(
                    self.name,  # type: ignore[arg-type]
                    FeatureControllerExitReason.STOP_REQUESTED,
                )
            )

    def finish(self, reason: FeatureControllerExitReason) -> None:
        if not self.result.done():
            self.result.set_result(FeatureControllerExit(self.name, reason))  # type: ignore[arg-type]


class _RuntimeHarness:
    def __init__(self, *, fail_at: str | None = None) -> None:
        self.fail_at = fail_at
        self.close_failure: str | None = None
        self.order: list[str] = []
        self.counts: dict[str, int] = {}
        self.chmod_calls = 0
        self.stack = ExitStack()
        self.admission = _FakeAdmission(self)
        self.backend: _FakeBackend | None = None
        self.audit: _FakeAudit | None = None
        self.network: _FakeNetwork | None = None
        self.world: _FakeWorld | None = None
        self.store: _FakeStore | None = None
        self.brain: _FakeBrain | None = None
        self.brain_controller: _FakeBrainController | None = None
        self.arbiter: _FakeArbiter | None = None
        self.reaction: _FakeFeature | None = None
        self.vote: _FakeFeature | None = None
        self.block_audit_close = False
        self.audit_close_entered = asyncio.Event()
        self.release_audit_close = asyncio.Event()

    def __enter__(self) -> "_RuntimeHarness":
        harness = self

        class AdmissionFactory:
            @staticmethod
            async def connect(*args: object, **kwargs: object) -> _FakeAdmission:
                harness.mark("admission.connect")
                harness.maybe_fail("admission.connect")
                return harness.admission

        self.stack.enter_context(
            patch.object(runtime_module, "BrokerAdmissionSession", AdmissionFactory)
        )
        self.stack.enter_context(
            patch.object(runtime_module, "BrokeredStructuredLLMBackend", self.make_backend)
        )
        self.stack.enter_context(
            patch.object(runtime_module, "JsonlAiAuditSink", self.make_audit)
        )
        self.stack.enter_context(patch.object(runtime_module, "NetworkClient", self.make_network))
        self.stack.enter_context(patch.object(runtime_module, "WorldState", self.make_world))
        self.stack.enter_context(
            patch.object(runtime_module, "DiscussionStateStore", self.make_store)
        )
        self.stack.enter_context(patch.object(runtime_module, "LLMBrain", self.make_brain))
        self.stack.enter_context(
            patch.object(runtime_module, "BrainController", self.make_brain_controller)
        )
        self.stack.enter_context(
            patch.object(runtime_module, "BrainInvocationArbiter", self.make_arbiter)
        )
        self.stack.enter_context(
            patch.object(
                runtime_module,
                "DeterministicSpeakingFrequencyPolicy",
                self.make_frequency,
            )
        )
        self.stack.enter_context(
            patch.object(runtime_module, "ReactionChatController", self.make_reaction)
        )
        self.stack.enter_context(
            patch.object(runtime_module, "VoteAbilityController", self.make_vote)
        )
        self.stack.enter_context(patch.object(runtime_module.os, "chmod", self.chmod))
        return self

    def __exit__(self, *args: object) -> None:
        self.stack.close()

    def mark(self, name: str) -> None:
        self.counts[name] = self.counts.get(name, 0) + 1
        self.order.append(name)

    def maybe_fail(self, name: str) -> None:
        if self.fail_at == name:
            raise RuntimeError(f"controlled {name}")

    def chmod(self, *args: object, **kwargs: object) -> None:
        self.chmod_calls += 1
        self.mark("chmod")

    def make_backend(self, admission: _FakeAdmission) -> _FakeBackend:
        self.mark("backend.construct")
        self.maybe_fail("backend.construct")
        self.backend = _FakeBackend(self, admission)
        return self.backend

    def make_audit(self, *args: object, **kwargs: object) -> _FakeAudit:
        self.mark("audit.construct")
        self.maybe_fail("audit.construct")
        self.audit = _FakeAudit(self)
        return self.audit

    def make_network(self, *args: object, **kwargs: object) -> _FakeNetwork:
        self.mark("network.construct")
        self.maybe_fail("network.construct")
        self.network = _FakeNetwork(self)
        return self.network

    def make_world(self, source: object, *args: object, **kwargs: object) -> _FakeWorld:
        self.mark("world.construct")
        self.maybe_fail("world.construct")
        self.world = _FakeWorld(self, source)
        return self.world

    def make_store(self, bound: BoundDiscussionContext) -> _FakeStore:
        self.mark("store.construct")
        self.maybe_fail("store.construct")
        self.store = _FakeStore(self, bound)
        return self.store

    def make_brain(self, **kwargs: object) -> _FakeBrain:
        self.mark("brain.construct")
        self.maybe_fail("brain.construct")
        self.brain = _FakeBrain(self, kwargs)
        return self.brain

    def make_brain_controller(self, **kwargs: object) -> _FakeBrainController:
        self.mark("brain_controller.construct")
        self.maybe_fail("brain_controller.construct")
        self.brain_controller = _FakeBrainController(self, kwargs)
        return self.brain_controller

    def make_arbiter(self, **kwargs: object) -> _FakeArbiter:
        self.mark("arbiter.construct")
        self.maybe_fail("arbiter.construct")
        self.arbiter = _FakeArbiter(
            self,
            kwargs["controller"],  # type: ignore[arg-type]
            kwargs["admission"],  # type: ignore[arg-type]
        )
        return self.arbiter

    def make_frequency(self, **kwargs: object) -> object:
        self.mark("frequency.construct")
        self.maybe_fail("frequency.construct")
        return SimpleNamespace(**kwargs)

    def make_reaction(self, **kwargs: object) -> _FakeFeature:
        self.mark("reaction.construct")
        self.maybe_fail("reaction.construct")
        self.reaction = _FakeFeature(self, "reaction_chat", kwargs)
        return self.reaction

    def make_vote(self, **kwargs: object) -> _FakeFeature:
        self.mark("vote.construct")
        self.maybe_fail("vote.construct")
        self.vote = _FakeFeature(self, "vote_ability", kwargs)
        return self.vote


class PhaseSixRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def _connect(
        self,
        harness: _RuntimeHarness,
        pending: PendingDiscussionContext,
    ) -> Phase5ClientRuntime:
        return await Phase5ClientRuntime.connect_phase6(
            _config(),
            object(),  # type: ignore[arg-type]
            pending_discussion_context=pending,
            request_id_factory=lambda: "opaque-request",
        )

    def _assert_no_runtime_tasks(self) -> None:
        names = {
            task.get_name()
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and task.get_name().startswith("aiwolf-")
            and not task.done()
        }
        self.assertEqual(names, set())

    def _assert_pre_monitor_exit(self, runtime, reason, success, detail, lifecycle):
        self.assertIsNotNone(runtime.exit)
        self.assertEqual(runtime.exit.reason, reason)
        self.assertIs(runtime.exit.success, success)
        self.assertEqual(runtime.exit.detail, detail)
        self.assertEqual(runtime.lifecycle, lifecycle)
        for component in ("network", "world", "reaction", "vote_ability"):
            self.assertIsNone(getattr(runtime.exit, component))

    def _assert_pre_monitor_drained(self, harness, runtime, pending, *, iterated=True):
        self.assertFalse(pending.manifest_is_retained)
        self.assertEqual(harness.network.action_calls, 0)
        self.assertEqual(harness.backend.generate_calls, 0)
        for owner in ("store", "brain", "brain_controller", "arbiter", "reaction", "vote"):
            self.assertEqual(harness.counts.get(f"{owner}.construct", 0), 0)
        self.assertEqual(harness.network.event_iterator_calls, int(iterated))
        self.assertEqual(harness.network.iterator_close_count, int(iterated))
        for name in ("audit.close", "backend.close", "world.stop", "network.stop"):
            self.assertEqual(harness.order.count(name), 1)
        source = runtime._phase6_event_source
        for future in (source.bound_outcome, source._context_outcome):
            self.assertTrue(future.done())
            self.assertFalse(getattr(future, "_log_traceback", False))
        self.assertEqual(
            [task.get_name() for task in asyncio.all_tasks()
             if task is not asyncio.current_task() and not task.done()], []
        )

    async def test_public_close_won_start_returns_cached_terminal_result(self) -> None:
        outcomes = (
            (None, Phase5RuntimeExitReason.STOPPED, True, None, Phase5RuntimeLifecycle.ENDED),
            ("audit.close", Phase5RuntimeExitReason.CLEANUP_FAILED, False,
             "audit:RuntimeError", Phase5RuntimeLifecycle.FAILED),
        )
        for entry in ("run", "start_wait"):
            for timing in ("before_bind", "at_bind"):
                for fault, reason, success, detail, lifecycle in outcomes:
                    with self.subTest(entry=entry, timing=timing, fault=fault), _RuntimeHarness() as h:
                        h.block_audit_close = True
                        h.close_failure = fault
                        p = _pending()
                        r = await self._connect(h, p)
                        close = None
                        start_returned = asyncio.Event()

                        async def start_wait():
                            await r.start()
                            start_returned.set()
                            return await r.wait()

                        def close_at_bind():
                            nonlocal close
                            close = asyncio.create_task(r.aclose())

                        if timing == "at_bind":
                            p.on_bind = close_at_bind
                            h.network.publish(_snapshot(1))
                        owner = asyncio.create_task(r.run() if entry == "run" else start_wait())
                        if timing == "before_bind":
                            self.assertEqual(await asyncio.wait_for(h.network.event_pull_requests.get(), 2), 1)
                            close = asyncio.create_task(r.aclose())
                        await asyncio.wait_for(h.audit_close_entered.wait(), 2)
                        second = asyncio.create_task(r.aclose())
                        h.release_audit_close.set()
                        result, first_close, second_close = await asyncio.wait_for(
                            asyncio.gather(owner, close, second, return_exceptions=True), 2
                        )
                        await asyncio.wait_for(r.aclose(), 2)
                        self._assert_pre_monitor_drained(h, r, p)
                        self.assertEqual(p.bind_calls, int(timing == "at_bind"))
                        self.assertIsNone(first_close)
                        self.assertIsNone(second_close)
                        if entry == "start_wait":
                            self.assertTrue(start_returned.is_set())
                        self._assert_pre_monitor_exit(r, reason, success, detail, lifecycle)
                        self.assertIs(result, r.exit)
                        self.assertIs(await asyncio.wait_for(r.wait(), 2), result)

    async def test_ready_invalid_bind_early_cancellation_retains_failure(self) -> None:
        outcomes = (
            (None, Phase5RuntimeExitReason.CONTROLLER_FAILED, "DISCUSSION_CONTEXT_INVALID"),
            ("audit.close", Phase5RuntimeExitReason.CLEANUP_FAILED, "audit:RuntimeError"),
        )
        for entry in ("run", "start"):
            for explicit_close in (False, True):
                for fault, reason, detail in outcomes:
                    with self.subTest(entry=entry, close=explicit_close, fault=fault), _RuntimeHarness() as h:
                        h.block_audit_close = True
                        h.close_failure = fault
                        p = _pending()
                        r = await self._connect(h, p)
                        close = None
                        owner = None

                        def cancel_before_consumption():
                            nonlocal close
                            if explicit_close:
                                close = asyncio.create_task(r.aclose())
                            owner.cancel()

                        p.on_bind = cancel_before_consumption
                        h.network.publish(_snapshot(1, role_id="wrong-role"))
                        owner = asyncio.create_task(r.run() if entry == "run" else r.start())
                        await asyncio.wait_for(h.audit_close_entered.wait(), 2)
                        bound = r._phase6_event_source.bound_outcome
                        self.assertTrue(bound.done())
                        self.assertFalse(bound.cancelled())
                        self.assertIsInstance(bound.exception(), DiscussionContextError)
                        h.release_audit_close.set()
                        results = await asyncio.wait_for(asyncio.gather(
                            *([owner, close] if close is not None else [owner]),
                            return_exceptions=True,
                        ), 2)
                        await asyncio.wait_for(r.aclose(), 2)
                        self._assert_pre_monitor_drained(h, r, p)
                        self.assertEqual(p.bind_calls, 1)
                        self.assertIsInstance(results[0], asyncio.CancelledError)
                        if explicit_close:
                            self.assertIsNone(results[1])
                        with self.assertRaisesRegex(RuntimeError, "requires start"):
                            await r.wait()
                        self._assert_pre_monitor_exit(r, reason, False, detail, Phase5RuntimeLifecycle.FAILED)

    async def test_late_valid_current_after_shutdown_does_not_bind_disposed_pending(self) -> None:
        outcomes = (
            (None, Phase5RuntimeExitReason.STOPPED, True, None, Phase5RuntimeLifecycle.ENDED),
            ("audit.close", Phase5RuntimeExitReason.CLEANUP_FAILED, False,
             "audit:RuntimeError", Phase5RuntimeLifecycle.FAILED),
        )
        for entry in ("run", "start_wait"):
            for fault, reason, success, detail, lifecycle in outcomes:
                with self.subTest(entry=entry, fault=fault), _RuntimeHarness() as h:
                    h.block_audit_close = True
                    h.close_failure = fault
                    p = _pending()
                    r = await self._connect(h, p)

                    async def start_wait():
                        await r.start()
                        return await r.wait()

                    owner = asyncio.create_task(r.run() if entry == "run" else start_wait())
                    self.assertEqual(await asyncio.wait_for(h.network.event_pull_requests.get(), 2), 1)
                    close = asyncio.create_task(r.aclose())
                    await asyncio.wait_for(h.audit_close_entered.wait(), 2)
                    self.assertFalse(p.manifest_is_retained)
                    h.network.publish(_snapshot(1))
                    # The real source resumes after World's synchronous commit before
                    # this public update waiter can resume; no scheduler delay is used.
                    await asyncio.wait_for(h.world.wait_for_update(0), 2)
                    h.release_audit_close.set()
                    result, close_result = await asyncio.wait_for(
                        asyncio.gather(owner, close, return_exceptions=True), 2
                    )
                    await asyncio.wait_for(r.aclose(), 2)
                    self._assert_pre_monitor_drained(h, r, p)
                    self.assertIsNone(close_result)
                    self.assertEqual(p.bind_calls, 0)
                    self._assert_pre_monitor_exit(r, reason, success, detail, lifecycle)
                    self.assertIs(result, r.exit)

    async def test_public_requires_start_distinguishes_new_closed_and_pending_start(self) -> None:
        for fault, reason, success, detail, lifecycle in (
            (None, Phase5RuntimeExitReason.STOPPED, True, None, Phase5RuntimeLifecycle.ENDED),
            ("audit.close", Phase5RuntimeExitReason.CLEANUP_FAILED, False,
             "audit:RuntimeError", Phase5RuntimeLifecycle.FAILED),
        ):
            with self.subTest(fault=fault), _RuntimeHarness() as h:
                p = _pending()
                r = await self._connect(h, p)
                with self.assertRaisesRegex(RuntimeError, r"Phase5ClientRuntime.wait\(\) requires start\(\)"):
                    await r.wait()
                self.assertIsNone(r.exit)
                h.close_failure = fault
                self.assertIsNone(await asyncio.wait_for(r.aclose(), 2))
                for entry in (r.start, r.run):
                    with self.assertRaisesRegex(RuntimeError, "may only be called once"):
                        await entry()
                with self.assertRaisesRegex(RuntimeError, "requires start"):
                    await r.wait()
                self._assert_pre_monitor_exit(r, reason, success, detail, lifecycle)
                self._assert_pre_monitor_drained(h, r, p, iterated=False)
        with _RuntimeHarness() as h:
            p = _pending()
            r = await self._connect(h, p)
            start = asyncio.create_task(r.start())
            self.assertEqual(await asyncio.wait_for(h.network.event_pull_requests.get(), 2), 1)
            with self.assertRaisesRegex(RuntimeError, "may only be called once"):
                await r.start()
            with self.assertRaisesRegex(RuntimeError, "requires start"):
                await r.wait()
            await asyncio.wait_for(r.aclose(), 2)
            self.assertIsNone(await asyncio.wait_for(start, 2))
            self.assertIs(await r.wait(), r.exit)
            self._assert_pre_monitor_drained(h, r, p)

    async def test_pre_monitor_cancellation_without_failure_keeps_existing_classification(self) -> None:
        for timing in ("pending", "bound"):
            for fault, reason, detail in (
                (None, Phase5RuntimeExitReason.CONTROLLER_FAILED, "CancelledError"),
                ("audit.close", Phase5RuntimeExitReason.CLEANUP_FAILED, "audit:RuntimeError"),
            ):
                with self.subTest(timing=timing, fault=fault), _RuntimeHarness() as h:
                    h.close_failure = fault
                    p = _pending()
                    r = await self._connect(h, p)
                    owner = None
                    if timing == "bound":
                        p.on_bind = lambda: owner.cancel()
                        h.network.publish(_snapshot(1))
                    owner = asyncio.create_task(r.start())
                    if timing == "pending":
                        self.assertEqual(await asyncio.wait_for(h.network.event_pull_requests.get(), 2), 1)
                        owner.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(owner, 2)
                    await asyncio.wait_for(r.aclose(), 2)
                    with self.assertRaisesRegex(RuntimeError, "requires start"):
                        await r.wait()
                    self._assert_pre_monitor_exit(r, reason, False, detail, Phase5RuntimeLifecycle.FAILED)
                    self._assert_pre_monitor_drained(h, r, p)

    async def test_invalid_bind_public_exception_and_exit_with_or_without_close(self) -> None:
        for entry in ("start", "run"):
            for explicit_close in (False, True):
                for fault, reason, detail in (
                    (None, Phase5RuntimeExitReason.CONTROLLER_FAILED, "DISCUSSION_CONTEXT_INVALID"),
                    ("audit.close", Phase5RuntimeExitReason.CLEANUP_FAILED, "audit:RuntimeError"),
                ):
                    with self.subTest(entry=entry, close=explicit_close, fault=fault), _RuntimeHarness() as h:
                        h.close_failure = fault
                        p = _pending()
                        r = await self._connect(h, p)
                        close = None

                        def close_at_bind():
                            nonlocal close
                            close = asyncio.create_task(r.aclose())

                        if explicit_close:
                            p.on_bind = close_at_bind
                        h.network.publish(_snapshot(1, role_id="wrong-role"))
                        with self.assertRaises(DiscussionContextError) as raised:
                            await asyncio.wait_for(r.run() if entry == "run" else r.start(), 2)
                        self.assertEqual(raised.exception.code, "DISCUSSION_CONTEXT_INVALID")
                        if close is not None:
                            self.assertIsNone(await asyncio.wait_for(close, 2))
                        await asyncio.wait_for(r.aclose(), 2)
                        with self.assertRaisesRegex(RuntimeError, "requires start"):
                            await r.wait()
                        self._assert_pre_monitor_exit(r, reason, False, detail, Phase5RuntimeLifecycle.FAILED)
                        self._assert_pre_monitor_drained(h, r, p)

    async def test_partial_graph_failures_preserve_public_exception_and_exit(self) -> None:
        for failure in (
            "store.construct", "brain.construct", "brain_controller.construct", "arbiter.construct",
            "reaction.construct", "vote.construct", "reaction_chat.start", "vote_ability.start",
        ):
            for fault, reason, detail in (
                (None, Phase5RuntimeExitReason.CONTROLLER_FAILED, "RuntimeError"),
                ("audit.close", Phase5RuntimeExitReason.CLEANUP_FAILED, "audit:RuntimeError"),
            ):
                with self.subTest(failure=failure, fault=fault), _RuntimeHarness(fail_at=failure) as h:
                    h.close_failure = fault
                    p = _pending()
                    r = await self._connect(h, p)
                    h.network.publish(_snapshot(1))
                    with self.assertRaisesRegex(RuntimeError, f"controlled {failure}"):
                        await asyncio.wait_for(r.run(), 2)
                    await asyncio.wait_for(r.aclose(), 2)
                    with self.assertRaisesRegex(RuntimeError, "requires start"):
                        await r.wait()
                    self._assert_pre_monitor_exit(r, reason, False, detail, Phase5RuntimeLifecycle.FAILED)
                    self.assertFalse(p.manifest_is_retained)
                    self.assertEqual(h.network.action_calls, 0)
                    self.assertEqual(h.backend.generate_calls, 0)
                    self.assertEqual(h.network.iterator_close_count, 1)
                    for name in ("audit.close", "backend.close", "world.stop", "network.stop"):
                        self.assertEqual(h.order.count(name), 1)
                    self._assert_no_runtime_tasks()

    async def test_consumed_invalid_bind_cancellation_without_close_retains_failure(self) -> None:
        for entry in ("run", "start"):
            for fault, reason, detail in (
                (None, Phase5RuntimeExitReason.CONTROLLER_FAILED, "DISCUSSION_CONTEXT_INVALID"),
                ("audit.close", Phase5RuntimeExitReason.CLEANUP_FAILED, "audit:RuntimeError"),
            ):
                with self.subTest(entry=entry, fault=fault), _RuntimeHarness() as h:
                    h.block_audit_close = True
                    h.close_failure = fault
                    p = _pending()
                    r = await self._connect(h, p)
                    h.network.publish(_snapshot(1, role_id="wrong-role"))
                    owner = asyncio.create_task(r.run() if entry == "run" else r.start())
                    await asyncio.wait_for(h.audit_close_entered.wait(), 2)
                    # With no close caller, entering cleanup proves start consumed
                    # the bind exception before the test cancels its cleanup join.
                    owner.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(owner, 2)
                    h.release_audit_close.set()
                    await asyncio.wait_for(r.aclose(), 2)
                    with self.assertRaisesRegex(RuntimeError, "requires start"):
                        await r.wait()
                    self._assert_pre_monitor_exit(r, reason, False, detail, Phase5RuntimeLifecycle.FAILED)
                    self._assert_pre_monitor_drained(h, r, p)

    async def test_all_start_close_callers_cancel_cleanup_owner_still_publishes(self) -> None:
        for role, fault, reason, success, detail, lifecycle in (
            ("opaque-role", None, Phase5RuntimeExitReason.STOPPED, True, None, Phase5RuntimeLifecycle.ENDED),
            ("wrong-role", None, Phase5RuntimeExitReason.CONTROLLER_FAILED, False,
             "DISCUSSION_CONTEXT_INVALID", Phase5RuntimeLifecycle.FAILED),
            ("opaque-role", "audit.close", Phase5RuntimeExitReason.CLEANUP_FAILED, False,
             "audit:RuntimeError", Phase5RuntimeLifecycle.FAILED),
            ("wrong-role", "audit.close", Phase5RuntimeExitReason.CLEANUP_FAILED, False,
             "audit:RuntimeError", Phase5RuntimeLifecycle.FAILED),
        ):
            with self.subTest(role=role, fault=fault), _RuntimeHarness() as h:
                h.block_audit_close = True
                h.close_failure = fault
                p = _pending()
                r = await self._connect(h, p)
                owner = None
                close = None

                def close_and_cancel():
                    nonlocal close
                    close = asyncio.create_task(r.aclose())
                    owner.cancel()

                p.on_bind = close_and_cancel
                h.network.publish(_snapshot(1, role_id=role))
                owner = asyncio.create_task(r.run())
                await asyncio.wait_for(h.audit_close_entered.wait(), 2)
                # Second cancellation also abandons startup's shielded cleanup join.
                owner.cancel()
                close.cancel()
                results = await asyncio.wait_for(
                    asyncio.gather(owner, close, return_exceptions=True), 2
                )
                self.assertTrue(all(isinstance(item, asyncio.CancelledError) for item in results))
                self.assertFalse(r._cleanup_task.done())
                h.release_audit_close.set()
                await asyncio.wait_for(asyncio.shield(r._cleanup_task), 2)
                # Assert before repeated close: publication belongs to cleanup itself.
                self._assert_pre_monitor_exit(r, reason, success, detail, lifecycle)
                self.assertIsNone(await asyncio.wait_for(r.aclose(), 2))
                self._assert_pre_monitor_drained(h, r, p)
                with self.assertRaisesRegex(RuntimeError, "requires start"):
                    await r.wait()

    async def test_pre_bind_source_and_world_failures_retain_public_classification(self) -> None:
        for cause in ("clean_source", "world_terminal", "source_error", "reducer_error", "world_task_error"):
            with self.subTest(cause=cause), _RuntimeHarness() as h:
                p = _pending()
                r = await self._connect(h, p)
                if cause == "world_task_error":
                    async def broken_world():
                        raise RuntimeError("controlled raw World task failure")
                    h.world.run = broken_world
                else:
                    if cause == "clean_source":
                        h.network.end_source()
                    elif cause == "world_terminal":
                        h.world.finish(WorldStateExitReason.FAILED)
                    elif cause == "source_error":
                        h.network.fail_source(RuntimeError("controlled source failure"))
                    else:
                        h.world.fail_reducer_on_next_event(RuntimeError("controlled reducer failure"))
                        h.network.publish(_snapshot(1, freshness=Freshness.STALE, with_self=False))
                with self.assertRaisesRegex(RuntimeError, "World terminated before discussion context bind"):
                    await asyncio.wait_for(r.run(), 2)
                await asyncio.wait_for(r.aclose(), 2)
                if cause == "world_task_error":
                    self._assert_pre_monitor_exit(
                        r, Phase5RuntimeExitReason.CLEANUP_FAILED, False,
                        "task:RuntimeError", Phase5RuntimeLifecycle.FAILED,
                    )
                else:
                    self._assert_pre_monitor_exit(
                        r, Phase5RuntimeExitReason.CONTROLLER_FAILED, False,
                        "RuntimeError", Phase5RuntimeLifecycle.FAILED,
                    )
                with self.assertRaisesRegex(RuntimeError, "requires start"):
                    await r.wait()
                self._assert_pre_monitor_drained(h, r, p, iterated=cause != "world_task_error")

    async def test_pure_wait_cancellation_keeps_ordinary_monitor_owned(self) -> None:
        with _RuntimeHarness() as h:
            p = _pending()
            r = await self._connect(h, p)
            h.network.publish(_snapshot(1))
            await asyncio.wait_for(r.start(), 2)
            waiter_entered = asyncio.Event()

            async def wait_public():
                waiter_entered.set()
                return await r.wait()

            waiter = asyncio.create_task(wait_public())
            await asyncio.wait_for(waiter_entered.wait(), 2)
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(waiter, 2)
            self.assertFalse(r._monitor_task.done())
            self.assertIsNone(r.exit)
            self.assertEqual(r.lifecycle, Phase5RuntimeLifecycle.RUNNING)
            self.assertNotIn("audit.close", h.order)
            await asyncio.wait_for(r.aclose(), 2)
            result = await asyncio.wait_for(r.wait(), 2)
            self.assertIs(result, r.exit)
            self.assertEqual(result.reason, Phase5RuntimeExitReason.STOPPED)
            self.assertTrue(result.success)
            for name in ("vote_ability.stop", "reaction_chat.stop", "arbiter.stop", "store.close",
                         "audit.close", "backend.close", "world.stop", "network.stop"):
                self.assertEqual(h.order.count(name), 1)
            self.assertEqual(h.network.iterator_close_count, 1)
            self._assert_no_runtime_tasks()

    async def test_monitored_ready_context_failure_precedes_pending_wrapper_and_feature(self) -> None:
        for role, fault, reason, detail in (
            ("changed-role", None, Phase5RuntimeExitReason.CONTROLLER_FAILED, "DISCUSSION_CONTEXT_INVALID"),
            ("changed-role", "audit.close", Phase5RuntimeExitReason.CLEANUP_FAILED, "audit:RuntimeError"),
            ("opaque-role", None, Phase5RuntimeExitReason.CONTROLLER_FAILED, "FAILED"),
        ):
            with self.subTest(role=role, fault=fault), _RuntimeHarness() as h:
                h.close_failure = fault
                p = _pending()
                r = await self._connect(h, p)
                h.network.publish(_snapshot(1))
                await asyncio.wait_for(r.start(), 2)
                for expected in (1, 2):
                    self.assertEqual(await asyncio.wait_for(h.network.event_pull_requests.get(), 2), expected)
                loop = asyncio.get_running_loop()
                prior_handler = loop.get_exception_handler()
                warnings = []
                observations = []
                raw_context = r._phase6_event_source._context_outcome
                monitor_code = r._monitor.__func__.__code__

                def trace(frame, event, arg):
                    # Observe the first resumed arbitration without yielding,
                    # changing task order, or retrieving the Future exception.
                    if (event == "line" and frame.f_code is monitor_code
                            and "done" in frame.f_locals and not observations
                            and raw_context.done() and not raw_context.cancelled()):
                        observations.append((
                            getattr(getattr(raw_context, "_exception", None), "code", None),
                            r._context_task.done(),
                            r._context_task in frame.f_locals["done"],
                            r._cleanup_task is not None,
                            r._stop_requested,
                        ))
                    return trace

                prior_trace = sys.gettrace()
                loop.set_exception_handler(lambda unused_loop, context: warnings.append(context))
                sys.settrace(trace)
                try:
                    h.reaction.finish(FeatureControllerExitReason.FAILED)
                    loop.call_soon(h.network.publish, _snapshot(2, role_id=role))
                    loop.call_soon(h.network.publish, _snapshot(3))
                    result = await asyncio.wait_for(r.wait(), 2)
                finally:
                    sys.settrace(prior_trace)
                    loop.set_exception_handler(prior_handler)
                await asyncio.wait_for(r.aclose(), 2)
                self.assertFalse(p.manifest_is_retained)
                self.assertEqual(p.bind_calls, 1)
                self.assertEqual(h.network.event_iterator_calls, 1)
                self.assertEqual(h.network.iterator_close_count, 1)
                self.assertEqual(h.backend.generate_calls, 0)
                self.assertEqual(h.network.action_calls, 0)
                for name in ("vote_ability.stop", "reaction_chat.stop", "arbiter.stop",
                             "brain_controller.stop", "store.close", "audit.close",
                             "backend.close", "world.stop", "network.stop"):
                    self.assertEqual(h.order.count(name), 1)
                for outcome in (r._phase6_event_source.bound_outcome, raw_context):
                    self.assertTrue(outcome.done())
                    self.assertFalse(getattr(outcome, "_log_traceback", False))
                self.assertEqual(warnings, [])
                self.assertEqual([task.get_name() for task in asyncio.all_tasks()
                                  if task is not asyncio.current_task() and not task.done()], [])
                if role == "changed-role":
                    self.assertEqual(observations, [("DISCUSSION_CONTEXT_INVALID", False, False, False, False)])
                    self.assertEqual(h.network.event_yield_count, 2)
                    self.assertEqual(h.network.event_pull_count, 2)
                    self.assertEqual(
                        [snapshot.version for snapshot in h.world.committed_snapshots
                         if snapshot.freshness is Freshness.CURRENT], [1, 2]
                    )
                self.assertIs(result, r.exit)
                self.assertEqual(result.reason, reason)
                self.assertFalse(result.success)
                self.assertEqual(result.detail, detail)
                self.assertEqual(r.lifecycle, Phase5RuntimeLifecycle.FAILED)

    async def test_monitored_shutdown_ignores_late_context_input_after_retirement(self) -> None:
        with _RuntimeHarness() as h:
            h.block_audit_close = True
            p = _pending()
            r = await self._connect(h, p)
            h.network.publish(_snapshot(1))
            await asyncio.wait_for(r.start(), 2)
            for expected in (1, 2):
                self.assertEqual(await asyncio.wait_for(h.network.event_pull_requests.get(), 2), expected)
            close = asyncio.create_task(r.aclose())
            await asyncio.wait_for(h.audit_close_entered.wait(), 2)
            h.network.publish(_snapshot(2, role_id="changed-role"))
            await asyncio.wait_for(h.world.wait_for_update(1), 2)
            h.release_audit_close.set()
            await asyncio.wait_for(close, 2)
            result = await asyncio.wait_for(r.wait(), 2)
            self.assertEqual(result.reason, Phase5RuntimeExitReason.STOPPED)
            self.assertTrue(result.success)
            self.assertIsNone(result.detail)
            self.assertEqual(r.lifecycle, Phase5RuntimeLifecycle.ENDED)
            self.assertEqual(p.bind_calls, 1)
            self.assertFalse(p.manifest_is_retained)
            self.assertEqual(h.network.action_calls, 0)
            self.assertEqual(h.backend.generate_calls, 0)
            self.assertEqual(h.network.iterator_close_count, 1)
            for name in ("vote_ability.stop", "reaction_chat.stop", "arbiter.stop", "store.close",
                         "audit.close", "backend.close", "world.stop", "network.stop"):
                self.assertEqual(h.order.count(name), 1)
            for outcome in (r._phase6_event_source.bound_outcome, r._phase6_event_source._context_outcome):
                self.assertTrue(outcome.done())
                self.assertFalse(getattr(outcome, "_log_traceback", False))
            self.assertEqual([task.get_name() for task in asyncio.all_tasks()
                              if task is not asyncio.current_task() and not task.done()], [])

    async def test_pre_resource_pending_validation_is_mandatory_owned_and_redacted(self) -> None:
        vectors: list[tuple[str, object]] = [
            ("missing", None),
            ("wrong-type", object()),
        ]
        disposed = _pending()
        disposed.discard_manifest()
        vectors.append(("disposed", disposed))
        bad_hash = _pending()
        bad_hash.context_sha256 = "0" * 64
        vectors.append(("hash", bad_hash))
        vectors.append(("game", _pending(game_id="other-game")))
        vectors.append(("player", _pending(player_id="other-player")))

        for label, value in vectors:
            with self.subTest(label=label), _RuntimeHarness() as harness:
                with self.assertRaises(DiscussionContextError) as raised:
                    await Phase5ClientRuntime.connect_phase6(
                        _config(),
                        object(),  # type: ignore[arg-type]
                        pending_discussion_context=value,  # type: ignore[arg-type]
                    )
                self.assertEqual(raised.exception.code, "DISCUSSION_CONTEXT_INVALID")
                self.assertEqual(harness.chmod_calls, 0)
                self.assertFalse(harness.counts)
                if isinstance(value, PendingDiscussionContext):
                    self.assertFalse(value.manifest_is_retained)
                evidence = "|".join(
                    (repr(_config()), repr(value), str(raised.exception))
                )
                for sentinel in (ENTRY_SECRET, ADMISSION_SECRET, MANIFEST_SENTINEL):
                    self.assertNotIn(sentinel, evidence)

    async def test_empty_stale_first_current_binds_once_before_one_shared_graph(self) -> None:
        pending = _pending()
        with _RuntimeHarness() as harness:
            runtime = await self._connect(harness, pending)
            assert harness.world is not None
            assert harness.network is not None
            start = asyncio.create_task(runtime.start())
            self.assertEqual(await harness.network.event_pull_requests.get(), 1)
            self.assertIsNone(runtime.brain)
            self.assertIsNone(runtime.reaction)
            self.assertEqual(harness.backend.generate_calls, 0)  # type: ignore[union-attr]
            self.assertEqual(harness.network.action_calls, 0)

            harness.world.publish(_snapshot(1, freshness=Freshness.STALE, with_self=False))
            self.assertEqual(await harness.network.event_pull_requests.get(), 2)
            self.assertIsNone(runtime.discussion_store)
            harness.world.publish(_snapshot(2))
            await start
            self.assertEqual(await harness.network.event_pull_requests.get(), 3)

            bound = runtime.discussion_context
            self.assertIsInstance(bound, BoundDiscussionContext)
            self.assertEqual(pending.bind_calls, 1)
            self.assertFalse(pending.manifest_is_retained)
            self.assertIs(harness.store.bound, bound)  # type: ignore[union-attr]
            self.assertIs(harness.reaction.discussion_context, bound)  # type: ignore[union-attr]
            self.assertIs(harness.vote.discussion_context, bound)  # type: ignore[union-attr]
            self.assertIs(
                harness.brain.kwargs["audit"],  # type: ignore[union-attr]
                harness.brain_controller.kwargs["discussion_audit"],  # type: ignore[union-attr]
            )
            self.assertIs(
                harness.brain_controller.kwargs["discussion_state"],  # type: ignore[union-attr]
                harness.store,
            )
            self.assertIs(harness.reaction.invoker, harness.arbiter)  # type: ignore[union-attr]
            self.assertIs(harness.vote.invoker, harness.arbiter)  # type: ignore[union-attr]
            for name in (
                "store.construct",
                "brain.construct",
                "brain_controller.construct",
                "arbiter.construct",
                "reaction.construct",
                "vote.construct",
            ):
                self.assertEqual(harness.counts[name], 1)
            self.assertLess(
                harness.order.index("vote.construct"),
                harness.order.index("reaction_chat.start"),
            )
            self.assertEqual(harness.backend.generate_calls, 0)  # type: ignore[union-attr]
            self.assertEqual(harness.network.action_calls, 0)
            self.assertNotIn(MANIFEST_SENTINEL, repr(runtime))
            await runtime.aclose()
            cleanup = [
                item
                for item in harness.order
                if item.endswith(".stop") or item.endswith(".close")
            ]
            self.assertEqual(
                cleanup,
                [
                    "vote_ability.stop",
                    "reaction_chat.stop",
                    "arbiter.stop",
                    "brain_controller.stop",
                    "store.close",
                    "audit.close",
                    "backend.close",
                    "world.stop",
                    "network.stop",
                ],
            )
            self._assert_no_runtime_tasks()

    async def test_first_current_mismatch_is_terminal_and_never_searches_later(self) -> None:
        vectors = (
            ("missing-self", dict(with_self=False)),
            ("player", dict(player_id="other-player")),
            ("role", dict(role_id="other-role")),
            ("modifier", dict(modifier_ids=("other-modifier",))),
            ("duplicate", dict(modifier_ids=("opaque-modifier", "opaque-modifier"))),
        )
        for label, changes in vectors:
            with self.subTest(label=label), _RuntimeHarness() as harness:
                pending = _pending()
                runtime = await self._connect(harness, pending)
                assert harness.world is not None
                assert harness.network is not None
                start = asyncio.create_task(runtime.start())
                self.assertEqual(await harness.network.event_pull_requests.get(), 1)
                harness.world.publish(_snapshot(1, **changes))
                harness.world.publish(_snapshot(2))
                with self.assertRaises(DiscussionContextError) as raised:
                    await start
                self.assertEqual(raised.exception.code, "DISCUSSION_CONTEXT_INVALID")
                self.assertEqual(pending.bind_calls, 1)
                self.assertIsNone(runtime.discussion_context)
                self.assertEqual(harness.counts.get("brain.construct", 0), 0)
                self.assertEqual(harness.backend.generate_calls, 0)  # type: ignore[union-attr]
                self.assertEqual(harness.network.action_calls, 0)  # type: ignore[union-attr]
                self.assertEqual(harness.network.event_yield_count, 1)
                self.assertEqual(harness.world.snapshot().version, 2)
                self.assertEqual(runtime.lifecycle, Phase5RuntimeLifecycle.FAILED)
                self._assert_no_runtime_tasks()

    async def test_first_current_parks_until_complete_graph_and_context_owner_exist(self) -> None:
        pending = _pending()
        with _RuntimeHarness() as harness:
            runtime = await self._connect(harness, pending)
            assert harness.world is not None
            assert harness.network is not None
            harness.world.publish(_snapshot(1))
            harness.world.publish(_snapshot(4))
            await runtime.start()
            await harness.world.wait_for_update(1)

            self.assertEqual(harness.network.event_iterator_calls, 1)
            self.assertEqual(harness.network.event_yield_count, 2)
            self.assertEqual(pending.bind_calls, 1)
            self.assertLess(
                harness.order.index("reaction_chat.start"),
                harness.order.index("network.event.2"),
            )
            self.assertLess(
                harness.order.index("vote_ability.start"),
                harness.order.index("network.event.2"),
            )
            self.assertIs(runtime.discussion_context, harness.store.bound)  # type: ignore[union-attr]
            self.assertIs(harness.reaction.discussion_context, runtime.discussion_context)  # type: ignore[union-attr]
            self.assertIs(harness.vote.discussion_context, runtime.discussion_context)  # type: ignore[union-attr]
            await runtime.aclose()
            self.assertEqual(harness.network.iterator_close_count, 1)
            self._assert_no_runtime_tasks()

    async def test_phase6_source_delegates_snapshot_and_rejects_second_attach_or_iteration(self) -> None:
        pending = _pending()
        with _RuntimeHarness() as harness:
            runtime = await self._connect(harness, pending)
            assert harness.world is not None
            assert harness.network is not None
            source = harness.world.source
            self.assertIs(source.snapshot(), harness.network.snapshot())  # type: ignore[attr-defined]
            with self.assertRaisesRegex(RuntimeError, "already has a World"):
                source.attach_world(harness.world)  # type: ignore[attr-defined]

            start = asyncio.create_task(runtime.start())
            self.assertEqual(await harness.network.event_pull_requests.get(), 1)
            duplicate = source.events()  # type: ignore[attr-defined]
            with self.assertRaisesRegex(RuntimeError, "only be iterated once"):
                await anext(duplicate)
            harness.world.publish(_snapshot(1))
            await start
            await runtime.aclose()
            self.assertEqual(harness.network.event_iterator_calls, 1)
            self.assertEqual(harness.network.iterator_close_count, 1)
            self._assert_no_runtime_tasks()

    async def test_matching_and_non_current_sequence_is_lossless_and_allows_version_jumps(self) -> None:
        pending = _pending()
        with _RuntimeHarness() as harness:
            runtime = await self._connect(harness, pending)
            assert harness.world is not None
            assert harness.network is not None
            snapshots = (
                _snapshot(1, freshness=Freshness.STALE, with_self=False),
                _snapshot(3),
                _snapshot(8, freshness=Freshness.STALE, with_self=False),
                _snapshot(13),
            )
            for snapshot in snapshots:
                harness.world.publish(snapshot)
            await runtime.start()
            await harness.world.wait_for_update(8)

            self.assertEqual(harness.network.event_iterator_calls, 1)
            self.assertEqual(harness.network.event_yield_count, 4)
            self.assertEqual(
                [snapshot.version for snapshot in harness.world.committed_snapshots[:4]],
                [1, 3, 8, 13],
            )
            self.assertEqual(pending.bind_calls, 1)
            await runtime.aclose()
            self.assertEqual(harness.network.iterator_close_count, 1)
            self._assert_no_runtime_tasks()

    async def test_later_mismatch_has_exact_failure_precedence_without_feature_opportunity(self) -> None:
        pending = _pending()
        with _RuntimeHarness() as harness:
            runtime = await self._connect(harness, pending)
            assert harness.world is not None
            assert harness.network is not None
            harness.world.publish(_snapshot(1))
            await runtime.start()
            self.assertEqual(await harness.network.event_pull_requests.get(), 1)
            self.assertEqual(await harness.network.event_pull_requests.get(), 2)

            harness.world.publish(_snapshot(2))
            self.assertEqual(await harness.network.event_pull_requests.get(), 3)
            waiter = asyncio.create_task(runtime.wait())
            self.assertFalse(waiter.done())
            harness.world.publish(_snapshot(3, role_id="changed-role"))
            harness.world.publish(_snapshot(4))
            assert harness.reaction is not None
            harness.reaction.finish(FeatureControllerExitReason.FAILED)
            result = await waiter
            self.assertEqual(result.reason, Phase5RuntimeExitReason.CONTROLLER_FAILED)
            self.assertEqual(result.detail, "DISCUSSION_CONTEXT_INVALID")
            self.assertEqual(pending.bind_calls, 1)
            self.assertEqual(harness.backend.generate_calls, 0)  # type: ignore[union-attr]
            self.assertEqual(harness.network.action_calls, 0)  # type: ignore[union-attr]
            self.assertEqual(harness.network.event_yield_count, 3)
            self.assertEqual(runtime.lifecycle, Phase5RuntimeLifecycle.FAILED)
            self._assert_no_runtime_tasks()

    async def test_later_modifier_or_bound_hash_mismatch_is_terminal_without_rebind(self) -> None:
        for mismatch in ("modifier", "hash"):
            with self.subTest(mismatch=mismatch), _RuntimeHarness() as harness:
                pending = _pending()
                runtime = await self._connect(harness, pending)
                assert harness.world is not None
                assert harness.network is not None
                harness.world.publish(_snapshot(1))
                await runtime.start()
                self.assertEqual(await harness.network.event_pull_requests.get(), 1)
                self.assertEqual(await harness.network.event_pull_requests.get(), 2)
                if mismatch == "hash":
                    object.__setattr__(runtime.discussion_context, "context_sha256", "0" * 64)
                    harness.world.publish(_snapshot(2))
                else:
                    harness.world.publish(
                        _snapshot(2, modifier_ids=("changed-modifier",))
                    )
                result = await runtime.wait()
                self.assertEqual(
                    (result.reason, result.detail),
                    (
                        Phase5RuntimeExitReason.CONTROLLER_FAILED,
                        "DISCUSSION_CONTEXT_INVALID",
                    ),
                )
                self.assertEqual(pending.bind_calls, 1)
                self._assert_no_runtime_tasks()

    async def test_current_wins_same_wake_with_network_terminal_then_monitor_closes(self) -> None:
        pending = _pending()
        with _RuntimeHarness() as harness:
            runtime = await self._connect(harness, pending)
            assert harness.world is not None
            assert harness.network is not None
            start = asyncio.create_task(runtime.start())
            self.assertEqual(await harness.network.event_pull_requests.get(), 1)
            harness.world.publish(_snapshot(1))
            harness.network.finish(
                ClientExit(
                    ClientExitReason.INTERNAL_ERROR,
                    False,
                    ClientLifecycle.FAILED,
                )
            )
            await start
            self.assertEqual(pending.bind_calls, 1)
            self.assertIsNotNone(runtime.discussion_store)
            result = await runtime.wait()
            self.assertEqual(result.reason, Phase5RuntimeExitReason.NETWORK_FAILED)
            self._assert_no_runtime_tasks()

    async def test_first_current_is_bound_without_world_update_waiter(self) -> None:
        pending = _pending()
        with _RuntimeHarness() as harness:
            runtime = await self._connect(harness, pending)
            assert harness.world is not None
            assert harness.network is not None
            harness.world.publish(_snapshot(1))
            await runtime.start()
            self.assertEqual(pending.bind_calls, 1)
            self.assertIsNotNone(runtime.discussion_context)
            self.assertIsNotNone(runtime.discussion_store)
            await runtime.aclose()
            self._assert_no_runtime_tasks()

    async def test_network_or_world_terminal_before_bind_fails_closed(self) -> None:
        for owner in ("network", "world"):
            with self.subTest(owner=owner), _RuntimeHarness() as harness:
                pending = _pending()
                runtime = await self._connect(harness, pending)
                assert harness.world is not None
                assert harness.network is not None
                start = asyncio.create_task(runtime.start())
                self.assertEqual(await harness.network.event_pull_requests.get(), 1)
                if owner == "network":
                    harness.network.finish(
                        ClientExit(
                            ClientExitReason.INTERNAL_ERROR,
                            False,
                            ClientLifecycle.FAILED,
                        )
                    )
                else:
                    harness.world.finish(WorldStateExitReason.FAILED)
                with self.assertRaisesRegex(RuntimeError, "terminated before"):
                    await start
                self.assertEqual(pending.bind_calls, 0)
                self.assertFalse(pending.manifest_is_retained)
                self.assertEqual(harness.counts.get("brain.construct", 0), 0)
                self._assert_no_runtime_tasks()

    async def test_clean_source_end_and_source_or_reducer_failure_are_bounded(self) -> None:
        for failure in (
            "clean-before-bind",
            "source-before-bind",
            "reducer-before-bind",
            "source-after-bind",
            "reducer-after-bind",
        ):
            with self.subTest(failure=failure), _RuntimeHarness() as harness:
                pending = _pending()
                runtime = await self._connect(harness, pending)
                assert harness.world is not None
                assert harness.network is not None
                if failure.endswith("before-bind"):
                    start = asyncio.create_task(runtime.start())
                    self.assertEqual(await harness.network.event_pull_requests.get(), 1)
                    if failure == "clean-before-bind":
                        harness.network.end_source()
                    elif failure == "source-before-bind":
                        harness.network.fail_source(
                            RuntimeError("controlled source failure")
                        )
                    else:
                        harness.world.fail_reducer_on_next_event(
                            RuntimeError("controlled reducer failure")
                        )
                        harness.world.publish(
                            _snapshot(1, freshness=Freshness.STALE, with_self=False)
                        )
                    with self.assertRaisesRegex(RuntimeError, "terminated before"):
                        await start
                    self.assertEqual(pending.bind_calls, 0)
                    self.assertEqual(harness.counts.get("brain.construct", 0), 0)
                else:
                    harness.world.publish(_snapshot(1))
                    await runtime.start()
                    self.assertEqual(await harness.network.event_pull_requests.get(), 1)
                    self.assertEqual(await harness.network.event_pull_requests.get(), 2)
                    if failure == "source-after-bind":
                        harness.network.fail_source(RuntimeError("controlled source failure"))
                    else:
                        harness.world.fail_reducer_on_next_event(
                            RuntimeError("controlled reducer failure")
                        )
                        harness.world.publish(_snapshot(2, freshness=Freshness.STALE))
                    result = await runtime.wait()
                    self.assertEqual(result.reason, Phase5RuntimeExitReason.WORLD_FAILED)
                    self.assertEqual(pending.bind_calls, 1)
                self.assertEqual(harness.network.event_iterator_calls, 1)
                self.assertEqual(harness.network.iterator_close_count, 1)
                self._assert_no_runtime_tasks()

    async def test_partial_graph_and_feature_start_failures_cleanup_once_in_order(self) -> None:
        failures = (
            "store.construct",
            "brain.construct",
            "brain_controller.construct",
            "arbiter.construct",
            "reaction.construct",
            "vote.construct",
            "reaction_chat.start",
            "vote_ability.start",
        )
        for failure in failures:
            with self.subTest(failure=failure), _RuntimeHarness(fail_at=failure) as harness:
                pending = _pending()
                runtime = await self._connect(harness, pending)
                assert harness.world is not None
                harness.world.publish(_snapshot(1))
                with self.assertRaisesRegex(RuntimeError, "controlled"):
                    await runtime.start()
                self.assertFalse(pending.manifest_is_retained)
                for name in (
                    "audit.close",
                    "backend.close",
                    "world.stop",
                    "network.stop",
                ):
                    self.assertEqual(harness.order.count(name), 1)
                if "brain_controller.stop" in harness.order and "store.close" in harness.order:
                    self.assertLess(
                        harness.order.index("brain_controller.stop"),
                        harness.order.index("store.close"),
                    )
                self.assertEqual(runtime.lifecycle, Phase5RuntimeLifecycle.FAILED)
                self._assert_no_runtime_tasks()

    async def test_connect_construction_failure_disposes_pending_and_all_partial_owners(self) -> None:
        for failure in ("audit.start", "network.construct", "world.construct"):
            with self.subTest(failure=failure), _RuntimeHarness(fail_at=failure) as harness:
                pending = _pending()
                with self.assertRaisesRegex(RuntimeError, "controlled"):
                    await self._connect(harness, pending)
                self.assertFalse(pending.manifest_is_retained)
                if harness.audit is not None:
                    self.assertEqual(harness.order.count("audit.close"), 1)
                if harness.backend is not None:
                    self.assertEqual(harness.order.count("backend.close"), 1)
                if harness.world is not None:
                    self.assertEqual(harness.order.count("world.stop"), 1)
                if harness.network is not None:
                    self.assertEqual(harness.order.count("network.stop"), 1)
                self._assert_no_runtime_tasks()

    async def test_cancel_during_first_bind_wait_drains_every_owner(self) -> None:
        pending = _pending()
        with _RuntimeHarness() as harness:
            runtime = await self._connect(harness, pending)
            assert harness.world is not None
            assert harness.network is not None
            start = asyncio.create_task(runtime.start())
            self.assertEqual(await harness.network.event_pull_requests.get(), 1)
            start.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await start
            self.assertFalse(pending.manifest_is_retained)
            self.assertEqual(harness.counts.get("brain.construct", 0), 0)
            for name in ("audit.close", "backend.close", "world.stop", "network.stop"):
                self.assertEqual(harness.order.count(name), 1)
            self._assert_no_runtime_tasks()

    async def test_cancel_while_source_is_parked_drains_without_graph_or_future_leak(self) -> None:
        pending = _pending()
        with _RuntimeHarness() as harness:
            runtime = await self._connect(harness, pending)
            assert harness.world is not None
            assert harness.network is not None
            start: asyncio.Task[None] | None = None

            def cancel_start() -> None:
                assert start is not None
                start.cancel()

            pending.on_bind = cancel_start
            start = asyncio.create_task(runtime.start())
            harness.world.publish(_snapshot(1))
            with self.assertRaises(asyncio.CancelledError):
                await start
            self.assertEqual(pending.bind_calls, 1)
            self.assertEqual(harness.counts.get("brain.construct", 0), 0)
            self.assertEqual(harness.network.event_yield_count, 1)
            self.assertEqual(harness.network.iterator_close_count, 1)
            for name in ("audit.close", "backend.close", "world.stop", "network.stop"):
                self.assertEqual(harness.order.count(name), 1)
            self._assert_no_runtime_tasks()

    async def test_new_concurrent_repeated_close_and_cancelled_first_close_are_idempotent(self) -> None:
        pending = _pending()
        with _RuntimeHarness() as harness:
            harness.block_audit_close = True
            runtime = await self._connect(harness, pending)
            first = asyncio.create_task(runtime.aclose())
            await harness.audit_close_entered.wait()
            second = asyncio.create_task(runtime.aclose())
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            harness.release_audit_close.set()
            await second
            await runtime.aclose()
            self.assertFalse(pending.manifest_is_retained)
            for name in ("audit.close", "backend.close", "world.stop", "network.stop"):
                self.assertEqual(harness.order.count(name), 1)
            self.assertEqual(runtime.exit.reason, Phase5RuntimeExitReason.STOPPED)  # type: ignore[union-attr]
            self._assert_no_runtime_tasks()

    async def test_close_at_first_bind_prevents_late_graph_and_joins_cleanup(self) -> None:
        for cleanup_failure in (None, "audit.close"):
            for cancel_owner in (None, "start", "close", "both"):
                with self.subTest(cleanup_failure=cleanup_failure, cancel_owner=cancel_owner):
                    with _RuntimeHarness() as harness:
                        pending = _pending()
                        harness.block_audit_close = True
                        harness.close_failure = cleanup_failure
                        runtime = await self._connect(harness, pending)
                        assert harness.world is not None
                        assert harness.network is not None
                        assert harness.backend is not None
                        close: asyncio.Task[None] | None = None

                        def close_at_bind() -> None:
                            nonlocal close
                            close = asyncio.create_task(runtime.aclose())

                        pending.on_bind = close_at_bind
                        harness.world.publish(_snapshot(1))
                        start = asyncio.create_task(runtime.start())
                        await asyncio.wait_for(harness.audit_close_entered.wait(), 2)
                        assert close is not None
                        second = asyncio.create_task(runtime.aclose())
                        try:
                            self.assertEqual(runtime.lifecycle, Phase5RuntimeLifecycle.STOPPING)
                            self.assertIsNone(runtime.discussion_store)
                            self.assertIsNone(runtime.brain)
                            self.assertIsNone(runtime.reaction)
                            self.assertIsNone(runtime.vote_ability)
                            if cancel_owner in ("start", "both"):
                                start.cancel()
                            if cancel_owner in ("close", "both"):
                                close.cancel()
                            harness.release_audit_close.set()
                            results = await asyncio.wait_for(
                                asyncio.gather(start, close, second, return_exceptions=True), 2
                            )
                            for index, owner in enumerate(("start", "close", "second")):
                                if owner == cancel_owner or (cancel_owner == "both" and index < 2):
                                    self.assertIsInstance(results[index], asyncio.CancelledError)
                                else:
                                    self.assertIsNone(results[index])
                            await runtime.aclose()
                            self.assertFalse(pending.manifest_is_retained)
                            self.assertEqual(pending.bind_calls, 1)
                            self.assertEqual(harness.network.event_iterator_calls, 1)
                            self.assertEqual(harness.network.event_yield_count, 1)
                            self.assertEqual(harness.network.iterator_close_count, 1)
                            self.assertEqual(harness.network.action_calls, 0)
                            self.assertEqual(harness.backend.generate_calls, 0)
                            self.assertEqual(harness.counts.get("store.construct", 0), 0)
                            self.assertEqual(harness.counts.get("brain.construct", 0), 0)
                            for name in ("audit.close", "backend.close", "world.stop", "network.stop"):
                                self.assertEqual(harness.order.count(name), 1)
                            assert runtime.exit is not None
                            self.assertEqual(
                                runtime.exit.reason,
                                Phase5RuntimeExitReason.CLEANUP_FAILED
                                if cleanup_failure else Phase5RuntimeExitReason.STOPPED,
                            )
                            self.assertEqual(runtime.exit.success, cleanup_failure is None)
                            self.assertEqual(
                                runtime.exit.detail,
                                "audit:RuntimeError" if cleanup_failure else None,
                            )
                            self.assertEqual(
                                runtime.lifecycle,
                                Phase5RuntimeLifecycle.FAILED
                                if cleanup_failure else Phase5RuntimeLifecycle.ENDED,
                            )
                            self._assert_no_runtime_tasks()
                        finally:
                            # Keep a failing regression finite on the old late-owner behavior.
                            harness.release_audit_close.set()
                            if harness.vote is not None:
                                await harness.vote.stop()
                            if harness.reaction is not None:
                                await harness.reaction.stop()
                            if harness.arbiter is not None:
                                await harness.arbiter.stop()
                            if harness.store is not None:
                                harness.store.close()
                            await asyncio.wait_for(
                                asyncio.gather(start, close, second, return_exceptions=True), 2
                            )

    async def test_close_before_first_bind_settles_start_without_synthetic_failure(self) -> None:
        for cleanup_failure in (None, "audit.close"):
            with self.subTest(cleanup_failure=cleanup_failure), _RuntimeHarness() as harness:
                pending = _pending()
                harness.block_audit_close = True
                harness.close_failure = cleanup_failure
                runtime = await self._connect(harness, pending)
                assert harness.network is not None
                start = asyncio.create_task(runtime.start())
                self.assertEqual(await harness.network.event_pull_requests.get(), 1)
                close = asyncio.create_task(runtime.aclose())
                await asyncio.wait_for(harness.audit_close_entered.wait(), 2)
                self.assertFalse(pending.manifest_is_retained)
                self.assertEqual(pending.bind_calls, 0)
                self.assertIsNone(runtime.discussion_store)
                self.assertFalse(start.done())
                second = asyncio.create_task(runtime.aclose())
                harness.release_audit_close.set()
                self.assertEqual(
                    await asyncio.wait_for(
                        asyncio.gather(start, close, second, return_exceptions=True), 2
                    ),
                    [None, None, None],
                )
                await runtime.aclose()
                self.assertEqual(pending.bind_calls, 0)
                self.assertEqual(harness.counts.get("brain.construct", 0), 0)
                self.assertEqual(harness.network.event_yield_count, 0)
                self.assertEqual(harness.network.iterator_close_count, 1)
                self.assertEqual(harness.network.action_calls, 0)
                for name in ("audit.close", "backend.close", "world.stop", "network.stop"):
                    self.assertEqual(harness.order.count(name), 1)
                assert runtime.exit is not None
                self.assertEqual(
                    runtime.exit.reason,
                    Phase5RuntimeExitReason.CLEANUP_FAILED
                    if cleanup_failure else Phase5RuntimeExitReason.STOPPED,
                )
                self.assertEqual(runtime.exit.success, cleanup_failure is None)
                self.assertEqual(
                    runtime.lifecycle,
                    Phase5RuntimeLifecycle.FAILED
                    if cleanup_failure else Phase5RuntimeLifecycle.ENDED,
                )
                self._assert_no_runtime_tasks()

    async def test_invalid_bind_with_close_preserves_primary_and_cleanup_failure(self) -> None:
        for cleanup_failure in (None, "audit.close"):
            for cancel_start in (False, True):
                with self.subTest(cleanup_failure=cleanup_failure, cancel_start=cancel_start), _RuntimeHarness() as harness:
                    pending = _pending()
                    harness.block_audit_close = True
                    harness.close_failure = cleanup_failure
                    runtime = await self._connect(harness, pending)
                    assert harness.world is not None
                    assert harness.network is not None
                    close: asyncio.Task[None] | None = None

                    def close_at_bind() -> None:
                        nonlocal close
                        close = asyncio.create_task(runtime.aclose())

                    pending.on_bind = close_at_bind
                    harness.world.publish(_snapshot(1, role_id="wrong-role"))
                    start = asyncio.create_task(runtime.start())
                    await asyncio.wait_for(harness.audit_close_entered.wait(), 2)
                    assert close is not None
                    second = asyncio.create_task(runtime.aclose())
                    close.cancel()
                    if cancel_start:
                        start.cancel()
                    harness.release_audit_close.set()
                    results = await asyncio.wait_for(
                        asyncio.gather(start, close, second, return_exceptions=True), 2
                    )
                    self.assertIsInstance(
                        results[0], asyncio.CancelledError if cancel_start else DiscussionContextError
                    )
                    self.assertIsInstance(results[1], asyncio.CancelledError)
                    self.assertIsNone(results[2])
                    await runtime.aclose()
                    assert runtime.exit is not None
                    self.assertEqual(
                        runtime.exit.reason,
                        Phase5RuntimeExitReason.CLEANUP_FAILED
                        if cleanup_failure else Phase5RuntimeExitReason.CONTROLLER_FAILED,
                    )
                    self.assertEqual(
                        runtime.exit.detail,
                        "audit:RuntimeError" if cleanup_failure else "DISCUSSION_CONTEXT_INVALID",
                    )
                    self.assertFalse(runtime.exit.success)
                    self.assertEqual(runtime.lifecycle, Phase5RuntimeLifecycle.FAILED)
                    self.assertFalse(pending.manifest_is_retained)
                    self.assertEqual(pending.bind_calls, 1)
                    self.assertIsNone(runtime.discussion_store)
                    self.assertEqual(harness.counts.get("brain.construct", 0), 0)
                    self.assertEqual(harness.network.iterator_close_count, 1)
                    self.assertEqual(harness.network.action_calls, 0)
                    for name in ("audit.close", "backend.close", "world.stop", "network.stop"):
                        self.assertEqual(harness.order.count(name), 1)
                    self._assert_no_runtime_tasks()

    async def test_started_concurrent_repeated_close_drains_one_iterator_once(self) -> None:
        pending = _pending()
        with _RuntimeHarness() as harness:
            runtime = await self._connect(harness, pending)
            assert harness.world is not None
            assert harness.network is not None
            harness.world.publish(_snapshot(1))
            await runtime.start()
            await asyncio.gather(runtime.aclose(), runtime.aclose())
            await runtime.aclose()
            self.assertEqual(harness.network.event_iterator_calls, 1)
            self.assertEqual(harness.network.iterator_close_count, 1)
            for name in (
                "vote_ability.stop",
                "reaction_chat.stop",
                "arbiter.stop",
                "audit.close",
                "backend.close",
                "world.stop",
                "network.stop",
            ):
                self.assertEqual(harness.order.count(name), 1)
            self._assert_no_runtime_tasks()

    async def test_normal_terminal_context_task_is_not_a_feature_result(self) -> None:
        pending = _pending()
        with _RuntimeHarness() as harness:
            runtime = await self._connect(harness, pending)
            assert harness.world is not None
            assert harness.network is not None
            harness.world.publish(_snapshot(1))
            await runtime.start()
            harness.network.finish(
                ClientExit(ClientExitReason.GAME_ENDED, True, ClientLifecycle.ENDED),
                close_events=False,
            )
            harness.world.finish(WorldStateExitReason.CLIENT_ENDED)
            assert harness.reaction is not None
            assert harness.vote is not None
            harness.reaction.finish(FeatureControllerExitReason.WORLD_ENDED)
            harness.vote.finish(FeatureControllerExitReason.WORLD_ENDED)
            result = await runtime.wait()
            self.assertEqual(result.reason, Phase5RuntimeExitReason.GAME_ENDED)
            self.assertTrue(result.success)
            self.assertEqual(result.reaction.reason, FeatureControllerExitReason.WORLD_ENDED)  # type: ignore[union-attr]
            self.assertEqual(result.vote_ability.reason, FeatureControllerExitReason.WORLD_ENDED)  # type: ignore[union-attr]
            self._assert_no_runtime_tasks()

    async def test_post_start_run_cancellation_uses_the_same_bounded_cleanup(self) -> None:
        pending = _pending()
        with _RuntimeHarness() as harness:
            runtime = await self._connect(harness, pending)
            assert harness.world is not None
            assert harness.network is not None
            harness.world.publish(_snapshot(1))
            run = asyncio.create_task(runtime.run())
            self.assertEqual(await harness.network.event_pull_requests.get(), 1)
            self.assertEqual(await harness.network.event_pull_requests.get(), 2)
            run.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await run
            for name in (
                "vote_ability.stop",
                "reaction_chat.stop",
                "arbiter.stop",
                "store.close",
                "audit.close",
                "backend.close",
                "world.stop",
                "network.stop",
            ):
                self.assertEqual(harness.order.count(name), 1)
            self._assert_no_runtime_tasks()

    async def test_cleanup_failure_keeps_cached_once_only_cleanup_and_stronger_exit(self) -> None:
        pending = _pending()
        with _RuntimeHarness() as harness:
            runtime = await self._connect(harness, pending)
            harness.close_failure = "audit.close"
            await runtime.aclose()
            await runtime.aclose()
            self.assertEqual(runtime.exit.reason, Phase5RuntimeExitReason.CLEANUP_FAILED)  # type: ignore[union-attr]
            self.assertEqual(harness.order.count("audit.close"), 1)
            self.assertEqual(harness.order.count("backend.close"), 1)
            self._assert_no_runtime_tasks()

    async def test_legacy_connect_remains_complete_context_free_composition(self) -> None:
        with _RuntimeHarness() as harness:
            runtime = await Phase5ClientRuntime.connect(
                _config(),
                object(),  # type: ignore[arg-type]
                request_id_factory=lambda: "legacy-request",
            )
            self.assertIsNotNone(runtime.brain)
            self.assertIsNotNone(runtime.reaction)
            self.assertIsNotNone(runtime.vote_ability)
            self.assertIsNone(runtime.discussion_context)
            self.assertIsNone(runtime.discussion_store)
            self.assertIs(harness.world.source, harness.network)  # type: ignore[union-attr]
            self.assertEqual(harness.counts.get("store.construct", 0), 0)
            self.assertNotIn("discussion_state", harness.brain_controller.kwargs)  # type: ignore[union-attr]
            self.assertNotIn("discussion_audit", harness.brain_controller.kwargs)  # type: ignore[union-attr]
            self.assertNotIn("discussion_context", harness.reaction.kwargs)  # type: ignore[union-attr]
            self.assertNotIn("discussion_context", harness.vote.kwargs)  # type: ignore[union-attr]
            await runtime.start()
            await runtime.aclose()
            cleanup = [
                item
                for item in harness.order
                if item.endswith(".stop") or item.endswith(".close")
            ]
            self.assertEqual(
                cleanup,
                [
                    "vote_ability.stop",
                    "reaction_chat.stop",
                    "arbiter.stop",
                    "brain_controller.stop",
                    "audit.close",
                    "backend.close",
                    "world.stop",
                    "network.stop",
                ],
            )
            self._assert_no_runtime_tasks()


if __name__ == "__main__":
    unittest.main()
