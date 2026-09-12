from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
import unittest

from ai_client.brain import (
    AbilityDecision,
    BrainActionContext,
    BrainActionOption,
    BrainInput,
    BrainController,
    BrainInvocationArbiter,
    BrainInvocationPriority,
    ChatDecision,
    CoDeclareDecision,
    NoDecision,
    VoteDecision,
    DispatchDeadline,
    DecisionStatus,
)
from ai_client.llm import (
    AiAuditError,
    AiAuditErrorCode,
    AiAuditStatus,
    AuditWriteAck,
    BackendIdentity,
    DecisionValidationCode,
    DecisionValidationError,
    LLMBackendError,
    LLMBackendErrorCode,
    LLMBrain,
    LLMBrainConfig,
    LLMBrainSnapshot,
    LLMClientIdentity,
    LLMInvocationError,
    LLMInvocationStatus,
    LLMMessage,
    LLMUsage,
    PromptProjection,
    PromptProjectionError,
    StructuredGenerationResponse,
    parse_llm_decision,
    project_brain_input,
)
from ai_client.network import (
    AbilityAction,
    ChatAction,
    CoDeclareAction,
    CoReportAction,
    VoteAction,
    SendReceipt,
)
from ai_client.world import (
    AbilityResultRecord,
    AbilityResultView,
    ChatRecord,
    CoDeclarationRecord,
    CoView,
    CurrentActionsView,
    CurrentPhaseDeadline,
    Freshness,
    HistoryRetention,
    HistoryView,
    PhaseView,
    PlayerView,
    RevealedRoleView,
    SelfView,
    WorldSnapshot,
    TransportObservationView,
)


def retention(count: int = 0) -> HistoryRetention:
    return HistoryRetention(
        total_seen=count,
        retained_count=count,
        retained_bytes=0,
        dropped_count=0,
        dropped_through_order=None,
        first_retained_order=1 if count else None,
        last_order=count or None,
        max_history_records=1024,
        max_history_bytes=2 * 1024 * 1024,
        complete=True,
    )


def brain_input(*, long_text: bool = False, allows_abstain: bool = True) -> BrainInput:
    human = "あいうえおか"
    message = human if long_text else "hello"
    display_name = human if long_text else "Self"
    co_comment = human if long_text else "claim"
    ability_description = human if long_text else "look"
    records = (
        ChatRecord(1, 1, "day", "public", "p2", "P2", message),
        CoDeclarationRecord(2, 1, "day", "p3", "role:wolf", co_comment),
    )
    options = (
        BrainActionOption(
            "action:0", ChatAction(1, 2, "day", 1, "chat", "public")
        ),
        BrainActionOption(
            "action:1",
            VoteAction(1, 2, "vote", 1, "vote", ("p2", "p3"), 1, allows_abstain),
        ),
        BrainActionOption(
            "action:2",
            AbilityAction(
                1, 2, "night", 1, "ability", "inspect", ability_description, ("p2", "p3"), 1, 1
            ),
        ),
        BrainActionOption(
            "action:3",
            CoDeclareAction(1, 2, "day", 1, "co_declare", ("role:seer",)),
        ),
        BrainActionOption(
            "action:4", CoReportAction(1, 2, "day", 1, "co_report")
        ),
    )
    return BrainInput(
        snapshot=WorldSnapshot(
            version=4,
            freshness=Freshness.CURRENT,
            is_caught_up=True,
            last_applied_seq=9,
            players=(PlayerView("p1", display_name), PlayerView("p2", "Peer")),
            alive_player_ids=("p1", "p2"),
            phase=PhaseView("day", 1, 999),
            self_view=SelfView("p1", "role:villager", ("modifier:x",)),
            revealed_roles=(RevealedRoleView("p2", "role:wolf"),),
            history_retention=retention(2),
        ),
        action_context=BrainActionContext(4, 9, 9, True, options),
        history=HistoryView(records, True, retention(2)),
        co=CoView((records[1],), (), True, retention(1)),
        ability_results=AbilityResultView(
            (AbilityResultRecord(3, 1, "night", "inspect_result", "p2", "wolf", None),),
            True,
            retention(1),
        ),
    )


