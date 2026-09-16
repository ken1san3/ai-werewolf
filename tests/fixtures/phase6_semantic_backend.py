"""Model-free semantic fixture, never used by the public provider CLI."""
from __future__ import annotations
import hashlib
import json
from ai_client.llm.types import BackendIdentity, LLMUsage, StructuredGenerationResponse


def semantic_response(value: dict) -> dict:
    """Respond solely to the authorized projected input and offered options."""
    capture = value["capture"]
    context = value["context"]
    options = value["action_context"]["options"]
    option = options[0] if options else None
    kind = option["action_kind"] if option else "none"
    proposal = {"schema_version": "aiwolf.discussion-proposal.v1",
        "base_revision": capture["base_revision"], "decision_kind": kind,
        "option_id": None if option is None else option["option_id"],
        "speech_act": {"kind": "NONE"}, "reaction": None,
        "assessment_updates": [], "claim_updates": [], "relation_updates": [],
        "strategy_update": None, "co_judgment": None, "pre_vote_reassessment": None}
    decision = {"kind": kind}
    if option:
        decision["option_id"] = option["option_id"]
    peer_records = [event for event in value["memory"]["records"]
        if event["source"]["record_kind"] == "chat" and event["actor_player_ids"]
        and context["player_id"] not in event["actor_player_ids"]
        and event["source"]["visibility"] in {"PUBLIC", "AUTHORIZED_PRIVATE"}]
    if capture["trigger"]["kind"] == "PEER_CHAT":
        proposal["reaction"] = {"trigger": capture["trigger"]["source"],
                                "score": 100, "reason": "DIRECT_QUESTION"}
    if kind == "chat":
        peers = [event for event in peer_records if event["channel_id"] == option["channel"]]
        if peers:
            source = peers[-1]
            # The source text controls a typed stance, independently of its opaque identity.
            stance = "OPPOSE" if "oppose" in (source["text_excerpt"] or "").lower() else "SUPPORT"
            proposal["speech_act"] = {"kind": "ANSWER", "addressee_player_id": source["actor_player_ids"][0],
                "in_reply_to": source["source"], "source_interpretation": "QUESTION",
                "topic": "VOTE", "stance": stance, "evidence": [source["source"]]}
            decision["message"] = f"I {stance.lower()} that proposal; let us compare the evidence."
        else:
            peer = next(player for player in capture["current_player_ids"] if player != context["player_id"])
            proposal["speech_act"] = {"kind": "QUESTION", "addressee_player_id": peer,
                "subject_player_id": None, "topic": "VOTE", "source": None}
            decision["message"] = f"{peer}, which evidence should guide our vote?"
    elif kind == "vote":
        targets = list(option["valid_targets"])
        public_peers = [event for event in peer_records if event["source"]["visibility"] == "PUBLIC"]
        source = public_peers[-1] if public_peers else None
        if source and "oppose" in (source["text_excerpt"] or "").lower():
            targets.reverse()
        chosen = targets[0] if targets else None
        decision["target_player_id"] = chosen
        proposal["pre_vote_reassessment"] = {"option_id": option["option_id"],
            "ranked_target_player_ids": targets, "preferred_target_player_id": chosen,
            "evidence": [] if source is None else [source["source"]]}
    elif kind == "ability":
        decision["target_player_ids"] = option["valid_targets"][:option["target_count"]]
    elif kind == "co_declare":
        claim = option["claimed_role_ids"][0]
        decision.update(claimed_role_id=claim, comment="Please compare my claim with the public evidence.")
        proposal["co_judgment"] = {"decision": "DECLARE", "selected_option_id": option["option_id"], "claimed_role_id": claim}
    elif kind != "none":
        raise ValueError("unsupported fixture action")
    return {"decision": decision, "discussion": proposal}


class Phase6SemanticBackend:
    identity = BackendIdentity("phase6-deterministic-fixture", "fixture://local", "/semantic", "fixture-semantic-v1", hashlib.sha256(b"phase6-semantic-v1").hexdigest())

    async def generate(self, request):
        value = json.loads(request.messages[1].content)
        text = json.dumps(semantic_response(value), separators=(",", ":"))
        return StructuredGenerationResponse(request.request_id, text, self.identity.model, "stop", LLMUsage(None, None))

    async def aclose(self):
        return None
