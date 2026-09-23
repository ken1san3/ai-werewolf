"""Hash-bound, text-free aggregation of the new minimal candidate only."""
from __future__ import annotations

import hashlib
import json

from scripts.phase6_minimal_output_probe import CASE_IDS, canonical_bytes, _strict_json

QUESTION_IDS = frozenset(f"G{g:02}-{v}" for g in (1, 5, 6, 7, 8, 9, 10, 11, 16) for v in (1, 2))
NONE_IDS = frozenset(("G14-1", "G14-2"))
BASELINE = {
    "denominator": 32, "hard_fail": 14, "semantic_pass": 7, "style_pass": 15,
    "act_text_mismatch": 23, "fabricated_evidence": 0, "secret_disclosure": 5,
    "state_contradiction": 3, "ability_contradiction": 0, "unknown": 0,
    "question_answers": 9, "question_denominator": 18, "legal_none": 2,
    "none_denominator": 2, "copy": 4, "peer_long_exact_copy": None,
}
REASONS = frozenset(("FABRICATED_EVIDENCE", "SECRET_DISCLOSURE", "STATE_CONTRADICTION",
                     "ABILITY_CONTRADICTION", "INVALID_REFERENCE", "COPY", "STRUCTURAL_INVALID"))
STATES = frozenset(("PASS", "FAIL", "UNKNOWN"))
ANNOTATION_KEYS = frozenset(("case_id", "input_sha256", "raw_sha256", "hard", "semantic", "style",
    "reasons", "act_text_mismatch", "question_answer", "legal_none", "copy", "grounding_supported"))


def baseline_bytes():
    # Saved measurements, not a recomputation of the old annotation.
    return canonical_bytes(BASELINE)


