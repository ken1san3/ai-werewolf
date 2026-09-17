"""No-provider A-I contract regressions with receiver-owned synthetic facts."""
from dataclasses import replace
import copy
import json

import jsonschema
import pytest

from tests.test_phase6_memory_projection import _bound, _request, _retention, _chats
from ai_client.brain import BrainActionContext, BrainActionOption, ChatDecision
from ai_client.brain.controller import _invalid_or_repeated_self_text
from ai_client.discussion.context import canonical_json_bytes
from ai_client.discussion.projection import _repair_reservation, discussion_output_schema
from ai_client.discussion.state import DiscussionStateStore, DiscussionViews
from ai_client.llm.prompt import project_brain_input, PromptProjectionError, build_repair_projection
from ai_client.llm.decision import parse_llm_output, DecisionValidationError, _validate_generated_text
from ai_client.llm.types import LLMBrainConfig, DiscussionChatConfig, DecisionValidationCode
from ai_client.network import VoteAction, AbilityAction, CoDeclareAction
from ai_client.world import (AbilityResultRecord, AbilityResultView, CoDeclarationRecord,
    CoReportRecord, CoView, HistoryView, PlayerView, DeathView, TransportObservationView)

CONFIG = LLMBrainConfig(short_chat=DiscussionChatConfig())


def request_with(*, results=(), claims=(), chats=(), dead=False, trigger="INITIAL_CHAT", options=None):
    request = _request(chats, trigger_order=chats[-1].order if trigger == "PEER_CHAT" else None)
    records = (*chats, *claims, *results)
    retention = _retention(records)
    history = HistoryView(records, True, retention)
    co = CoView(tuple(r for r in claims if isinstance(r, CoDeclarationRecord)),
                tuple(r for r in claims if isinstance(r, CoReportRecord)), True, retention)
    ability = AbilityResultView(results, True, retention)
    snapshot = replace(request.snapshot, history_retention=retention)
    if dead:
        death = DeathView("opaque-peer", 1, "executed")
        snapshot = replace(snapshot, players=(PlayerView("opaque-peer", "peer", False, death),
                           PlayerView("opaque-self", "self")), alive_player_ids=("opaque-self",), deaths=(death,))
    capture = DiscussionStateStore(_bound()).capture(
        DiscussionViews(snapshot, history, co, ability, TransportObservationView(1, None, None, False, (), None)),
        replace(request.discussion.trigger, kind=trigger,
                owner="vote_ability" if trigger in ("PRE_VOTE", "ABILITY") else "reaction_chat"))
    action_context = request.action_context if options is None else replace(request.action_context, options=options)
    return replace(request, snapshot=snapshot, history=history, co=co, ability_results=ability,
                   discussion=capture, action_context=action_context)


def payload(projection, kind="chat", option="action:0", text="Which claim explains your vote?"):
    return {"decision": {"kind": kind, "option_id": option, "message": text} if kind == "chat" else {"kind": "none"},
            "discussion": {"schema_version": "aiwolf.discussion-proposal.v1",
                "base_revision": projection.discussion_capture.base_revision,
                "decision_kind": kind, "option_id": option if kind != "none" else None,
                "speech_act": {"kind": "NONE"}, "reaction": None,
                "assessment_updates": [], "claim_updates": [], "relation_updates": [],
                "strategy_update": None, "co_judgment": None, "pre_vote_reassessment": None}}


@pytest.mark.parametrize("event,result", [("inspect_result", "not_wolf"), ("medium_result", "wolf")])
def test_personal_result_value_and_own_role_attribute_are_distinct(event, result):
    record = AbilityResultRecord(3, 1, "opaque-phase", event, "opaque-peer", result)
    projection = project_brain_input(request_with(results=(record,)), config=CONFIG)
    value = projection.canonical_input
    observed = value["grounding"]["ability_results"]["records"]
    assert observed[0]["result_id"] == result
    assert observed[0]["target_player_id"] == "opaque-peer"
    assert value["context"]["inspect_result"] == "opaque-inspect"
    other = project_brain_input(request_with(), config=CONFIG)
    assert not other.canonical_input["grounding"]["ability_results"]["records"]
    assert f'"result_id":"{result}"' not in other.messages[1].content
    assert "NOT observed ability results" in projection.messages[0].content


