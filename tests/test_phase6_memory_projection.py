from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import sys

import pytest

from ai_client.brain import BrainActionContext, BrainActionOption, BrainInput
from ai_client.discussion.context import (
    AuthorizedChatChannelContext,
    AuthorizedDiscussionContext,
    BoundDiscussionContext,
    CountParityWinCondition,
    canonical_json_bytes,
    canonical_sha256,
)
from ai_client.discussion.model import (
    ClaimAssessment,
    ClaimVerdict,
    DiscussionCapture,
    DiscussionTrigger,
    EvidenceRecordKind,
    EvidenceRef,
    EvidenceVisibility,
    PlayerAssessment,
)
from ai_client.discussion.projection import (
    DiscussionPromptConfig,
    _repair_message,
    _repair_reservation,
    token_proxy_units,
)
from ai_client.discussion.state import DiscussionStateStore, DiscussionViews
from ai_client.llm.prompt import (
    PromptProjectionError,
    build_repair_projection,
    canonical_prompt_json,
    project_brain_input,
)
from ai_client.llm.types import DecisionValidationCode, LLMBrainConfig, LLMMessage
from ai_client.network import ChatAction
from ai_client.world import (
    AbilityResultView,
    ChatRecord,
    CoView,
    Freshness,
    HistoryRetention,
    HistoryView,
    PhaseView,
    PlayerView,
    SelfView,
    TransportObservationView,
    WorldSnapshot,
)


def _bound() -> BoundDiscussionContext:
    context = AuthorizedDiscussionContext(
        schema_version="aiwolf.discussion-context.v1",
        game_id="opaque-game",
        player_id="opaque-self",
        role_id="opaque-role",
        modifier_ids=(),
        content_manifest_sha256="a" * 64,
        team="opaque-team",
        count_as="opaque-count",
        attack_result="opaque-attack",
        inspect_result="opaque-inspect",
        medium_result="opaque-medium",
        win_conditions=(CountParityWinCondition("count_parity", "opaque-count", "other", "gte"),),
        abilities=(),
        passives=(),
        chat_channels=(
            AuthorizedChatChannelContext("opaque-private", False),
            AuthorizedChatChannelContext("opaque-public", True),
        ),
        knows_teammates=False,
        authorized_known_player_ids=(),
        known_players_complete=False,
    )
    return BoundDiscussionContext("a" * 64, canonical_sha256(context), context)


def _retention(records: tuple[object, ...]) -> HistoryRetention:
    orders = [item.order for item in records]
    return HistoryRetention(
        total_seen=len(records),
        retained_count=len(records),
        retained_bytes=0,
        dropped_count=0,
        dropped_through_order=None,
        first_retained_order=min(orders) if orders else None,
        last_order=max(orders) if orders else None,
        max_history_records=128,
        max_history_bytes=65536,
        complete=True,
    )


def _request(
    records: tuple[object, ...] = (),
    *,
    trigger_order: int | None = None,
) -> BrainInput:
    retention = _retention(records)
    snapshot = WorldSnapshot(
        version=1,
        freshness=Freshness.CURRENT,
        is_caught_up=True,
        last_applied_seq=1,
        players=(PlayerView("opaque-peer", "peer"), PlayerView("opaque-self", "self")),
        alive_player_ids=("opaque-peer", "opaque-self"),
        phase=PhaseView("opaque-phase", 1),
        self_view=SelfView("opaque-self", "opaque-role", ()),
        history_retention=retention,
    )
    history = HistoryView(records, True, retention)
    co = CoView((), (), True, retention)
    ability = AbilityResultView((), True, retention)
    views = DiscussionViews(
        snapshot,
        history,
        co,
        ability,
        TransportObservationView(1, None, None, False, (), None),
    )
    source = None
    if trigger_order is not None:
        from ai_client.discussion.state import evidence_ref_for_record

        source = evidence_ref_for_record(
            next(item for item in records if item.order == trigger_order),
            bound_context=_bound(),
        )
    trigger = DiscussionTrigger(
        owner="reaction_chat",
        kind="PEER_CHAT" if source is not None else "INITIAL_CHAT",
        day=1,
        phase="opaque-phase",
        connection_generation=1,
        action_generation=1,
        mapping_order=trigger_order or 0,
        source=source,
    )
    capture = DiscussionStateStore(_bound()).capture(views, trigger)
    action = ChatAction(1, 1, "opaque-phase", 1, "chat", "opaque-public")
    return BrainInput(
        snapshot,
        BrainActionContext(1, 1, 1, True, (BrainActionOption("action:0", action),)),
        history,
        co,
        ability,
        capture,
    )


