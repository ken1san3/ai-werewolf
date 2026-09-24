"""Deterministic statistics for the bounded T506 recovery experiment.

The module deliberately knows nothing about providers, processes, or persisted raw
outputs.  It validates the public annotation rows and computes only the fixed,
paired 32-case comparisons from the approved experiment design.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


CASE_IDS = tuple(f"G{group:02d}-{variant}" for group in range(1, 17) for variant in (1, 2))
SEEDS = (4242027, 4242028, 4242029)
QUESTION_GROUPS = frozenset({1, 5, 6, 7, 8, 9, 10, 11, 16})
QUESTION_CASE_IDS = tuple(case_id for case_id in CASE_IDS if int(case_id[1:3]) in QUESTION_GROUPS)
ANNOTATIONS = frozenset({"PASS", "FAIL", "UNKNOWN"})
STATUSES = frozenset(
    {
        "NOT_RUN",
        "ACCEPTED",
        "CHOICE_ERROR",
        "CHOICE_INVALID",
        "NO_LEGAL_GROUNDING",
        "OUTPUT_ERROR",
        "OUTPUT_INVALID",
        "FILTER_EXHAUSTED",
        "ERROR",
    }
)
VIOLATION_CODES = frozenset(
    {
        "FABRICATED_EVIDENCE",
        "AUTHORITY_VIOLATION",
        "SECRET_DISCLOSURE",
        "STATE_CONTRADICTION",
        "ABILITY_CONTRADICTION",
        "EXACT_PEER_COPY",
    }
)
EXPECTED_KEYS = tuple((case_id, seed) for case_id in CASE_IDS for seed in SEEDS)
BOOTSTRAP_SEED = 20260924
BOOTSTRAP_SAMPLES = 100_000


class RowValidationError(ValueError):
    """Raised when rows cannot belong to the preregistered experiment."""


def _is_bool_or_none(value: Any) -> bool:
    return value is None or type(value) is bool


def _validate_row(row: Mapping[str, Any]) -> tuple[str, int]:
    if not isinstance(row, Mapping):
        raise RowValidationError("each row must be a mapping")
    case_id = row.get("case_id")
    seed = row.get("seed")
    if case_id not in CASE_IDS:
        raise RowValidationError(f"unexpected case_id: {case_id!r}")
    if seed not in SEEDS or type(seed) is not int:
        raise RowValidationError(f"unexpected seed for {case_id}: {seed!r}")
    if type(row.get("structural_pass")) is not bool:
        raise RowValidationError(f"structural_pass must be bool for {case_id}/{seed}")
    if row.get("status") not in STATUSES:
        raise RowValidationError(f"unexpected status for {case_id}/{seed}: {row.get('status')!r}")
    output_hash = row.get("final_output_sha256")
    if output_hash is not None and (
        not isinstance(output_hash, str)
        or len(output_hash) != 64
        or any(character not in "0123456789abcdef" for character in output_hash)
    ):
        raise RowValidationError(f"invalid final_output_sha256 for {case_id}/{seed}")
    for field in ("semantic_annotation", "style_annotation", "hard_annotation"):
        if row.get(field) not in ANNOTATIONS:
            raise RowValidationError(f"invalid {field} for {case_id}/{seed}")
    question_value = row.get("content_answers_question")
    if not _is_bool_or_none(question_value):
        raise RowValidationError(f"content_answers_question must be bool or null for {case_id}/{seed}")
    if case_id not in QUESTION_CASE_IDS and question_value is not None:
        raise RowValidationError(f"content_answers_question is inapplicable for {case_id}")
    for field in ("act_text_mismatch", "legal_none"):
        if not _is_bool_or_none(row.get(field)):
            raise RowValidationError(f"{field} must be bool or null for {case_id}/{seed}")
    if not case_id.startswith("G14-") and row.get("legal_none") is not None:
        raise RowValidationError(f"legal_none is only applicable to G14: {case_id}")
    violations = row.get("violations")
    if not isinstance(violations, list) or any(code not in VIOLATION_CODES for code in violations):
        raise RowValidationError(f"invalid violations for {case_id}/{seed}")
    if len(violations) != len(set(violations)):
        raise RowValidationError(f"duplicate violation code for {case_id}/{seed}")
    calls = row.get("provider_calls")
    if type(calls) is not int or calls < 0:
        raise RowValidationError(f"provider_calls must be a nonnegative int for {case_id}/{seed}")
    return case_id, seed


def _index_rows(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[str, int], Mapping[str, Any]]:
    indexed: dict[tuple[str, int], Mapping[str, Any]] = {}
    for row in rows:
        key = _validate_row(row)
        if key in indexed:
            raise RowValidationError(f"duplicate row: {key[0]}/{key[1]}")
        indexed[key] = row
    return indexed


def _accepted(row: Mapping[str, Any]) -> bool:
    """A prior readable attempt does not make a failed row an accepted output."""
    return bool(row["status"] == "ACCEPTED" and row["structural_pass"]
                and row["final_output_sha256"] is not None)


def _outcome(row: Mapping[str, Any] | None, metric: str) -> int:
    if row is None:
        return 0
    if metric == "semantic":
        return int(_accepted(row) and row["semantic_annotation"] == "PASS")
    if metric == "question":
        return int(row["content_answers_question"] is True)
    if metric == "style":
        return int(row["style_annotation"] == "PASS")
    if metric == "hard":
        return int(_accepted(row) and row["hard_annotation"] == "PASS")
    raise AssertionError(metric)


def _unknown(row: Mapping[str, Any] | None, metric: str) -> bool:
    if row is None:
        return True
    if metric == "semantic":
        return row["semantic_annotation"] == "UNKNOWN"
    if metric == "question":
        return row["content_answers_question"] is None
    if metric == "style":
        return row["style_annotation"] == "UNKNOWN"
    if metric == "hard":
        return row["hard_annotation"] == "UNKNOWN"
    raise AssertionError(metric)


def summarize(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate and summarize an arm while retaining all 96 expected rows."""
    indexed = _index_rows(rows)
    missing = [f"{case_id}/{seed}" for case_id, seed in EXPECTED_KEYS if (case_id, seed) not in indexed]
    metric_cases = {
        "semantic": CASE_IDS,
        "question": QUESTION_CASE_IDS,
        "style": CASE_IDS,
        "hard": CASE_IDS,
    }
    metrics: dict[str, Any] = {}
    for metric, case_ids in metric_cases.items():
        applicable_keys = tuple((case_id, seed) for case_id in case_ids for seed in SEEDS)
        passes = sum(_outcome(indexed.get(key), metric) for key in applicable_keys)
        unknowns = sum(_unknown(indexed.get(key), metric) for key in applicable_keys)
        metrics[metric] = {
            "passes": passes,
            "denominator": len(applicable_keys),
            "rate": passes / len(applicable_keys),
            "unknowns": unknowns,
        }

    annotation_counts = {
        field: {value: sum(row[field] == value for row in indexed.values()) for value in sorted(ANNOTATIONS)}
        for field in ("semantic_annotation", "style_annotation", "hard_annotation")
    }
    act_text_mismatch = {
        "true": sum(row["act_text_mismatch"] is True for row in indexed.values()),
        "false": sum(row["act_text_mismatch"] is False for row in indexed.values()),
        "unknown": sum(row["act_text_mismatch"] is None for row in indexed.values()) + len(missing),
    }
    legal_rows = [row for row in indexed.values() if row["case_id"].startswith("G14-")]
    missing_legal = sum((case_id, seed) not in indexed for case_id in ("G14-1", "G14-2") for seed in SEEDS)
    legal_none = {
        "pass": sum(row["legal_none"] is True for row in legal_rows),
        "fail": sum(row["legal_none"] is False for row in legal_rows),
        "unknown": sum(row["legal_none"] is None for row in legal_rows) + missing_legal,
    }

    violation_counts = {code: 0 for code in sorted(VIOLATION_CODES)}
    for row in indexed.values():
        for code in row["violations"]:
            violation_counts[code] += 1
    # UNKNOWN is never promotable through selection.  Count each unknown rubric
    # result (including the appropriate missing-row placeholders) conservatively.
    absolute_unknowns = sum(metric["unknowns"] for metric in metrics.values())
    product_absolute_safety_eligible = (
        not missing
        and all(_accepted(row) for row in indexed.values())
        and all(row["hard_annotation"] == "PASS" for row in indexed.values())
        and not any(violation_counts.values())
        and absolute_unknowns == 0
        and act_text_mismatch["unknown"] == 0
        and legal_none["unknown"] == 0
    )
    return {
        "rows_present": len(indexed),
        "rows_expected": len(EXPECTED_KEYS),
        "missing_rows": missing,
        "complete": not missing,
        "metrics": metrics,
        "content_only_annotation_counts": annotation_counts,
        "act_text_mismatch": act_text_mismatch,
        "legal_none": legal_none,
        "provider_calls": sum(row["provider_calls"] for row in indexed.values()),
        "structural_failures": sum(not _accepted(row) for row in indexed.values()) + len(missing),
        "hard_failures": len(EXPECTED_KEYS) - metrics["hard"]["passes"],
        "violation_counts": violation_counts,
        "absolute_violation_or_unknown_count": sum(violation_counts.values()) + absolute_unknowns,
        "product_absolute_safety_eligible": product_absolute_safety_eligible,
    }