def test_authoritative_state_and_own_claim_history_without_peer_claim_pollution():
    claims = (CoDeclarationRecord(2, 1, "opaque-phase", "opaque-self", "claim-a", "Earlier claim."),
              CoReportRecord(3, 1, "opaque-phase", "opaque-self", "inspect", "opaque-peer", "not_wolf"),
              CoDeclarationRecord(4, 1, "opaque-phase", "opaque-peer", "claim-b", "Other claim."))
    projection = project_brain_input(request_with(claims=claims, dead=True), config=CONFIG)
    ground = projection.canonical_input["grounding"]
    assert ground["current"]["day"] == 1 and ground["current"]["phase"] == "opaque-phase"
    assert ground["current"]["alive_player_ids"] == ("opaque-self",)
    assert ground["current"]["players"][0]["death"] == {"day": 1, "public_cause": "executed"}
    assert [r["order"] for r in ground["self_co"]["records"]] == [2, 3]
    assert ground["self_co"]["records"][0]["claimed_role_id"] == "claim-a"
    assert ground["self_co"]["records"][1]["claimed_result"] == "not_wolf"


def test_grounding_limits_omissions_and_mandatory_latest_result():
    results = tuple(AbilityResultRecord(i, 1, "opaque-phase", "inspection", "opaque-peer", f"result-{i}") for i in range(1, 11))
    projection = project_brain_input(request_with(results=results), config=CONFIG)
    values = projection.canonical_input["grounding"]["ability_results"]
    assert values["records"][-1]["result_id"] == "result-10"
    assert len(values["records"]) <= 8
    assert values["omitted_count"] == 10 - len(values["records"])
    assert values["complete"] is False
    assert values["omitted_through_order"] >= 2
    base = project_brain_input(request_with(), config=CONFIG)
    bound = base.prompt_bytes + _repair_reservation()[0]
    with pytest.raises(PromptProjectionError, match="PROMPT_TOO_LARGE"):
        project_brain_input(request_with(results=results), config=replace(CONFIG, max_prompt_bytes=bound))


@pytest.mark.parametrize("mutation", ["alive", "long_result", "long_comment"])
def test_invalid_grounding_fails_closed(mutation):
    request = request_with()
    if mutation == "alive":
        request = replace(request, snapshot=replace(request.snapshot, alive_player_ids=()))
    elif mutation == "long_result":
        request = request_with(results=(AbilityResultRecord(3, 1, "opaque-phase", "inspection", "opaque-peer", "x" * 129),))
    else:
        request = request_with(claims=(CoDeclarationRecord(3, 1, "opaque-phase", "opaque-self", "claim", "x" * 201),))
    with pytest.raises(PromptProjectionError, match="PROMPT_INVALID"):
        project_brain_input(request, config=CONFIG)


def test_strategy_and_speech_act_instructions_present_without_fixed_role_policy():
    text = project_brain_input(request_with(), config=CONFIG).messages[0].content
    for phrase in ("Private role", "not public introductions", "grounding.self_co", "asking why = QUESTION",
                   "answering that question = ANSWER", "disputing an accusation = REBUTTAL",
                   "revising suspicion = OPINION_CHANGE", "NONE only", "never hides a response", "deliberately deceive"):
        assert phrase in text
    assert "werewolf" not in text.lower()


def test_smoke_repair_instructions_separate_private_planning_and_response_labels():
    text = project_brain_input(request_with(), config=CONFIG).messages[0].content
    for phrase in (
        "not public introductions", "help opponents eliminate your team", "strategic benefit",
        "SAME conversation", "open with the issue, not your role/status",
        "Repeat claims only with a new reason", "Check speech_act against your text",
        "asking why = QUESTION", "answering that question = ANSWER",
        "disputing an accusation = REBUTTAL", "revising suspicion = OPINION_CHANGE",
        "actual in_reply_to references and prior/current/causes",
        "if unavailable, say something supportable, never invent them",
    ):
        assert phrase in text


COPY = "I am the guard. I need to verify the claims before voting."


