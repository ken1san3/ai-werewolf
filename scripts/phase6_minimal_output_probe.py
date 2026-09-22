"""Offline-only minimal-output contract. No provider, dispatch, or state commit.

This is a public-synthetic diagnostic, not a production parser or quality judge.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from types import MappingProxyType
from typing import Mapping

from jsonschema import Draft202012Validator

from ai_client.discussion import model as dm
from ai_client.discussion.projection import (
    _evidence_array_definition, _evidence_schema, _speech_schema,
)

VERSION = "phase6.minimal-output.v0"
TRIGGER_ACTIONS = {
    "INITIAL_CHAT": ("none", "chat"), "PEER_CHAT": ("none", "chat"),
    "CO_OPPORTUNITY": ("none", "co_declare"),
    "PRE_VOTE": ("none", "vote"), "ABILITY": ("none", "ability"),
}
FORBIDDEN = frozenset({"assessment_updates", "claim_updates", "relation_updates",
                       "strategy_update", "private_updates"})
CODES = frozenset({"JSON_INVALID", "SHAPE_INVALID", "VALUE_NOT_OFFERED",
                   "BINDING_INVALID", "TEXT_INVALID", "PRIVATE_UPDATE_FORBIDDEN"})
PURPOSES = ("UTTERANCE", "OPINION_CURRENT", "REACTION", "PRE_VOTE")
HOST_KEYS = frozenset({
    "case_id", "trigger", "actor_player_id", "current_player_ids", "offered_options",
    "projected_evidence", "captured_evidence", "reaction_source", "prior_assessments",
    "base_revision", "context_sha256", "max_text", "max_text_utf8_bytes",
    "max_candidate_utf8_bytes", "requires_private_update",
})
CASE_IDS = tuple(f"G{group:02}-{variant}" for group in range(1, 17) for variant in (1, 2))


class ProbeError(ValueError):
    applicability = "APPLICABILITY_INVALID"

    def __init__(self, code: str):
        if code not in CODES:
            raise ValueError("unknown diagnostic code")
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise ProbeError(code)


def plain(value):
    """Lossless container representation; never repair candidate values."""
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


def canonical_bytes(value) -> bytes:
    return json.dumps(plain(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _strict_json(raw: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result

    def constant(_value):
        raise ValueError("nonfinite")

    def number(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("nonfinite")
        return result

    def unicode_check(value):
        if isinstance(value, str):
            value.encode("utf-8")
        elif isinstance(value, dict):
            for key, item in value.items():
                unicode_check(key)
                unicode_check(item)
        elif isinstance(value, list):
            for item in value:
                unicode_check(item)

    try:
        if type(raw) is not bytes:
            raise ValueError("bytes required")
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                            parse_constant=constant, parse_float=number)
        unicode_check(result)
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail("JSON_INVALID")


def _forbidden_key(value) -> bool:
    if isinstance(value, dict):
        return bool(FORBIDDEN.intersection(value)) or any(_forbidden_key(v) for v in value.values())
    if isinstance(value, list):
        return any(_forbidden_key(v) for v in value)
    return False


@dataclass(frozen=True)
class HostBinding:
    authority: Mapping
    canonical_input_bytes: bytes
    input_sha256: str
    canonical_private_view_bytes: bytes
    captured_private_state_sha256: str

    def __post_init__(self):
        object.__setattr__(self, "authority", _freeze(self.authority))


def bind(authority: Mapping) -> HostBinding:
    """Seal trusted public fixture values. Hashes do not authenticate a sender."""
    data = plain(authority)
    _host_shape(data)
    raw = canonical_bytes(data)
    private = canonical_bytes({"prior_assessments": data["prior_assessments"]})
    return HostBinding(data, raw, sha256(raw), private, sha256(private))


def _closed(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def output_schema(authority: Mapping) -> dict:
    """Close decisions over trusted offers; schema PASS does not judge meaning."""
    host = plain(authority)
    _host_shape(host)
    trigger = host["trigger"]
    ident = {"type": "string", "minLength": 1, "maxLength": dm.MAX_ID_SCALARS}
    ref = {"$ref": "#/$defs/evidence_ref"}
    evidence = {"$ref": "#/$defs/evidence_array"}
    score = {"type": "integer", "minimum": 0, "maximum": 100}
    branches = []
    if trigger != "PRE_VOTE" or any(o["allows_abstain"] for o in host["offered_options"]):
        branches.append(_closed({"kind": {"const": "none"}}))
    for option in host["offered_options"]:
        kind = option["action_kind"]
        properties = {"kind": {"const": kind}, "option_id": {"const": option["option_id"]}}
        if kind in ("vote", "ability"):
            targets = {"enum": option["valid_targets"]} if option["valid_targets"] else False
            if kind == "vote":
                properties["target_player_id"] = targets
            else:
                properties["target_player_ids"] = {
                    "type": "array", "items": targets, "uniqueItems": True,
                    "minItems": option["target_count"], "maxItems": option["target_count"],
                }
        elif kind == "co_declare":
            properties["claimed_role_id"] = {"enum": option["claimed_role_ids"]} if option["claimed_role_ids"] else False
        branches.append(_closed(properties))
    decision = {"oneOf": branches} if branches else False
    details = {
        "INITIAL_CHAT": {"type": "null"}, "ABILITY": {"type": "null"},
        "PEER_CHAT": _closed({"trigger": ref, "score": score,
                              "reason": {"enum": [item.value for item in dm.ReactionReason]}}),
        "CO_OPPORTUNITY": {"oneOf": [
            _closed({"decision": {"enum": ["SILENCE", "DEFER"]},
                     "selected_option_id": {"type": "null"}, "claimed_role_id": {"type": "null"}}),
            _closed({"decision": {"const": "DECLARE"}, "selected_option_id": ident,
                     "claimed_role_id": ident}),
        ]},
        "PRE_VOTE": _closed({"option_id": ident,
            "ranked_target_player_ids": {"type": "array", "items": ident,
                                          "maxItems": dm.MAX_EVENT_PLAYERS, "uniqueItems": True},
            "preferred_target_player_id": {"anyOf": [ident, {"type": "null"}]},
            "evidence": evidence}),
    }
    schema = _closed({"schema_version": {"const": VERSION}, "decision": decision,
        "speech_act": {"$ref": "#/$defs/speech_act"},
        "grounding": {"type": "array", "items": _closed({"purpose": {"enum": list(PURPOSES)}, "ref": ref})},
        "utterance": {"type": ["string", "null"]}, "trigger_detail": details[trigger]})
    schema["$defs"] = {
        "player_id": ident, "evidence_ref": _evidence_schema(),
        "evidence_array": _evidence_array_definition(),
        "evidence_nonempty": _evidence_array_definition(minimum=1),
        "topic": {"enum": [item.value for item in dm.DiscussionTopic]},
        "stance": {"enum": [item.value for item in dm.DiscussionStance]},
        "relation": {"enum": [item.value for item in dm.RelationKind]},
        "speech_act": _speech_schema(),
    }
    return schema


def _ref(value):
    if not isinstance(value, dict) or set(value) != {"record_kind", "order", "visibility"}:
        raise ValueError("reference shape")
    return dm.EvidenceRef(dm.EvidenceRecordKind(value["record_kind"]), value["order"],
                          dm.EvidenceVisibility(value["visibility"]))


def _refs(values):
    return tuple(_ref(value) for value in values)


def _ref_key(value):
    return value["record_kind"], value["order"], value["visibility"]


def _speech(value):
    kind = dm.SpeechActKind(value["kind"])
    if kind is dm.SpeechActKind.NONE:
        return dm.SpeechActNone(kind)
    if kind is dm.SpeechActKind.CLAIM:
        return dm.SpeechActClaim(kind, value["subject_player_id"], dm.DiscussionTopic(value["topic"]),
                                dm.DiscussionStance(value["stance"]), _refs(value["evidence"]))
    if kind is dm.SpeechActKind.QUESTION:
        return dm.SpeechActQuestion(kind, value["addressee_player_id"], value["subject_player_id"],
            dm.DiscussionTopic(value["topic"]), None if value["source"] is None else _ref(value["source"]))
    if kind in (dm.SpeechActKind.ANSWER, dm.SpeechActKind.REBUTTAL):
        cls = dm.SpeechActAnswer if kind is dm.SpeechActKind.ANSWER else dm.SpeechActRebuttal
        return cls(kind, value["addressee_player_id"], _ref(value["in_reply_to"]),
            value["source_interpretation"], dm.DiscussionTopic(value["topic"]),
            dm.DiscussionStance(value["stance"]), _refs(value["evidence"]))
    if kind is dm.SpeechActKind.OPINION_CHANGE:
        return dm.SpeechActOpinionChange(kind, value["subject_player_id"], dm.OpinionDimension(value["dimension"]),
                                        value["prior"], value["current"], _refs(value["causes"]))
    return dm.SpeechActRelationHypothesis(kind, value["source_player_id"], value["target_player_id"],
        dm.RelationKind(value["relation"]), value["confidence"], _refs(value["evidence"]))


def _local_shapes(value, host):
    if not Draft202012Validator(output_schema(host)).is_valid(value):
        _fail("SHAPE_INVALID")
    trigger = host["trigger"]
    try:
        _speech(value["speech_act"])
        for item in value["grounding"]:
            _ref(item["ref"])
        detail = value["trigger_detail"]
        if trigger == "PEER_CHAT":
            dm.ReactionAssessment(_ref(detail["trigger"]), detail["score"], dm.ReactionReason(detail["reason"]))
        elif trigger == "CO_OPPORTUNITY":
            dm.CoJudgment(dm.CoJudgmentDecision(detail["decision"]), detail["selected_option_id"], detail["claimed_role_id"])
        elif trigger == "PRE_VOTE":
            dm.PreVoteReassessment(detail["option_id"], tuple(detail["ranked_target_player_ids"]),
                                    detail["preferred_target_player_id"], _refs(detail["evidence"]))
    except (ValueError, TypeError, KeyError):
        _fail("SHAPE_INVALID")


def _id(value):
    return (type(value) is str and 0 < len(value) <= dm.MAX_ID_SCALARS
            and len(value.encode("utf-8")) <= dm.MAX_ID_UTF8_BYTES)


def _hash(value):
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _host_shape(host):
    try:
        if type(host) is not dict or set(host) != HOST_KEYS or host["trigger"] not in TRIGGER_ACTIONS:
            raise ValueError
        players = host["current_player_ids"]
        if (type(players) is not list or not players or len(players) > dm.MAX_EVENT_PLAYERS
                or any(not _id(p) for p in players) or players != sorted(set(players))
                or host["actor_player_id"] not in players or not _id(host["case_id"])):
            raise ValueError
        if type(host["base_revision"]) is not int or host["base_revision"] < 0 or not _hash(host["context_sha256"]):
            raise ValueError
        if any(type(host[key]) is not int or host[key] <= 0 for key in
               ("max_text", "max_text_utf8_bytes", "max_candidate_utf8_bytes")):
            raise ValueError
        if type(host["requires_private_update"]) is not bool:
            raise ValueError
        options = host["offered_options"]
        if (type(options) is not list or any(type(o) is not dict for o in options)
                or len({o["option_id"] for o in options}) != len(options)):
            raise ValueError
        for option in options:
            kind = option["action_kind"]
            extra = {"chat": {"channel"}, "vote": {"valid_targets", "allows_abstain"},
                     "ability": {"valid_targets", "target_count"}, "co_declare": {"claimed_role_ids"}}[kind]
            if set(option) != {"action_kind", "option_id"} | extra or not _id(option["option_id"]):
                raise ValueError
            if kind not in TRIGGER_ACTIONS[host["trigger"]]:
                raise ValueError
            if kind == "chat" and not _id(option["channel"]):
                raise ValueError
            if kind in ("vote", "ability", "co_declare"):
                values = option["claimed_role_ids"] if kind == "co_declare" else option["valid_targets"]
                if type(values) is not list or len(set(values)) != len(values) or any(not _id(v) for v in values):
                    raise ValueError
                if kind != "co_declare" and any(v not in players for v in values):
                    raise ValueError
            if kind == "vote" and type(option["allows_abstain"]) is not bool:
                raise ValueError
            if kind == "ability" and (type(option["target_count"]) is not int or option["target_count"] < 0):
                raise ValueError
        for field in ("projected_evidence", "captured_evidence"):
            records = host[field]
            if type(records) is not list:
                raise ValueError
            identities = []
            for record in records:
                if type(record) is not dict or set(record) != {"ref", "actor_player_ids", "channel_id"}:
                    raise ValueError
                ref = _ref(record["ref"])
                identities.append(dm.evidence_identity(ref))
                actors = record["actor_player_ids"]
                if type(actors) is not list or len(set(actors)) != len(actors) or any(a not in players for a in actors):
                    raise ValueError
                if ref.record_kind in (dm.EvidenceRecordKind.CHAT, dm.EvidenceRecordKind.CO_DECLARATION, dm.EvidenceRecordKind.CO_REPORT):
                    if len(actors) != 1:
                        raise ValueError
                if ref.record_kind is dm.EvidenceRecordKind.CHAT:
                    if not _id(record["channel_id"]):
                        raise ValueError
                elif record["channel_id"] is not None:
                    raise ValueError
            if len(set(identities)) != len(identities):
                raise ValueError
        source = host["reaction_source"]
        if host["trigger"] == "PEER_CHAT":
            if _ref(source).record_kind is not dm.EvidenceRecordKind.CHAT:
                raise ValueError
        elif source is not None:
            raise ValueError
        priors = host["prior_assessments"]
        if (type(priors) is not list or any(type(p) is not dict for p in priors)
                or len({p["subject_player_id"] for p in priors}) != len(priors)):
            raise ValueError
        for prior in priors:
            if set(prior) != {"subject_player_id", "suspicion", "credibility", "prior_evidence_identities"}:
                raise ValueError
            if prior["subject_player_id"] not in players:
                raise ValueError
            if any(type(prior[key]) is not int or not 0 <= prior[key] <= 100 for key in ("suspicion", "credibility")):
                raise ValueError
            identities = prior["prior_evidence_identities"]
            if type(identities) is not list:
                raise ValueError
            for identity in identities:
                if (type(identity) is not list or len(identity) != 2 or type(identity[1]) is not int
                        or identity[1] < 0 or identity[0] not in {item.value for item in dm.EvidenceRecordKind}):
                    raise ValueError
            if len(set(map(tuple, identities))) != len(identities):
                raise ValueError
    except (ValueError, TypeError, KeyError, UnicodeError):
        _fail("BINDING_INVALID")


def _offered(value, host):
    decision, speech, detail = value["decision"], value["speech_act"], value["trigger_detail"]
    kind, trigger = decision["kind"], host["trigger"]
    if kind not in TRIGGER_ACTIONS[trigger]:
        _fail("VALUE_NOT_OFFERED")
    options = {o["option_id"]: o for o in host["offered_options"]}
    option = None
    if kind != "none":
        option = options.get(decision["option_id"])
        if option is None or option["action_kind"] != kind:
            _fail("VALUE_NOT_OFFERED")
        if kind == "vote" and decision["target_player_id"] not in option["valid_targets"]:
            _fail("VALUE_NOT_OFFERED")
        if kind == "ability":
            targets = decision["target_player_ids"]
            if (len(targets) != option["target_count"] or len(set(targets)) != len(targets)
                    or any(t not in option["valid_targets"] for t in targets)):
                _fail("VALUE_NOT_OFFERED")
        if kind == "co_declare" and decision["claimed_role_id"] not in option["claimed_role_ids"]:
            _fail("VALUE_NOT_OFFERED")
    players = host["current_player_ids"]
    for key in ("subject_player_id", "source_player_id", "target_player_id", "addressee_player_id"):
        if speech.get(key) is not None and speech[key] not in players:
            _fail("VALUE_NOT_OFFERED")
    if speech["kind"] == "OPINION_CHANGE":
        prior = next((p for p in host["prior_assessments"] if p["subject_player_id"] == speech["subject_player_id"]), None)
        if prior is None or speech["prior"] != prior[speech["dimension"].lower()]:
            _fail("VALUE_NOT_OFFERED")
        old = set(map(tuple, prior["prior_evidence_identities"]))
        if all((r["record_kind"], r["order"]) in old for r in speech["causes"]):
            _fail("VALUE_NOT_OFFERED")
    if trigger == "CO_OPPORTUNITY":
        if detail["decision"] == "DECLARE":
            if (kind != "co_declare" or detail["selected_option_id"] != decision["option_id"]
                    or detail["claimed_role_id"] != decision["claimed_role_id"]):
                _fail("VALUE_NOT_OFFERED")
        elif kind != "none":
            _fail("VALUE_NOT_OFFERED")
    if trigger == "PRE_VOTE":
        offered = options.get(detail["option_id"])
        if offered is None or offered["action_kind"] != "vote":
            _fail("VALUE_NOT_OFFERED")
        if any(t not in offered["valid_targets"] for t in detail["ranked_target_player_ids"]):
            _fail("VALUE_NOT_OFFERED")
        if kind == "vote":
            if detail["preferred_target_player_id"] != decision["target_player_id"] or detail["option_id"] != decision["option_id"]:
                _fail("VALUE_NOT_OFFERED")
        elif detail["preferred_target_player_id"] is not None or offered["allows_abstain"] is not True:
            _fail("VALUE_NOT_OFFERED")
    return option


def expected_grounding(value) -> list[dict]:
    """Extract explicit ref fields structurally; never infer meaning from text."""
    result = {}
    def add(purpose, ref):
        if ref is not None:
            result[(purpose, *_ref_key(ref))] = {"purpose": purpose, "ref": dict(ref)}
    speech, detail = value["speech_act"], value["trigger_detail"]
    for ref in speech.get("evidence", ()):
        add("UTTERANCE", ref)
    add("UTTERANCE", speech.get("source"))
    add("UTTERANCE", speech.get("in_reply_to"))
    for ref in speech.get("causes", ()):
        add("OPINION_CURRENT", ref)
    if isinstance(detail, dict):
        add("REACTION", detail.get("trigger"))
        for ref in detail.get("evidence", ()):
            add("PRE_VOTE", ref)
    return list(result.values())


def _bindings(value, binding, host, option):
    _host_shape(host)
    try:
        input_value = _strict_json(binding.canonical_input_bytes)
        private_value = _strict_json(binding.canonical_private_view_bytes)
        expected_private = {"prior_assessments": host["prior_assessments"]}
        if (input_value != host or canonical_bytes(host) != binding.canonical_input_bytes
                or sha256(binding.canonical_input_bytes) != binding.input_sha256
                or private_value != expected_private or canonical_bytes(expected_private) != binding.canonical_private_view_bytes
                or sha256(binding.canonical_private_view_bytes) != binding.captured_private_state_sha256):
            _fail("BINDING_INVALID")
    except (ProbeError, ValueError, TypeError):
        _fail("BINDING_INVALID")
    projected = {_ref_key(r["ref"]): r for r in host["projected_evidence"]}
    captured = {_ref_key(r["ref"]): r for r in host["captured_evidence"]}
    projected_identity = {key[:2]: record for key, record in projected.items()}
    captured_identity = {key[:2]: record for key, record in captured.items()}
    for identity in projected_identity.keys() & captured_identity.keys():
        if projected_identity[identity] != captured_identity[identity]:
            _fail("BINDING_INVALID")
    expected = Counter((g["purpose"], *_ref_key(g["ref"])) for g in expected_grounding(value))
    actual = Counter((g["purpose"], *_ref_key(g["ref"])) for g in value["grounding"])
    if expected != actual:
        _fail("BINDING_INVALID")
    for item in value["grounding"]:
        key = _ref_key(item["ref"])
        if key not in projected or key not in captured or projected[key] != captured[key]:
            _fail("BINDING_INVALID")
    speech, detail = value["speech_act"], value["trigger_detail"]
    if speech["kind"] in ("ANSWER", "REBUTTAL"):
        source = captured[_ref_key(speech["in_reply_to"])]
        actors = source["actor_player_ids"]
        if (source["ref"]["record_kind"] not in ("chat", "co_declaration", "co_report")
                or len(actors) != 1 or actors[0] == host["actor_player_id"]
                or speech["addressee_player_id"] != actors[0]):
            _fail("BINDING_INVALID")
    if host["trigger"] == "PEER_CHAT":
        if detail["trigger"] != host["reaction_source"]:
            _fail("BINDING_INVALID")
        source = captured[_ref_key(detail["trigger"])]
        if option is not None and option["channel"] != source["channel_id"]:
            _fail("BINDING_INVALID")


def _text(value, host):
    text = value["utterance"]
    if value["decision"]["kind"] not in ("chat", "co_declare"):
        if text is not None:
            _fail("TEXT_INVALID")
        return
    if type(text) is not str or not 1 <= len(text) <= host["max_text"]:
        _fail("TEXT_INVALID")
    size = len(text.encode("utf-8"))
    if size > host["max_text_utf8_bytes"]:
        _fail("TEXT_INVALID")
    if len(text) >= 190 or size >= 570:
        ending = text.rstrip().rstrip('"\'\u201d\u2019)]}').rstrip()
        if not ending.endswith((".", "!", "?")):
            _fail("TEXT_INVALID")


@dataclass(frozen=True)
class ProbeResult:
    applicability: str
    raw_sha256: str
    input_sha256: str
    private_before_sha256: str
    private_after_sha256: str
    semantic_status: str = "NOT_EVALUATED"


def validate(raw: bytes, binding: HostBinding) -> ProbeResult:
    """Validate one manual candidate. No I/O, model calls, repairs or updates."""
    if not isinstance(binding, HostBinding) or not isinstance(binding.authority, Mapping):
        _fail("BINDING_INVALID")
    host = plain(binding.authority)
    cap = host.get("max_candidate_utf8_bytes")
    if type(cap) is not int or cap <= 0:
        _fail("BINDING_INVALID")
    if type(raw) is bytes and len(raw) > cap:
        _fail("TEXT_INVALID")
    value = _strict_json(raw)
    if _forbidden_key(value):
        _fail("PRIVATE_UPDATE_FORBIDDEN")
    _local_shapes(value, host)
    # Shape-invalid sidecars are host faults, not candidate authority decisions.
    _host_shape(host)
    option = _offered(value, host)
    if type(binding.canonical_input_bytes) is not bytes or type(binding.canonical_private_view_bytes) is not bytes:
        _fail("BINDING_INVALID")
    before = sha256(binding.canonical_private_view_bytes)
    _bindings(value, binding, host, option)
    _text(value, host)
    after = sha256(binding.canonical_private_view_bytes)
    if before != after:
        _fail("BINDING_INVALID")
    status = "APPLICABILITY_UNRESOLVED" if host["requires_private_update"] else "APPLICABILITY_COVERED"
    return ProbeResult(status, sha256(raw), binding.input_sha256, before, after)


def metadata_coverage(rows) -> dict:
    """Coverage of public metadata only; never a quality/32-generation score."""
    if type(rows) not in (list, tuple) or len(rows) != 32:
        _fail("BINDING_INVALID")
    expected = set(CASE_IDS)
    seen, counts = set(), Counter({trigger: 0 for trigger in TRIGGER_ACTIONS})
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"case_id", "trigger", "requires_private_update"}
                or row["case_id"] not in expected or row["case_id"] in seen
                or row["trigger"] not in TRIGGER_ACTIONS
                or row["requires_private_update"] is not None and type(row["requires_private_update"]) is not bool):
            _fail("BINDING_INVALID")
        group = int(row["case_id"][1:3])
        trigger = ("INITIAL_CHAT" if group in (4, 14) else "PRE_VOTE" if group == 13
                   else "CO_OPPORTUNITY" if group == 15 else "PEER_CHAT")
        if row["trigger"] != trigger:
            _fail("BINDING_INVALID")
        seen.add(row["case_id"])
        counts[trigger] += 1
    return {"total": 32, "trigger_counts": dict(counts),
            "covered": 0, "invalid": 0, "unresolved": 32,
            "none_controls": ["G14-1", "G14-2"], "semantic_status": "NOT_EVALUATED"}
