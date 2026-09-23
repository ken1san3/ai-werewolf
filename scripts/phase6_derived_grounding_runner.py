"""One frozen T504 derived-grounding measurement, using the T499 lifecycle."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import phase6_minimal_suite_runner as legacy


EXPERIMENT = "minimal_derived_grounding_v1"
TASK = "T504"
RUNNER = "scripts/phase6_derived_grounding_runner.py"
DESIGN = "Docs/ai/design/PHASE6_MINIMAL_GROUNDING_RESPONSIBILITY_DESIGN.md"
DESIGN_REVIEW = "Docs/ai/handoffs/tasks/T501_MINIMAL_GROUNDING_DESIGN_REVIEW.md"
DESIGN_DELTA_REVIEW = "Docs/ai/handoffs/tasks/T503_DERIVED_GROUNDING_REVIEW.md"
TOOL_REVIEW = "Docs/ai/handoffs/tasks/T503_DERIVED_GROUNDING_TOOL_REVIEW.md"
TOOL_APPROVAL = "Docs/ai/handoffs/tasks/T503_DERIVED_GROUNDING_TOOL_APPROVAL.json"
EXTRA_SOURCES = tuple(dict.fromkeys(legacy.EXTRA_SOURCES + (
    RUNNER,
    "scripts/phase6_derived_grounding_snapshot.py",
    "tests/test_phase6_derived_grounding_runner.py",
    "tests/test_phase6_derived_grounding_snapshot.py",
    "tests/test_phase6_derived_grounding_probe.py",
)))
PROFILE = legacy.RunnerProfile(
    EXPERIMENT, TASK, RUNNER, DESIGN, TOOL_APPROVAL,
    (DESIGN, DESIGN_REVIEW, DESIGN_DELTA_REVIEW, TOOL_REVIEW, TOOL_APPROVAL),
    EXTRA_SOURCES, "minimal-derived-grounding-v1.run.claim", "T504OUTER", True)


def prepare(output: Path, baseline_aggregate_path=None):
    return legacy.prepare(output, baseline_aggregate_path, PROFILE)


def verify(plan):
    return legacy.verify(plan, PROFILE)


def stage(suite, row, private, identity, save, budget):
    return legacy.stage(suite, row, private, identity, save, budget, PROFILE)


def run(output: Path):
    return legacy.run(output, PROFILE)


def supervise(output: Path):
    return legacy.supervise(output, PROFILE)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", action="store_true")
    group.add_argument("--run", action="store_true")
    group.add_argument("--supervise", action="store_true")
    group.add_argument("--prepare-and-supervise", action="store_true")
    args = parser.parse_args()
    try:
        if args.prepare:
            prepare(args.output)
            print("PREPARED")
            return 0
        if args.prepare_and_supervise:
            prepare(args.output)
            result = supervise(args.output)
        else:
            result = supervise(args.output) if args.supervise else run(args.output)
        ok = result.get("status") == "COMPLETE" if args.run else (
            result.get("exit_code") == 0 and result.get("ownership_complete") is True
            and result.get("owned_alive_after") == 0)
        print("COMPLETE" if ok else "STOPPED")
        return 0 if ok else 2
    except Exception as error:
        print(error.code if isinstance(error, legacy.RunError) else "PRIVATE_EVIDENCE_ERROR")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