@pytest.mark.parametrize("source,candidate,rejected", [
    (COPY, COPY, True), (COPY, COPY.upper(), True), (COPY, "  " + COPY.replace(" ", "  "), True),
    ("I agree.", "I agree.", False), (COPY, "I agree with that claim. " + COPY, False),
    (COPY, COPY.replace("verify", "question"), False)])
def test_cross_player_long_whole_copy_only(source, candidate, rejected):
    chat = replace(_chats(1)[0], message=source)
    request = request_with(chats=(chat,))
    assert _invalid_or_repeated_self_text(request, ChatDecision("action:0", candidate)) is rejected


def test_copy_scope_public_received_and_self_guard_preserved():
    chat = replace(_chats(1)[0], message=COPY)
    decision = ChatDecision("action:0", COPY)
    private = request_with(chats=(replace(chat, channel="opaque-private"),))
    assert not _invalid_or_repeated_self_text(private, decision)
    assert not _invalid_or_repeated_self_text(request_with(), decision)
    own = request_with(chats=(replace(chat, player_id="opaque-self"),))
    assert _invalid_or_repeated_self_text(own, decision)
    # A brief same-player re-mention is allowed when the whole new utterance differs.
    assert not _invalid_or_repeated_self_text(own, ChatDecision("action:0", "I still need evidence."))
    co = CoDeclarationRecord(2, 1, "opaque-phase", "opaque-peer", "claim", COPY)
    assert _invalid_or_repeated_self_text(request_with(claims=(co,)), decision)


@pytest.mark.parametrize("size,punctuation,valid", [(189, False, True), (190, False, False),
                                                    (200, True, True), (200, False, False)])
def test_complete_text_char_boundary_no_mutation(size, punctuation, valid):
    projection = project_brain_input(request_with(), config=CONFIG)
    text = "x" * (size - int(punctuation)) + ("." if punctuation else "")
    if valid:
        parsed = parse_llm_output(json.dumps(payload(projection, text=text)), projection=projection)
        assert parsed.decision.message == text
    else:
        with pytest.raises(DecisionValidationError) as exc:
            parse_llm_output(json.dumps(payload(projection, text=text)), projection=projection)
        assert exc.value.code is DecisionValidationCode.TEXT_BOUND


def test_utf8_near_cap_and_closing_quote_and_repair_preserve_original():
    projection = project_brain_input(request_with(), config=CONFIG)
    with pytest.raises(DecisionValidationError):
        _validate_generated_text("\U0001f600" * 143, projection=projection, char_limit=200)
    text = "x" * 195 + '."'
    _validate_generated_text(text, projection=projection, char_limit=200)
    invalid = json.dumps(payload(projection, text="x" * 200))
    repair = build_repair_projection(projection, validation_code=DecisionValidationCode.TEXT_BOUND,
                                    invalid_output=invalid, config=CONFIG)
    assert repair.messages[:2] == projection.messages
    assert repair.decision_schema == projection.decision_schema
    parsed = parse_llm_output(json.dumps(payload(projection, text="Please explain your vote.")), projection=repair)
    assert parsed.decision.message == "Please explain your vote."


def test_none_null_pair_and_evidence_allowed_values():
    projection = project_brain_input(request_with(chats=_chats(2)), config=CONFIG)
    value = payload(projection, kind="none")
    assert parse_llm_output(json.dumps(value), projection=projection)
    invalid = copy.deepcopy(value)
    invalid["discussion"]["option_id"] = "action:0"
    with pytest.raises(DecisionValidationError):
        parse_llm_output(json.dumps(invalid), projection=projection)
    ground = projection.canonical_input["grounding"]
    assert ground["allowed_evidence_refs"] == tuple(r["source"] for r in projection.canonical_input["memory"]["records"])
    invalid = copy.deepcopy(value)
    invalid["discussion"]["assessment_updates"] = [{"target_player_id": "opaque-peer", "suspicion": 50,
        "credibility": 50, "confidence": 50, "evidence": [{"record_kind": "chat", "order": 999, "visibility": "PUBLIC"}]}]
    with pytest.raises(DecisionValidationError):
        parse_llm_output(json.dumps(invalid), projection=projection)