def _chats(count: int, *, private: bool = False) -> tuple[ChatRecord, ...]:
    channel = "opaque-private" if private else "opaque-public"
    return tuple(
        ChatRecord(index, 1, "opaque-phase", channel, "opaque-peer", "peer", f"message-{index}")
        for index in range(1, count + 1)
    )


def _with_assessment(
    request: BrainInput,
    *,
    count: int = 1,
    long_ids: bool = False,
) -> BrainInput:
    capture = request.discussion
    assert capture is not None
    state = replace(
        capture.state,
        assessments=tuple(
            PlayerAssessment(
                player_id=(
                    "opaque-peer"
                    if count == 1
                    else f"p{index:02d}" + ("x" * 120 if long_ids else "")
                ),
                suspicion=61,
                credibility=39,
                confidence=73,
                evidence=(),
            )
            for index in range(count)
        ),
    )
    material = {
        "schema_version": capture.schema_version,
        "capture_ordinal": capture.capture_ordinal,
        "game_id": capture.game_id,
        "player_id": capture.player_id,
        "context_sha256": capture.context_sha256,
        "state_sha256": canonical_sha256(state),
        "epoch": capture.epoch,
        "base_revision": capture.base_revision,
        "fact_revision": capture.fact_revision,
        "world_version": capture.world_version,
        "last_applied_seq": capture.last_applied_seq,
        "trigger": capture.trigger,
        "context": capture.context,
        "state": state,
        "evidence": state.important_events,
    }
    updated = DiscussionCapture(
        capture_id=canonical_sha256(material),
        **material,
    )
    return replace(request, discussion=updated)


def test_p6b_projection_ceiling_literals_and_lower_only_config() -> None:
    default = DiscussionPromptConfig()
    assert (
        default.max_important_old_records,
        default.max_newest_records,
        default.max_combined_records,
        default.max_important_text_utf8_bytes,
        default.max_important_text_scalars,
        default.max_recent_text_utf8_bytes,
        default.max_recent_text_scalars,
        default.max_memory_section_bytes,
        default.max_context_bytes,
        default.max_state_section_bytes,
        default.max_proposal_bytes,
        default.max_token_proxy_units,
        default.max_prompt_bytes,
    ) == (12, 12, 24, 768, 160, 2048, 512, 16384, 8192, 8192, 16384, 8192, 32768)
    assert DiscussionPromptConfig(max_newest_records=12, max_important_old_records=12, max_combined_records=0)
    with pytest.raises(ValueError):
        DiscussionPromptConfig(max_newest_records=13)


def test_p6b_token_proxy_literal_known_answer_and_exact_formula() -> None:
    assert token_proxy_units("") == 0
    assert token_proxy_units("abcd") == 1
    assert token_proxy_units("abcde") == 2
    assert token_proxy_units("ab_cd-9") == 4
    assert token_proxy_units("A あ") == 5


def test_p6b_projection_mandatory_order_and_complete_budget_rejection() -> None:
    request = _request()
    projection = project_brain_input(request, config=LLMBrainConfig())
    assert tuple(projection.canonical_input) == (
        "schema_version",
        "action_context",
        "capture",
        "context",
        "limits",
        "lifecycle",
        "state",
        "memory",
    )
    assert set(projection.canonical_input) >= {"action_context", "capture", "context", "lifecycle", "state", "memory"}
    with pytest.raises(PromptProjectionError) as failure:
        project_brain_input(
            request,
            config=LLMBrainConfig(),
            discussion_config=DiscussionPromptConfig(max_prompt_bytes=projection.prompt_bytes - 1),
        )
    assert failure.value.code == "PROMPT_TOO_LARGE"


