from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "phase6_recovery_statistics.py"
SPEC = importlib.util.spec_from_file_location("phase6_recovery_statistics", MODULE_PATH)
assert SPEC and SPEC.loader
stats = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stats)


def rows(*, semantic="PASS", style="PASS", hard="PASS", structural=True, calls=1):
    result = []
    for case_id in stats.CASE_IDS:
        question = int(case_id[1:3]) in stats.QUESTION_GROUPS
        for seed in stats.SEEDS:
            result.append(
                {
                    "case_id": case_id,
                    "seed": seed,
                    "structural_pass": structural,
                    "status": "ACCEPTED" if structural else "OUTPUT_INVALID",
                    "final_output_sha256": "a" * 64,
                    "semantic_annotation": semantic,
                    "style_annotation": style,
                    "hard_annotation": hard,
                    "content_answers_question": True if question else None,
                    "act_text_mismatch": False,
                    "legal_none": True if case_id.startswith("G14-") else None,
                    "violations": [],
                    "provider_calls": calls,
                }
            )
    return result


def find(data, case_id="G01-1", seed=4242027):
    return next(row for row in data if row["case_id"] == case_id and row["seed"] == seed)


def test_summary_separates_content_annotation_from_structural_outcome_and_product_gate():
    data = rows(structural=False)
    summary = stats.summarize(data)
    assert summary["metrics"]["semantic"] == {
        "passes": 0,
        "denominator": 96,
        "rate": 0.0,
        "unknowns": 0,
    }
    assert summary["metrics"]["style"]["passes"] == 96
    assert summary["metrics"]["hard"]["passes"] == 0
    assert summary["content_only_annotation_counts"]["semantic_annotation"]["PASS"] == 96
    assert summary["content_only_annotation_counts"]["hard_annotation"]["PASS"] == 96
    assert not summary["product_absolute_safety_eligible"]


def test_missing_and_unknown_stay_in_denominator_and_make_comparison_inconclusive():
    baseline = rows()
    candidate = rows()
    find(candidate)["semantic_annotation"] = "UNKNOWN"
    candidate.pop()
    summary = stats.summarize(candidate)
    assert summary["rows_present"] == 95
    assert summary["metrics"]["semantic"]["denominator"] == 96
    assert summary["metrics"]["semantic"]["unknowns"] == 2
    result = stats.compare(baseline, candidate)
    assert result["metrics"]["semantic"]["decision"] == "INCONCLUSIVE"
    assert not result["metrics"]["semantic"]["complete"]


def test_paired_case_cluster_bootstrap_and_fixed_secondary_margins():
    baseline = rows()
    candidate = rows()
    result = stats.compare(baseline, candidate)
    assert result["bootstrap_seed"] == 20260924
    assert result["bootstrap_samples"] == 100_000
    assert result["metrics"]["semantic"]["clusters"] == 32
    assert result["metrics"]["question"]["clusters"] == 18
    assert result["metrics"]["semantic"]["lower_95"] == 0
    assert result["metrics"]["question"]["margin"] == -1 / 18
    assert result["metrics"]["style"]["margin"] == -2 / 32
    assert result["metrics"]["hard"]["margin"] == -1 / 32
    assert result["decision"] == "QUALIFIED"


def test_integrity_is_explicit_and_cannot_be_hidden_by_good_scores():
    result = stats.compare(rows(), rows(), comparison_integrity=False)
    assert result["decision"] == "INCONCLUSIVE"
    assert all(metric["decision"] == "INCONCLUSIVE" for metric in result["metrics"].values())
    with pytest.raises(ValueError, match="must be bool"):
        stats.compare(rows(), rows(), comparison_integrity=None)


