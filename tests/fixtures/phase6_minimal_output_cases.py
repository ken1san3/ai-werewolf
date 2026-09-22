"""Public invented cases, independent of old prompts, outputs and annotations."""
from dataclasses import dataclass
from copy import deepcopy

from scripts.phase6_minimal_output_probe import HostBinding, VERSION, bind


def ref(order=1, kind="chat", visibility="PUBLIC"):
    return {"record_kind": kind, "order": order, "visibility": visibility}


def authority(case_id="P01", trigger="INITIAL_CHAT", *, abstain=False):
    records = [
        {"ref": ref(1), "actor_player_ids": ["p-b"], "channel_id": "public"},
        {"ref": ref(2), "actor_player_ids": ["p-c"], "channel_id": "public"},
        {"ref": ref(3, "ability_result", "AUTHORIZED_PRIVATE"), "actor_player_ids": ["p-a"], "channel_id": None},
        {"ref": ref(4, "co_declaration"), "actor_player_ids": ["p-b"], "channel_id": None},
    ]
    options = {
        "INITIAL_CHAT": {"action_kind": "chat", "option_id": "chat:0", "channel": "public"},
        "PEER_CHAT": {"action_kind": "chat", "option_id": "chat:0", "channel": "public"},
        "CO_OPPORTUNITY": {"action_kind": "co_declare", "option_id": "co:0", "claimed_role_ids": ["claim-a", "claim-b"]},
        "PRE_VOTE": {"action_kind": "vote", "option_id": "vote:0", "valid_targets": ["p-b"], "allows_abstain": abstain},
        "ABILITY": {"action_kind": "ability", "option_id": "ability:0", "valid_targets": ["p-b"], "target_count": 1},
    }
    return {
        "case_id": case_id, "trigger": trigger, "actor_player_id": "p-a",
        "current_player_ids": ["p-a", "p-b", "p-c"],
        "offered_options": [options[trigger]], "projected_evidence": deepcopy(records),
        "captured_evidence": deepcopy(records), "reaction_source": ref(1) if trigger == "PEER_CHAT" else None,
        "prior_assessments": [{"subject_player_id": "p-b", "suspicion": 80, "credibility": 20,
                                "prior_evidence_identities": [["chat", 1]]}],
        "base_revision": 2, "context_sha256": "a" * 64,
        "max_text": 200, "max_text_utf8_bytes": 600, "max_candidate_utf8_bytes": 16384,
        "requires_private_update": False,
    }


def _candidate(decision, speech, *, text=None, detail=None, grounding=()):
    return {"schema_version": VERSION, "decision": decision, "speech_act": speech,
            "grounding": [{"purpose": purpose, "ref": value} for purpose, value in grounding],
            "utterance": text, "trigger_detail": detail}


def _claim(evidence=()):
    return {"kind": "CLAIM", "subject_player_id": "p-b", "topic": "EVENT",
            "stance": "UNCERTAIN", "evidence": list(evidence)}


def _reaction():
    return {"trigger": ref(1), "score": 50, "reason": "DIRECT_QUESTION"}


@dataclass(frozen=True)
class PublicCase:
    case_id: str
    binding: HostBinding
    candidate: dict
    rubric_ids: tuple[str, ...]