class ProjectionAndDecisionTests(unittest.TestCase):
    def test_projection_is_deterministic_bounded_allowlisted_and_fail_closed_report(self) -> None:
        request = brain_input(long_text=True)
        config = LLMBrainConfig(max_human_text_chars=5, max_history_records=2)
        first = project_brain_input(request, config=config)
        second = project_brain_input(request, config=config)
        self.assertEqual(first, second)
        self.assertEqual(first.prompt_bytes, len(first_prompt := _prompt_bytes(first)))
        self.assertEqual(first.included_history_records, 2)
        self.assertEqual(first.omitted_history_records, 0)
        payload = json.loads(first.messages[1].content)
        expected_text = {"original_chars": 6, "text": "あいうえお", "truncated": True}
        self.assertEqual(payload["snapshot"]["players"][0]["display_name"], expected_text)
        self.assertEqual(payload["history"]["records"][0]["message"], expected_text)
        self.assertEqual(payload["history"]["records"][1]["comment"], expected_text)
        self.assertEqual(payload["co"]["declarations"][0]["comment"], expected_text)
        self.assertEqual(payload["action_context"]["options"][2]["description"], expected_text)

        # Representative opaque identifiers, received options, and private authorized
        # records are neither normalized nor truncated while human text is shortened.
        self.assertEqual(payload["snapshot"]["self"]["role_id"], "role:villager")
        self.assertEqual(payload["snapshot"]["self"]["modifier_ids"], ["modifier:x"])
        self.assertEqual(payload["snapshot"]["revealed_roles"], [
            {"player_id": "p2", "role_id": "role:wolf"}
        ])
        self.assertEqual(
            [option["option_id"] for option in payload["action_context"]["options"]],
            ["action:0", "action:1", "action:2", "action:3", "action:4"],
        )
        self.assertEqual(payload["action_context"]["options"][2]["ability_id"], "inspect")
        self.assertEqual(payload["action_context"]["options"][2]["valid_targets"], ["p2", "p3"])
        self.assertEqual(payload["ability_results"]["records"][0]["event_type"], "inspect_result")
        self.assertEqual(payload["ability_results"]["records"][0]["result_id"], "wolf")
        self.assertNotIn("co_report", [
            branch["properties"]["kind"]["const"]
            for branch in first.decision_schema["oneOf"]
        ])
        serialized = first_prompt.decode("utf-8")
        for secret in ("AIWOLF_ENTRY_TOKEN", "Authorization", "connection_token"):
            self.assertNotIn(secret, serialized)

    def test_projection_preserves_opaque_values_and_does_not_read_environment(self) -> None:
        sentinel = "do-not-project-environment-secret"
        os.environ["AIWOLF_TEST_SENTINEL"] = sentinel
        try:
            projection = project_brain_input(brain_input(), config=LLMBrainConfig())
        finally:
            del os.environ["AIWOLF_TEST_SENTINEL"]
        text = projection.messages[1].content
        self.assertNotIn(sentinel, text)
        self.assertIn("modifier:x", text)
        self.assertIn("role:villager", text)

    def test_complete_contract_overflow_raises_before_backend_boundary(self) -> None:
        with self.assertRaises(PromptProjectionError) as raised:
            project_brain_input(
                brain_input(long_text=True),
                config=LLMBrainConfig(max_prompt_bytes=1024),
            )
        self.assertGreater(raised.exception.projection.prompt_bytes, 1024)

    def test_all_current_decisions_parse(self) -> None:
        projection = project_brain_input(brain_input(), config=LLMBrainConfig())
        vectors = (
            ('{"kind":"none"}', NoDecision),
            ('{"kind":"chat","option_id":"action:0","message":"ok"}', ChatDecision),
            ('{"kind":"vote","option_id":"action:1","target_player_id":"p2"}', VoteDecision),
            ('{"kind":"ability","option_id":"action:2","target_player_ids":["p3"]}', AbilityDecision),
            ('{"kind":"co_declare","option_id":"action:3","claimed_role_id":"role:seer","comment":"yes"}', CoDeclareDecision),
        )
        for text, expected in vectors:
            with self.subTest(text=text):
                self.assertIsInstance(parse_llm_decision(text, projection=projection), expected)

    def test_parser_rejects_non_json_duplicates_unknowns_and_unauthorized_values(self) -> None:
        projection = project_brain_input(brain_input(), config=LLMBrainConfig())
        vectors = (
            ("```json\n{}\n```", DecisionValidationCode.JSON_SYNTAX),
            ('leading {"kind":"none"}', DecisionValidationCode.JSON_SYNTAX),
            ('{"kind":"none"} trailing', DecisionValidationCode.JSON_SYNTAX),
            ('{"kind":"none","kind":"none"}', DecisionValidationCode.JSON_DUPLICATE_KEY),
            ('{"kind":"none","extra":1}', DecisionValidationCode.SCHEMA),
            ('{"kind":"vote","option_id":"fake","target_player_id":"p2"}', DecisionValidationCode.OPTION_NOT_OFFERED),
            ('{"kind":"vote","option_id":"action:1","target_player_id":"p9"}', DecisionValidationCode.VALUE_NOT_OFFERED),
            ('{"kind":"co_report","option_id":"action:4","report_kind":"inspect","target_player_id":"p2","claimed_result":"wolf"}', DecisionValidationCode.OPTION_NOT_OFFERED),
            ('{"kind":"none","x":NaN}', DecisionValidationCode.JSON_SYNTAX),
            ('{"kind":"none","x":Infinity}', DecisionValidationCode.JSON_SYNTAX),
            ('{"kind":"ability","option_id":"action:2","target_player_ids":[]}', DecisionValidationCode.VALUE_NOT_OFFERED),
            ('{"kind":"ability","option_id":"action:2","target_player_ids":["p2","p2"]}', DecisionValidationCode.VALUE_NOT_OFFERED),
            ('{"kind":"co_declare","option_id":"action:3","claimed_role_id":"role:wolf","comment":"ok"}', DecisionValidationCode.VALUE_NOT_OFFERED),
        )
        for text, code in vectors:
            with self.subTest(text=text), self.assertRaises(DecisionValidationError) as raised:
                parse_llm_decision(text, projection=projection)
            self.assertEqual(raised.exception.code, code)

        non_abstain = project_brain_input(
            brain_input(allows_abstain=False), config=LLMBrainConfig()
        )
        with self.assertRaises(DecisionValidationError) as raised:
            parse_llm_decision(
                '{"kind":"vote","option_id":"action:1","target_player_id":null}',
                projection=non_abstain,
            )
        self.assertEqual(raised.exception.code, DecisionValidationCode.VALUE_NOT_OFFERED)

    def test_generated_text_exact_bounds(self) -> None:
        projection = project_brain_input(
            brain_input(), config=LLMBrainConfig(max_generated_text_chars=3)
        )
        parse_llm_decision(
            '{"kind":"chat","option_id":"action:0","message":"abc"}',
            projection=projection,
        )
        for message in ("", "abcd"):
            with self.assertRaises(DecisionValidationError) as raised:
                parse_llm_decision(
                    json.dumps({"kind": "chat", "option_id": "action:0", "message": message}),
                    projection=projection,
                )
            self.assertEqual(raised.exception.code, DecisionValidationCode.TEXT_BOUND)

        parse_llm_decision(
            '{"kind":"co_declare","option_id":"action:3","claimed_role_id":"role:seer","comment":"abc"}',
            projection=projection,
        )
        for comment in ("", "abcd"):
            with self.assertRaises(DecisionValidationError) as raised:
                parse_llm_decision(
                    json.dumps(
                        {
                            "kind": "co_declare",
                            "option_id": "action:3",
                            "claimed_role_id": "role:seer",
                            "comment": comment,
                        }
                    ),
                    projection=projection,
                )
            self.assertEqual(raised.exception.code, DecisionValidationCode.TEXT_BOUND)

    def test_explicit_test_only_co_report_vocabulary_maps_positive_union_member(self) -> None:
        from ai_client.brain import CoReportDecision

        base = project_brain_input(brain_input(), config=LLMBrainConfig())
        canonical = _plain(base.canonical_input)
        report = canonical["action_context"]["options"][4]
        report.update(
            {
                "claimed_results": ["result:wolf", "result:not_wolf"],
                "eligible": True,
                "report_kinds": ["inspect_result"],
                "valid_targets": ["p2", "p3"],
            }
        )
        schema = _plain(base.decision_schema)
        schema["oneOf"].append(
            {
                "additionalProperties": False,
                "properties": {
                    "claimed_result": {"enum": report["claimed_results"]},
                    "kind": {"const": "co_report"},
                    "option_id": {"const": "action:4"},
                    "report_kind": {"enum": report["report_kinds"]},
                    "target_player_id": {"enum": report["valid_targets"]},
                },
                "required": [
                    "kind",
                    "option_id",
                    "report_kind",
                    "target_player_id",
                    "claimed_result",
                ],
                "type": "object",
            }
        )
        messages = (
            base.messages[0],
            LLMMessage(
                "user",
                json.dumps(
                    canonical,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )
        encoded = json.dumps(
            {
                "messages": [
                    {"content": item.content, "role": item.role} for item in messages
                ],
                "output_schema": schema,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        projection = PromptProjection(
            messages=messages,
            decision_schema=schema,
            canonical_input=canonical,
            prompt_bytes=len(encoded),
            prompt_sha256=hashlib.sha256(encoded).hexdigest(),
            included_history_records=base.included_history_records,
            omitted_history_records=base.omitted_history_records,
        )
        decision = parse_llm_decision(
            '{"kind":"co_report","option_id":"action:4","report_kind":"inspect_result","target_player_id":"p2","claimed_result":"result:wolf"}',
            projection=projection,
        )
        self.assertEqual(
            decision,
            CoReportDecision("action:4", "inspect_result", "p2", "result:wolf"),
        )


def _prompt_bytes(projection: object) -> bytes:
    return json.dumps(
        {
            "messages": [
                {"role": item.role, "content": item.content}
                for item in projection.messages
            ],
            "output_schema": _plain(projection.decision_schema),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _plain(value: object) -> object:
    from collections.abc import Mapping

    if isinstance(value, Mapping):
        return {key: _plain(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_plain(child) for child in value]
    return value


class ScriptedBackend:
    def __init__(self, outputs: list[str | BaseException]) -> None:
        self.outputs = list(outputs)
        self.requests = []
        self.identity = BackendIdentity(
            "fake", "http://127.0.0.1:1", "/v1/chat/completions", "test", "0" * 64
        )

    async def generate(self, request: object) -> StructuredGenerationResponse:
        self.requests.append(request)
        value = self.outputs.pop(0)
        if isinstance(value, BaseException):
            raise value
        return StructuredGenerationResponse(
            request.request_id, value, "test", "stop", LLMUsage(3, 2)
        )

    async def aclose(self) -> None:
        pass


class FirstCallBlockingBackend(ScriptedBackend):
    def __init__(self, outputs: list[str]) -> None:
        super().__init__(outputs)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.active = 0
        self.max_active = 0

    async def generate(self, request: object) -> StructuredGenerationResponse:
        ordinal = len(self.requests)
        self.requests.append(request)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if ordinal == 0:
                self.entered.set()
                await self.release.wait()
            value = self.outputs.pop(0)
            return StructuredGenerationResponse(
                request.request_id, value, "test", "stop", LLMUsage()
            )
        finally:
            self.active -= 1


class MemoryAudit:
    def __init__(self, error: BaseException | None = None) -> None:
        self.records = []
        self.error = error

    async def start(self) -> None:
        pass

    async def write(self, record: object) -> AuditWriteAck:
        if self.error is not None:
            raise self.error
        self.records.append(record)
        return AuditWriteAck(len(self.records))

    async def aclose(self) -> None:
        pass


class ControllerWorld:
    def __init__(self, action: object | tuple[object, ...]) -> None:
        self.actions = action if isinstance(action, tuple) else (action,)
        self.value = WorldSnapshot(
            version=1,
            freshness=Freshness.CURRENT,
            is_caught_up=True,
            last_applied_seq=8,
            phase=PhaseView("day", 1, 999),
            self_view=SelfView("p1", "role:villager"),
        )
        self.update = asyncio.Event()
        self.deadline = CurrentPhaseDeadline(1, "day", 1, 1, 2, 999999.0)

    def snapshot(self) -> WorldSnapshot:
        return self.value

    def current_actions(self) -> CurrentActionsView:
        return CurrentActionsView(
            self.value.version,
            self.value.last_applied_seq,
            self.value.last_applied_seq,
            True,
            self.actions,
        )

    def history(self) -> HistoryView:
        return HistoryView((), True, self.value.history_retention)

    def co_for_day(self, _day: int) -> CoView:
        return CoView((), (), True, self.value.history_retention)

    def ability_results(self) -> AbilityResultView:
        return AbilityResultView((), True, self.value.history_retention)

    def transport_observations(self) -> TransportObservationView:
        return TransportObservationView(1, None, None, False, (), self.deadline)

    async def wait_for_update(self, after_version: int) -> WorldSnapshot:
        while self.value.version <= after_version:
            await self.update.wait()
        return self.value

    def advance(self, *, actions: tuple[object, ...] | None = None) -> None:
        self.value = replace(self.value, version=self.value.version + 1)
        if actions is not None:
            self.actions = actions
        event = self.update
        self.update = asyncio.Event()
        event.set()


class ControllerSender:
    def __init__(self, audit: MemoryAudit) -> None:
        self.audit = audit
        self.calls = []

    async def _send(self, name: str, *args: object) -> SendReceipt:
        if not self.audit.records:
            raise AssertionError("dispatch preceded durable audit")
        self.calls.append((name, args))
        return SendReceipt("send-1", 1)

    async def send_chat(self, *args: object) -> SendReceipt:
        return await self._send("chat", *args)

    async def send_vote(self, *args: object) -> SendReceipt:
        return await self._send("vote", *args)

    async def send_ability(self, *args: object) -> SendReceipt:
        return await self._send("ability", *args)

    async def send_co_declare(self, *args: object) -> SendReceipt:
        return await self._send("co_declare", *args)

    async def send_co_report(self, *args: object) -> SendReceipt:
        return await self._send("co_report", *args)


class LLMBrainTests(unittest.IsolatedAsyncioTestCase):
    def make_brain(self, backend: ScriptedBackend, audit: MemoryAudit, **kwargs: object) -> LLMBrain:
        return LLMBrain(
            backend=backend,
            audit=audit,
            identity=LLMClientIdentity("game-1", "p1"),
            request_id_factory=lambda: "request-1",
            utc_clock=lambda: datetime(2026, 9, 11, tzinfo=timezone.utc),
            **kwargs,
        )

    async def test_valid_action_returns_only_after_durable_audit(self) -> None:
        backend = ScriptedBackend([
            '{"kind":"vote","option_id":"action:1","target_player_id":"p2"}'
        ])
        audit = MemoryAudit()
        brain = self.make_brain(backend, audit)
        decision = await brain.decide(brain_input())
        self.assertEqual(decision, VoteDecision("action:1", "p2"))
        self.assertEqual([record.status for record in audit.records], [AiAuditStatus.DECISION])
        self.assertEqual(audit.records[0].decision.vote_target_player_id, "p2")
        self.assert_snapshot(
            brain,
            backend_calls=1,
            decisions=1,
            status=LLMInvocationStatus.DECISION,
            error=None,
        )

    async def test_direct_controller_and_shared_arbiter_dispatch_typed_chat(self) -> None:
        action = ChatAction(1, 2, "day", 1, "chat", "public")
        world = ControllerWorld(action)
        audit = MemoryAudit()
        brain = self.make_brain(
            ScriptedBackend([
                '{"kind":"chat","option_id":"action:0","message":"hello"}'
            ]),
            audit,
        )
        sender = ControllerSender(audit)
        controller = BrainController(world=world, sender=sender, brain=brain)
        now = asyncio.get_running_loop().time()
        world.deadline = CurrentPhaseDeadline(1, "day", 1, 1, 2, now + 5)
        arbiter = BrainInvocationArbiter(controller=controller)
        result = await arbiter.invoke(
            owner="reaction_chat",
            priority=BrainInvocationPriority.REACTION,
            allowed_handles=(action,),
            timeout_seconds=2,
            dispatch_deadline=DispatchDeadline(1, "day", 1, 1, 2, now + 5),
        )
        self.assertEqual(result.outcome.status, DecisionStatus.SENT)
        self.assertEqual(sender.calls, [("chat", (action, "hello"))])
        self.assertEqual(result.dispatched_decision, ChatDecision("action:0", "hello"))
        await arbiter.stop()

    async def test_direct_production_composition_dispatches_vote_ability_and_co(self) -> None:
        common = (1, 2, "day", 1)
        vectors = (
            (
                VoteAction(*common, "vote", ("p2",), 1, False),
                '{"kind":"vote","option_id":"action:0","target_player_id":"p2"}',
                "vote",
                "vote_ability",
                BrainInvocationPriority.RESERVATION_ACTION,
            ),
            (
                AbilityAction(*common, "ability", "inspect", "look", ("p2",), 1, 1),
                '{"kind":"ability","option_id":"action:0","target_player_ids":["p2"]}',
                "ability",
                "vote_ability",
                BrainInvocationPriority.RESERVATION_ACTION,
            ),
            (
                CoDeclareAction(*common, "co_declare", ("role:seer",)),
                '{"kind":"co_declare","option_id":"action:0","claimed_role_id":"role:seer","comment":"claim"}',
                "co_declare",
                "reaction_chat",
                BrainInvocationPriority.REACTION,
            ),
        )
        for action, output, expected_send, owner, priority in vectors:
            with self.subTest(expected_send=expected_send):
                world = ControllerWorld(action)
                audit = MemoryAudit()
                sender = ControllerSender(audit)
                brain = self.make_brain(ScriptedBackend([output]), audit)
                controller = BrainController(world=world, sender=sender, brain=brain)
                arbiter = BrainInvocationArbiter(controller=controller)
                now = asyncio.get_running_loop().time()
                world.deadline = CurrentPhaseDeadline(1, "day", 1, 1, 2, now + 5)
                result = await arbiter.invoke(
                    owner=owner,
                    priority=priority,
                    allowed_handles=(action,),
                    timeout_seconds=2,
                    dispatch_deadline=DispatchDeadline(1, "day", 1, 1, 2, now + 5),
                )
                self.assertEqual(result.outcome.status, DecisionStatus.SENT)
                self.assertEqual(sender.calls[0][0], expected_send)
                self.assertEqual(len(audit.records), 1)
                await arbiter.stop()

    async def test_audit_failure_through_controller_is_explicit_zero_send(self) -> None:
        action = ChatAction(1, 2, "day", 1, "chat", "public")
        world = ControllerWorld(action)
        audit = MemoryAudit(AiAuditError(AiAuditErrorCode.WRITE_FAILED))
        sender = ControllerSender(audit)
        brain = self.make_brain(
            ScriptedBackend([
                '{"kind":"chat","option_id":"action:0","message":"blocked"}'
            ]),
            audit,
        )
        controller = BrainController(world=world, sender=sender, brain=brain)
        request = controller.capture_input(allowed_handles=(action,))
        self.assertIsNotNone(request)
        outcome = await controller.decide_and_send(request, timeout_seconds=2)
        self.assertEqual(outcome.status, DecisionStatus.BRAIN_FAILED)
        self.assertEqual(outcome.error_type, "LLMInvocationError")
        self.assertEqual(sender.calls, [])
        self.assertEqual(brain.snapshot().last_status, LLMInvocationStatus.AUDIT_FAILED)
        await controller.stop()

    async def test_fabricated_choice_and_expired_deadline_are_zero_send(self) -> None:
        action = ChatAction(1, 2, "day", 1, "chat", "public")
        for outputs, expired, expected in (
            ([
                '{"kind":"chat","option_id":"fabricated","message":"x"}',
                '{"kind":"chat","option_id":"fabricated","message":"x"}',
            ], False, DecisionStatus.BRAIN_FAILED),
            (['{"kind":"chat","option_id":"action:0","message":"late"}'], True, DecisionStatus.DEADLINE_SUPPRESSED),
        ):
            with self.subTest(expired=expired):
                world = ControllerWorld(action)
                audit = MemoryAudit()
                sender = ControllerSender(audit)
                backend = ScriptedBackend(outputs)
                brain = self.make_brain(backend, audit)
                controller = BrainController(world=world, sender=sender, brain=brain)
                arbiter = BrainInvocationArbiter(controller=controller)
                now = asyncio.get_running_loop().time()
                end = now - 0.01 if expired else now + 5
                if not expired:
                    world.deadline = CurrentPhaseDeadline(1, "day", 1, 1, 2, end)
                result = await arbiter.invoke(
                    owner="reaction_chat",
                    priority=BrainInvocationPriority.REACTION,
                    allowed_handles=(action,),
                    timeout_seconds=2,
                    dispatch_deadline=DispatchDeadline(1, "day", 1, 1, 2, end),
                )
                self.assertEqual(result.outcome.status, expected)
                self.assertEqual(sender.calls, [])
                self.assertEqual(len(backend.requests), 0 if expired else 2)
                await arbiter.stop()

    async def test_world_and_sibling_progress_while_backend_pending_then_stale_blocks_send(self) -> None:
        action = ChatAction(1, 2, "day", 1, "chat", "public")
        world = ControllerWorld(action)
        audit = MemoryAudit()
        sender = ControllerSender(audit)
        backend = FirstCallBlockingBackend([
            '{"kind":"chat","option_id":"action:0","message":"late"}'
        ])
        brain = self.make_brain(backend, audit)
        controller = BrainController(world=world, sender=sender, brain=brain)
        arbiter = BrainInvocationArbiter(controller=controller)
        now = asyncio.get_running_loop().time()
        world.deadline = CurrentPhaseDeadline(1, "day", 1, 1, 2, now + 5)
        invocation = asyncio.create_task(
            arbiter.invoke(
                owner="reaction_chat",
                priority=BrainInvocationPriority.REACTION,
                allowed_handles=(action,),
                timeout_seconds=2,
                dispatch_deadline=DispatchDeadline(1, "day", 1, 1, 2, now + 5),
            )
        )
        await backend.entered.wait()
        sibling_progress = []

        async def sibling() -> None:
            await asyncio.sleep(0)
            sibling_progress.append("ran")

        sibling_task = asyncio.create_task(sibling())
        world.advance(actions=())
        await sibling_task
        self.assertEqual(world.snapshot().version, 2)
        self.assertEqual(sibling_progress, ["ran"])
        backend.release.set()
        result = await invocation
        self.assertEqual(result.outcome.status, DecisionStatus.STALE)
        self.assertEqual(sender.calls, [])
        self.assertEqual(backend.active, 0)
        await arbiter.stop()

    async def test_reservation_is_next_without_preempting_active_reaction(self) -> None:
        chat = ChatAction(1, 2, "day", 1, "chat", "public")
        vote = VoteAction(1, 2, "day", 1, "vote", ("p2",), 1, False)
        world = ControllerWorld((chat, vote))
        audit = MemoryAudit()
        sender = ControllerSender(audit)
        backend = FirstCallBlockingBackend(
            [
                '{"kind":"chat","option_id":"action:0","message":"first"}',
                '{"kind":"vote","option_id":"action:0","target_player_id":"p2"}',
                '{"kind":"chat","option_id":"action:0","message":"second"}',
            ]
        )
        brain = self.make_brain(backend, audit)
        controller = BrainController(world=world, sender=sender, brain=brain)
        arbiter = BrainInvocationArbiter(controller=controller)
        now = asyncio.get_running_loop().time()
        world.deadline = CurrentPhaseDeadline(1, "day", 1, 1, 2, now + 5)
        deadline = DispatchDeadline(1, "day", 1, 1, 2, now + 5)
        first = asyncio.create_task(
            arbiter.invoke(
                owner="reaction_chat",
                priority=BrainInvocationPriority.REACTION,
                allowed_handles=(chat,),
                timeout_seconds=3,
                dispatch_deadline=deadline,
            )
        )
        await backend.entered.wait()
        queued_reaction = asyncio.create_task(
            arbiter.invoke(
                owner="reaction_chat",
                priority=BrainInvocationPriority.REACTION,
                allowed_handles=(chat,),
                timeout_seconds=3,
                dispatch_deadline=deadline,
            )
        )
        reservation = asyncio.create_task(
            arbiter.invoke(
                owner="vote_ability",
                priority=BrainInvocationPriority.RESERVATION_ACTION,
                allowed_handles=(vote,),
                timeout_seconds=3,
                dispatch_deadline=deadline,
            )
        )
        await asyncio.sleep(0)
        self.assertEqual(len(backend.requests), 1)
        backend.release.set()
        results = await asyncio.gather(first, queued_reaction, reservation)
        self.assertTrue(all(item.outcome.status is DecisionStatus.SENT for item in results))
        self.assertEqual([name for name, _args in sender.calls], ["chat", "vote", "chat"])
        self.assertEqual(backend.max_active, 1)
        self.assertEqual(brain.snapshot().calls, 3)
        await arbiter.stop()

    def assert_snapshot(
        self,
        brain: LLMBrain,
        *,
        backend_calls: int,
        decisions: int = 0,
        explicit_no_decisions: int = 0,
        repair_attempts: int = 0,
        failures: int = 0,
        cancellations: int = 0,
        audit_failures: int = 0,
        status: LLMInvocationStatus,
        error: str | None,
    ) -> None:
        self.assertEqual(
            brain.snapshot(),
            LLMBrainSnapshot(
                active=False,
                calls=1,
                backend_calls=backend_calls,
                decisions=decisions,
                explicit_no_decisions=explicit_no_decisions,
                repair_attempts=repair_attempts,
                failures=failures,
                cancellations=cancellations,
                audit_failures=audit_failures,
                last_status=status,
                last_error_code=error,
            ),
        )

    async def test_prompt_rejection_is_audited_with_exact_terminal_snapshot(self) -> None:
        backend = ScriptedBackend(['{"kind":"none"}'])
        audit = MemoryAudit()
        brain = self.make_brain(
            backend,
            audit,
            config=LLMBrainConfig(max_prompt_bytes=1024),
        )
        with self.assertRaises(LLMInvocationError) as raised:
            await brain.decide(brain_input())
        self.assertEqual(raised.exception.status, LLMInvocationStatus.PROMPT_REJECTED)
        self.assertEqual(backend.requests, [])
        self.assertEqual([record.status for record in audit.records], [AiAuditStatus.PROMPT_REJECTED])
        self.assert_snapshot(
            brain,
            backend_calls=0,
            failures=1,
            status=LLMInvocationStatus.PROMPT_REJECTED,
            error="PROMPT_TOO_LARGE",
        )

    async def test_repair_disabled_output_invalid_has_exact_terminal_snapshot(self) -> None:
        backend = ScriptedBackend(["invalid"])
        audit = MemoryAudit()
        brain = self.make_brain(
            backend,
            audit,
            config=LLMBrainConfig(max_schema_repair_attempts=0),
        )
        with self.assertRaises(LLMInvocationError) as raised:
            await brain.decide(brain_input())
        self.assertEqual(raised.exception.status, LLMInvocationStatus.OUTPUT_INVALID)
        self.assertEqual([record.status for record in audit.records], [AiAuditStatus.OUTPUT_INVALID])
        self.assert_snapshot(
            brain,
            backend_calls=1,
            failures=1,
            status=LLMInvocationStatus.OUTPUT_INVALID,
            error=DecisionValidationCode.JSON_SYNTAX.value,
        )

    async def test_repaired_complete_contract_overflow_prevents_second_backend_call(self) -> None:
        request = brain_input()
        base = project_brain_input(request, config=LLMBrainConfig())
        config = LLMBrainConfig(max_prompt_bytes=base.prompt_bytes)
        backend = ScriptedBackend(["invalid", '{"kind":"none"}'])
        audit = MemoryAudit()
        brain = self.make_brain(backend, audit, config=config)
        with self.assertRaises(LLMInvocationError) as raised:
            await brain.decide(request)
        self.assertEqual(raised.exception.status, LLMInvocationStatus.PROMPT_REJECTED)
        self.assertEqual(len(backend.requests), 1)
        self.assertEqual(
            [record.status for record in audit.records],
            [AiAuditStatus.OUTPUT_INVALID, AiAuditStatus.PROMPT_REJECTED],
        )
        self.assertGreater(audit.records[1].prompt_bytes, base.prompt_bytes)
        self.assert_snapshot(
            brain,
            backend_calls=1,
            repair_attempts=1,
            failures=1,
            status=LLMInvocationStatus.PROMPT_REJECTED,
            error="PROMPT_TOO_LARGE",
        )

    async def test_explicit_none_is_not_a_failure(self) -> None:
        audit = MemoryAudit()
        brain = self.make_brain(ScriptedBackend(['{"kind":"none"}']), audit)
        self.assertEqual(await brain.decide(brain_input()), NoDecision())
        snapshot = brain.snapshot()
        self.assertEqual(snapshot.last_status, LLMInvocationStatus.EXPLICIT_NO_DECISION)
        self.assert_snapshot(
            brain,
            backend_calls=1,
            explicit_no_decisions=1,
            status=LLMInvocationStatus.EXPLICIT_NO_DECISION,
            error=None,
        )

    async def test_one_repair_uses_unchanged_schema_then_succeeds(self) -> None:
        backend = ScriptedBackend([
            "not json",
            '{"kind":"chat","option_id":"action:0","message":"fixed"}',
        ])
        audit = MemoryAudit()
        brain = self.make_brain(backend, audit)
        decision = await brain.decide(brain_input())
        self.assertEqual(decision, ChatDecision("action:0", "fixed"))
        self.assertEqual(len(backend.requests), 2)
        self.assertEqual(backend.requests[0].output_schema, backend.requests[1].output_schema)
        self.assertEqual(
            [record.status for record in audit.records],
            [AiAuditStatus.OUTPUT_INVALID, AiAuditStatus.REPAIR_SUCCEEDED],
        )
        self.assert_snapshot(
            brain,
            backend_calls=2,
            decisions=1,
            repair_attempts=1,
            status=LLMInvocationStatus.REPAIR_SUCCEEDED,
            error=None,
        )

    async def test_two_invalid_outputs_fail_closed(self) -> None:
        audit = MemoryAudit()
        brain = self.make_brain(ScriptedBackend(["bad", "still bad"]), audit)
        with self.assertRaises(LLMInvocationError) as raised:
            await brain.decide(brain_input())
        self.assertEqual(raised.exception.status, LLMInvocationStatus.REPAIR_FAILED)
        self.assertEqual(
            [record.status for record in audit.records],
            [AiAuditStatus.OUTPUT_INVALID, AiAuditStatus.REPAIR_FAILED],
        )
        self.assert_snapshot(
            brain,
            backend_calls=2,
            repair_attempts=1,
            failures=1,
            status=LLMInvocationStatus.REPAIR_FAILED,
            error=DecisionValidationCode.JSON_SYNTAX.value,
        )

    async def test_backend_failure_has_no_repair_and_is_audited(self) -> None:
        backend = ScriptedBackend([
            LLMBackendError(LLMBackendErrorCode.CONNECT_FAILED, retryable=True)
        ])
        audit = MemoryAudit()
        brain = self.make_brain(backend, audit)
        with self.assertRaises(LLMInvocationError):
            await brain.decide(brain_input())
        self.assertEqual(len(backend.requests), 1)
        self.assertEqual(audit.records[0].status, AiAuditStatus.BACKEND_FAILED)
        self.assert_snapshot(
            brain,
            backend_calls=1,
            failures=1,
            status=LLMInvocationStatus.BACKEND_FAILED,
            error=LLMBackendErrorCode.CONNECT_FAILED.value,
        )

    async def test_audit_failure_prevents_decision(self) -> None:
        audit = MemoryAudit(AiAuditError(AiAuditErrorCode.WRITE_FAILED))
        brain = self.make_brain(ScriptedBackend(['{"kind":"none"}']), audit)
        with self.assertRaises(LLMInvocationError) as raised:
            await brain.decide(brain_input())
        self.assertEqual(raised.exception.status, LLMInvocationStatus.AUDIT_FAILED)
        self.assert_snapshot(
            brain,
            backend_calls=1,
            failures=1,
            audit_failures=1,
            status=LLMInvocationStatus.AUDIT_FAILED,
            error=AiAuditErrorCode.WRITE_FAILED.value,
        )

    async def test_concurrent_call_rejected_and_cancellation_propagates(self) -> None:
        entered = asyncio.Event()

        class BlockingBackend(ScriptedBackend):
            async def generate(self, request: object) -> StructuredGenerationResponse:
                self.requests.append(request)
                entered.set()
                await asyncio.Event().wait()
                raise AssertionError

        backend = BlockingBackend([])
        brain = self.make_brain(backend, MemoryAudit())
        task = asyncio.create_task(brain.decide(brain_input()))
        await entered.wait()
        with self.assertRaises(RuntimeError):
            await brain.decide(brain_input())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        self.assert_snapshot(
            brain,
            backend_calls=1,
            cancellations=1,
            status=LLMInvocationStatus.CANCELLED,
            error=LLMInvocationStatus.CANCELLED.value,
        )
        self.assertEqual(len(asyncio.all_tasks()), 1)


if __name__ == "__main__":
    unittest.main()