def _cluster_differences(
    baseline: Mapping[tuple[str, int], Mapping[str, Any]],
    candidate: Mapping[tuple[str, int], Mapping[str, Any]],
    metric: str,
    case_ids: Sequence[str],
) -> list[float]:
    return [
        sum(_outcome(candidate.get((case_id, seed)), metric) - _outcome(baseline.get((case_id, seed)), metric) for seed in SEEDS)
        / len(SEEDS)
        for case_id in case_ids
    ]


def _bootstrap_lower_bound(differences: Sequence[float]) -> float:
    rng = random.Random(BOOTSTRAP_SEED)
    size = len(differences)
    samples = [sum(differences[rng.randrange(size)] for _ in range(size)) / size for _ in range(BOOTSTRAP_SAMPLES)]
    samples.sort()
    return samples[math.ceil(0.05 * BOOTSTRAP_SAMPLES) - 1]


def compare(
    baseline_rows: Iterable[Mapping[str, Any]],
    candidate_rows: Iterable[Mapping[str, Any]],
    *,
    comparison_integrity: bool = True,
) -> dict[str, Any]:
    """Run the preregistered paired comparisons.

    ``comparison_integrity`` is explicit so callers can invalidate a comparison
    after checking frozen runtime/profile evidence.  It must be a real boolean.
    """
    if type(comparison_integrity) is not bool:
        raise ValueError("comparison_integrity must be bool")
    baseline = _index_rows(baseline_rows)
    candidate = _index_rows(candidate_rows)
    configurations = {
        "semantic": (CASE_IDS, -2 / 32),
        "question": (QUESTION_CASE_IDS, -1 / 18),
        "style": (CASE_IDS, -2 / 32),
        "hard": (CASE_IDS, -1 / 32),
    }
    results: dict[str, Any] = {}
    for metric, (case_ids, margin) in configurations.items():
        expected = tuple((case_id, seed) for case_id in case_ids for seed in SEEDS)
        complete = all(key in baseline and key in candidate for key in expected)
        has_unknown = any(
            _unknown(baseline.get(key), metric) or _unknown(candidate.get(key), metric) for key in expected
        )
        differences = _cluster_differences(baseline, candidate, metric, case_ids)
        observed = sum(differences) / len(differences)
        lower = _bootstrap_lower_bound(differences)
        conclusive = comparison_integrity and complete and not has_unknown
        results[metric] = {
            "clusters": len(case_ids),
            "observed_difference": observed,
            "lower_95": lower,
            "margin": margin,
            "decision": "QUALIFIED" if conclusive and lower >= margin else "INCONCLUSIVE",
            "superior": bool(conclusive and lower > 0),
            "complete": complete,
            "has_unknown": has_unknown,
        }
    qualified = all(result["decision"] == "QUALIFIED" for result in results.values())
    return {
        "comparison_integrity": comparison_integrity,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "bootstrap_samples": BOOTSTRAP_SAMPLES,
        "metrics": results,
        "decision": "QUALIFIED" if qualified else "INCONCLUSIVE",
    }


