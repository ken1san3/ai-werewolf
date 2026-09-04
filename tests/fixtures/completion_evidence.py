"""Shared, production-independent evidence checks for completion tests.

This module deliberately imports neither the server nor the production client.
It contains only the vocabulary and bookkeeping rules used by the separate
process completion fixtures. A driver records observations; the parent test
uses these helpers to decide whether those observations prove completion.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from math import ceil
from typing import Any


STATUS_SCHEMA_VERSION = "phase-completion-evidence/v1"
EVIDENCE_CONTRACT = "phase3.1/addendum-c-v1"

ACTION_EVIDENCE_OUTCOMES = frozenset(
    {"sent", "deadline_suppressed", "stale_before_send", "no_legal_target", "send_error"}
)

# Keep this table in one place. The production rejection vocabulary is
# checked by a contract test, but importing the production package here would
# make the supposedly protocol-only fixture depend on the server.
BOUNDARY_REJECTION_REASONS = frozenset(
    {
        "action_deadline_passed",
        "vote_unavailable",
        "action_closed",
        "action_unavailable",
        "actor_unavailable",
    }
)

DEFECT_REJECTION_REASONS = frozenset(
    {
        "ability_uses_exhausted",
        "abstention_disabled",
        "abstention_limit_reached",
        "claim_not_allowed",
        "co_limit_reached",
        "invalid_claimed_result",
        "invalid_comment",
        "invalid_message",
        "invalid_report_kind",
        "invalid_target",
        "self_vote_disabled",
        "unknown_ability",
        "unknown_claimed_role",
        "unknown_target",
        "unsupported_action",
        "invalid_action",
        "game_mismatch",
        "unsupported_protocol_version",
    }
)

ALL_REJECTION_REASONS = BOUNDARY_REJECTION_REASONS | DEFECT_REJECTION_REASONS

_REQUIRED_STATUS_FIELDS = frozenset(
    {
        "schema_version",
        "pid",
        "player_id",
        "resumed",
        "resume_events",
        "state_sync_events",
        "gap_events",
        "action_evidence",
        "action_rejections",
        "chat_messages_received",
        "game_end",
        "last_seq",
        "events_received",
        "send_errors",
        "server_imports",
        "production_import_guard",
        "client_exit_reason",
        "exception_type",
        "exception_message",
    }
)


class EvidenceValidationError(AssertionError):
    """Raised when completion evidence is malformed or insufficient."""


def rejection_class(reason: object) -> str:
    """Return boundary or fail-closed defect for a rejection reason."""

    if isinstance(reason, str) and reason in BOUNDARY_REJECTION_REASONS:
        return "boundary"
    return "defect"


def validate_rejection_vocabulary(reasons: Iterable[object]) -> None:
    """Reject defect and unknown reasons in a normal completion status."""

    for reason in reasons:
        if rejection_class(reason) != "boundary":
            raise EvidenceValidationError(f"non-boundary action rejection: {reason!r}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceValidationError(message)


def _non_negative_int(value: object, field: str) -> int:
    _require(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0,
        f"{field} must be non-negative int",
    )
    return value


def _positive_int(value: object, field: str) -> int:
    result = _non_negative_int(value, field)
    _require(result > 0, f"{field} must be positive int")
    return result


def _string(value: object, field: str) -> str:
    _require(isinstance(value, str) and bool(value), f"{field} must be a non-empty string")
    return value


def _sequence(value: object, field: str) -> Sequence[Any]:
    _require(isinstance(value, list), f"{field} must be a list")
    return value


def validate_status(
    status: object,
    *,
    expected_player_id: str | None = None,
    allow_defect_rejections: bool = False,
    require_addendum_c: bool = False,
) -> None:
    """Validate the common driver status envelope and evidence records."""

    _require(isinstance(status, Mapping), "status must be an object")
    missing = sorted(_REQUIRED_STATUS_FIELDS - set(status))
    _require(not missing, f"status is missing fields: {', '.join(missing)}")
    _require(status["schema_version"] == STATUS_SCHEMA_VERSION, "unsupported evidence schema version")
    if require_addendum_c:
        _require(
            status.get("evidence_contract") == EVIDENCE_CONTRACT,
            "unsupported Phase 3.1 evidence contract",
        )
        _require("initial_sync_seq" in status, "status is missing initial_sync_seq")
        initial_sync_seq = status.get("initial_sync_seq")
        _positive_int(initial_sync_seq, "initial_sync_seq")
        _string(status.get("player_id"), "player_id")
    _non_negative_int(status["pid"], "pid")
    player_id = status["player_id"]
    if expected_player_id is not None:
        _require(player_id == expected_player_id, f"status player_id mismatch: {player_id!r}")
    elif player_id is not None:
        _string(player_id, "player_id")
    _require(isinstance(status["resumed"], bool), "resumed must be bool")
    _non_negative_int(status["last_seq"], "last_seq")
    _non_negative_int(status["events_received"], "events_received")
    _require(isinstance(status["game_end"], bool), "game_end must be bool")
    _require(
        isinstance(status["production_import_guard"], bool),
        "production_import_guard must be bool",
    )
    _require(isinstance(status["server_imports"], list), "server_imports must be a list")
    _require(
        all(isinstance(item, str) for item in status["server_imports"]),
        "server_imports must contain strings",
    )
    _require(
        status["exception_type"] is None or isinstance(status["exception_type"], str),
        "exception_type must be string or null",
    )
    _require(
        status["exception_message"] is None or isinstance(status["exception_message"], str),
        "exception_message must be string or null",
    )

    resume_events = _sequence(status["resume_events"], "resume_events")
    for index, item in enumerate(resume_events):
        _require(isinstance(item, Mapping), f"resume_events[{index}] must be an object")
        _non_negative_int(item.get("seq"), f"resume_events[{index}].seq")
        _non_negative_int(
            item.get("requested_last_seq"),
            f"resume_events[{index}].requested_last_seq",
        )
        _require(
            item["requested_last_seq"] < item["seq"],
            f"resume_events[{index}] checkpoint must precede resume reply",
        )

    sync_events = _sequence(status["state_sync_events"], "state_sync_events")
    for index, item in enumerate(sync_events):
        _require(isinstance(item, Mapping), f"state_sync_events[{index}] must be an object")
        _non_negative_int(item.get("seq"), f"state_sync_events[{index}].seq")
        _require(
            isinstance(item.get("after_resume"), bool),
            f"state_sync_events[{index}].after_resume must be bool",
        )
        _require(
            isinstance(item.get("after_gap"), bool),
            f"state_sync_events[{index}].after_gap must be bool",
        )

    gap_events = _sequence(status["gap_events"], "gap_events")
    for index, item in enumerate(gap_events):
        _require(isinstance(item, Mapping), f"gap_events[{index}] must be an object")
        expected = _non_negative_int(
            item.get("expected_seq"),
            f"gap_events[{index}].expected_seq",
        )
        received = _non_negative_int(
            item.get("received_seq"),
            f"gap_events[{index}].received_seq",
        )
        _require(
            received > expected,
            f"gap_events[{index}] must have received_seq > expected_seq",
        )
        recovered = item.get("recovered_seq")
        if recovered is not None:
            recovered_seq = _non_negative_int(
                recovered,
                f"gap_events[{index}].recovered_seq",
            )
            _require(
                recovered_seq >= received,
                f"gap_events[{index}] recovered_seq must cover received_seq",
            )

    evidence = _sequence(status["action_evidence"], "action_evidence")
    keys: set[str] = set()
    for index, item in enumerate(evidence):
        _require(isinstance(item, Mapping), f"action_evidence[{index}] must be an object")
        key = _string(item.get("key"), f"action_evidence[{index}].key")
        _require(key not in keys, f"duplicate action evidence key: {key}")
        keys.add(key)
        _require(
            item.get("kind") in {"vote", "ability", "co_declare", "chat"},
            f"unknown action kind: {item.get('kind')!r}",
        )
        _non_negative_int(item.get("day"), f"action_evidence[{index}].day")
        _string(item.get("phase"), f"action_evidence[{index}].phase")
        _non_negative_int(
            item.get("action_generation"),
            f"action_evidence[{index}].action_generation",
        )
        if require_addendum_c:
            source_seq = _positive_int(
                item.get("source_seq"), f"action_evidence[{index}].source_seq"
            )
            _require(
                source_seq <= status["last_seq"],
                f"action_evidence[{index}].source_seq exceeds last_seq",
            )
        _require(
            isinstance(item.get("after_resume"), bool),
            f"action_evidence[{index}].after_resume must be bool",
        )
        _require(
            item.get("outcome") in ACTION_EVIDENCE_OUTCOMES,
            f"unknown action outcome: {item.get('outcome')!r}",
        )
        if item["kind"] == "ability":
            _string(item.get("ability_id"), f"action_evidence[{index}].ability_id")
        else:
            _require(
                "ability_id" not in item or item["ability_id"] is None,
                f"non-ability evidence must not contain ability_id: {key}",
            )

    rejections = _sequence(status["action_rejections"], "action_rejections")
    rejection_seqs: set[int] = set()
    for index, item in enumerate(rejections):
        _require(isinstance(item, Mapping), f"action_rejections[{index}] must be an object")
        _string(item.get("action"), f"action_rejections[{index}].action")
        reason = _string(item.get("reason"), f"action_rejections[{index}].reason")
        sequence = _non_negative_int(item.get("seq"), f"action_rejections[{index}].seq")
        _require(sequence not in rejection_seqs, f"duplicate rejection seq: {sequence}")
        rejection_seqs.add(sequence)
        _require(
            isinstance(item.get("after_resume"), bool),
            f"action_rejections[{index}].after_resume must be bool",
        )
    if not allow_defect_rejections:
        validate_rejection_vocabulary(item.get("reason") for item in rejections)

    messages = _sequence(status["chat_messages_received"], "chat_messages_received")
    for index, item in enumerate(messages):
        _require(isinstance(item, Mapping), f"chat_messages_received[{index}] must be an object")
        _non_negative_int(item.get("seq"), f"chat_messages_received[{index}].seq")
        _string(item.get("sender_player_id"), f"chat_messages_received[{index}].sender_player_id")

    errors = _sequence(status["send_errors"], "send_errors")
    for index, item in enumerate(errors):
        _require(isinstance(item, Mapping), f"send_errors[{index}] must be an object")
        _string(item.get("type"), f"send_errors[{index}].type")
        _string(item.get("message"), f"send_errors[{index}].message")
        _string(item.get("evidence_key"), f"send_errors[{index}].evidence_key")


def validate_stop_marker(
    marker: object,
    *,
    expected_player_id: str | None = None,
    expected_kind: str | None = None,
    require_addendum_c: bool = False,
) -> None:
    """Validate the process-incarnation marker used to bound an interruption."""

    _require(isinstance(marker, Mapping), "stop marker must be an object")
    _require(
        marker.get("schema_version") == STATUS_SCHEMA_VERSION,
        "unsupported stop-marker schema version",
    )
    if require_addendum_c:
        _require(
            marker.get("evidence_contract") == EVIDENCE_CONTRACT,
            "unsupported Phase 3.1 stop-marker contract",
        )
        _require("initial_sync_seq" in marker, "stop marker is missing initial_sync_seq")
        _positive_int(marker.get("initial_sync_seq"), "stop marker initial_sync_seq")
    _non_negative_int(marker.get("pid"), "stop marker pid")
    _string(marker.get("player_id"), "stop marker player_id")
    if expected_player_id is not None:
        _require(
            marker["player_id"] == expected_player_id,
            f"stop marker player_id mismatch: {marker['player_id']!r}",
        )
    if expected_kind is not None:
        _require(marker.get("kind") == expected_kind, "stop marker kind mismatch")
    else:
        _require(marker.get("kind") in {"action", "chat"}, "invalid stop marker kind")
    _non_negative_int(marker.get("last_seq"), "stop marker last_seq")
    evidence = marker.get("action_evidence")
    _require(isinstance(evidence, list), "stop marker action_evidence must be a list")
    # Reuse the exact status record validator so marker and final status
    # cannot silently diverge in their evidence vocabulary.
    validate_status(
        {
            "schema_version": STATUS_SCHEMA_VERSION,
            **({"evidence_contract": EVIDENCE_CONTRACT} if require_addendum_c else {}),
            "pid": marker["pid"],
            "player_id": marker["player_id"],
            "resumed": False,
            "resume_events": [],
            "state_sync_events": [],
            "gap_events": [],
            "action_evidence": evidence,
            "action_rejections": [],
            "chat_messages_received": [],
            "game_end": False,
            "last_seq": marker["last_seq"],
            "events_received": 0,
            "send_errors": [],
            "server_imports": [],
            "production_import_guard": True,
            "client_exit_reason": None,
            "exception_type": None,
            "exception_message": None,
            **({"initial_sync_seq": marker.get("initial_sync_seq")} if require_addendum_c else {}),
        },
        expected_player_id=marker["player_id"],
        require_addendum_c=require_addendum_c,
    )


def acceptance_quorum(expected: int) -> int:
    """Return the dynamic half-style quorum ceil(expected / 2)."""

    _require(
        isinstance(expected, int) and not isinstance(expected, bool) and expected >= 0,
        "expected must be non-negative int",
    )
    return ceil(expected / 2)


def distinct_players(records: Iterable[Mapping[str, Any]], field: str) -> frozenset[str]:
    """Collect distinct non-empty player IDs from authoritative records."""

    players: set[str] = set()
    for record in records:
        player_id = record.get(field)
        if isinstance(player_id, str) and player_id:
            players.add(player_id)
    return frozenset(players)


def assert_acceptance_quorum(
    expected: int,
    records: Iterable[Mapping[str, Any]],
    *,
    field: str,
) -> frozenset[str]:
    """Assert that authoritative accepted records meet the dynamic quorum."""

    accepted = distinct_players(records, field)
    required = acceptance_quorum(expected)
    _require(
        len(accepted) >= required,
        f"accepted {field} players {len(accepted)} below quorum {required} for expected {expected}",
    )
    return accepted


def derive_interrupted_rounds(
    rounds: Iterable[Mapping[str, Any]], *, stop_seq: int, resume_sync_seq: int
) -> frozenset[tuple[int, str]]:
    """Derive interrupted rounds from the player-local half-open seq interval."""

    _non_negative_int(stop_seq, "stop_seq")
    _non_negative_int(resume_sync_seq, "resume_sync_seq")
    _require(stop_seq < resume_sync_seq, "resume sync seq must be after stop seq")
    interrupted: set[tuple[int, str]] = set()
    for round_record in rounds:
        _require(isinstance(round_record, Mapping), "round record must be an object")
        day = _non_negative_int(round_record.get("day"), "round.day")
        phase = _string(round_record.get("phase"), "round.phase")
        start_seq = _non_negative_int(
            round_record.get("round_start_seq"),
            "round.round_start_seq",
        )
        if stop_seq < start_seq <= resume_sync_seq:
            interrupted.add((day, phase))
    return frozenset(interrupted)


def validate_interruption_bounds(
    completed_rounds: Iterable[Mapping[str, Any]],
    interrupted_rounds: Iterable[tuple[int, str]],
    *,
    stop_seq: int,
    resume_sync_seq: int,
) -> frozenset[tuple[int, str]]:
    """Validate that interruption excludes a strict, non-empty subset of rounds."""

    completed_records = tuple(completed_rounds)
    completed = {
        (
            _non_negative_int(item.get("day"), "completed_round.day"),
            _string(item.get("phase"), "completed_round.phase"),
        )
        for item in completed_records
    }
    derived = derive_interrupted_rounds(
        completed_records,
        stop_seq=stop_seq,
        resume_sync_seq=resume_sync_seq,
    )
    supplied = frozenset(interrupted_rounds)
    _require(
        supplied == derived,
        "caller-supplied interrupted rounds do not match the seq interval",
    )
    _require(bool(derived), "interrupted round set must not be empty")
    _require(bool(completed), "completed round set must not be empty")
    _require(derived < completed, "interrupted round set must not contain every completed round")
    return derived


def action_evidence_counts(
    statuses: Iterable[Mapping[str, Any]],
    *,
    kind: str,
    outcome: str = "sent",
) -> dict[str, int]:
    """Count semantic evidence keys, de-duplicated across process incarnations."""

    counts: dict[str, int] = {}
    for status in statuses:
        for item in status.get("action_evidence", ()):
            if item.get("kind") != kind or item.get("outcome") != outcome:
                continue
            semantic_key = str(item["key"])
            counts[semantic_key] = counts.get(semantic_key, 0) + 1
    return counts


def _action_state_from_reply(message_type: object, payload: object) -> Mapping[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    if message_type == "game.state_sync":
        action_state = payload.get("action_state")
    elif message_type == "player.action_state":
        action_state = payload
    else:
        return None
    return action_state if isinstance(action_state, Mapping) else None


def _offer_for_action(
    *,
    player_id: str,
    seq: int,
    message_type: str,
    day: object,
    phase: object,
    action: Mapping[str, Any],
) -> dict[str, Any] | None:
    action_type = action.get("type")
    if action_type not in {"vote", "ability", "co_declare", "chat"}:
        return None
    if not isinstance(day, int) or isinstance(day, bool) or day < 0:
        return None
    if not isinstance(phase, str) or not phase:
        return None
    common = {
        "player_id": player_id,
        "seq": seq,
        "type": message_type,
        "day": day,
        "phase": phase,
        "selectable": False,
    }
    if action_type == "vote":
        targets = action.get("valid_targets")
        target_count = action.get("target_count")
        allows_abstain = action.get("allows_abstain")
        selectable = (
            isinstance(targets, list)
            and isinstance(target_count, int)
            and not isinstance(target_count, bool)
            and target_count >= 0
            and isinstance(allows_abstain, bool)
            and (len(targets) >= target_count or allows_abstain)
        )
        common.update({"kind": "vote", "key": f"{player_id}|{day}|{phase}|vote"})
    elif action_type == "ability":
        ability_id = action.get("ability_id")
        targets = action.get("valid_targets")
        target_count = action.get("target_count")
        uses_remaining = action.get("uses_remaining")
        selectable = (
            isinstance(ability_id, str)
            and bool(ability_id)
            and isinstance(targets, list)
            and isinstance(target_count, int)
            and not isinstance(target_count, bool)
            and target_count >= 0
            and len(targets) >= target_count
            and uses_remaining != 0
        )
        if not isinstance(ability_id, str) or not ability_id:
            return None
        common.update(
            {
                "kind": "ability",
                "key": f"{player_id}|{day}|{phase}|ability|{ability_id}",
                "ability_id": ability_id,
            }
        )
    elif action_type == "co_declare":
        claimed = action.get("claimed_role_ids")
        selectable = isinstance(claimed, list) and bool(claimed)
        common.update({"kind": "co_declare", "key": f"{player_id}|{day}|co_declare"})
    else:
        selectable = True
        common.update({"kind": "chat", "key": f"{player_id}|{day}|chat"})
    common["selectable"] = selectable
    return common


def record_server_reply(
    ledger: dict[str, Any],
    *,
    player_id: str,
    seq: int,
    message_type: str,
    payload: object,
) -> None:
    """Record one already-created server reply without creating a reply."""

    _string(player_id, "ledger player_id")
    _positive_int(seq, "ledger seq")
    _string(message_type, "ledger type")
    action_state = _action_state_from_reply(message_type, payload)
    day = action_state.get("day") if action_state is not None else None
    phase = action_state.get("phase") if action_state is not None else None
    headers = ledger.setdefault("reply_headers", [])
    headers.append(
        {
            "player_id": player_id,
            "seq": seq,
            "type": message_type,
            "day": day if isinstance(day, int) and not isinstance(day, bool) else None,
            "phase": phase if isinstance(phase, str) else None,
        }
    )
    if action_state is None:
        return
    occurrences = ledger.setdefault("offer_occurrences", [])
    seen = {(item.get("player_id"), item.get("seq"), item.get("key")) for item in occurrences}
    actions = action_state.get("actions")
    if not isinstance(actions, list):
        return
    for action in actions:
        if not isinstance(action, Mapping):
            continue
        offer = _offer_for_action(
            player_id=player_id,
            seq=seq,
            message_type=message_type,
            day=day,
            phase=phase,
            action=action,
        )
        if offer is None:
            continue
        identity = (offer["player_id"], offer["seq"], offer["key"])
        if identity in seen:
            continue
        seen.add(identity)
        occurrences.append(offer)


def expected_opportunities(ledger: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Return the first selectable server occurrence for every semantic key."""

    expected: dict[str, Mapping[str, Any]] = {}
    for occurrence in ledger.get("offer_occurrences", ()):
        if not isinstance(occurrence, Mapping) or not occurrence.get("selectable"):
            continue
        key = occurrence.get("key")
        if not isinstance(key, str) or key in expected:
            continue
        expected[key] = occurrence
    return expected


