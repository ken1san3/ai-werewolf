#!/usr/bin/env python3
"""Print a one-screen summary of the project's current state.

Reduces the number of files an agent must open at session start.
Read-only: this script never modifies the repository.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Windows の既定コンソールは CP932 で、状態ファイル中の em dash などを
# エンコードできずに落ちる。呼び出し側へ PYTHONIOENCODING を要求せずに済むよう、
# このスクリプト自身が出力を UTF-8 へ切り替える。
for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

ROOT = Path(__file__).resolve().parent.parent
AI = ROOT / "Docs" / "ai"

ROLE_SECTIONS = {"implement": "1", "review": "2", "fix": "3", "design": "4"}
DESIGN_REQUIRED_MARKER = re.compile(r"(?m)^DESIGN: REQUIRED\s*$")
STATUS_LINE = re.compile(r"(?m)^Status:\s*(.*?)\s*$")
STATUS_VALUE = re.compile(
    r"^(REQUESTED|DRAFT|IN_REVIEW|APPROVED|SUPERSEDED)(?:\s+—(?:\s.*)?)?$"
)
TARGET_SUBPHASE_LINE = re.compile(r"(?m)^Target subphase:\s*([0-9]+(?:\.[0-9]+)*)\s*$")
DESIGN_GATE_LINE = re.compile(r"(?m)^Design gate:\s*(.*?)\s*$")
DESIGN_GATE_VALUES = {"REQUIRED", "NOT REQUIRED"}


def section(text: str, heading: str) -> str:
    pattern = rf"^## {re.escape(heading)}\s*$"
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if re.match(pattern, line):
            body: list[str] = []
            for following in lines[index + 1 :]:
                if following.startswith("## "):
                    break
                body.append(following)
            return "\n".join(body).strip()
    return ""


def runbook_section(text: str, number: str) -> str:
    match = re.search(
        rf"(?ms)^## {re.escape(number)}\. .*?(?=^## \d+\.|\Z)", text
    )
    return match.group(0).strip() if match else ""


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def active_review_blocks(inbox: str) -> list[str]:
    """Return complete actionable review blocks in severity order.

    Python's sort is stable, so reviews with the same severity retain their
    order in REVIEW_INBOX.md.
    """

    severity_order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    active: list[tuple[int, str]] = []
    pattern = re.compile(
        r"(?ms)^## R-\d{8}-\d+ \[(OPEN|IN_PROGRESS)\] "
        r"(\w+)\s*\n.*?(?=^## |\Z)"
    )
    for match in pattern.finditer(inbox):
        severity = match.group(2)
        active.append(
            (
                severity_order.get(severity, len(severity_order)),
                match.group(0).rstrip(),
            )
        )
    active.sort(key=lambda item: item[0])
    return [block for _, block in active]


def target_subphase(state: str | None = None) -> str | None:
    """Return the single subphase selected by CURRENT_STATE."""

    if state is None:
        state = read(AI / "CURRENT_STATE.md")
    match = TARGET_SUBPHASE_LINE.search(state)
    return match.group(1) if match else None


def design_gate(state: str | None = None) -> str | None:
    """Return the explicitly declared two-value Design Gate decision."""

    if state is None:
        state = read(AI / "CURRENT_STATE.md")
    declarations = DESIGN_GATE_LINE.findall(state)
    if len(declarations) != 1:
        return None
    value = declarations[0].strip()
    return value if value in DESIGN_GATE_VALUES else None


def design_phase_key(path: Path) -> str | None:
    match = re.match(r"^PHASE(\d+(?:_\d+)*)_", path.stem, flags=re.IGNORECASE)
    return match.group(1).replace("_", ".") if match else None


def design_request_documents() -> list[Path]:
    """Return all request files which explicitly require a Design Gate."""

    design_dir = AI / "design"
    if not design_dir.is_dir():
        return []
    return sorted(
        path
        for path in design_dir.glob("*_REQUEST.md")
        if DESIGN_REQUIRED_MARKER.search(read(path))
    )


def design_gate_documents(target: str | None = None) -> list[Path]:
    """Return required request files for exactly one target subphase."""

    target = target if target is not None else target_subphase()
    if target is None:
        return []
    return [
        path for path in design_request_documents() if design_phase_key(path) == target
    ]


def design_output_path(request: Path) -> Path:
    suffix = "_REQUEST.md"
    return request.with_name(request.name[:-len(suffix)] + "_DESIGN.md")


def display_path(path: Path) -> str:
    candidate = path if path.is_absolute() else ROOT / path
    try:
        return candidate.resolve().relative_to(ROOT).as_posix()
    except (OSError, ValueError):
        return path.as_posix()


def design_status(path: Path) -> tuple[str, bool | None]:
    match = STATUS_LINE.search(read(path))
    if match is None:
        return "(missing Status: line)", None
    value = match.group(1).strip()
    status_match = STATUS_VALUE.fullmatch(value)
    if status_match is None:
        return value or "(empty Status: line)", None
    return value, status_match.group(1) == "APPROVED"


def print_design_gate(state: str) -> None:
    print("=" * 60)
    print("DESIGN GATE")
    print("=" * 60)
    target = target_subphase(state)
    if target is None:
        print("target subphase: UNKNOWN — CURRENT_STATE に Target subphase が無い")
        print("implementation: blocked")
        return
    decision = design_gate(state)
    if decision is None:
        print(
            f"target subphase: Phase {target}: UNKNOWN — Design gate: "
            "REQUIRED / NOT REQUIRED の宣言が無い、重複、または語彙外"
        )
        print("implementation: blocked")
        return
    requests = design_gate_documents(target)
    if decision == "NOT REQUIRED":
        if requests:
            print(
                f"target subphase: Phase {target}: UNKNOWN — "
                "Design gate: NOT REQUIRED に DESIGN: REQUIRED の依頼書がある"
            )
            print("implementation: blocked")
            return
        print(f"target subphase: Phase {target}: DESIGN: NOT REQUIRED")
        print("implementation: allowed")
        return
    if len(requests) != 1:
        print(f"target subphase: Phase {target}: UNKNOWN — DESIGN: REQUIRED の依頼書を一意に決められない")
        print("implementation: blocked")
        return

    request = requests[0]
    design = design_output_path(request)
    print(f"target subphase: Phase {target}: DESIGN: REQUIRED")
    print(f"request document: {display_path(request)}")
    if not design.is_file():
        print(f"design document: {display_path(design)}")
        print("Status: UNKNOWN — _DESIGN.md が無い")
        print("approved: unknown")
        print("implementation: blocked")
        return

    status, approved = design_status(design)
    print(f"design document: {display_path(design)}")
    print(f"Status: {status}")
    if approved is True:
        print("approved: yes")
        print("implementation: allowed")
    elif approved is False:
        print("approved: no")
        print("implementation: blocked")
    else:
        print("approved: unknown")
        print("implementation: blocked")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print the project's dynamic AI session context."
    )
    parser.add_argument(
        "role",
        nargs="?",
        choices=tuple(ROLE_SECTIONS),
        help="append only the matching RUNBOOK section",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state = read(AI / "CURRENT_STATE.md")
    inbox = read(AI / "REVIEW_INBOX.md")
    questions = read(AI / "OPEN_QUESTIONS.md")

    print("=" * 60)
    print("CURRENT PHASE")
    print("=" * 60)
    print(section(state, "Current Phase") or "(unknown)")

    print()
    print("=" * 60)
    print("NEXT TASK")
    print("=" * 60)
    print(section(state, "Next Task") or "(unknown)")

    if args.role == "implement":
        print()
        print_design_gate(state)

    print()
    print("=" * 60)
    print("OPEN REVIEWS")
    print("=" * 60)
    active_reviews = active_review_blocks(inbox)
    if active_reviews:
        print("\n\n".join(active_reviews))
    else:
        print("none")

    print()
    print("=" * 60)
    print("TEST STATUS")
    print("=" * 60)
    print(section(state, "Test Status") or "(unknown)")

    print()
    print("=" * 60)
    print("OPEN QUESTIONS")
    print("=" * 60)
    titles = re.findall(r"^## (Q\d+ .*)$", questions, flags=re.MULTILINE)
    print("\n".join(titles) if titles else "none")

    print()
    print("=" * 60)
    print("GIT")
    print("=" * 60)
    print(git("log", "--oneline", "-3") or "(no history)")
    dirty = git("status", "--short")
    dirty_lines = [line for line in dirty.splitlines() if "_to_delete" not in line]
    print()
    print("dirty files:")
    print("\n".join(dirty_lines) if dirty_lines else "(clean)")

    if args.role:
        print()
        print("=" * 60)
        print(f"RUNBOOK: {args.role}")
        print("=" * 60)
        selected = runbook_section(read(AI / "RUNBOOK.md"), ROLE_SECTIONS[args.role])
        print(selected or "(RUNBOOK section not found)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
