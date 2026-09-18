#!/usr/bin/env python3
"""Print a one-screen summary of the project's current state.

Reduces the number of files an agent must open at session start.
Read-only: this script never modifies the repository.
"""

from __future__ import annotations

import argparse
import hashlib
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

ROLE_SECTIONS = {
    "integrate": "1",
    "architect": "2",
    "design": "2",
    "implement": "3",
    "review": "4",
    "fix": "5",
    "test": "6",
    "investigate": "7",
}
TASK_STATES = {
    "READY",
    "IN_PROGRESS",
    "REVIEW",
    "BLOCKED",
    "DECISION_REQUIRED",
    "DONE",
    "CANCELLED",
}
LIVE_TASK_STATES = {
    "READY",
    "IN_PROGRESS",
    "REVIEW",
    "BLOCKED",
    "DECISION_REQUIRED",
}
TASK_BLOCK = re.compile(r"(?ms)^## (T\d+)\s*$\n(.*?)(?=^## |\Z)")
TASK_FIELD = re.compile(r"(?m)^([A-Za-z][A-Za-z ]+):\s*(.*?)\s*$")
ACTIVE_TASK_LINE = re.compile(r"(?m)^Active task:\s*(T\d+)\s*$")
DESIGN_REQUIRED_MARKER = re.compile(r"(?m)^DESIGN: REQUIRED\s*$")
STATUS_LINE = re.compile(r"(?m)^Status:\s*(.*?)\s*$")
STATUS_VALUE = re.compile(
    r"^(REQUESTED|DRAFT|IN_REVIEW|APPROVED|SUPERSEDED)(?:\s+—(?:\s.*)?)?$"
)
REQUEST_STATUS_LINE = re.compile(r"(?m)^Request status:\s*(.*?)\s*$")
REQUEST_STATUS_VALUE = re.compile(r"^(OPEN|CLOSED)(?:\s+—(?:\s.*)?)?$")
TARGET_SUBPHASE_LINE = re.compile(r"(?m)^Target subphase:\s*([0-9]+(?:\.[0-9]+)*)\s*$")
TARGET_DESIGN_LINE = re.compile(r"(?m)^Target design:\s*(.*?)\s*$")
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


def task_records(board: str | None = None) -> list[dict[str, str]]:
    """Return model-neutral task board records in file order."""

    if board is None:
        board = read(AI / "TASKS.md")
    records: list[dict[str, str]] = []
    for match in TASK_BLOCK.finditer(board):
        record = {key: value for key, value in TASK_FIELD.findall(match.group(2))}
        record["Header ID"] = match.group(1)
        records.append(record)
    return records


def active_task_id(state: str | None = None) -> str | None:
    if state is None:
        state = read(AI / "CURRENT_STATE.md")
    declarations = ACTIVE_TASK_LINE.findall(state)
    return declarations[0] if len(declarations) == 1 else None


def display_task(record: dict[str, str]) -> str:
    task_id = record.get("Task ID", record.get("Header ID", "(unknown)"))
    title = record.get("Title", "(untitled)")
    responsibility = record.get("Role", record.get("Responsibility", "(unknown)"))
    state = record.get("State", "(unknown)")
    packet = record.get("Task packet", "(missing packet)").replace("`", "")
    return f"{task_id} [{state}] {title} — {responsibility}\n  packet: {packet}"


def handoff_summary(record: dict[str, str]) -> str:
    """Show attributable bytes/claims, never infer acceptance or worker liveness."""
    name = record.get("Handoff path", "").strip().strip("`")
    if not name:
        return "  evidence: no handoff pointer"
    path = ROOT / name
    try:
        data = path.read_bytes()
    except OSError:
        return f"  evidence: {name} (missing or unreadable)"
    body = data.decode("utf-8", errors="replace")
    claims = re.findall(r"(?im)^(?:Status|Verdict):[^\n]*", body)
    return (f"  evidence: {name}\n  SHA-256: {hashlib.sha256(data).hexdigest()}\n"
            f"  reported: {'; '.join(claims) or '(inspect artifact)'}; not integrated approval")