def test_p6b_projection_reserves_trigger_deduplicates_and_restores_chronology() -> None:
    projection = project_brain_input(_request(_chats(15), trigger_order=1), config=LLMBrainConfig())
    records = projection.canonical_input["memory"]["records"]
    orders = [item["source"]["order"] for item in records]
    assert orders == sorted(orders)
    assert orders.count(1) == 1
    assert 1 in orders


def test_p6b_projection_state_and_memory_rank_omission_markers_are_exact() -> None:
    projection = project_brain_input(
        _request(_chats(15)),
        config=LLMBrainConfig(),
        discussion_config=DiscussionPromptConfig(max_newest_records=2, max_important_old_records=1, max_combined_records=2),
    )
    assert projection.canonical_input["memory"]["included_records"] == 2
    assert projection.canonical_input["memory"]["omitted_records"] == 13
    assert projection.canonical_input["state"]["omitted_counts"] == {
        "assessments": 0,
        "claims": 0,
        "relations": 0,
        "strategy": 0,
    }


def test_p6b_projection_one_under_equal_one_over_matrix() -> None:
    # Mandatory-only complete contract: exact fixture KAT and hard byte edge.
    request = _request()
    baseline = project_brain_input(request, config=LLMBrainConfig())
    assert baseline.prompt_bytes == 10_429
    assert baseline.prompt_sha256 == (
        "c061a98161d478594cad9f0ce333ee9e22e2ef46ebfbfaf2accb345c6108e6a2"
    )
    with pytest.raises(PromptProjectionError) as mandatory_under:
        project_brain_input(
            request,
            config=LLMBrainConfig(),
            discussion_config=DiscussionPromptConfig(max_prompt_bytes=10_882),
        )
    assert mandatory_under.value.code == "PROMPT_TOO_LARGE"
    for limit in (10_883, 10_884):
        projected = project_brain_input(
            request,
            config=LLMBrainConfig(),
            discussion_config=DiscussionPromptConfig(max_prompt_bytes=limit),
        )
        assert (projected.prompt_bytes, projected.prompt_sha256) == (
            baseline.prompt_bytes,
            baseline.prompt_sha256,
        )

    # Context is mandatory and never truncated or omitted.
    assert request.discussion is not None
    assert len(canonical_json_bytes(request.discussion.context)) == 715
    with pytest.raises(PromptProjectionError) as context_under:
        project_brain_input(
            request,
            config=LLMBrainConfig(),
            discussion_config=DiscussionPromptConfig(max_context_bytes=714),
        )
    assert context_under.value.code == "PROMPT_TOO_LARGE"
    for limit in (715, 716):
        projected = project_brain_input(
            request,
            config=LLMBrainConfig(),
            discussion_config=DiscussionPromptConfig(max_context_bytes=limit),
        )
        assert projected.canonical_input["context"] == baseline.canonical_input["context"]

    # One real optional state candidate is omitted at S-1 and included at S/S+1.
    state_request = _with_assessment(request)
    state_full = project_brain_input(state_request, config=LLMBrainConfig())
    assert len(canonical_json_bytes(state_full.canonical_input["state"])) == 380
    state_under = project_brain_input(
        state_request,
        config=LLMBrainConfig(),
        discussion_config=DiscussionPromptConfig(max_state_section_bytes=379),
    )
    assert state_under.canonical_input["state"]["assessments"] == ()
    assert state_under.canonical_input["state"]["omitted_counts"] == {
        "strategy": 0,
        "assessments": 1,
        "claims": 0,
        "relations": 0,
    }
    assert state_under.canonical_input["state"]["state_projection_omitted"] is True
    for limit in (380, 381):
        projected = project_brain_input(
            state_request,
            config=LLMBrainConfig(),
            discussion_config=DiscussionPromptConfig(max_state_section_bytes=limit),
        )
        assert len(projected.canonical_input["state"]["assessments"]) == 1
        assert projected.canonical_input["state"]["state_projection_omitted"] is False

    # One real memory row is omitted at M-1 and included at M/M+1.
    memory_request = _request(_chats(1))
    memory_full = project_brain_input(memory_request, config=LLMBrainConfig())
    assert len(canonical_json_bytes(memory_full.canonical_input["memory"])) == 533
    memory_under = project_brain_input(
        memory_request,
        config=LLMBrainConfig(),
        discussion_config=DiscussionPromptConfig(max_memory_section_bytes=532),
    )
    assert memory_under.included_history_records == 0
    assert memory_under.omitted_history_records == 1
    assert memory_under.canonical_input["memory"]["records"] == ()
    assert memory_under.canonical_input["memory"]["memory_byte_exhausted"] is True
    for limit in (533, 534):
        projected = project_brain_input(
            memory_request,
            config=LLMBrainConfig(),
            discussion_config=DiscussionPromptConfig(max_memory_section_bytes=limit),
        )
        assert projected.included_history_records == 1
        assert projected.canonical_input["memory"]["records"][0]["source"]["order"] == 1

    # A reserved trigger is mandatory, precedes optional state selection, and
    # cannot be crowded out at its exact memory-section edge.
    trigger_request = _with_assessment(_request(_chats(15), trigger_order=1))
    trigger_limits = {
        "max_newest_records": 0,
        "max_important_old_records": 0,
        "max_combined_records": 1,
    }
    with pytest.raises(PromptProjectionError) as trigger_under:
        project_brain_input(
            trigger_request,
            config=LLMBrainConfig(),
            discussion_config=DiscussionPromptConfig(
                max_memory_section_bytes=531,
                **trigger_limits,
            ),
        )
    assert trigger_under.value.code == "PROMPT_TOO_LARGE"
    for limit in (532, 533):
        projected = project_brain_input(
            trigger_request,
            config=LLMBrainConfig(),
            discussion_config=DiscussionPromptConfig(
                max_memory_section_bytes=limit,
                **trigger_limits,
            ),
        )
        records = projected.canonical_input["memory"]["records"]
        assert tuple(item["source"]["order"] for item in records) == (1,)
        assert len(projected.canonical_input["state"]["assessments"]) == 1

    # Combined real trigger + state + optional memory pressure.  Complete-byte
    # and proxy budgets independently omit only the optional second record;
    # simultaneous pressure records both exact exhaustion markers.
    combined_request = _with_assessment(_request(_chats(2), trigger_order=1))
    combined = project_brain_input(combined_request, config=LLMBrainConfig())
    assert (combined.prompt_bytes, combined.token_proxy_units) == (11_798, 6_028)
    assert combined.included_history_records == 2
    byte_under = project_brain_input(
        combined_request,
        config=LLMBrainConfig(),
        discussion_config=DiscussionPromptConfig(max_prompt_bytes=12_251),
    )
    assert (byte_under.prompt_bytes, byte_under.token_proxy_units) == (11_358, 5_796)
    assert byte_under.included_history_records == 1
    assert byte_under.canonical_input["memory"]["memory_byte_exhausted"] is True
    assert byte_under.canonical_input["memory"]["token_proxy_exhausted"] is False
    for limit in (12_252, 12_253):
        projected = project_brain_input(
            combined_request,
            config=LLMBrainConfig(),
            discussion_config=DiscussionPromptConfig(max_prompt_bytes=limit),
        )
        assert projected.prompt_sha256 == combined.prompt_sha256
        assert projected.included_history_records == 2

    proxy_under = project_brain_input(
        combined_request,
        config=LLMBrainConfig(),
        discussion_config=DiscussionPromptConfig(max_token_proxy_units=6_216),
    )
    assert (proxy_under.prompt_bytes, proxy_under.token_proxy_units) == (11_358, 5_796)
    assert proxy_under.included_history_records == 1
    assert proxy_under.canonical_input["memory"]["memory_byte_exhausted"] is False
    assert proxy_under.canonical_input["memory"]["token_proxy_exhausted"] is True
    for limit in (6_217, 6_218):
        projected = project_brain_input(
            combined_request,
            config=LLMBrainConfig(),
            discussion_config=DiscussionPromptConfig(max_token_proxy_units=limit),
        )
        assert projected.prompt_sha256 == combined.prompt_sha256
        assert projected.included_history_records == 2
    both_under = project_brain_input(
        combined_request,
        config=LLMBrainConfig(),
        discussion_config=DiscussionPromptConfig(
            max_prompt_bytes=12_251,
            max_token_proxy_units=6_216,
        ),
    )
    assert (both_under.prompt_bytes, both_under.token_proxy_units) == (11_357, 5_795)
    assert both_under.included_history_records == 1
    assert both_under.canonical_input["memory"]["memory_byte_exhausted"] is True
    assert both_under.canonical_input["memory"]["token_proxy_exhausted"] is True
    assert tuple(
        item["source"]["order"]
        for item in both_under.canonical_input["memory"]["records"]
    ) == (1,)

    # Repair uses the immutable original and has its own exact complete edge.
    original_messages = baseline.messages
    repaired = build_repair_projection(
        baseline,
        validation_code=DecisionValidationCode.SCHEMA,
        invalid_output="",
        config=LLMBrainConfig(),
    )
    assert (repaired.prompt_bytes, repaired.token_proxy_units) == (10_835, 5_479)
    assert repaired.prompt_sha256 == (
        "465c15fd4e45b69862786ceade825ad7405b889a1599c8609354f2eee028b3aa"
    )
    with pytest.raises(PromptProjectionError) as repair_under:
        build_repair_projection(
            baseline,
            validation_code=DecisionValidationCode.SCHEMA,
            invalid_output="",
            config=LLMBrainConfig(),
            discussion_config=DiscussionPromptConfig(max_prompt_bytes=10_834),
        )
    assert repair_under.value.code == "PROMPT_TOO_LARGE"
    for limit in (10_835, 10_836):
        candidate = build_repair_projection(
            baseline,
            validation_code=DecisionValidationCode.SCHEMA,
            invalid_output="",
            config=LLMBrainConfig(),
            discussion_config=DiscussionPromptConfig(max_prompt_bytes=limit),
        )
        assert candidate.prompt_sha256 == repaired.prompt_sha256
    assert baseline.messages == original_messages
    assert repaired.messages[:-1] == baseline.messages
    assert repaired.canonical_input == baseline.canonical_input
    assert repaired.decision_schema == baseline.decision_schema

    # The legacy LLM configuration accepts values above 32 KiB, but Phase 6
    # still applies the hard 32-KiB/8192-proxy ceilings to a genuinely large
    # authorized candidate set rather than treating that legacy value as headroom.
    large_records = tuple(
        replace(record, message="x" * 2_048) for record in _chats(24)
    )
    assert sum(len(record.message.encode("utf-8")) for record in large_records) == 49_152
    large_request = _request(large_records)
    hard = project_brain_input(
        large_request,
        config=LLMBrainConfig(max_prompt_bytes=32_768),
    )
    legacy_over = project_brain_input(
        large_request,
        config=LLMBrainConfig(max_prompt_bytes=32_769),
    )
    assert hard.prompt_bytes == legacy_over.prompt_bytes == 16_357
    assert hard.token_proxy_units == legacy_over.token_proxy_units == 7_959
    assert hard.prompt_sha256 == legacy_over.prompt_sha256
    assert hard.included_history_records == legacy_over.included_history_records == 10
    assert hard.omitted_history_records == legacy_over.omitted_history_records == 14
    assert hard.canonical_input["memory"]["token_proxy_exhausted"] is True


