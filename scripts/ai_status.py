#!/usr/bin/env python3
"""Print a one-screen summary of the project's current state.

Reduces the number of files an agent must open at session start.
Read-only: this script never modifies the repository.
"""

from __future__ import annotations

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


def main() -> int:
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

    print()
    print("=" * 60)
    print("OPEN REVIEWS")
    print("=" * 60)
    open_items = re.findall(r"^## (R-\S+) \[OPEN\] (\w+)", inbox, flags=re.MULTILINE)
    if open_items:
        by_severity: dict[str, list[str]] = {}
        for identifier, severity in open_items:
            by_severity.setdefault(severity, []).append(identifier)
        for severity in ("Critical", "High", "Medium", "Low"):
            if severity in by_severity:
                print(f"{severity}: {', '.join(by_severity[severity])}")
        for severity, ids in by_severity.items():
            if severity not in ("Critical", "High", "Medium", "Low"):
                print(f"{severity}: {', '.join(ids)}")
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