@pytest.mark.parametrize("trigger", ["INITIAL_CHAT", "PEER_CHAT", "CO_OPPORTUNITY", "PRE_VOTE", "ABILITY"])
def test_trigger_schema_pairs_and_offered_values(trigger):
    common = dict(connection_generation=1, action_generation=1, phase="opaque-phase", day=1)
    handles = {"CO_OPPORTUNITY": CoDeclareAction(**common, type="co_declare", claimed_role_ids=("claim-a",)),
               "PRE_VOTE": VoteAction(**common, type="vote", valid_targets=("opaque-peer",), target_count=1, allows_abstain=False),
               "ABILITY": AbilityAction(**common, type="ability", ability_id="inspect", description=None,
                   valid_targets=("opaque-peer",), target_count=1, uses_remaining=1)}
    options = (BrainActionOption("action:0", handles[trigger]),) if trigger in handles else None
    request = request_with(trigger=trigger, options=options, chats=_chats(1) if trigger == "PEER_CHAT" else ())
    projection = project_brain_input(request, config=CONFIG)
    schema = json.loads(canonical_json_bytes(projection.decision_schema))
    jsonschema.Draft202012Validator.check_schema(schema)
    branches = schema["properties"]["discussion"]["oneOf"]
    assert len(branches) == (1 if trigger == "PRE_VOTE" else 2)
    if trigger != "PRE_VOTE":
        assert branches[0]["properties"]["decision_kind"] == {"const": "none"}
        assert branches[0]["properties"]["option_id"] == {"const": None}
    kind = branches[-1]["properties"]["decision_kind"]["const"]
    expected = (() if trigger == "PRE_VOTE" else ({"kind": "none", "option_id": None},))
    assert projection.canonical_input["grounding"]["allowed_decisions"] == (
        *expected, {"kind": kind, "option_id": "action:0"})
    value = payload(projection, kind="none")
    if trigger == "PEER_CHAT":
        value["discussion"]["reaction"] = {"trigger": json.loads(canonical_json_bytes(request.discussion.trigger.source)), "score": 60, "reason": "DIRECT_MENTION"}
    elif trigger == "CO_OPPORTUNITY":
        value["discussion"]["co_judgment"] = {"decision": "SILENCE", "selected_option_id": None, "claimed_role_id": None}
        assert parse_llm_output(json.dumps(value), projection=projection)
        invalid = copy.deepcopy(value)
        invalid["discussion"]["co_judgment"]["claimed_role_id"] = "claim-a"
        with pytest.raises(DecisionValidationError):
            parse_llm_output(json.dumps(invalid), projection=projection)
    elif trigger == "PRE_VOTE":
        value["decision"] = {"kind": "vote", "option_id": "action:0", "target_player_id": "opaque-peer"}
        value["discussion"].update(decision_kind="vote", option_id="action:0", pre_vote_reassessment={
            "option_id": "action:0", "ranked_target_player_ids": ["opaque-peer"], "preferred_target_player_id": "opaque-peer", "evidence": []})
        assert projection.canonical_input["grounding"]["current"]["vote_candidate_player_ids"] == ("opaque-peer",)
        assert parse_llm_output(json.dumps(value), projection=projection)
        invalid = copy.deepcopy(value)
        invalid["discussion"]["pre_vote_reassessment"]["ranked_target_player_ids"] = ["opaque-self"]
        with pytest.raises(DecisionValidationError):
            parse_llm_output(json.dumps(invalid), projection=projection)
    assert parse_llm_output(json.dumps(value), projection=projection)
    invalid = copy.deepcopy(value)
    invalid["discussion"].update(decision_kind=kind, option_id="not-offered")
    with pytest.raises(DecisionValidationError):
        parse_llm_output(json.dumps(invalid), projection=projection)