def positive_cases() -> tuple[PublicCase, ...]:
    """Thirteen manual contracts. No test or model runs at module import."""
    none = {"kind": "NONE"}
    chat = {"kind": "chat", "option_id": "chat:0"}
    values = [
        ("INITIAL_CHAT", _candidate({"kind": "none"}, none), ("ACT_TEXT",)),
        ("INITIAL_CHAT", _candidate(chat, _claim([ref(1)]), text="That claim needs evidence.",
            grounding=(("UTTERANCE", ref(1)),)), ("GROUND_SUPPORT",)),
        ("PEER_CHAT", _candidate(chat, {"kind": "QUESTION", "addressee_player_id": "p-b",
            "subject_player_id": None, "topic": "EVENT", "source": None},
            text="Why do you believe that?", detail=_reaction(),
            grounding=(("REACTION", ref(1)),)), ("ACT_TEXT",)),
        ("PEER_CHAT", _candidate(chat, {"kind": "ANSWER", "addressee_player_id": "p-b",
            "in_reply_to": ref(1), "source_interpretation": "QUESTION", "topic": "EVENT",
            "stance": "UNCERTAIN", "evidence": [ref(2)]},
            text="I am uncertain because the two accounts differ.", detail=_reaction(),
            grounding=(("UTTERANCE", ref(1)), ("UTTERANCE", ref(2)), ("REACTION", ref(1)))),
            ("ACT_TEXT", "QUESTION_RESPONSE", "GROUND_SUPPORT", "PRIVATE_DISCLOSURE")),
        ("PEER_CHAT", _candidate(chat, {"kind": "REBUTTAL", "addressee_player_id": "p-b",
            "in_reply_to": ref(1), "source_interpretation": "CLAIM", "topic": "EVENT",
            "stance": "OPPOSE", "evidence": [ref(2)]},
            text="I disagree; the public account contradicts that premise.", detail=_reaction(),
            grounding=(("UTTERANCE", ref(1)), ("UTTERANCE", ref(2)), ("REACTION", ref(1)))),
            ("ACT_TEXT", "GROUND_SUPPORT")),
        ("PEER_CHAT", _candidate(chat, {"kind": "OPINION_CHANGE", "subject_player_id": "p-b",
            "dimension": "SUSPICION", "prior": 80, "current": 30, "causes": [ref(2)]},
            text="The new account lowers my suspicion.", detail=_reaction(),
            grounding=(("OPINION_CURRENT", ref(2)), ("REACTION", ref(1)))), ("ACT_TEXT", "GROUND_SUPPORT")),
        ("PEER_CHAT", _candidate(chat, {"kind": "RELATION_HYPOTHESIS", "source_player_id": "p-b",
            "target_player_id": "p-c", "relation": "SUPPORTS", "confidence": 50, "evidence": [ref(2)]},
            text="Their arguments seem to support each other.", detail=_reaction(),
            grounding=(("UTTERANCE", ref(2)), ("REACTION", ref(1)))), ("GROUND_SUPPORT",)),
        ("CO_OPPORTUNITY", _candidate({"kind": "none"}, none,
            detail={"decision": "SILENCE", "selected_option_id": None, "claimed_role_id": None}), ("CO_STRATEGY",)),
        ("CO_OPPORTUNITY", _candidate({"kind": "co_declare", "option_id": "co:0", "claimed_role_id": "claim-a"},
            _claim(), text="This is my public claim.",
            detail={"decision": "DECLARE", "selected_option_id": "co:0", "claimed_role_id": "claim-a"}),
            ("CO_STRATEGY", "PRIVATE_DISCLOSURE")),
        ("PRE_VOTE", _candidate({"kind": "vote", "option_id": "vote:0", "target_player_id": "p-b"},
            _claim([ref(1)]), detail={"option_id": "vote:0", "ranked_target_player_ids": ["p-b"],
            "preferred_target_player_id": "p-b", "evidence": [ref(2)]},
            grounding=(("UTTERANCE", ref(1)), ("PRE_VOTE", ref(2)))), ("GROUND_SUPPORT",)),
        ("PRE_VOTE", _candidate({"kind": "none"}, none, detail={"option_id": "vote:0",
            "ranked_target_player_ids": [], "preferred_target_player_id": None, "evidence": []}), ("ACT_TEXT",)),
        ("ABILITY", _candidate({"kind": "ability", "option_id": "ability:0", "target_player_ids": ["p-b"]}, none), ()),
        ("ABILITY", _candidate({"kind": "none"}, none), ()),
    ]
    return tuple(PublicCase(f"P{i:02}", bind(authority(f"P{i:02}", trigger, abstain=i == 11)),
                            deepcopy(candidate), rubrics)
                 for i, (trigger, candidate, rubrics) in enumerate(values, 1))


def public_32_metadata() -> list[dict]:
    """Static public case IDs only. Unknown update needs remain null, never false."""
    rows = []
    for group in range(1, 17):
        trigger = ("INITIAL_CHAT" if group in (4, 14) else "PRE_VOTE" if group == 13
                   else "CO_OPPORTUNITY" if group == 15 else "PEER_CHAT")
        for variant in (1, 2):
            rows.append({"case_id": f"G{group:02}-{variant}", "trigger": trigger,
                         "requires_private_update": None})
    return rows


# Human rubric only: these are neither model outputs nor automatic expectations.
SEMANTIC_RUBRICS = {
    "ACT_TEXT": "発話が実際に行う質問・回答・反論・意見変更とkindが一致するか。非NONE率だけで評価しない。",
    "QUESTION_RESPONSE": "相手が尋ねた内容へ答えているか。真だが無関係な情報への話題変更を区別する。",
    "GROUND_SUPPORT": "参照が存在するだけでなく本文を支えるか。存在、真偽、関連性、含意を分ける。",
    "PRIVATE_DISCLOSURE": "知っている情報を公開する戦略的理由があるか。真の秘密自白と正当な騙り・否定・仮定を分ける。",
    "CO_STRATEGY": "COまたは保留はLLMの戦略判断。宣言が受理可能でも内容を客観的truthとみなさない。",
}
