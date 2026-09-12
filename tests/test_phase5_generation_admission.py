from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
import hashlib
import json
import struct
import time
import unittest

import pytest

from ai_client.llm.admission_broker import GenerationAdmissionBroker
from ai_client.llm.admission_client import (
    BrokerAdmissionSession,
    BrokeredStructuredLLMBackend,
)
from ai_client.llm.admission_metrics import (
    AdmissionMetric,
    AdmissionMetrics,
    serialize_admission_metric,
)
from ai_client.llm.admission_types import (
    GENERATION_IPC_PROTOCOL,
    AdmissionCredentials,
    AdmissionRequest,
    AdmissionStatus,
    GenerationBrokerConfig,
    GenerationPriority,
)
from ai_client.llm.backend import StructuredLLMBackend
from ai_client.llm.types import (
    BackendIdentity,
    LLMBackendError,
    LLMBackendErrorCode,
    LLMMessage,
    LLMUsage,
    ProviderQuiescence,
    ProviderTimingDiagnostic,
    ProviderTimingFieldState,
    ProviderTimingShapeStatus,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
)


def _token(value: int) -> str:
    return f"{value:064x}"


def _request(
    invocation_id: str,
    *,
    priority: GenerationPriority = GenerationPriority.REACTION,
    deadline: float | None = None,
) -> AdmissionRequest:
    return AdmissionRequest(
        invocation_id=invocation_id,
        priority=priority,
        phase="day",
        day=1,
        action_generation=2,
        mapping_order=3,
        not_after_monotonic=deadline or (time.monotonic() + 10.0),
    )


def _generation(request_id: str = "llm-1", marker: str = "private prompt") -> StructuredGenerationRequest:
    return StructuredGenerationRequest(
        request_id=request_id,
        messages=(LLMMessage("user", marker),),
        output_schema={"type": "object", "additionalProperties": False},
    )


def _response(
    request_id: str,
    text: str = '{"kind":"none"}',
    diagnostic: ProviderTimingDiagnostic | None = None,
) -> StructuredGenerationResponse:
    return StructuredGenerationResponse(
        request_id=request_id,
        text=text,
        provider_model="provider-model",
        finish_reason="stop",
        usage=LLMUsage(prompt_tokens=3, completion_tokens=2),
        provider_timing_diagnostic=diagnostic,
    )


def _diagnostic(
    status: ProviderTimingShapeStatus,
    *,
    state: ProviderTimingFieldState = ProviderTimingFieldState.VALID,
    extra_key_count: int = 0,
) -> ProviderTimingDiagnostic:
    return ProviderTimingDiagnostic(
        status=status,
        prompt_n=state,
        prompt_ms=state,
        predicted_n=state,
        predicted_ms=state,
        extra_key_count=extra_key_count,
    )


class _FakeBackend:
    def __init__(self, outcomes: list[object] | None = None) -> None:
        self._identity = BackendIdentity(
            backend_type="test_backend",
            endpoint_origin="http://127.0.0.1:8080",
            endpoint_path="/v1/chat/completions",
            model="test-model",
            config_fingerprint=hashlib.sha256(b"fake").hexdigest(),
        )
        self.outcomes = list(outcomes or [])
        self.calls: list[StructuredGenerationRequest] = []
        self.active = 0
        self.peak_active = 0
        self.started = asyncio.Event()
        self.closed = False

    @property
    def identity(self) -> BackendIdentity:
        return self._identity

    async def generate(
        self, request: StructuredGenerationRequest
    ) -> StructuredGenerationResponse:
        self.calls.append(request)
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        self.started.set()
        try:
            outcome = self.outcomes.pop(0) if self.outcomes else _response(request.request_id)
            if (
                isinstance(outcome, tuple)
                and len(outcome) == 2
                and isinstance(outcome[0], asyncio.Event)
                and isinstance(outcome[1], BaseException)
            ):
                await outcome[0].wait()
                raise outcome[1]
            if isinstance(outcome, asyncio.Event):
                await outcome.wait()
                return _response(request.request_id)
            if isinstance(outcome, BaseException):
                raise outcome
            assert isinstance(outcome, StructuredGenerationResponse)
            return outcome
        finally:
            self.active -= 1

    async def aclose(self) -> None:
        self.closed = True


class _ObservingMetrics(AdmissionMetrics):
    def __init__(self, capacity: int) -> None:
        super().__init__(capacity)
        self.enqueued_events: list[str] = []

    def record_nowait(
        self, metric: AdmissionMetric, *, critical: bool = False
    ) -> bool:
        self.enqueued_events.append(metric.event)
        return super().record_nowait(metric, critical=critical)