@pytest.mark.parametrize("act", ["QUESTION", "ANSWER", "REBUTTAL", "OPINION_CHANGE"])
def test_meaningful_non_none_speech_acts_parse(act):
    from tests.test_phase6_memory_projection import _with_assessment
    request = request_with(chats=_chats(1))
    if act == "OPINION_CHANGE":
        request = _with_assessment(request)
    projection = project_brain_input(request, config=CONFIG)
    source = json.loads(canonical_json_bytes(projection.canonical_input["grounding"]["allowed_evidence_refs"][0]))
    value = payload(projection)
    if act == "QUESTION":
        speech = {"kind": act, "addressee_player_id": "opaque-peer", "subject_player_id": None, "topic": "VOTE", "source": source}
    elif act in ("ANSWER", "REBUTTAL"):
        speech = {"kind": act, "addressee_player_id": "opaque-peer", "in_reply_to": source,
                  "source_interpretation": "QUESTION" if act == "ANSWER" else "CLAIM",
                  "topic": "VOTE", "stance": "OPPOSE", "evidence": []}
        value["decision"]["message"] = "My vote follows the conflicting claims." if act == "ANSWER" else "That claim conflicts with your earlier vote."
    else:
        speech = {"kind": act, "subject_player_id": "opaque-peer", "dimension": "SUSPICION", "prior": 61,
                  "current": 80, "causes": [source]}
        value["decision"]["message"] = "Your new claim makes me more suspicious."
    value["discussion"]["speech_act"] = speech
    assert parse_llm_output(json.dumps(value), projection=projection).proposal.speech_act.kind == act


@pytest.mark.parametrize("kind", ["chat", "co_declare"])
def test_public_copy_aborts_transaction_before_stage_or_send(kind):
    import asyncio
    from tests.test_phase6_discussion_transaction import _controller_case
    from ai_client.brain import CoDeclareDecision, DecisionStatus
    from ai_client.discussion.model import DiscussionTerminalReason
    controller, request, deadline, state, audit, sender = _controller_case(kind)
    result = controller.brain.output
    decision = ChatDecision("action:0", COPY) if kind == "chat" else CoDeclareDecision("action:0", "opaque-claim", COPY)
    controller.brain.output = replace(result, decision=decision)
    peer = CoDeclarationRecord(9, 1, "opaque-phase", "opaque-peer", "claim", COPY)
    request = replace(request, co=CoView((peer,), (), True, request.snapshot.history_retention))
    outcome = asyncio.run(controller.decide_and_send(request, dispatch_deadline=deadline))
    assert outcome.status is DecisionStatus.INVALID_DECISION
    assert state.calls == ["abort"] and not sender.calls
    assert len(audit.records) == 1 and audit.records[0].reason is DiscussionTerminalReason.STAGE_FAILED


def test_trigger_filters_non_trigger_actions_and_wrong_reaction_channel():
    from ai_client.network import ChatAction
    request = request_with(trigger="PEER_CHAT", chats=_chats(1))
    public = request.action_context.options[0]
    private = BrainActionOption("action:1", replace(public.handle, channel="opaque-private"))
    vote = BrainActionOption("action:2", VoteAction(1, 1, "opaque-phase", 1, "vote", ("opaque-peer",), 1, False))
    request = replace(request, action_context=replace(request.action_context, options=(public, private, vote)))
    projection = project_brain_input(request, config=CONFIG)
    assert projection.canonical_input["grounding"]["allowed_decisions"] == (
        {"kind": "none", "option_id": None}, {"kind": "chat", "option_id": "action:0"})
    schema = json.loads(canonical_json_bytes(projection.decision_schema))
    assert len(schema["properties"]["decision"]["oneOf"]) == 2