def coordination_warnings(
    state: str, records: list[dict[str, str]], *, focus: str | None = None,
) -> list[str]:
    """Small structural/staleness checks, not a second acceptance/dependency engine."""
    warnings: list[str] = []
    ids = [r.get("Task ID", r.get("Header ID", "")) for r in records]
    if len(ids) != len(set(ids)):
        warnings.append("Duplicate task IDs: reconcile TASKS before dispatch.")
    active = active_task_id(state)
    selected = [r for r in records if r.get("Task ID") == active]
    if active is None or len(selected) != 1:
        warnings.append("Active task is missing/ambiguous: reconcile CURRENT_STATE and TASKS.")
    else:
        board_state = selected[0].get("State")
        mirrors = re.findall(r"(?m)^Task state:\s*(\w+)\s*$", state)
        if mirrors != [board_state]:
            warnings.append("Task state mirror differs from TASKS; TASKS owns lifecycle.")
        if board_state not in LIVE_TASK_STATES:
            warnings.append(f"Active {active} is {board_state}: do not redispatch from an old pointer.")
    for record in records:
        task_id = record.get("Task ID", record.get("Header ID", "?"))
        if focus is not None and task_id != focus:
            continue
        lifecycle = record.get("State")
        if lifecycle not in TASK_STATES:
            warnings.append(f"{task_id}: invalid lifecycle; reconcile before dispatch.")
        if lifecycle not in LIVE_TASK_STATES:
            continue
        name = record.get("Handoff path", "").strip().strip("`")
        if name and (ROOT / name).is_file():
            warnings.append(f"{task_id} [{lifecycle}]: handoff exists; inspect returned evidence "
                            "and reconcile, do not redispatch or auto-close.")
        if lifecycle == "IN_PROGRESS":
            warnings.append(f"{task_id}: host ownership UNKNOWN to this read-only script; "
                            "inspect host before recovery, never duplicate dispatch.")
    return warnings


def git_summary() -> str:
    branch = git("branch", "--show-current") or "(unknown branch)"
    head = git("rev-parse", "--short", "HEAD") or "(unknown HEAD)"
    dirty = [
        line for line in git("status", "--short").splitlines() if "_to_delete" not in line
    ]
    return f"{branch} @ {head}; dirty entries: {len(dirty)}"


def target_subphase(state: str | None = None) -> str | None:
    """Return the single subphase selected by CURRENT_STATE."""

    if state is None:
        state = read(AI / "CURRENT_STATE.md")
    match = TARGET_SUBPHASE_LINE.search(state)
    return match.group(1) if match else None


def target_design(state: str | None = None) -> str | None:
    """Return the single design document selected by CURRENT_STATE."""

    if state is None:
        state = read(AI / "CURRENT_STATE.md")
    declarations = TARGET_DESIGN_LINE.findall(state)
    if len(declarations) != 1:
        return None
    value = declarations[0].strip().strip("`").strip()
    return value or None


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


def request_status(path: Path) -> tuple[str, bool | None]:
    """Return the request lifecycle marker and whether it is open.

    Older request files have no marker and remain open for compatibility.  An
    invalid explicit marker is kept in the gate so the documentation checker
    can report it instead of silently dropping a design request.
    """

    match = REQUEST_STATUS_LINE.search(read(path))
    if match is None:
        return "OPEN (implicit)", True
    value = match.group(1).strip()
    status_match = REQUEST_STATUS_VALUE.fullmatch(value)
    if status_match is None:
        return value or "(empty Request status: line)", None
    return value, status_match.group(1) == "OPEN"


def design_request_documents() -> list[Path]:
    """Return open request files which explicitly require a Design Gate."""

    design_dir = AI / "design"
    if not design_dir.is_dir():
        return []
    return sorted(
        path
        for path in design_dir.glob("*_REQUEST.md")
        if DESIGN_REQUIRED_MARKER.search(read(path)) and request_is_open(path)
    )


def design_gate_documents(target: str | None = None) -> list[Path]:
    """Return all open required request files for a target subphase."""

    target = target if target is not None else target_subphase()
    if target is None:
        return []
    return [
        path for path in design_request_documents() if design_phase_key(path) == target
    ]