def test_validation_rejects_duplicates_unexpected_keys_and_inapplicable_values():
    data = rows()
    with pytest.raises(stats.RowValidationError, match="duplicate row"):
        stats.summarize(data + [copy.deepcopy(data[0])])
    bad = rows()
    bad[0]["seed"] = 9
    with pytest.raises(stats.RowValidationError, match="unexpected seed"):
        stats.summarize(bad)
    bad = rows()
    find(bad, "G02-1")["content_answers_question"] = False
    with pytest.raises(stats.RowValidationError, match="inapplicable"):
        stats.summarize(bad)
    bad = rows()
    find(bad)["violations"] = ["MADE_UP"]
    with pytest.raises(stats.RowValidationError, match="invalid violations"):
        stats.summarize(bad)


def test_candidate_selection_uses_fixed_order_and_labels_ineligible_control():
    arm_a = rows(calls=2)
    arm_b = rows(calls=1)
    find(arm_b)["hard_annotation"] = "FAIL"
    selected = stats.select_candidate(
        {"z-arm": arm_a, "a-arm": arm_b}, comparison_integrity={"z-arm": True, "a-arm": True}
    )
    assert selected["arm_id"] == "z-arm"  # fewer HARD failures outrank call count
    assert selected["product_absolute_safety_eligible"]

    tied_unknown_a = rows(semantic="UNKNOWN")
    tied_unknown_b = rows(semantic="UNKNOWN")
    selected = stats.select_candidate(
        {"b": tied_unknown_b, "a": tied_unknown_a}, comparison_integrity={"b": True, "a": True}
    )
    assert selected["arm_id"] == "a"
    assert selected["selection_label"] == "EXPLORATORY_CONTROL_ONLY"


def test_product_absolute_gate_counts_every_required_failure_and_unknown():
    data = rows()
    find(data, "G01-1", 4242027)["violations"] = ["FABRICATED_EVIDENCE"]
    find(data, "G01-1", 4242028)["hard_annotation"] = "UNKNOWN"
    find(data, "G01-1", 4242029)["structural_pass"] = False
    summary = stats.summarize(data)
    assert not summary["product_absolute_safety_eligible"]
    assert summary["violation_counts"]["FABRICATED_EVIDENCE"] == 1
    assert summary["absolute_violation_or_unknown_count"] == 2


def test_closed_status_and_sha_contract_reject_arbitrary_public_text():
    data = rows()
    find(data)["status"] = "looks okay to me"
    with pytest.raises(stats.RowValidationError, match="unexpected status"):
        stats.summarize(data)
    data = rows()
    find(data)["final_output_sha256"] = "private-output-name"
    with pytest.raises(stats.RowValidationError, match="invalid final_output_sha256"):
        stats.summarize(data)


def test_absolute_safety_gate_requires_known_diagnostics_and_legal_none():
    data = rows()
    assert stats.summarize(data)["product_absolute_safety_eligible"]
    find(data)["act_text_mismatch"] = None
    assert not stats.summarize(data)["product_absolute_safety_eligible"]
    data = rows()
    find(data, "G14-1")["legal_none"] = False
    summary = stats.summarize(data)
    assert summary["legal_none"] == {"pass": 5, "fail": 1, "unknown": 0}
    assert not summary["product_absolute_safety_eligible"]


def test_semantic_failure_does_not_relabel_absolute_safety_gate():
    data = rows(semantic="FAIL")
    summary = stats.summarize(data)
    assert summary["metrics"]["semantic"]["passes"] == 0
    assert summary["content_only_annotation_counts"]["semantic_annotation"]["FAIL"] == 96
    assert summary["product_absolute_safety_eligible"]


def test_selection_requires_explicit_integrity_and_excludes_invalid_arms():
    with pytest.raises(ValueError, match="exactly"):
        stats.select_candidate({"a": rows()}, comparison_integrity={})
    selected = stats.select_candidate(
        {"invalid": rows(calls=0), "valid": rows(calls=1)},
        comparison_integrity={"invalid": False, "valid": True},
    )
    assert selected["arm_id"] == "valid"
    with pytest.raises(ValueError, match="no comparison-integrity-valid"):
        stats.select_candidate({"a": rows()}, comparison_integrity={"a": False})