@pytest.mark.parametrize("trigger", ["INITIAL_CHAT", "PEER_CHAT", "CO_OPPORTUNITY", "PRE_VOTE", "ABILITY"])
def test_maximum_options_schema_bounded_branches_and_prompt_fails_closed(trigger):
    from ai_client.discussion.projection import _option
    common = dict(connection_generation=1, action_generation=1, phase="opaque-phase", day=1)
    base = request_with(trigger=trigger, chats=_chats(1) if trigger == "PEER_CHAT" else ())
    handle = {
        "CO_OPPORTUNITY": CoDeclareAction(**common, type="co_declare", claimed_role_ids=("claim-a",)),
        "PRE_VOTE": VoteAction(**common, type="vote", valid_targets=("opaque-peer",), target_count=1, allows_abstain=False),
        "ABILITY": AbilityAction(**common, type="ability", ability_id="inspect", description=None,
            valid_targets=("opaque-peer",), target_count=1, uses_remaining=1),
    }.get(trigger, base.action_context.options[0].handle)
    options = tuple(BrainActionOption(f"action:{i}", handle) for i in range(63))
    request = replace(base, action_context=replace(base.action_context, options=options))
    schema = discussion_output_schema(options=[_option(option, 512) for option in options],
        capture=request.discussion, max_text=200, player_ids=("opaque-self", "opaque-peer"))
    assert len(schema["properties"]["discussion"]["oneOf"]) == (1 if trigger == "PRE_VOTE" else 2)
    jsonschema.Draft202012Validator.check_schema(schema)
    with pytest.raises(PromptProjectionError, match="PROMPT_TOO_LARGE"):
        project_brain_input(request, config=CONFIG)


@pytest.mark.parametrize("trigger", ["INITIAL_CHAT", "PEER_CHAT", "CO_OPPORTUNITY", "PRE_VOTE", "ABILITY"])
def test_standard_nine_player_roles_fit_mandatory_grounding(trigger):
    from tests.test_phase6_semantic_completion import objects, runner
    from ai_client.discussion.context import validate_discussion_bootstrap
    from ai_client.discussion.model import DiscussionTrigger
    from ai_client.discussion.state import evidence_ref_for_record
    from ai_client.network import ChatAction
    from ai_client.world import SelfView, ChatRecord, PhaseView
    content, preset, game = objects.__wrapped__()
    envelopes = runner._phase6_envelopes(content, preset, game)
    for owner, envelope in envelopes.items():
        peer = next(p for p in game.players if p != owner)
        context = envelope["context_payload"]
        channel = next(c["channel_id"] for c in context["chat_channels"] if c["is_public"])
        chat = ChatRecord(1, 1, "day", channel, peer, "peer", "Which evidence explains your vote?")
        result = AbilityResultRecord(2, 1, "day", "inspect_result", peer, "not_wolf")
        claim = CoDeclarationRecord(3, 1, "day", owner, context["role_id"], "My earlier claim.")
        base = request_with()
        retention = _retention((chat, result, claim))
        snapshot = replace(base.snapshot, players=tuple(PlayerView(p, p) for p in game.players),
            alive_player_ids=tuple(game.players), self_view=SelfView(owner, context["role_id"], ()),
            phase=PhaseView("day", 1), history_retention=retention)
        bound = validate_discussion_bootstrap(envelope, network_game_id="opaque-game", player_id=owner).bind(snapshot)
        history, co, ability = HistoryView((chat, result, claim), True, retention), CoView((claim,), (), True, retention), AbilityResultView((result,), True, retention)
        source = evidence_ref_for_record(chat, bound_context=bound) if trigger == "PEER_CHAT" else None
        capture = DiscussionStateStore(bound).capture(DiscussionViews(snapshot, history, co, ability,
            TransportObservationView(1, None, None, False, (), None)), DiscussionTrigger(
            "vote_ability" if trigger in ("ABILITY", "PRE_VOTE") else "reaction_chat", trigger, 1, "day", 1, 1, 1 if source else 0, source))
        common = dict(connection_generation=1, action_generation=1, day=1, phase="day")
        handle = {"CO_OPPORTUNITY": CoDeclareAction(**common, type="co_declare", claimed_role_ids=tuple(content.roles)),
                  "PRE_VOTE": VoteAction(**common, type="vote", valid_targets=tuple(p for p in game.players if p != owner), target_count=1, allows_abstain=False),
                  "ABILITY": AbilityAction(**common, type="ability", ability_id="inspect", description=None,
                        valid_targets=tuple(p for p in game.players if p != owner), target_count=1, uses_remaining=1)
                  }.get(trigger, ChatAction(**common, type="chat", channel=channel))
        request = replace(base, snapshot=snapshot, history=history, co=co, ability_results=ability, discussion=capture,
                          action_context=BrainActionContext(1, 1, 1, True, (BrainActionOption("action:0", handle),)))
        projection = project_brain_input(request, config=CONFIG)
        assert projection.token_proxy_units + _repair_reservation()[1] <= 8192
        assert projection.canonical_input["grounding"]["ability_results"]["records"][0]["result_id"] == "not_wolf"
        if source:
            assert json.loads(canonical_json_bytes(source)) in projection.canonical_input["grounding"]["allowed_evidence_refs"]