class AdmissionContractTests(unittest.TestCase):
    def test_frozen_bounded_contracts_and_secret_repr(self) -> None:
        config = GenerationBrokerConfig()
        assert config.max_clients == 9
        assert config.max_pending_total == 9
        assert config.max_active == 1
        assert config.max_calls_per_lease == 2
        assert len(config.config_fingerprint) == 64
        with pytest.raises(ValueError):
            GenerationBrokerConfig(max_clients=True)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            GenerationBrokerConfig(max_clients=10)
        with pytest.raises(ValueError):
            GenerationBrokerConfig(max_active=2)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            GenerationBrokerConfig(provider_drain_grace_seconds=0)
        with pytest.raises(ValueError):
            GenerationBrokerConfig(shutdown_grace_seconds=5.1)
        credentials = AdmissionCredentials("opaque-a", _token(1))
        assert _token(1) not in repr(credentials)
        request = _request("invocation-a")
        with pytest.raises(FrozenInstanceError):
            request.phase = "night"  # type: ignore[misc]
        assert GENERATION_IPC_PROTOCOL == "aiwolf.generation-ipc.v1"
        for name in (
            "ADMISSION_UNAVAILABLE",
            "ADMISSION_OVERLOADED",
            "ADMISSION_EXPIRED",
            "ADMISSION_POISONED",
            "ADMISSION_PROTOCOL",
        ):
            assert LLMBackendErrorCode[name].value == name

    def test_metric_serialization_is_fixed_prompt_free_metadata(self) -> None:
        encoded = serialize_admission_metric(
            AdmissionMetric(
                event="PROVIDER_CALL_TERMINAL",
                invocation_id="invocation-a",
                client_id="opaque-a",
                priority=GenerationPriority.REACTION,
                call_ordinal=1,
                backend_code="REQUEST_TIMEOUT",
                retryable=True,
                provider_quiescence=ProviderQuiescence.UNKNOWN,
                consumer_state="abandoned",
                poison_transition=True,
                terminal_status="PROVIDER_QUIESCENCE_UNKNOWN",
            )
        )
        value = json.loads(encoded)
        assert value["provider_quiescence"] == "UNKNOWN"
        assert value["retryable"] is True
        assert value["http_status"] is None
        assert value["poison_transition"] is True
        assert "private prompt" not in encoded.decode("utf-8")
        assert "generated result" not in encoded.decode("utf-8")
        assert _token(1) not in encoded.decode("utf-8")

    def test_metric_diagnostic_projection_is_complete_fixed_and_nullable(self) -> None:
        diagnostic = _diagnostic(
            ProviderTimingShapeStatus.VALID_WITH_EXTRA, extra_key_count=2
        )
        metric = AdmissionMetric(
            event="PROVIDER_CALL_TERMINAL",
            provider_timing_diagnostic_status=diagnostic.status,
            provider_timing_prompt_n_state=diagnostic.prompt_n,
            provider_timing_prompt_ms_state=diagnostic.prompt_ms,
            provider_timing_predicted_n_state=diagnostic.predicted_n,
            provider_timing_predicted_ms_state=diagnostic.predicted_ms,
            provider_timing_extra_key_count=diagnostic.extra_key_count,
        )
        value = json.loads(serialize_admission_metric(metric))
        self.assertEqual(value["provider_timing_diagnostic_status"], "VALID_WITH_EXTRA")
        self.assertEqual(value["provider_timing_extra_key_count"], 2)
        self.assertEqual(value["provider_timing_prompt_n_state"], "VALID")
        self.assertNotIn("details", value)
        self.assertNotIn("private-extra-key", value)
        null_value = json.loads(serialize_admission_metric(AdmissionMetric(event="ENQUEUED")))
        self.assertIsNone(null_value["provider_timing_diagnostic_status"])
        self.assertIsNone(null_value["provider_timing_extra_key_count"])
        with self.assertRaises(ValueError):
            AdmissionMetric(
                event="PROVIDER_CALL_TERMINAL",
                provider_timing_diagnostic_status=diagnostic.status,
            )


class AdmissionBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.config = GenerationBrokerConfig(
            authentication_timeout_seconds=0.5,
            cancellation_grace_seconds=0.5,
            provider_drain_grace_seconds=1.0,
            shutdown_grace_seconds=2.0,
        )
        self.registry = {
            "opaque-a": _token(1),
            "opaque-b": _token(2),
            "opaque-c": _token(3),
        }
        self.sessions: list[BrokerAdmissionSession] = []
        self.broker: GenerationAdmissionBroker | None = None

    async def asyncTearDown(self) -> None:
        await asyncio.gather(
            *(session.aclose() for session in self.sessions),
            return_exceptions=True,
        )
        if self.broker is not None:
            await self.broker.aclose()

    async def _start(
        self,
        backend: _FakeBackend,
        *,
        registry: dict[str, str] | None = None,
        fairness_seed: str = "seed-one",
        config: GenerationBrokerConfig | None = None,
        backend_request_timeout_seconds: float = 0.5,
        metrics: AdmissionMetrics | None = None,
    ) -> GenerationAdmissionBroker:
        self.config = config or self.config
        self.registry = registry or self.registry
        self.broker = GenerationAdmissionBroker(
            self.registry,
            backend,
            fairness_seed=fairness_seed,
            config=self.config,
            metrics=metrics,
            backend_request_timeout_seconds=backend_request_timeout_seconds,
        )
        await self.broker.start()
        return self.broker

    async def _connect(self, client_id: str) -> BrokerAdmissionSession:
        assert self.broker is not None
        ready = self.broker.ready
        session = await BrokerAdmissionSession.connect(
            ready.host,
            ready.port,
            AdmissionCredentials(client_id, self.registry[client_id]),
            config=self.config,
        )
        self.sessions.append(session)
        return session

    async def test_auth_replay_bad_frame_and_owner_private_proxy(self) -> None:
        backend = _FakeBackend()
        broker = await self._start(backend)
        a = await self._connect("opaque-a")
        with pytest.raises(LLMBackendError) as wrong:
            await BrokerAdmissionSession.connect(
                broker.ready.host,
                broker.ready.port,
                AdmissionCredentials("opaque-b", _token(999)),
                config=self.config,
            )
        assert wrong.value.code is LLMBackendErrorCode.ADMISSION_UNAVAILABLE
        with pytest.raises(LLMBackendError):
            await BrokerAdmissionSession.connect(
                broker.ready.host,
                broker.ready.port,
                AdmissionCredentials("opaque-a", self.registry["opaque-a"]),
                config=self.config,
            )

        # A malformed unauthenticated client is isolated; it cannot disturb A.
        reader, writer = await asyncio.open_connection(broker.ready.host, broker.ready.port)
        writer.write(struct.pack("!I", 0))
        await writer.drain()
        assert await reader.read() == b""
        writer.close()
        await writer.wait_closed()

        result = await a.acquire(_request("a-one"))
        assert result.status is AdmissionStatus.OFFERED
        assert result.lease is not None
        assert await result.lease.claim() is AdmissionStatus.GRANTED
        proxy = BrokeredStructuredLLMBackend(a)
        with result.lease.activate():
            generated = await proxy.generate(_generation(marker="do-not-log-this"))
        assert generated.text == '{"kind":"none"}'
        await result.lease.release()
        assert backend.peak_active == 1
        await broker.metrics.flush()
        evidence = b"".join(
            serialize_admission_metric(record) for record in broker.metrics.records
        )
        assert b"do-not-log-this" not in evidence
        assert self.registry["opaque-a"].encode() not in evidence

    async def test_diagnostic_stays_broker_owned_and_associated_across_two_sessions(self) -> None:
        absent = _diagnostic(
            ProviderTimingShapeStatus.ABSENT,
            state=ProviderTimingFieldState.NOT_OBSERVED,
        )
        extra = _diagnostic(
            ProviderTimingShapeStatus.VALID_WITH_EXTRA,
            extra_key_count=1,
        )
        backend = _FakeBackend(
            [
                _response("request-a", '{"kind":"none","owner":"a"}', absent),
                _response("request-b", '{"kind":"none","owner":"b"}', extra),
            ]
        )
        broker = await self._start(backend)
        a = await self._connect("opaque-a")
        b = await self._connect("opaque-b")
        seen: dict[str, StructuredGenerationResponse] = {}
        for owner, session in (("a", a), ("b", b)):
            admission = await session.acquire(_request(f"invocation-{owner}"))
            assert admission.lease is not None
            assert await admission.lease.claim() is AdmissionStatus.GRANTED
            proxy = BrokeredStructuredLLMBackend(session)
            with admission.lease.activate():
                seen[owner] = await proxy.generate(_generation(f"request-{owner}"))
            await admission.lease.release()
        self.assertEqual(seen["a"].text, '{"kind":"none","owner":"a"}')
        self.assertEqual(seen["b"].text, '{"kind":"none","owner":"b"}')
        self.assertIsNone(seen["a"].provider_timing_diagnostic)
        self.assertIsNone(seen["b"].provider_timing_diagnostic)
        await broker.metrics.flush()
        calls = [
            record
            for record in broker.metrics.records
            if record.event == "PROVIDER_CALL_TERMINAL"
        ]
        self.assertEqual(
            {
                record.invocation_id: record.provider_timing_diagnostic_status
                for record in calls
            },
            {
                "invocation-a": ProviderTimingShapeStatus.ABSENT,
                "invocation-b": ProviderTimingShapeStatus.VALID_WITH_EXTRA,
            },
        )
        evidence = b"".join(serialize_admission_metric(record) for record in calls)
        self.assertNotIn(b'"owner":"a"', evidence)
        self.assertNotIn(b'"owner":"b"', evidence)

    async def test_reservation_priority_nonpreemption_and_capacity(self) -> None:
        gate = asyncio.Event()
        backend = _FakeBackend([gate])
        broker = await self._start(backend)
        a = await self._connect("opaque-a")
        b = await self._connect("opaque-b")
        c = await self._connect("opaque-c")
        first = await a.acquire(_request("reaction-a"))
        assert first.lease is not None
        assert await first.lease.claim() is AdmissionStatus.GRANTED
        proxy = BrokeredStructuredLLMBackend(a)
        with first.lease.activate():
            provider = asyncio.create_task(proxy.generate(_generation()))
            await backend.started.wait()
            reaction_wait = asyncio.create_task(b.acquire(_request("reaction-b")))
            reservation_wait = asyncio.create_task(
                c.acquire(
                    _request(
                        "reservation-c", priority=GenerationPriority.RESERVATION
                    )
                )
            )
            await asyncio.sleep(0)
            assert not reaction_wait.done()
            assert not reservation_wait.done()
            gate.set()
            await provider
        await first.lease.release()
        reservation = await reservation_wait
        assert reservation.status is AdmissionStatus.OFFERED
        assert reservation.lease is not None
        assert await reservation.lease.claim() is AdmissionStatus.GRANTED
        await reservation.lease.release()
        reaction = await reaction_wait
        assert reaction.lease is not None
        assert await reaction.lease.claim() is AdmissionStatus.GRANTED
        await reaction.lease.release()
        assert backend.peak_active == 1
        assert broker.snapshot.pending_total == 0

        small_config = GenerationBrokerConfig(
            max_pending_total=1,
            authentication_timeout_seconds=0.5,
            cancellation_grace_seconds=0.5,
            provider_drain_grace_seconds=1.0,
            shutdown_grace_seconds=2.0,
        )
        await asyncio.gather(*(s.aclose() for s in self.sessions))
        self.sessions.clear()
        await broker.aclose()
        self.broker = None
        backend2 = _FakeBackend()
        await self._start(backend2, config=small_config)
        a2 = await self._connect("opaque-a")
        b2 = await self._connect("opaque-b")
        occupied = await a2.acquire(_request("occupied"))
        overloaded = await b2.acquire(_request("overloaded"))
        assert occupied.status is AdmissionStatus.OFFERED
        assert overloaded.status is AdmissionStatus.OVERLOADED

    async def test_waiting_replacement_and_result_bearing_successor(self) -> None:
        backend = _FakeBackend()
        await self._start(backend)
        a = await self._connect("opaque-a")
        b = await self._connect("opaque-b")
        blocker = await a.acquire(_request("blocker"))
        assert blocker.lease is not None
        assert await blocker.lease.claim() is AdmissionStatus.GRANTED
        old_task = asyncio.create_task(b.acquire(_request("old-reaction")))
        await asyncio.sleep(0)
        replacement_task = asyncio.create_task(b.replace_waiting(
            "old-reaction",
            _request("replacement", priority=GenerationPriority.RESERVATION),
        ))
        old_result = await old_task
        assert old_result.status is AdmissionStatus.REPLACED
        assert not replacement_task.done()
        await blocker.lease.release()
        replacement = await replacement_task
        assert replacement.status is AdmissionStatus.OFFERED
        assert replacement.lease is not None
        assert await replacement.lease.claim() is AdmissionStatus.GRANTED
        await replacement.lease.release()

        reaction = await a.acquire(_request("active-reaction"))
        assert reaction.lease is not None
        assert await reaction.lease.claim() is AdmissionStatus.GRANTED
        successor_handle = await a.reserve_successor(
            "active-reaction",
            _request("attached-successor", priority=GenerationPriority.RESERVATION),
        )
        wait_task = asyncio.create_task(successor_handle.wait_offer())
        await asyncio.sleep(0)
        assert not wait_task.done()
        await reaction.lease.release()
        successor = await wait_task
        assert successor.status is AdmissionStatus.OFFERED
        assert successor.lease is not None
        assert successor.lease.invocation_id == "attached-successor"
        assert successor.lease is not reaction.lease
        assert await successor.lease.claim() is AdmissionStatus.GRANTED
        assert await successor_handle.cancel() is AdmissionStatus.GRANTED
        await successor.lease.release()
        with pytest.raises(LLMBackendError) as repeated_wait:
            await successor_handle.wait_offer()
        assert repeated_wait.value.code is LLMBackendErrorCode.ADMISSION_PROTOCOL

    async def test_cancelled_successor_wait_cleanup_is_bounded_and_deterministic(self) -> None:
        backend = _FakeBackend()
        broker = await self._start(backend)
        a = await self._connect("opaque-a")
        reaction = await a.acquire(_request("cleanup-reaction"))
        assert reaction.lease is not None
        assert await reaction.lease.claim() is AdmissionStatus.GRANTED
        successor = await a.reserve_successor(
            "cleanup-reaction",
            _request(
                "cancelled-successor",
                priority=GenerationPriority.RESERVATION,
            ),
        )
        waiter = asyncio.create_task(successor.wait_offer())
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert "cancelled-successor" in a._results

        assert await successor.cancel() is AdmissionStatus.CANCELLED
        await asyncio.sleep(0)
        assert "cancelled-successor" not in a._results
        terminal = successor._future.result()
        assert terminal.status is AdmissionStatus.CANCELLED
        assert terminal.lease is None

        for _ in range(5):
            assert await successor.cancel() is AdmissionStatus.CANCELLED
            await asyncio.sleep(0)
            assert "cancelled-successor" not in a._results
        assert broker.snapshot.pending_total == 1
        assert broker.snapshot.claimed == "cleanup-reaction"
        await reaction.lease.release()

    async def test_cancel_deadline_and_stale_successor_are_terminal(self) -> None:
        backend = _FakeBackend()
        await self._start(backend)
        a = await self._connect("opaque-a")
        expired = await a.acquire(
            _request("expired", deadline=time.monotonic() - 0.001)
        )
        assert expired.status is AdmissionStatus.EXPIRED
        offered = await a.acquire(_request("cancel-me"))
        assert offered.status is AdmissionStatus.OFFERED
        assert await a.cancel("cancel-me") is AdmissionStatus.CANCELLED
        assert await a.cancel("cancel-me") is AdmissionStatus.CANCELLED
        stale = await a.reserve_successor(
            "cancel-me",
            _request("stale-successor", priority=GenerationPriority.RESERVATION),
        )
        assert (await stale.wait_offer()).status is AdmissionStatus.UNAVAILABLE

    async def test_cancel_first_control_lane_sends_one_frame_and_invalidates_offer(
        self,
    ) -> None:
        backend = _FakeBackend()
        broker = await self._start(backend)
        a = await self._connect("opaque-a")
        offered = await a.acquire(_request("cancel-first"))
        assert offered.lease is not None

        sent = asyncio.Event()
        allow = asyncio.Event()
        frames: list[str] = []
        original_send = a._send

        async def gated_send(message_type: str, **fields: object) -> None:
            if message_type in {"CANCEL", "CLAIM"}:
                frames.append(message_type)
                sent.set()
                await allow.wait()
            await original_send(message_type, **fields)

        a._send = gated_send  # type: ignore[method-assign]
        cancel = asyncio.create_task(a.cancel("cancel-first"))
        await sent.wait()
        claim = asyncio.create_task(offered.lease.claim())
        await asyncio.sleep(0)
        assert frames == ["CANCEL"]
        assert len(a._acks) == 1
        allow.set()
        assert await cancel is AdmissionStatus.CANCELLED
        assert await claim is AdmissionStatus.CANCELLED
        with pytest.raises(LLMBackendError) as inactive:
            with offered.lease.activate():
                pass
        assert inactive.value.code is LLMBackendErrorCode.ADMISSION_PROTOCOL
        assert a._acks == {}
        assert a._control_lanes == {}
        assert a._closed is False
        assert broker.snapshot.pending_total == 0

    async def test_claim_first_control_lane_sends_one_frame_and_is_nonpreemptive(
        self,
    ) -> None:
        backend = _FakeBackend()
        broker = await self._start(backend)
        a = await self._connect("opaque-a")
        offered = await a.acquire(_request("claim-first"))
        assert offered.lease is not None

        sent = asyncio.Event()
        allow = asyncio.Event()
        frames: list[str] = []
        original_send = a._send

        async def gated_send(message_type: str, **fields: object) -> None:
            if message_type in {"CANCEL", "CLAIM"}:
                frames.append(message_type)
                sent.set()
                await allow.wait()
            await original_send(message_type, **fields)

        a._send = gated_send  # type: ignore[method-assign]
        claim = asyncio.create_task(offered.lease.claim())
        await sent.wait()
        cancel = asyncio.create_task(a.cancel("claim-first"))
        await asyncio.sleep(0)
        assert frames == ["CLAIM"]
        assert len(a._acks) == 1
        allow.set()
        assert await claim is AdmissionStatus.GRANTED
        assert await cancel is AdmissionStatus.GRANTED
        with offered.lease.activate():
            pass
        assert broker.snapshot.claimed == "claim-first"
        assert a._closed is False
        await offered.lease.release()
        assert a._acks == {}
        assert a._control_lanes == {}
        assert broker.snapshot.pending_total == 0

    async def test_cancelled_control_waiters_leave_no_orphan_ack_or_claimed_lease(
        self,
    ) -> None:
        backend = _FakeBackend()
        broker = await self._start(backend)
        a = await self._connect("opaque-a")

        before = await a.acquire(_request("cancel-before-lane"))
        assert before.lease is not None
        before_lane = before.lease._lane
        await before_lane.lock.acquire()
        before_frames: list[str] = []
        original_send = a._send

        async def observe_before(message_type: str, **fields: object) -> None:
            before_frames.append(message_type)
            await original_send(message_type, **fields)

        a._send = observe_before  # type: ignore[method-assign]
        blocked_claim = asyncio.create_task(before.lease.claim())
        await asyncio.sleep(0)
        blocked_claim.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocked_claim
        before_lane.lock.release()
        assert before_frames == []
        assert a._acks == {}
        assert await a.cancel("cancel-before-lane") is AdmissionStatus.CANCELLED

        after = await a.acquire(_request("cancel-after-frame"))
        assert after.lease is not None
        sent = asyncio.Event()
        allow = asyncio.Event()
        after_frames: list[str] = []

        async def observe_after(message_type: str, **fields: object) -> None:
            await original_send(message_type, **fields)
            after_frames.append(message_type)
            if message_type == "CLAIM":
                sent.set()
                await allow.wait()

        a._send = observe_after  # type: ignore[method-assign]
        cancelled_claim = asyncio.create_task(after.lease.claim())
        await sent.wait()
        cancelled_claim.cancel()
        await asyncio.sleep(0)
        for _ in range(4):
            cancelled_claim.cancel()
            await asyncio.sleep(0)
        allow.set()
        with pytest.raises(asyncio.CancelledError):
            await cancelled_claim
        assert after_frames == ["CLAIM", "ABANDON"]
        assert after.lease._abandon_disposition == "ABANDON_ACKNOWLEDGED"
        assert a._claimed_invocation is None
        assert a._acks == {}
        assert a._control_lanes == {}
        assert a._closed is False
        await after.lease.release()
        await after.lease.release()
        assert "RELEASE" not in after_frames
        assert broker.snapshot.pending_total == 0

    async def test_missing_control_ack_closes_session_and_cleans_claimed_slot(
        self,
    ) -> None:
        config = GenerationBrokerConfig(
            authentication_timeout_seconds=0.5,
            cancellation_grace_seconds=0.05,
            provider_drain_grace_seconds=0.1,
            shutdown_grace_seconds=0.2,
        )
        backend = _FakeBackend()
        broker = await self._start(
            backend,
            config=config,
            backend_request_timeout_seconds=0.05,
        )
        a = await self._connect("opaque-a")
        offered = await a.acquire(_request("missing-claim-ack"))
        assert offered.lease is not None
        original_emit_ack = broker._emit_ack

        def drop_claim_ack(
            client_id: str,
            invocation_id: str,
            status: AdmissionStatus | str,
        ) -> None:
            value = status.value if isinstance(status, AdmissionStatus) else status
            if invocation_id == "missing-claim-ack" and value == "GRANTED":
                return
            original_emit_ack(client_id, invocation_id, status)

        broker._emit_ack = drop_claim_ack  # type: ignore[method-assign]
        with pytest.raises(LLMBackendError) as missing:
            await offered.lease.claim()
        assert missing.value.code is LLMBackendErrorCode.ADMISSION_UNAVAILABLE
        for _ in range(200):
            if broker.snapshot.pending_total == 0:
                break
            await asyncio.sleep(0)
        assert a._closed is True
        assert a._acks == {}
        assert a._control_lanes == {}
        assert broker.snapshot.pending_total == 0

    async def test_http_error_envelope_preserves_every_attribute_and_privacy(self) -> None:
        source = LLMBackendError(
            LLMBackendErrorCode.HTTP_STATUS,
            http_status=503,
            retryable=True,
            provider_quiescence=ProviderQuiescence.PROVEN_TERMINAL,
        )
        backend = _FakeBackend([source])
        broker = await self._start(backend)
        a = await self._connect("opaque-a")
        connection = broker._connections["opaque-a"]
        emitted: list[tuple[str, dict[str, object]]] = []
        original_emit = connection.emit

        def capture(message_type: str, **fields: object) -> bool:
            if message_type == "ERROR":
                emitted.append((message_type, dict(fields)))
            return original_emit(message_type, **fields)

        connection.emit = capture  # type: ignore[method-assign]
        admitted = await a.acquire(_request("http-error"))
        assert admitted.lease is not None
        assert await admitted.lease.claim() is AdmissionStatus.GRANTED
        proxy = BrokeredStructuredLLMBackend(a)
        with admitted.lease.activate():
            with pytest.raises(LLMBackendError) as caught:
                await proxy.generate(
                    _generation("http-vector", marker="secret-http-prompt")
                )
        error = caught.value
        assert error.code is LLMBackendErrorCode.HTTP_STATUS
        assert error.http_status == 503
        assert error.retryable is True
        assert error.provider_quiescence is ProviderQuiescence.PROVEN_TERMINAL
        assert str(error) == "HTTP_STATUS"
        assert error.args == ("HTTP_STATUS",)
        assert broker.snapshot.poisoned is False
        assert emitted == [
            (
                "ERROR",
                {
                    "call_ordinal": 1,
                    "code": "HTTP_STATUS",
                    "http_status": 503,
                    "invocation_id": "http-error",
                    "provider_quiescence": "PROVEN_TERMINAL",
                    "retryable": True,
                },
            )
        ]
        wire_evidence = json.dumps(emitted, sort_keys=True)
        assert "secret-http-prompt" not in wire_evidence
        assert self.registry["opaque-a"] not in wire_evidence
        await admitted.lease.release()
        await broker.metrics.flush()
        metric = next(
            record
            for record in broker.metrics.records
            if record.event == "PROVIDER_CALL_TERMINAL"
        )
        assert metric.backend_code == "HTTP_STATUS"
        assert metric.http_status == 503
        assert metric.retryable is True
        assert metric.provider_quiescence is ProviderQuiescence.PROVEN_TERMINAL

    async def test_http_error_boundaries_and_retryability_round_trip(self) -> None:
        vectors = (
            (100, False, ProviderQuiescence.NOT_STARTED),
            (599, True, ProviderQuiescence.PROVEN_TERMINAL),
        )
        backend = _FakeBackend(
            [
                LLMBackendError(
                    LLMBackendErrorCode.HTTP_STATUS,
                    http_status=status,
                    retryable=retryable,
                    provider_quiescence=quiescence,
                )
                for status, retryable, quiescence in vectors
            ]
        )
        await self._start(backend)
        a = await self._connect("opaque-a")
        proxy = BrokeredStructuredLLMBackend(a)
        for index, (status, retryable, quiescence) in enumerate(vectors):
            admitted = await a.acquire(_request(f"http-boundary-{index}"))
            assert admitted.lease is not None
            assert await admitted.lease.claim() is AdmissionStatus.GRANTED
            with admitted.lease.activate():
                with pytest.raises(LLMBackendError) as caught:
                    await proxy.generate(_generation(f"http-call-{index}"))
            error = caught.value
            assert error.code is LLMBackendErrorCode.HTTP_STATUS
            assert error.http_status == status
            assert error.retryable is retryable
            assert error.provider_quiescence is quiescence
            await admitted.lease.release()

    async def test_malformed_http_error_closes_only_session_as_protocol_error(self) -> None:
        backend = _FakeBackend(
            [
                LLMBackendError(
                    LLMBackendErrorCode.HTTP_STATUS,
                    http_status=503,
                    retryable=True,
                    provider_quiescence=ProviderQuiescence.PROVEN_TERMINAL,
                )
            ]
        )
        broker = await self._start(backend)
        a = await self._connect("opaque-a")
        b = await self._connect("opaque-b")
        original_error_sender = broker._emit_backend_error

        def malformed_sender(
            client_id: str,
            invocation_id: str,
            ordinal: int,
            error: LLMBackendError,
        ) -> bool:
            if error.code is LLMBackendErrorCode.HTTP_STATUS:
                # Normative malformed vector: HTTP_STATUS without http_status.
                return broker._emit(
                    client_id,
                    "ERROR",
                    invocation_id=invocation_id,
                    call_ordinal=ordinal,
                    code="HTTP_STATUS",
                    retryable=True,
                    provider_quiescence="PROVEN_TERMINAL",
                )
            return original_error_sender(client_id, invocation_id, ordinal, error)

        broker._emit_backend_error = malformed_sender  # type: ignore[method-assign]
        admitted = await a.acquire(_request("malformed-http"))
        assert admitted.lease is not None
        assert await admitted.lease.claim() is AdmissionStatus.GRANTED
        proxy = BrokeredStructuredLLMBackend(a)
        with admitted.lease.activate():
            with pytest.raises(LLMBackendError) as caught:
                await proxy.generate(_generation("malformed-http-call"))
        error = caught.value
        assert error.code is LLMBackendErrorCode.ADMISSION_PROTOCOL
        assert error.http_status is None
        assert error.retryable is False
        assert error.provider_quiescence is ProviderQuiescence.UNKNOWN
        assert str(error) == "ADMISSION_PROTOCOL"
        assert broker.snapshot.poisoned is False
        # Only A was closed. B remains authenticated and can still be admitted.
        b_result = await b.acquire(_request("healthy-peer"))
        assert b_result.status is AdmissionStatus.OFFERED
        assert b_result.lease is not None
        assert await b_result.lease.claim() is AdmissionStatus.GRANTED
        await b_result.lease.release()

    async def test_safe_errors_allow_repair_unknown_errors_permanently_poison(self) -> None:
        backend = _FakeBackend(
            [
                LLMBackendError(
                    LLMBackendErrorCode.CONNECT_FAILED,
                    retryable=True,
                    provider_quiescence=ProviderQuiescence.NOT_STARTED,
                ),
                _response("llm-repair"),
                LLMBackendError(
                    LLMBackendErrorCode.REQUEST_TIMEOUT,
                    retryable=True,
                    provider_quiescence=ProviderQuiescence.UNKNOWN,
                ),
            ]
        )
        broker = await self._start(backend)
        a = await self._connect("opaque-a")
        b = await self._connect("opaque-b")
        first = await a.acquire(_request("safe-error"))
        assert first.lease is not None
        assert await first.lease.claim() is AdmissionStatus.GRANTED
        proxy = BrokeredStructuredLLMBackend(a)
        with first.lease.activate():
            with pytest.raises(LLMBackendError) as safe:
                await proxy.generate(_generation("llm-initial"))
            assert safe.value.provider_quiescence is ProviderQuiescence.NOT_STARTED
            assert safe.value.retryable is True
            assert safe.value.http_status is None
            repaired = await proxy.generate(_generation("llm-repair"))
        assert repaired.request_id == "llm-repair"
        await first.lease.release()

        poison = await b.acquire(_request("unknown"))
        assert poison.lease is not None
        assert await poison.lease.claim() is AdmissionStatus.GRANTED
        proxy_b = BrokeredStructuredLLMBackend(b)
        with poison.lease.activate():
            with pytest.raises(LLMBackendError) as unknown:
                await proxy_b.generate(_generation("llm-unknown"))
        assert unknown.value.code is LLMBackendErrorCode.REQUEST_TIMEOUT
        assert unknown.value.provider_quiescence is ProviderQuiescence.UNKNOWN
        assert unknown.value.retryable is True
        assert unknown.value.http_status is None
        assert broker.snapshot.poisoned is True
        assert broker.snapshot.poison_reason == "PROVIDER_QUIESCENCE_UNKNOWN"
        await poison.lease.release()
        assert a._claimed_invocation is None
        assert a._call_ordinals == {}
        assert a._request_ids == {}
        assert a._generations == {}
        assert a._control_lanes == {}
        rejected = await a.acquire(_request("after-poison"))
        assert rejected.status is AdmissionStatus.POISONED
        await broker.metrics.flush()
        assert any(
            record.event == "ADMISSION_POISONED"
            and record.terminal_status == "PROVIDER_QUIESCENCE_UNKNOWN"
            for record in broker.metrics.records
        )

    async def test_unknown_poison_waiters_and_metric_precede_error_delivery(self) -> None:
        gate = asyncio.Event()
        source = LLMBackendError(
            LLMBackendErrorCode.REQUEST_TIMEOUT,
            retryable=True,
            provider_quiescence=ProviderQuiescence.UNKNOWN,
        )
        backend = _FakeBackend([(gate, source)])
        metrics = _ObservingMetrics(self.config.metrics_queue_capacity)
        broker = await self._start(backend, metrics=metrics)
        a = await self._connect("opaque-a")
        b = await self._connect("opaque-b")
        admitted = await a.acquire(_request("poison-order"))
        assert admitted.lease is not None
        assert await admitted.lease.claim() is AdmissionStatus.GRANTED
        proxy = BrokeredStructuredLLMBackend(a)
        observations: list[tuple[bool, bool]] = []
        error_fields: list[dict[str, object]] = []
        original_error_sender = broker._emit_backend_error

        def observe_sender(
            client_id: str,
            invocation_id: str,
            ordinal: int,
            error: LLMBackendError,
        ) -> bool:
            error_fields.append(
                {
                    "call_ordinal": ordinal,
                    "code": error.code.value,
                    "invocation_id": invocation_id,
                    "provider_quiescence": error.provider_quiescence.value,
                    "retryable": error.retryable,
                }
            )
            observations.append(
                (
                    broker.snapshot.poisoned,
                    "ADMISSION_POISONED" in metrics.enqueued_events,
                )
            )
            return original_error_sender(client_id, invocation_id, ordinal, error)

        broker._emit_backend_error = observe_sender  # type: ignore[method-assign]
        with admitted.lease.activate():
            call = asyncio.create_task(proxy.generate(_generation("poison-call")))
            await backend.started.wait()
            successor = await a.reserve_successor(
                "poison-order",
                _request(
                    "poisoned-successor",
                    priority=GenerationPriority.RESERVATION,
                ),
            )
            queued = asyncio.create_task(b.acquire(_request("poisoned-waiter")))
            await asyncio.sleep(0)
            gate.set()
            with pytest.raises(LLMBackendError) as caught:
                await call
        assert caught.value.code is LLMBackendErrorCode.REQUEST_TIMEOUT
        assert caught.value.http_status is None
        assert caught.value.retryable is True
        assert caught.value.provider_quiescence is ProviderQuiescence.UNKNOWN
        assert error_fields == [
            {
                "call_ordinal": 1,
                "code": "REQUEST_TIMEOUT",
                "invocation_id": "poison-order",
                "provider_quiescence": "UNKNOWN",
                "retryable": True,
            }
        ]
        assert "http_status" not in error_fields[0]
        assert observations == [(True, True)]
        assert (await successor.wait_offer()).status is AdmissionStatus.POISONED
        assert (await queued).status is AdmissionStatus.POISONED
        assert broker.snapshot.poisoned is True

    async def test_unclassified_secret_error_is_never_transported_or_logged(self) -> None:
        sentinel = "SECRET https://provider.invalid Authorization body prompt schema"
        backend = _FakeBackend([RuntimeError(sentinel)])
        broker = await self._start(backend)
        a = await self._connect("opaque-a")
        connection = broker._connections["opaque-a"]
        emitted: list[tuple[str, dict[str, object]]] = []
        original_emit = connection.emit

        def capture(message_type: str, **fields: object) -> bool:
            emitted.append((message_type, dict(fields)))
            return original_emit(message_type, **fields)

        connection.emit = capture  # type: ignore[method-assign]
        admitted = await a.acquire(_request("secret-unclassified"))
        assert admitted.lease is not None
        assert await admitted.lease.claim() is AdmissionStatus.GRANTED
        proxy = BrokeredStructuredLLMBackend(a)
        with admitted.lease.activate():
            with pytest.raises(LLMBackendError) as caught:
                await proxy.generate(_generation("secret-call", marker=sentinel))
        assert caught.value.code is LLMBackendErrorCode.ADMISSION_POISONED
        assert caught.value.http_status is None
        assert caught.value.retryable is False
        assert caught.value.provider_quiescence is ProviderQuiescence.UNKNOWN
        await broker.metrics.flush()
        evidence = json.dumps(emitted, sort_keys=True) + b"".join(
            serialize_admission_metric(record) for record in broker.metrics.records
        ).decode("utf-8")
        assert sentinel not in evidence
        assert "Authorization" not in evidence
        assert broker.snapshot.poisoned is True

    async def test_abandoned_provider_drains_before_successor_offer(self) -> None:
        gate = asyncio.Event()
        backend = _FakeBackend([gate])
        broker = await self._start(backend)
        a = await self._connect("opaque-a")
        active = await a.acquire(_request("active"))
        assert active.lease is not None
        assert await active.lease.claim() is AdmissionStatus.GRANTED
        proxy = BrokeredStructuredLLMBackend(a)
        sent_frames: list[str] = []
        original_send = a._send

        async def observed_send(message_type: str, **fields: object) -> None:
            sent_frames.append(message_type)
            await original_send(message_type, **fields)

        a._send = observed_send  # type: ignore[method-assign]
        with active.lease.activate():
            call = asyncio.create_task(proxy.generate(_generation()))
            await backend.started.wait()
            successor_handle = await a.reserve_successor(
                "active",
                _request("next", priority=GenerationPriority.RESERVATION),
            )
            successor = asyncio.create_task(successor_handle.wait_offer())
            call.cancel()
            with pytest.raises(asyncio.CancelledError):
                await call
            assert broker.snapshot.draining is True
            assert not successor.done()
            assert a._claimed_invocation is None
            assert a._call_ordinals == {}
            assert a._request_ids == {}
            assert a._generations == {}
            assert a._closed is False
        await active.lease.release()
        await active.lease.release()
        assert sent_frames.count("ABANDON") == 1
        assert "RELEASE" not in sent_frames
        assert broker.snapshot.draining is True
        assert not successor.done()
        assert "next" in a._results
        gate.set()
        admitted = await successor
        assert admitted.status is AdmissionStatus.OFFERED
        assert admitted.lease is not None
        assert await admitted.lease.claim() is AdmissionStatus.GRANTED
        await admitted.lease.release()
        assert broker.snapshot.poisoned is False
        await broker.metrics.flush()
        assert any(
            record.terminal_status == "ABANDONED_DRAINED"
            for record in broker.metrics.records
        )

    async def test_drain_grace_expiry_permanently_poisoned(self) -> None:
        gate = asyncio.Event()
        backend = _FakeBackend([gate])
        config = GenerationBrokerConfig(
            authentication_timeout_seconds=0.5,
            cancellation_grace_seconds=0.05,
            provider_drain_grace_seconds=0.05,
            shutdown_grace_seconds=0.1,
        )
        broker = await self._start(
            backend,
            config=config,
            backend_request_timeout_seconds=0.05,
        )
        a = await self._connect("opaque-a")
        active = await a.acquire(_request("drain-timeout"))
        assert active.lease is not None
        assert await active.lease.claim() is AdmissionStatus.GRANTED
        proxy = BrokeredStructuredLLMBackend(a)
        with active.lease.activate():
            call = asyncio.create_task(proxy.generate(_generation()))
            await backend.started.wait()
            successor = await a.reserve_successor(
                "drain-timeout",
                _request(
                    "poisoned-after-drain",
                    priority=GenerationPriority.RESERVATION,
                ),
            )
            call.cancel()
            with pytest.raises(asyncio.CancelledError):
                await call
        await active.lease.release()
        await active.lease.release()
        await asyncio.sleep(0.08)
        assert broker.snapshot.poisoned is True
        assert broker.snapshot.poison_reason == "PROVIDER_QUIESCENCE_UNKNOWN"
        poisoned_successor = await successor.wait_offer()
        assert poisoned_successor.status is AdmissionStatus.POISONED
        assert poisoned_successor.lease is None
        assert a._closed is False
        assert a._acks == {}
        assert a._control_lanes == {}
        assert a._results == {}
        rejected = await a.acquire(_request("after-drain-timeout"))
        assert rejected.status is AdmissionStatus.POISONED

    async def test_proxy_rejects_unleased_third_and_reused_requests(self) -> None:
        backend = _FakeBackend()
        await self._start(backend)
        a = await self._connect("opaque-a")
        proxy = BrokeredStructuredLLMBackend(a)
        with pytest.raises(LLMBackendError) as unleased:
            await proxy.generate(_generation())
        assert unleased.value.code is LLMBackendErrorCode.ADMISSION_PROTOCOL
        result = await a.acquire(_request("two-calls"))
        assert result.lease is not None
        assert await result.lease.claim() is AdmissionStatus.GRANTED
        with result.lease.activate():
            await proxy.generate(_generation("one"))
            with pytest.raises(LLMBackendError) as reused:
                await proxy.generate(_generation("one"))
            assert reused.value.code is LLMBackendErrorCode.ADMISSION_PROTOCOL
            await proxy.generate(_generation("two"))
            with pytest.raises(LLMBackendError) as third:
                await proxy.generate(_generation("three"))
            assert third.value.code is LLMBackendErrorCode.ADMISSION_PROTOCOL
        await result.lease.release()

    async def test_shutdown_is_finite_and_closes_only_owned_backend(self) -> None:
        backend = _FakeBackend()
        broker = await self._start(backend)
        await self._connect("opaque-a")
        await asyncio.wait_for(broker.aclose(), timeout=2.5)
        assert backend.closed is True
        assert broker.owns_external_provider is False
        assert broker.shutdown_clean is True
        self.broker = None


assert isinstance(_FakeBackend(), StructuredLLMBackend)


def test_seeded_fairness_has_stable_exact_vectors_and_seed_changes_start() -> None:
    registry = {
        "opaque-a": _token(1),
        "opaque-b": _token(2),
        "opaque-c": _token(3),
    }
    config = GenerationBrokerConfig()
    first = GenerationAdmissionBroker(
        registry,
        _FakeBackend(),
        fairness_seed="seed-one",
        config=config,
        backend_request_timeout_seconds=0.5,
    )
    second = GenerationAdmissionBroker(
        registry,
        _FakeBackend(),
        fairness_seed="another-seed",
        config=config,
        backend_request_timeout_seconds=0.5,
    )
    assert first._order == ("opaque-c", "opaque-b", "opaque-a")
    assert first._cursor[GenerationPriority.REACTION] == 1
    assert second._order == ("opaque-a", "opaque-b", "opaque-c")
    assert second._cursor[GenerationPriority.REACTION] == 0
    assert (
        first._order[first._cursor[GenerationPriority.REACTION]]
        != second._order[second._cursor[GenerationPriority.REACTION]]
    )