def _require(condition):
    if not condition:
        raise ValueError("QUALITY_BINDING_INVALID")


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _combine(result_bytes, annotation_bytes, baseline_bytes, *, exact_hashes,
             experiment, task_id, annotation_version, report_version, derived=False):
    """No private text, model calls, semantic inference or old annotation reads."""
    _require(set(exact_hashes) == {"result", "annotation", "baseline"})
    for name, raw in (("result", result_bytes), ("annotation", annotation_bytes), ("baseline", baseline_bytes)):
        _require(type(raw) is bytes and _digest(raw) == exact_hashes[name])
    result, annotation, baseline = map(_strict_json, (result_bytes, annotation_bytes, baseline_bytes))
    _require(baseline == BASELINE and canonical_bytes(baseline) == baseline_bytes)
    _require(type(annotation) is dict and set(annotation) == {"version", "result_sha256", "rows"})
    _require(annotation["version"] == annotation_version and annotation["result_sha256"] == exact_hashes["result"])
    _require(result.get("experiment") == experiment and result.get("task_id") == task_id)
    rows, notes = result.get("rows"), annotation["rows"]
    _require(type(rows) is list and type(notes) is list and len(rows) == len(notes) == 32)
    _require([r.get("case_id") for r in rows] == list(CASE_IDS))
    _require([r.get("case_id") for r in notes] == list(CASE_IDS))
    safe_rows = []
    counts = dict(denominator=32, hard_pass=0, hard_fail=0, hard_unknown=0,
                  semantic_pass=0, semantic_fail=0, semantic_unknown=0,
                  style_pass=0, style_fail=0, style_unknown=0,
                  act_text_mismatch=0, fabricated_evidence=0, secret_disclosure=0,
                  state_contradiction=0, ability_contradiction=0, unknown=0,
                  question_answers=0, question_denominator=18, legal_none=0, none_denominator=2,
                  copy=0, peer_long_exact_copy=0, screen_executed_count=0,
                  applicability_unresolved=0, applicability_invalid=0, applicability_covered=0)
    for row, note in zip(rows, notes):
        mechanical = row.get("mechanical_status")
        _require(mechanical in STATES)
        if derived:
            forbidden = {"derived_grounding", "derived_grounding_sha256", "canonical_ref_value",
                         "ref_key", "snapshot", "snapshot_sha256", "snapshot_content_sha256"}
            _require(not (set(row) & forbidden))
            counts_by_purpose = row.get("derived_grounding_purpose_counts")
            if mechanical == "PASS":
                _require(type(row.get("derived_grounding_item_count")) is int
                         and type(counts_by_purpose) is dict
                         and set(counts_by_purpose) == {"UTTERANCE", "OPINION_CURRENT", "REACTION", "PRE_VOTE"}
                         and all(type(value) is int and value >= 0 for value in counts_by_purpose.values())
                         and sum(counts_by_purpose.values()) == row["derived_grounding_item_count"])
            else:
                _require(row.get("derived_grounding_item_count") is None
                         and counts_by_purpose is None)
        _require(type(note) is dict and set(note) == ANNOTATION_KEYS)
        _require(note["input_sha256"] == row["input_sha256"] and note["raw_sha256"] == row.get("raw_sha256"))
        _require(all(note[k] in STATES for k in ("hard", "semantic", "style")))
        reasons = note["reasons"]
        _require(type(reasons) is list and len(set(reasons)) == len(reasons) and set(reasons) <= REASONS)
        for key in ("act_text_mismatch", "question_answer", "legal_none", "copy", "grounding_supported"):
            _require(note[key] is None or type(note[key]) is bool)
        _require(note["question_answer"] is None if row["case_id"] not in QUESTION_IDS else True)
        _require(note["legal_none"] is None if row["case_id"] not in NONE_IDS else True)
        _require(not reasons or note["hard"] == "FAIL")
        _require(("COPY" in reasons) == (note["copy"] is True))
        # A known failure dominates unknown, while missing evidence never becomes PASS.
        hard = "FAIL" if "FAIL" in (mechanical, note["hard"]) else (
            "UNKNOWN" if "UNKNOWN" in (mechanical, note["hard"]) else "PASS")
        if row.get("raw_sha256") is None:
            _require(all(note[k] == "UNKNOWN" for k in ("hard", "semantic", "style")))
            _require(all(note[k] is None for k in ("act_text_mismatch", "question_answer", "legal_none", "copy", "grounding_supported")))
        metrics = [note["act_text_mismatch"], note["copy"], note["grounding_supported"]]
        if row["case_id"] in QUESTION_IDS:
            metrics.append(note["question_answer"])
        if row["case_id"] in NONE_IDS:
            metrics.append(note["legal_none"])
            if note["legal_none"] is True:
                _require(row.get("speech_act") == "NONE")
        unknown = "UNKNOWN" in (hard, note["semantic"], note["style"]) or None in metrics
        counts["unknown"] += unknown
        for layer, value in (("hard", hard), ("semantic", note["semantic"]), ("style", note["style"])):
            counts[layer+"_"+value.lower()] += 1
        for reason in ("FABRICATED_EVIDENCE", "SECRET_DISCLOSURE", "STATE_CONTRADICTION", "ABILITY_CONTRADICTION"):
            counts[reason.lower()] += reason in reasons
        for metric, key in (("act_text_mismatch", "act_text_mismatch"), ("question_answers", "question_answer"),
                            ("legal_none", "legal_none"), ("copy", "copy")):
            counts[metric] += note[key] is True
        screened = row.get("peer_long_exact_copy")
        _require(screened is None or type(screened) is bool)
        counts["screen_executed_count"] += screened is not None
        counts["peer_long_exact_copy"] += screened is True
        applicability = row.get("applicability")
        _require(applicability in ("UNRESOLVED", "INVALID", "COVERED"))
        counts["applicability_"+applicability.lower()] += 1
        safe_rows.append({"case_id": row["case_id"], "hard": hard, "semantic": note["semantic"],
                          "style": note["style"], "unknown": unknown, "applicability": applicability,
                          "reasons": reasons})
    gates = {
        "hard": counts["hard_fail"] <= 14, "semantic": counts["semantic_pass"] >= 7,
        "style": counts["style_pass"] > 15, "act_text": counts["act_text_mismatch"] < 23,
        "fabricated": counts["fabricated_evidence"] == 0, "secret": counts["secret_disclosure"] <= 5,
        "state": counts["state_contradiction"] <= 3, "ability": counts["ability_contradiction"] == 0,
        "unknown": counts["unknown"] == 0, "questions": counts["question_answers"] >= 9,
        "legal_none": counts["legal_none"] == 2, "copy": counts["copy"] <= 4,
        "peer_exact": counts["peer_long_exact_copy"] == 0 and counts["screen_executed_count"] == 32,
        "run_integrity": result.get("status") == "COMPLETE" and result.get("source_unchanged") is True
            and result.get("owned_processes_remaining") == 0 and result.get("listener_free") is True
            and result.get("provider_calls") == 32 and all(r.get("call_consumed") == 1 for r in rows),
    }
    return {"version": report_version, "hashes": dict(exact_hashes), "baseline": dict(BASELINE),
            "candidate": counts, "gates": gates, "rows": safe_rows,
            "decision": "TEST_ONLY_CANDIDATE" if all(gates.values()) else "REJECTED",
            "product_adoption": False, "causal_attribution": "CONFOUNDED"}


def combine(result_bytes, annotation_bytes, baseline_bytes, *, exact_hashes):
    """Preserve the exact T499 quality identity and evaluation contract."""
    return _combine(result_bytes, annotation_bytes, baseline_bytes, exact_hashes=exact_hashes,
                    experiment="minimal_output_v1", task_id="T499",
                    annotation_version="minimal-quality.v1", report_version="minimal-quality.v1")


def combine_derived(result_bytes, annotation_bytes, baseline_bytes, *, exact_hashes):
    """Apply unchanged thresholds under the separate T504 evidence identity."""
    return _combine(result_bytes, annotation_bytes, baseline_bytes, exact_hashes=exact_hashes,
                    experiment="minimal_derived_grounding_v1", task_id="T504",
                    annotation_version="minimal-derived-grounding-quality.v1",
                    report_version="minimal-derived-grounding-quality.v1", derived=True)