def test_p6b_projection_final_recheck_hashes_emitted_contract() -> None:
    projection = project_brain_input(_request(_chats(3)), config=LLMBrainConfig())
    complete = canonical_prompt_json(projection.messages, projection.decision_schema).encode("utf-8")
    assert projection.prompt_bytes == len(complete)
    assert projection.prompt_sha256 == hashlib.sha256(complete).hexdigest()
    assert projection.token_proxy_units == token_proxy_units(complete.decode("utf-8"))


def test_p6b_repair_reuses_immutable_projection_and_suppresses_oversize_second_call() -> None:
    original = project_brain_input(_request(), config=LLMBrainConfig())
    original_messages = original.messages
    repaired = build_repair_projection(
        original,
        validation_code=DecisionValidationCode.SCHEMA,
        invalid_output="x" * 32,
        config=LLMBrainConfig(),
    )
    assert original.messages == original_messages
    assert repaired.canonical_input == original.canonical_input
    assert repaired.messages[:-1] == original.messages
    with pytest.raises(PromptProjectionError):
        from ai_client.discussion.projection import build_discussion_repair_projection

        build_discussion_repair_projection(
            original,
            validation_code=DecisionValidationCode.SCHEMA,
            invalid_output="x",
            llm_config=LLMBrainConfig(),
            config=DiscussionPromptConfig(max_prompt_bytes=original.prompt_bytes),
        )


