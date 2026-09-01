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


def design_gate_documents() -> list[Path]:
    """Return design documents that declare a required Design Gate."""

    design_dir = AI / "design"
    if not design_dir.is_dir():
        return []
    return sorted(
        path
        for path in design_dir.glob("*.md")
        if DESIGN_REQUIRED_MARKER.search(read(path))
    )


def design_phase_label(path: Path) -> str:
    match = re.search(r"PHASE(\d+(?:_\d+)*)", path.stem, flags=re.IGNORECASE)
    return f"Phase {match.group(1).replace('_', '.')}" if match else path.stem


def design_status(path: Path) -> tuple[str, bool]:
    match = STATUS_LINE.search(read(path))
    value = match.group(1).strip() if match else "(missing Status: line)"
    approved = bool(
        re.match(
            r"^(?:DESIGN REVIEW:\s*)?APPROVED(?:\s|$)",
            value,
            flags=re.IGNORECASE,
        )
    )
    return value, approved


def print_design_gate() -> None:
    print("=" * 60)
    print("DESIGN GATE")
    print("=" * 60)
    documents = design_gate_documents()
    if not documents:
        print("target subphase: DESIGN: NOT REQUIRED (no declarative design marker)")
        return
    for path in documents:
        status, approved = design_status(path)
        print(f"target subphase: {design_phase_label(path)}: DESIGN: REQUIRED")
        print(f"design document: {path.relative_to(ROOT)}")
        print(f"Status: {status}")
        print(f"approved: {'yes' if approved else 'no'}")


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
        print_design_gate()

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