def target_design_request(
    requests: list[Path], target: str | None
) -> Path | None:
    """Resolve CURRENT_STATE's design selection to one open request."""

    if target is None:
        return None
    target_name = Path(target).name
    matches = [
        request
        for request in requests
        if design_output_path(request).name == target_name
    ]
    return matches[0] if len(matches) == 1 else None


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


def request_is_open(path: Path) -> bool:
    """Return whether a request still contributes an open Design Gate item.

    CLOSED is effective only when its paired design is present and approved.
    This prevents a prematurely closed or malformed design from disappearing
    from the gate.
    """

    _, is_open = request_status(path)
    if is_open is not False:
        return True
    design = design_output_path(path)
    if not design.is_file():
        return True
    _, approved = design_status(design)
    return approved is not True


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
    if not requests:
        print(
            f"target subphase: Phase {target}: UNKNOWN — "
            "開いている DESIGN: REQUIRED の依頼書が無い"
        )
        print("implementation: blocked")
        return

    selected_design = target_design(state)
    request = target_design_request(requests, selected_design)
    if request is None:
        print(
            f"target subphase: Phase {target}: UNKNOWN — "
            "CURRENT_STATE の Target design が開いている REQUEST を一意に指していない"
        )
        print(
            "open request documents: "
            + ", ".join(display_path(item) for item in requests)
        )
        print("implementation: blocked")
        return

    design = design_output_path(request)
    print(f"target subphase: Phase {target}: DESIGN: REQUIRED")
    print(
        "open request documents: "
        + ", ".join(display_path(item) for item in requests)
    )
    print(f"target design: {display_path(design)}")
    print(f"request document: {display_path(request)}")
    if not design.is_file():
        print(f"design document: {display_path(design)}")
        print("Status: UNKNOWN — _DESIGN.md が無い")
        print("approved: unknown")
        print("implementation: blocked")
        return

    status, approved = design_status(design)
    print(f"design document: {display_path(design)}")
    status_match = STATUS_VALUE.fullmatch(status)
    if status_match is None:
        print("Status: UNKNOWN — design Status value is not recognized")
    else:
        # The source line may retain historical reviewer/model attribution after an em
        # dash.  Normal bootstrap output is responsibility-based, so emit only the
        # canonical workflow state here and leave the evidence in its source document.
        print(f"Status: {status_match.group(1)}")
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
        help="select responsibility context; use --details for its RUNBOOK",
    )
    parser.add_argument("--details", action="store_true",
                        help="explicitly inspect selected evidence, design gate and RUNBOOK")
    parser.add_argument("--task", help="assigned task ID; defaults to CURRENT_STATE active task")
    parser.add_argument("--all-live", action="store_true",
                        help="explicitly list all live task pointers for dispatch reconciliation")
    return parser.parse_args()


MAX_READ_SCOPE_CHARS = 120


def reference_scope(line: str, end: int) -> str:
    """Return the packet's own 'read only this much' text that follows one reference."""
    scope = line[end:].strip().lstrip("：:-—").strip().rstrip("。").strip()
    if len(scope) > MAX_READ_SCOPE_CHARS:
        scope = scope[:MAX_READ_SCOPE_CHARS] + "…"
    return scope


def packet_read_entries(record: dict[str, str]) -> list[tuple[str, str]]:
    """Return explicit packet/canonical pointers with the scope the packet declared.

    The scope keeps whole-file reads from replacing the narrower read the packet
    already asked for; it never widens a reference or adds a new one.
    """
    name = record.get("Task packet", "").strip().strip("`")
    entries: list[tuple[str, str]] = [(name, "")] if name else []
    if name:
        packet = (ROOT / name).resolve()
        if not packet.is_relative_to(ROOT.resolve()):
            raise ValueError("task packet is outside repository")
        if not packet.is_file():
            raise ValueError("task packet is missing")
        for line in section(read(packet), "Canonical references").splitlines():
            matches = list(re.finditer(r"`([^`]+)`", line))
            # A scope belongs to exactly one reference; several on a line stay unscoped.
            scope = reference_scope(line, matches[-1].end()) if len(matches) == 1 else ""
            entries += [(match.group(1), scope) for match in matches]
    result: list[tuple[str, str]] = []
    seen: dict[str, int] = {}
    for value, scope in entries:
        path = (ROOT / value).resolve()
        if not path.is_relative_to(ROOT.resolve()):
            raise ValueError("canonical reference is outside repository")
        if not path.is_file():
            raise ValueError("canonical reference is missing")
        normalized = path.relative_to(ROOT.resolve()).as_posix()
        if normalized in seen:
            # Keep the first declared scope; a later bare repeat must not widen it.
            if scope and not result[seen[normalized]][1]:
                result[seen[normalized]] = (normalized, scope)
            continue
        seen[normalized] = len(result)
        result.append((normalized, scope))
    return result