@pytest.mark.parametrize("abstains", [(False,), (True,), (False, True)])
def test_pre_vote_none_schema_allowed_and_semantic_agree(abstains):
    options = tuple(BrainActionOption(f"action:{i}", VoteAction(1, 1, "opaque-phase", 1,
        "vote", ("opaque-peer",), 1, allowed)) for i, allowed in enumerate(abstains))
    projection = project_brain_input(request_with(trigger="PRE_VOTE", options=options), config=CONFIG)
    schema = json.loads(canonical_json_bytes(projection.decision_schema))
    offered_none = {"kind": "none", "option_id": None} in projection.canonical_input["grounding"]["allowed_decisions"]
    assert offered_none is any(abstains)
    for i, allowed in enumerate(abstains):
        value = payload(projection, kind="none")
        value["discussion"]["pre_vote_reassessment"] = {"option_id": f"action:{i}",
            "ranked_target_player_ids": [], "preferred_target_player_id": None, "evidence": []}
        assert jsonschema.Draft202012Validator(schema).is_valid(value) is allowed
        if allowed:
            assert parse_llm_output(json.dumps(value), projection=projection)
        else:
            with pytest.raises(DecisionValidationError):
                parse_llm_output(json.dumps(value), projection=projection)
        # A non-null preference must not make a non-abstaining option legal for none.
        invalid = copy.deepcopy(value)
        invalid["discussion"]["pre_vote_reassessment"]["preferred_target_player_id"] = "opaque-peer"
        assert not jsonschema.Draft202012Validator(schema).is_valid(invalid)
        with pytest.raises(DecisionValidationError):
            parse_llm_output(json.dumps(invalid), projection=projection)
    # A no-option PRE_VOTE has no legal output and must not reach a provider.
    with pytest.raises(PromptProjectionError, match="PROMPT_INVALID"):
        project_brain_input(request_with(trigger="PRE_VOTE", options=()), config=CONFIG)


def test_co_judgment_decision_pair_instruction_is_explicit():
    system = project_brain_input(request_with(), config=CONFIG).messages[0].content
    assert "DECLARE requires co_declare with the same option/claimed_role_id" in system
    assert "SILENCE/DEFER require none" in system


@pytest.mark.parametrize("judgment,decision,valid", [
    ("DECLARE", "co_declare", True), ("SILENCE", "none", True), ("DEFER", "none", True),
    ("DECLARE", "none", False), ("SILENCE", "co_declare", False), ("DEFER", "co_declare", False)])
def test_co_judgment_cross_object_pairs_remain_strict(judgment, decision, valid):
    options = (BrainActionOption("action:0", CoDeclareAction(1, 1, "opaque-phase", 1, "co_declare", ("claim-a", "claim-b"))),)
    projection = project_brain_input(request_with(trigger="CO_OPPORTUNITY", options=options), config=CONFIG)
    value = payload(projection, kind="none")
    if decision == "co_declare":
        value["decision"] = {"kind": "co_declare", "option_id": "action:0", "claimed_role_id": "claim-a", "comment": "My claim matters here."}
        value["discussion"].update(decision_kind="co_declare", option_id="action:0")
    value["discussion"]["co_judgment"] = {"decision": judgment,
        "selected_option_id": "action:0" if judgment == "DECLARE" else None,
        "claimed_role_id": "claim-a" if judgment == "DECLARE" else None}
    if valid:
        assert parse_llm_output(json.dumps(value), projection=projection)
        if judgment == "DECLARE":
            value["discussion"]["co_judgment"]["claimed_role_id"] = "claim-b"
            with pytest.raises(DecisionValidationError):
                parse_llm_output(json.dumps(value), projection=projection)
    else:
        with pytest.raises(DecisionValidationError):
            parse_llm_output(json.dumps(value), projection=projection)