def test_p6b_saturated_producer_reserves_empty_repair_for_every_validation_code() -> None:
    records = tuple(replace(record, message="x" * 2_048) for record in _chats(24))
    original = project_brain_input(_request(records), config=LLMBrainConfig())
    assert original.token_proxy_units <= 8_192
    assert original.prompt_bytes <= 32_768

    for code in DecisionValidationCode:
        repaired = build_repair_projection(
            original,
            validation_code=code,
            invalid_output="",
            config=LLMBrainConfig(),
        )
        assert repaired.token_proxy_units <= 8_192
        assert repaired.prompt_bytes <= 32_768
        assert repaired.messages[:-1] == original.messages
        assert repaired.decision_schema == original.decision_schema


def test_p6b_final_omitted_count_digit_growth_keeps_repair_reservation() -> None:
    request = _with_assessment(_request(), count=12, long_ids=True)
    capture = request.discussion
    assert capture is not None
    claims = tuple(
        ClaimAssessment(
            claim=EvidenceRef(
                record_kind=EvidenceRecordKind.CHAT,
                order=index + 1,
                visibility=EvidenceVisibility.PUBLIC,
            ),
            speaker_player_id=f"speaker-{index:02d}",
            verdict=ClaimVerdict.UNVERIFIED,
            confidence=50,
            evidence=(),
        )
        for index in range(10)
    )
    state = replace(capture.state, claims=claims)
    material = {
        "schema_version": capture.schema_version,
        "capture_ordinal": capture.capture_ordinal,
        "game_id": capture.game_id,
        "player_id": capture.player_id,
        "context_sha256": capture.context_sha256,
        "state_sha256": canonical_sha256(state),
        "epoch": capture.epoch,
        "base_revision": capture.base_revision,
        "fact_revision": capture.fact_revision,
        "world_version": capture.world_version,
        "last_applied_seq": capture.last_applied_seq,
        "trigger": capture.trigger,
        "context": capture.context,
        "state": state,
        "evidence": state.important_events,
    }
    request = replace(
        request,
        discussion=DiscussionCapture(capture_id=canonical_sha256(material), **material),
    )

    unconstrained = project_brain_input(
        request,
        config=LLMBrainConfig(),
        discussion_config=DiscussionPromptConfig(max_state_section_bytes=300),
    )
    assert unconstrained.canonical_input["state"]["omitted_counts"] == {
        "strategy": 0,
        "assessments": 12,
        "claims": 10,
        "relations": 0,
    }
    exact_limit = unconstrained.prompt_bytes + _repair_reservation()[0]
    with pytest.raises(PromptProjectionError) as one_byte_short:
        project_brain_input(
            request,
            config=LLMBrainConfig(),
            discussion_config=DiscussionPromptConfig(
                max_prompt_bytes=exact_limit - 1,
                max_state_section_bytes=300,
            ),
        )
    assert one_byte_short.value.code == "PROMPT_TOO_LARGE"

    projected = project_brain_input(
        request,
        config=LLMBrainConfig(),
        discussion_config=DiscussionPromptConfig(
            max_prompt_bytes=exact_limit,
            max_state_section_bytes=300,
        ),
    )
    assert projected.canonical_input["state"]["omitted_counts"]["assessments"] == 12
    assert projected.canonical_input["state"]["omitted_counts"]["claims"] == 10
    assert projected.prompt_bytes + _repair_reservation()[0] == exact_limit