def packet_read_set(record: dict[str, str]) -> list[str]:
    """Return explicit packet/canonical pointers, never preload their contents/history."""
    return [path for path, _ in packet_read_entries(record)]


def main() -> int:
    args = parse_args()
    role = args.role or "integrate"
    state = read(AI / "CURRENT_STATE.md")
    records = task_records()
    selected_id = args.task or active_task_id(state)
    selected = [record for record in records if record.get("Task ID") == selected_id]
    print("AUTHORITY: CURRENT_STATE phase/holds; TASKS lifecycle; packets scope; handoffs evidence.")
    print("Historical next actions never authorize dispatch. Host/process ownership: UNKNOWN here.")
    hold = section(state, "Continuation Hold")
    if hold:
        print("CONTINUATION HOLD\n" + hold + "\n")
    print("CURRENT PHASE\n" + (section(state, "Current Phase") or "(unknown)"))
    print(f"ACTIVE TASK: {active_task_id(state) or 'UNKNOWN'}; SELECTED TASK: {selected_id or 'UNKNOWN'}")
    print("GIT\n" + git_summary())
    if len(selected) != 1:
        print("Task selection missing/ambiguous; reconcile TASKS. No fallback or dispatch.")
        return 1
    record = selected[0]
    print("\nSELECTED TASK\n" + display_task(record))
    if record.get("State") not in LIVE_TASK_STATES:
        print("Historical task: do not redispatch. Inspect archive only if explicitly needed.")
        return 1
    print("Acceptance: " + record.get("Acceptance", "UNKNOWN"))
    print("Required independence: " + record.get("Review required", "UNKNOWN"))
    print("Design Gate (declared only): " + record.get("Design Gate", "UNKNOWN"))
    print("Status output is not design approval, acceptance or execution authorization.")
    try:
        entries = packet_read_entries(record)
    except ValueError as error:
        print(f"READ SET ERROR: {error}; reconcile packet before dispatch.")
        return 1
    print("\nCONTEXT READ SET\nAGENTS.md (active rules)\n" + "\n".join(
        f"{path} — {scope}" if scope else path for path, scope in entries))
    print("Read only the scope shown after a path; a scoped reference is not a whole-file read.")
    print("Relevant diff only; additional sources only for a named unresolved question.")
    if args.all_live:
        print("\nLIVE TASK POINTERS\n" + "\n".join(
            display_task(item) for item in records if item.get("State") in LIVE_TASK_STATES))
    print("\nRECONCILIATION")
    print("\n".join(coordination_warnings(state, records, focus=selected_id)) or
          "No structural warning; still verify dependencies, evidence and host ownership.")
    print("\nCURRENT BLOCKERS\n" + (section(state, "Current Blockers") or "(unknown)"))
    if role == "integrate":
        print("\nNEXT INTEGRATION ACTION\n" + (section(state, "Next Integration Action") or "(unknown)"))
    if args.details:
        print("\nSELECTED EVIDENCE (explicit details only)\n" + handoff_summary(record))
        print_design_gate(state)
        print("Design permission only; continuation holds, task scope and independent gates still apply.")
        print(f"\nRUNBOOK: {role}")
        print(runbook_section(read(AI / "RUNBOOK.md"), ROLE_SECTIONS[role]) or "(not found)")
    print("\nRouting on demand: Docs/ai/INDEX.md; Docs/ai/OPERATIONS.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