def assert_action_coverage(
    ledger: Mapping[str, Any],
    statuses: Iterable[Mapping[str, Any]],
    *,
    marker: Mapping[str, Any],
    restarted_status: Mapping[str, Any],
    restarted_player_id: str,
    resume_sync_seq: int,
) -> dict[str, frozenset[str]]:
    """Prove all server opportunities are covered by independent client evidence."""

    stop_seq = _non_negative_int(marker.get("last_seq"), "stop marker last_seq")
    _positive_int(resume_sync_seq, "resume_sync_seq")
    _require(stop_seq < resume_sync_seq, "resume sync seq must be after stop seq")
    _string(restarted_player_id, "restarted_player_id")
    expected = expected_opportunities(ledger)
    occurrences = {
        (item.get("key"), item.get("seq")): item
        for item in ledger.get("offer_occurrences", ())
        if isinstance(item, Mapping)
    }
    expected_for_restart = {
        key for key, item in expected.items() if item.get("player_id") == restarted_player_id
    }
    interrupted = frozenset(
        key
        for key, item in expected.items()
        if item.get("player_id") == restarted_player_id
        and stop_seq < item.get("seq", -1) <= resume_sync_seq
    )
    _require(bool(interrupted), "interrupted opportunity set must not be empty")
    _require(
        bool(expected_for_restart) and len(interrupted) < len(expected_for_restart),
        "interrupted opportunity set must be a proper subset of restarted opportunities",
    )

    all_records: list[Mapping[str, Any]] = []
    marker_records = marker.get("action_evidence", ())
    _require(isinstance(marker_records, list), "stop marker action_evidence must be a list")
    all_records.extend(item for item in marker_records if isinstance(item, Mapping))
    for status in statuses:
        _require(isinstance(status, Mapping), "status must be an object")
        evidence = status.get("action_evidence", ())
        _require(isinstance(evidence, list), "status action_evidence must be a list")
        all_records.extend(item for item in evidence if isinstance(item, Mapping))

    sent: set[str] = set()
    invalid: list[str] = []
    for record in all_records:
        key = record.get("key")
        source_seq = record.get("source_seq")
        if not isinstance(key, str) or not isinstance(source_seq, int) or isinstance(source_seq, bool):
            invalid.append(f"malformed evidence {record!r}")
            continue
        occurrence = occurrences.get((key, source_seq))
        if occurrence is None:
            invalid.append(f"no server occurrence for {key}@{source_seq}")
            continue
        for field in ("kind", "day", "phase"):
            if record.get(field) != occurrence.get(field):
                invalid.append(f"mismatched {field} for {key}@{source_seq}")
        if record.get("kind") == "ability" and record.get("ability_id") != occurrence.get("ability_id"):
            invalid.append(f"mismatched ability for {key}@{source_seq}")
        outcome = record.get("outcome")
        if outcome == "sent":
            if not occurrence.get("selectable") or key not in expected:
                invalid.append(f"sent evidence is not an expected selectable opportunity: {key}")
            else:
                sent.add(key)
        elif outcome == "stale_before_send":
            player_id = occurrence.get("player_id")
            if not (
                player_id == restarted_player_id
                and key in interrupted
                and stop_seq < source_seq <= resume_sync_seq
            ):
                invalid.append(f"stale evidence outside interruption window: {key}@{source_seq}")
        elif outcome in {"deadline_suppressed", "no_legal_target", "send_error"}:
            invalid.append(f"failed action outcome for {key}@{source_seq}: {outcome}")
        else:
            invalid.append(f"unknown action outcome: {outcome!r}")
    _require(not invalid, "invalid action coverage: " + "; ".join(invalid[:8]))

    required = frozenset(expected) - interrupted
    _require(required <= sent <= frozenset(expected), "required/sent/expected coverage mismatch")
    expected_players = {item.get("player_id") for item in expected.values()}
    for player_id in expected_players - {restarted_player_id}:
        player_expected = {
            key for key, item in expected.items() if item.get("player_id") == player_id
        }
        _require(
            {key for key in sent if key in player_expected} == player_expected,
            f"non-restarted player coverage mismatch: {player_id}",
        )

    marker_sent = any(
        item.get("outcome") == "sent"
        and isinstance(item.get("source_seq"), int)
        and item["source_seq"] <= stop_seq
        for item in marker_records
        if isinstance(item, Mapping)
    )
    _require(marker_sent, "stop marker must contain a sent action at or before stop_seq")
    _require(
        marker.get("player_id") == restarted_player_id,
        "stop marker player does not match restarted player",
    )
    _require(
        marker.get("pid") != restarted_status.get("pid"),
        "replacement process must have a different pid",
    )
    post_resume_sent = False
    for record in restarted_status.get("action_evidence", ()):
        key = record.get("key") if isinstance(record, Mapping) else None
        source_seq = record.get("source_seq") if isinstance(record, Mapping) else None
        if (
            isinstance(key, str)
            and record.get("outcome") == "sent"
            and record.get("after_resume") is True
            and key in required
            and isinstance(source_seq, int)
            and source_seq > resume_sync_seq
            and isinstance(occurrences.get((key, source_seq), {}).get("seq"), int)
            and occurrences[key, source_seq]["seq"] > resume_sync_seq
        ):
            post_resume_sent = True
            break
    _require(post_resume_sent, "replacement process lacks a post-resume sent opportunity")
    return {
        "expected": frozenset(expected),
        "interrupted": interrupted,
        "required": required,
        "sent": frozenset(sent),
    }