def test_p6b_repair_reservation_matches_actual_serializer_at_sys_maxsize() -> None:
    original = project_brain_input(_request(), config=LLMBrainConfig())
    baseline = canonical_prompt_json(original.messages, original.decision_schema)
    byte_deltas: list[int] = []
    proxy_deltas: list[int] = []
    for code in DecisionValidationCode:
        for truncated in (False, True):
            repair = _repair_message(
                validation_code=code.value,
                excerpt="",
                original_scalars=sys.maxsize,
                original_utf8_bytes=sys.maxsize,
                excerpt_truncated=truncated,
                output_sha256="f" * 64,
            )
            complete = canonical_prompt_json(
                original.messages + (LLMMessage(role="user", content=repair),),
                original.decision_schema,
            )
            byte_deltas.append(len(complete.encode("utf-8")) - len(baseline.encode("utf-8")))
            proxy_deltas.append(token_proxy_units(complete) - token_proxy_units(baseline))

    assert _repair_reservation() == (max(byte_deltas), max(proxy_deltas)) == (454, 189)


def test_p6b_nonempty_repair_excerpt_shrinks_at_exact_complete_boundary() -> None:
    original = project_brain_input(_request(), config=LLMBrainConfig())
    full = build_repair_projection(
        original,
        validation_code=DecisionValidationCode.SCHEMA,
        invalid_output="x" * 32,
        config=LLMBrainConfig(),
    )
    exact = build_repair_projection(
        original,
        validation_code=DecisionValidationCode.SCHEMA,
        invalid_output="x" * 32,
        config=LLMBrainConfig(),
        discussion_config=DiscussionPromptConfig(max_prompt_bytes=full.prompt_bytes),
    )
    over = build_repair_projection(
        original,
        validation_code=DecisionValidationCode.SCHEMA,
        invalid_output="x" * 32,
        config=LLMBrainConfig(),
        discussion_config=DiscussionPromptConfig(max_prompt_bytes=full.prompt_bytes + 1),
    )
    under = build_repair_projection(
        original,
        validation_code=DecisionValidationCode.SCHEMA,
        invalid_output="x" * 32,
        config=LLMBrainConfig(),
        discussion_config=DiscussionPromptConfig(max_prompt_bytes=full.prompt_bytes - 1),
    )
    assert exact.prompt_sha256 == over.prompt_sha256 == full.prompt_sha256
    assert under.prompt_bytes <= full.prompt_bytes - 1
    repair = json.loads(under.messages[-1].content)["repair"]
    assert repair["invalid_output_excerpt"]
    assert repair["invalid_output_excerpt_truncated"] is True