def select_candidate(
    arms: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    comparison_integrity: Mapping[str, bool],
) -> dict[str, Any]:
    """Select one test-only comparison arm using the fixed conservative ordering."""
    if not isinstance(arms, Mapping) or not arms:
        raise ValueError("arms must be a non-empty mapping")
    if not isinstance(comparison_integrity, Mapping) or set(comparison_integrity) != set(arms):
        raise ValueError("comparison_integrity must contain exactly the supplied arm IDs")
    if any(type(valid) is not bool for valid in comparison_integrity.values()):
        raise ValueError("comparison_integrity values must be bool")
    summaries: dict[str, dict[str, Any]] = {}
    for arm_id, rows in arms.items():
        if not isinstance(arm_id, str) or not arm_id:
            raise ValueError("arm IDs must be non-empty strings")
        summaries[arm_id] = summarize(rows)

    def rank(item: tuple[str, dict[str, Any]]) -> tuple[Any, ...]:
        arm_id, summary = item
        return (
            -summary["metrics"]["semantic"]["rate"],
            summary["hard_failures"],
            summary["absolute_violation_or_unknown_count"],
            summary["provider_calls"],
            arm_id,
        )

    eligible_for_selection = [(arm_id, summary) for arm_id, summary in summaries.items() if comparison_integrity[arm_id]]
    if not eligible_for_selection:
        raise ValueError("no comparison-integrity-valid arm was supplied")
    selected_id, selected = min(eligible_for_selection, key=rank)
    return {
        "arm_id": selected_id,
        "comparison_integrity": True,
        "product_absolute_safety_eligible": selected["product_absolute_safety_eligible"],
        "selection_label": "EXPLORATORY_CONTROL_ONLY",
        "summary": selected,
    }