def test_p6b_projection_keeps_system_static_and_private_dynamic_data_in_user_json() -> None:
    public = project_brain_input(_request(_chats(1)), config=LLMBrainConfig())
    private = project_brain_input(_request(_chats(1, private=True)), config=LLMBrainConfig())
    assert public.messages[0] == private.messages[0]
    assert "opaque-private" not in private.messages[0].content
    assert "opaque-private" in private.messages[1].content


def test_p6b_lowered_record_limits_select_exact_capture_subset() -> None:
    projection = project_brain_input(
        _request(_chats(20)),
        config=LLMBrainConfig(),
        discussion_config=DiscussionPromptConfig(max_newest_records=3, max_important_old_records=2, max_combined_records=4),
    )
    orders = [item["source"]["order"] for item in projection.canonical_input["memory"]["records"]]
    assert orders == [17, 18, 19, 20]


def test_p6b_all_4225_lower_only_record_configurations_are_independent() -> None:
    # Four deliberately tiny records keep the complete prompt below the
    # independent byte/proxy gates for every count triple, so this exhaustive
    # cross-product measures N/O/U independence rather than budget omission.
    request = _request(_chats(4))
    all_orders = tuple(range(4, 0, -1))
    checked = 0
    for newest_limit in range(13):
        newest = all_orders[:newest_limit]
        remaining = tuple(order for order in all_orders if order not in newest)
        for old_limit in range(13):
            older = remaining[:old_limit]
            priority = newest + older
            for combined_limit in range(25):
                projection = project_brain_input(
                    request,
                    config=LLMBrainConfig(),
                    discussion_config=DiscussionPromptConfig(
                        max_newest_records=newest_limit,
                        max_important_old_records=old_limit,
                        max_combined_records=combined_limit,
                    ),
                )
                actual = tuple(
                    item["source"]["order"]
                    for item in projection.canonical_input["memory"]["records"]
                )
                expected = tuple(sorted(priority[:combined_limit]))
                assert actual == expected, (
                    newest_limit,
                    old_limit,
                    combined_limit,
                )
                assert projection.included_history_records == len(expected)
                assert projection.omitted_history_records == 4 - len(expected)
                checked += 1
    assert checked == 4_225
