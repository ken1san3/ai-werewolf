"""Docs consistency checker.

レビューで繰り返し出た「文書の書き忘れ・書き換え漏れ」を機械的に検出する。
実装と文書、文書と文書の間で機械的に照合できる不変条件だけを見る。

    python scripts/check_docs.py

失敗した検査を1行ずつ出力し、1件でもあれば exit code 1。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "Docs" / "ai"
DESIGN = DOCS / "spec" / "DESIGN.md"
CURRENT_STATE = DOCS / "CURRENT_STATE.md"
CORE = ROOT / "server" / "aiwolf_core"
TASK_BOARD = DOCS / "TASKS.md"
MODEL_ASSIGNMENTS = DOCS / "MODEL_ASSIGNMENTS.md"
ROLE_DOCUMENTS = {
    "Integrator": DOCS / "roles" / "INTEGRATOR.md",
    "Architect": DOCS / "roles" / "ARCHITECT.md",
    "Implementer": DOCS / "roles" / "IMPLEMENTER.md",
    "Reviewer": DOCS / "roles" / "REVIEWER.md",
    "Tester": DOCS / "roles" / "TESTER.md",
    "Investigator": DOCS / "roles" / "INVESTIGATOR.md",
}

RESPONSIBILITIES = {
    "Integrator",
    "Architect",
    "Implementer",
    "Reviewer",
    "Tester",
    "Investigator",
}
MODEL_NAME = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:Astra|Sol|Luna|Terra|Claude|Codex|Qwen|Gemini|GPT-[0-9A-Za-z.-]+)"
    r"(?![A-Za-z0-9_])",
    re.IGNORECASE,
)

sys.path.insert(0, str(ROOT))
from scripts import ai_status

problems: list[str] = []


def fail(check: str, detail: str) -> None:
    problems.append(f"[{check}] {detail}")


# 過去の記録は当時の節番号・ルール名・ファイル構成を保存する。現在の文書との一致は
# 求めない。検査すると、書いた当時は正しかった記述が「不整合」として報告される。
ARCHIVE_DIRS = {"_to_delete", "review_archive"}


def docs_files() -> list[Path]:
    """検査対象。AGENTS.md はリポジトリ直下だが運用の規範なので含める。"""

    files = [p for p in DOCS.rglob("*.md") if not ARCHIVE_DIRS & set(p.parts)]
    files.append(ROOT / "AGENTS.md")
    return sorted(files)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- 1. DESIGN の節番号参照が実在するか -------------------------------------
def check_design_sections() -> None:
    design = read(DESIGN)
    sections = set(re.findall(r"(?m)^#{2,3} (\d+(?:\.\d+)?)[\. ]", design))
    for path in docs_files():
        if path.name in {"SPEC_REVIEW.md", "REVIEW_INBOX.md"}:
            continue  # 過去の指摘は当時の節番号を保存する
        text = read(path)
        for ref in set(re.findall(r"(?:DESIGN(?:\.md)?[^\n]{0,20}?)§(\d+(?:\.\d+)?)", text)):
            if ref not in sections:
                fail("design-section", f"{path.relative_to(ROOT)} が DESIGN §{ref} を参照するが節が無い")


# --- 2. decisions / failures / handoffs の参照が実在するか --------------------
def check_decision_refs() -> None:
    decisions = {p.name.split("_")[0] for p in (DOCS / "decisions").glob("D*.md")}
    failures = {p.name.split("_")[0] for p in (DOCS / "failures").glob("F*.md")}
    for path in docs_files():
        text = read(path)
        for ref in set(re.findall(r"\bD(\d{3})(?:_[A-Z0-9_]+)?\b", text)):
            if f"D{ref}" not in decisions:
                fail("decision-ref", f"{path.relative_to(ROOT)} が D{ref} を参照するが存在しない")
        for ref in set(re.findall(r"\bF(\d{3})(?:_[A-Z0-9_]+)?\b", text)):
            if f"F{ref}" not in failures:
                fail("failure-ref", f"{path.relative_to(ROOT)} が F{ref} を参照するが存在しない")


# --- 3. OPEN_QUESTIONS: 未決と解決済みが重複していないか ----------------------
def check_open_questions() -> None:
    text = read(DOCS / "OPEN_QUESTIONS.md")
    resolved = set(re.findall(r"(?m)^\| (Q\d+)", text))
    open_ids = set(re.findall(r"(?m)^## (Q\d+)", text))
    for qid in sorted(resolved & open_ids):
        fail("open-questions", f"{qid} が解決済み表と未決の両方にある")
    numbers = sorted(int(q[1:]) for q in resolved | open_ids)
    for missing in set(range(1, max(numbers) + 1)) - set(numbers):
        fail("open-questions", f"Q{missing} がどこにも無い（欠番）")


# --- 4. REVIEW_INBOX: ID の重複と欠番 ----------------------------------------
def review_archive_files() -> list[Path]:
    """Return only monthly review records, excluding narrative archives."""

    return sorted((DOCS / "review_archive").glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].md"))


def check_review_inbox() -> None:
    text = read(DOCS / "REVIEW_INBOX.md")
    archive_text = "\n".join(read(path) for path in review_archive_files())
    ids = re.findall(r"(?m)^## (R-\d{8}-\d+) \[", f"{text}\n{archive_text}")
    for rid in sorted({i for i in ids if ids.count(i) > 1}):
        fail("review-inbox", f"{rid} が重複している")
    states = set(re.findall(r"(?m)^## R-\d{8}-\d+ \[(\w+)\]", text))
    for state in sorted(states - {"OPEN", "IN_PROGRESS", "FIXED", "REJECTED", "DEFERRED"}):
        fail("review-inbox", f"未定義の状態 [{state}] がある")
    severities = set(re.findall(r"(?m)^## R-\d{8}-\d+ \[\w+\] (\w+)", text))
    for severity in sorted(severities - {"Critical", "High", "Medium", "Low"}):
        fail("review-inbox", f"未定義の重要度 {severity} がある")
    completed = re.findall(
        r"(?m)^## (R-\d{8}-\d+) \[(FIXED|REJECTED|DEFERRED)\]",
        text,
    )
    for rid, state in completed:
        fail("review-inbox", f"{rid} [{state}] が REVIEW_INBOX.md に残っている")
    for block in re.split(r"(?m)^(?=## R-)", text):
        m = re.match(r"## (R-\d{8}-\d+) \[FIXED\]", block)
        if m and "\nFix:" not in block:
            fail("review-inbox", f"{m.group(1)} が FIXED なのに Fix: 行が無い")


def check_known_failures_have_active_review() -> None:
    """Keep the test-status failure list connected to the work queue."""

    state = read(CURRENT_STATE)
    status = re.search(r"(?ms)^## Test Status\s*\n(.*?)(?=^## |\Z)", state)
    if status is None:
        return
    known = re.search(r"(?m)^Known failing:\s*(.+)$", status.group(1))
    if known is None:
        return
    value = known.group(1).replace("**", "").replace("`", "").strip()
    if value == "なし" or value.startswith("なし。"):
        return
    inbox = read(DOCS / "REVIEW_INBOX.md")
    active = re.findall(r"(?m)^## R-\d{8}-\d+ \[(?:OPEN|IN_PROGRESS)\]", inbox)
    covered_task = False
    if not active:
        task_ids = set(re.findall(r"\bT\d+\b", value))
        covered_task = any(
            record.get("Task ID") in task_ids
            and record.get("State") in ai_status.LIVE_TASK_STATES
            for record in ai_status.task_records(read(TASK_BOARD))
        )
    if not active and not covered_task:
        fail(
            "known-failing-review",
            "CURRENT_STATE.md に既知の失敗があるのに OPEN review または対応する live task が無い",
        )


# --- 5. ai_status が参照する RUNBOOK の role 節が実在するか ----------------
def check_runbook_sections() -> None:
    runbook = read(DOCS / "RUNBOOK.md")
    for role, number in ai_status.ROLE_SECTIONS.items():
        if not ai_status.runbook_section(runbook, number):
            fail("runbook-section", f"role '{role}' の RUNBOOK §{number} が無い")


# --- 5a. 現役責務と model assignment が分離されているか --------------------
def active_workflow_files() -> list[Path]:
    files = [
        ROOT / "AGENTS.md",
        ROOT / "README.md",
        ROOT / "CONTRIBUTING.md",
        DOCS / "INDEX.md",
        DOCS / "CURRENT_STATE.md",
        DOCS / "RUNBOOK.md",
        DOCS / "PROMPTS.md",
        DOCS / "ROADMAP.md",
        DOCS / "TASKS.md",
        DOCS / "WORKFLOW.md",
        DOCS / "ARCHITECTURE.md",
        DOCS / "OPERATIONS.md",
        DOCS / "MAIN_INTEGRATOR_PROMPT.md",
        DOCS / "OPEN_QUESTIONS.md",
        DOCS / "REVIEW_INBOX.md",
    ]
    files.extend(sorted((DOCS / "tasks").glob("T*.md")))
    files.extend(sorted((DOCS / "roles").glob("*.md")))
    files.extend(sorted((DOCS / "prompts").glob("*.md")))
    return files


def check_role_model_separation() -> None:
    for responsibility, path in ROLE_DOCUMENTS.items():
        if not path.is_file():
            fail("role-doc", f"{responsibility} の role document が無い")

    for path in active_workflow_files():
        if not path.is_file():
            fail("role-model", f"{path.relative_to(ROOT)} が見つからない")
            continue
        match = MODEL_NAME.search(read(path))
        if match:
            fail(
                "role-model",
                f"{path.relative_to(ROOT)} に現役割当として解釈され得る model 名 "
                f"'{match.group(0)}' がある",
            )

    if not MODEL_ASSIGNMENTS.is_file():
        fail("model-assignments", "Docs/ai/MODEL_ASSIGNMENTS.md が見つからない")
        return
    text = read(MODEL_ASSIGNMENTS)
    assigned = {
        match.group(1)
        for match in re.finditer(
            r"(?m)^\| (Integrator|Architect|Implementer|Reviewer|Tester|Investigator) \|",
            text,
        )
    }
    for responsibility in sorted(RESPONSIBILITIES - assigned):
        fail(
            "model-assignments",
            f"MODEL_ASSIGNMENTS.md に {responsibility} の行が無い",
        )
    if not MODEL_NAME.search(text):
        fail(
            "model-assignments",
            "MODEL_ASSIGNMENTS.md に現在の具体的な model assignment が無い",
        )


def task_path(value: str) -> Path | None:
    match = re.search(r"`([^`]+)`", value)
    if match is None:
        return None
    try:
        candidate = (ROOT / match.group(1)).resolve()
        candidate.relative_to(ROOT)
    except (OSError, RuntimeError, ValueError):
        return None
    return candidate


def check_task_board() -> None:
    if not TASK_BOARD.is_file():
        fail("task-board", "Docs/ai/TASKS.md が見つからない")
        return

    records = ai_status.task_records(read(TASK_BOARD))
    if not records:
        fail("task-board", "TASKS.md に task record が無い")
        return

    ids = [record.get("Task ID", "") for record in records]
    for task_id in sorted({value for value in ids if value and ids.count(value) > 1}):
        fail("task-board", f"Task ID {task_id} が重複している")

    required = {
        "Task ID",
        "Title",
        "Role",
        "State",
        "Priority",
        "Dependencies",
        "Scope",
        "Goal",
        "Acceptance",
        "Tests",
        "Notes",
        "Expected files",
        "Design Gate",
        "Review required",
        "Task packet",
        "Handoff path",
    }
    by_id: dict[str, dict[str, str]] = {}
    for record in records:
        header_id = record.get("Header ID", "")
        task_id = record.get("Task ID", "")
        if task_id != header_id:
            fail(
                "task-board",
                f"header {header_id or '(missing)'} と Task ID {task_id or '(missing)'} が一致しない",
            )
        if task_id:
            by_id[task_id] = record
        for field in sorted(required - record.keys()):
            fail("task-board", f"{header_id or '(unknown)'} に {field}: が無い")
        state = record.get("State")
        if state not in ai_status.TASK_STATES:
            fail("task-board", f"{task_id or header_id} の State '{state}' は許可語彙外")
        responsibility = record.get("Role")
        if responsibility not in RESPONSIBILITIES:
            fail(
                "task-board",
                f"{task_id or header_id} の Responsibility '{responsibility}' は未定義",
            )

        packet = task_path(record.get("Task packet", ""))
        if packet is None or not packet.is_file():
            fail("task-board", f"{task_id or header_id} の task packet が実在しない")
        else:
            packet_text = read(packet)
            if not re.search(
                rf"(?m)^Task ID:\s*{re.escape(task_id)}\s*$",
                packet_text,
            ):
                fail("task-board", f"{task_id} の packet 内 Task ID が一致しない")
            if not re.search(
                rf"(?m)^Responsibility:\s*{re.escape(responsibility or '')}\s*$",
                packet_text,
            ):
                fail("task-board", f"{task_id} の packet 内 Responsibility が一致しない")

        handoff = task_path(record.get("Handoff path", ""))
        if state == "DONE" and (handoff is None or not handoff.is_file()):
            fail("task-board", f"DONE の {task_id or header_id} に handoff が実在しない")

        gate = record.get("Design Gate")
        if state == "READY" and gate not in {
            "APPROVED",
            "NOT REQUIRED",
            "PRODUCES DESIGN",
        }:
            fail("task-board", f"READY の {task_id or header_id} は Design Gate 未充足")

    state_text = read(CURRENT_STATE)
    active_id = ai_status.active_task_id(state_text)
    if active_id is None:
        fail("task-board", "CURRENT_STATE.md に一意な Active task: Txxx が無い")
        return
    active = by_id.get(active_id)
    if active is None:
        fail("task-board", f"Active task {active_id} が TASKS.md に存在しない")
        return
    if active.get("State") not in ai_status.LIVE_TASK_STATES:
        fail("task-board", f"Active task {active_id} が live state ではない")
    declared_states = re.findall(r"(?m)^Task state:\s*(\w+)\s*$", state_text)
    if declared_states != [active.get("State")]:
        fail(
            "task-board",
            f"CURRENT_STATE の Task state と {active_id} の State が一致しない",
        )

    gate = active.get("Design Gate")
    decision = ai_status.design_gate(state_text)
    if gate == "APPROVED":
        selected = ai_status.target_design(state_text)
        design = DOCS / "design" / (selected or "")
        _, approved = ai_status.design_status(design)
        if decision != "REQUIRED" or not design.is_file() or approved is not True:
            fail(
                "task-board",
                f"{active_id} は Design Gate APPROVED だが CURRENT_STATE/design と不整合",
            )
    elif gate == "NOT REQUIRED" and decision != "NOT REQUIRED":
        fail(
            "task-board",
            f"{active_id} は Design Gate NOT REQUIRED だが CURRENT_STATE と不整合",
        )


# --- 5b. Design Gate の対象と Status 語彙が機械判定できるか ---------------
def check_design_target() -> None:
    state = read(CURRENT_STATE)
    target = ai_status.target_subphase(state)
    decision = ai_status.design_gate(state)
    selected_design = ai_status.target_design(state)
    if target is None and decision is None and selected_design is None:
        return
    if target is None:
        fail("design-target", "CURRENT_STATE.md に Target subphase: 行が無い")
        return
    if decision is None:
        fail(
            "design-target",
            "CURRENT_STATE.md に Design gate: REQUIRED / NOT REQUIRED の宣言が無い、重複、または語彙外",
        )
        return
    roadmap = read(DOCS / "ROADMAP.md")
    subphase_heading = rf"(?m)^## {re.escape(target)}(?:\s|$)"
    phase_heading = rf"(?m)^# Phase {re.escape(target)}(?:\s|$)"
    if not re.search(subphase_heading, roadmap) and not re.search(phase_heading, roadmap):
        fail("design-target", f"Target subphase {target} の ROADMAP 節が無い")
    requests = ai_status.design_gate_documents(target)
    if decision == "NOT REQUIRED" and requests:
        fail(
            "design-target",
            f"Target subphase {target} は Design gate: NOT REQUIRED なのに DESIGN: REQUIRED の REQUEST がある",
        )
    elif decision == "REQUIRED" and not requests:
        fail(
            "design-target",
            f"Target subphase {target} に開いている DESIGN: REQUIRED の REQUEST が無い",
        )
    elif decision == "REQUIRED":
        selected = ai_status.target_design(state)
        if selected is None:
            fail(
                "design-target",
                "CURRENT_STATE.md に Target design: の一意な宣言が無い",
            )
        elif ai_status.target_design_request(requests, selected) is None:
            fail(
                "design-target",
                "CURRENT_STATE.md の Target design: が開いている REQUEST を一意に指していない",
            )


def check_design_gate_status() -> None:
    design_dir = DOCS / "design"
    if not design_dir.is_dir():
        fail("design-status", "Docs/ai/design/ が見つからない")
        return
    for path in sorted(design_dir.glob("*.md")):
        status, valid = ai_status.design_status(path)
        if status.startswith("(missing") or status.startswith("(empty"):
            fail("design-status", f"{path.relative_to(ROOT)} に Status: 行が無い")
        elif valid is None:
            fail(
                "design-status",
                f"{path.relative_to(ROOT)} の Status: '{status}' は許可語彙外",
            )


def check_design_request_status() -> None:
    design_dir = DOCS / "design"
    if not design_dir.is_dir():
        return
    for path in sorted(design_dir.glob("*_REQUEST.md")):
        status, is_open = ai_status.request_status(path)
        if is_open is None:
            fail(
                "design-request-status",
                f"{path.relative_to(ROOT)} の Request status: '{status}' は OPEN / CLOSED の許可語彙外",
            )
            continue
        if is_open is False:
            design = ai_status.design_output_path(path)
            if not design.is_file():
                fail(
                    "design-request-status",
                    f"{path.relative_to(ROOT)} は CLOSED だが対応する _DESIGN.md が無い",
                )
                continue
            _, approved = ai_status.design_status(design)
            if approved is not True:
                fail(
                    "design-request-status",
                    f"{path.relative_to(ROOT)} は CLOSED だが対応する _DESIGN.md が APPROVED ではない",
                )


# --- 6. CURRENT_STATE の next integration action が実在ファイルを指すか ----
def check_next_task_file() -> None:
    text = read(CURRENT_STATE)
    section = re.search(
        r"(?ms)^## Next Integration Action\s*\n(.*?)(?=^## |\Z)",
        text,
    )
    if section is None:
        fail(
            "next-task-file",
            "CURRENT_STATE.md に ## Next Integration Action 節が無い",
        )
        return

    references = re.findall(r"`([^`\r\n]+)`", section.group(1))
    for reference in references:
        if re.match(r"^[a-z][a-z0-9+.-]*://", reference, re.IGNORECASE):
            continue
        relative = Path(reference)
        # CURRENT_STATE 内の参照は、リポジトリルート基準と Docs/ai
        # 基準の両方を許容する。いずれも ROOT 外は対象外にする。
        for base in (ROOT, CURRENT_STATE.parent):
            try:
                candidate = (base / relative).resolve()
                candidate.relative_to(ROOT)
            except (OSError, RuntimeError, ValueError):
                continue
            if candidate.is_file():
                return

    fail(
        "next-task-file",
        "CURRENT_STATE.md の ## Next Integration Action に、バッククォートで囲まれた"
        "リポジトリ内の実在ファイルパスが無い",
    )


# --- 7. DESIGN が挙げるイベント名が実装に存在するか ---------------------------
def check_event_names() -> None:
    core_events = core_event_types()
    design = read(DESIGN)
    for name in set(re.findall(r"`([A-Z][A-Z_]{4,})`", design)):
        if name in {"PUBLIC", "PRIVATE", "SERVER", "AI"}:
            continue
        if name not in core_events:
            fail("event-name", f"DESIGN が `{name}` を挙げるが実装に無い")


def core_event_types() -> set[str]:
    core_events = set()
    for path in CORE.glob("*.py"):
        core_events |= set(re.findall(r'type="([A-Z][A-Z_]+)"', read(path)))
    return core_events


def check_world_core_event_catalog() -> None:
    reducer = read(ROOT / "ai_client" / "world" / "reducer.py")
    block = re.search(
        r"(?ms)^KNOWN_CORE_TYPES = frozenset\(\s*\{(.*?)\}\s*\)",
        reducer,
    )
    if block is None:
        fail("world-event-catalog", "reducer.py の KNOWN_CORE_TYPES を見つけられない")
        return
    catalog = set(re.findall(r'"([A-Z][A-Z_]+)"', block.group(1)))
    core_events = core_event_types()
    missing = sorted(core_events - catalog)
    extra = sorted(catalog - core_events)
    if missing:
        fail(
            "world-event-catalog",
            "core にあって KNOWN_CORE_TYPES に無い event type: " + ", ".join(missing),
        )
    if extra:
        fail(
            "world-event-catalog",
            "KNOWN_CORE_TYPES にあって core に無い event type: " + ", ".join(extra),
        )


def rule_key_paths(block: str) -> set[str]:
    """rules ブロックのキーをドット区切りのパス集合にする。

    入れ子のキーまで見る。`^  (\\w+):` だけを見ていた頃は、`medium.targets` のような
    2階層目の削除漏れを検出できなかった（2026-08-31、R-20260831-71 で発覚）。
    """

    paths: set[str] = set()
    stack: list[str] = []
    for line in block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.match(r"^(\s+)([\w.]+):", line)
        if match is None:
            continue
        depth = len(match.group(1)) // 2 - 1
        if depth < 0:
            continue
        del stack[depth:]
        stack.append(match.group(2))
        paths.add(".".join(stack))
    return paths


# --- 8. DESIGN §5 のルールキーが RulesConfig と一致するか ---------------------
def check_rule_keys() -> None:
    design = read(DESIGN)
    block = re.search(r"# content/presets/standard_9\.yaml\nrules:\n(.*?)\n\nroles:", design, re.S)
    if block is None:
        fail("rule-keys", "DESIGN §5 の rules ブロックを見つけられない")
        return
    design_all = rule_key_paths(block.group(1))
    # 「未実装」注記のあるキーは preset に有っても無くてよい。実装が追いついた時点で
    # 注記が古くなるだけであり、その除去は Reviewer の仕事（Codex に DESIGN を
    # 書き換えさせない）。
    future = future_rule_keys()
    preset = read(ROOT / "content" / "presets" / "standard_9.yaml")
    preset_block = re.search(r"(?ms)^rules:\n(.*?)^roles:", preset)
    preset_keys = rule_key_paths(preset_block.group(1))
    for key in sorted((design_all - future) - preset_keys):
        fail("rule-keys", f"DESIGN §5 に `{key}` があるが standard_9.yaml に無い")
    for key in sorted(preset_keys - design_all):
        fail("rule-keys", f"standard_9.yaml に `{key}` があるが DESIGN §5 に無い")


# --- 9. DESIGN §11 の役職一覧が content と一致するか --------------------------
def check_role_table() -> None:
    design = read(DESIGN)
    table = re.search(r"(?ms)^## 11\. 初期実装役職.*?^\n(\| id \|.*?)\n\n", design)
    if table is None:
        fail("role-table", "DESIGN §11 の役職表を見つけられない")
        return
    design_roles = set(re.findall(r"(?m)^\| `(\w+)` \|", table.group(1)))
    content_roles = {p.stem for p in (ROOT / "content" / "roles").glob("*.yaml")}
    for role in sorted(design_roles - content_roles):
        fail("role-table", f"DESIGN §11 に `{role}` があるが content/roles に無い")
    for role in sorted(content_roles - design_roles):
        fail("role-table", f"content/roles に `{role}` があるが DESIGN §11 に無い")


# --- 10. DESIGN が挙げるチャネル / 死因が content registry にあるか ------------
def check_registry_ids() -> None:
    design = read(DESIGN)
    channels = set(re.findall(r"(?m)^  - id: (\w+)", read(ROOT / "content" / "chat_channels.yaml")))
    block = re.search(r"(?ms)^### 4\.6 ChatChannel\n\n```\n(.*?)```", design)
    if block is None:
        fail("channel-id", "DESIGN §4.6 のチャネル一覧を見つけられない")
    if block:
        for name in re.findall(r"(?m)^(\w+)\s", block.group(1)):
            if name not in channels:
                fail("channel-id", f"DESIGN §4.6 の `{name}` が chat_channels.yaml に無い")
    causes = set(re.findall(r"(?m)^  - id: (\w+)", read(ROOT / "content" / "death_causes.yaml")))
    block = re.search(r"(?ms)^### 7\.2 DeathCause\n\n```\n(.*?)```", design)
    if block is None:
        fail("death-cause", "DESIGN §7.2 の死因一覧を見つけられない")
    if block:
        for name in re.findall(r"(?m)^(\w+)\s", block.group(1)):
            if name not in causes:
                fail("death-cause", f"DESIGN §7.2 の `{name}` が death_causes.yaml に無い")


# --- 11. TEST_POLICY の節が担当 Phase を宣言しているか ------------------------
def check_test_policy_phases() -> None:
    for line in read(DOCS / "TEST_POLICY.md").splitlines():
        if line.startswith("## ") and re.match(r"## \d+\.", line) and "［" not in line:
            fail("test-policy", f"担当 Phase の表記が無い: {line}")


# --- 12. 文書が挙げる rules.<path> が RulesConfig に存在するか ----------------
def future_rule_keys() -> set[str]:
    """DESIGN §5 で「未実装」と注記されたルールキー。実装より先に書いてよい。"""

    block = re.search(r"# content/presets/standard_9\.yaml\nrules:\n(.*?)\n\nroles:", read(DESIGN), re.S)
    if block is None:
        return set()
    return {
        m.group(1)
        for line in block.group(1).splitlines()
        if (m := re.match(r"^  (\w+):", line)) and "未実装" in line
    }


def check_rule_paths() -> None:
    fields = set(re.findall(r"(?m)^    (\w+): ", read(CORE / "models.py")))
    allowed = fields | future_rule_keys()
    # 現況の記録（CURRENT_STATE / REVIEW_INBOX / SPEC_REVIEW）は当時の名前を保存するので見ない。
    narrative = {"CURRENT_STATE.md", "REVIEW_INBOX.md", "SPEC_REVIEW.md"}
    for path in docs_files():
        if path.name in narrative:
            continue
        for ref in set(re.findall(r"`?rules\.([a-z_0-9]+(?:\.[a-z_0-9]+)*)`?", read(path))):
            unknown = [part for part in ref.split(".") if part not in allowed]
            if unknown:
                fail("rule-path", f"{path.relative_to(ROOT)} の rules.{ref} に無い項目 `{unknown[0]}`")


# --- 13. 実装の乱数イベントが DESIGN §10 の表にあるか -------------------------
def check_random_event_table() -> None:
    design = read(DESIGN)
    table = re.search(r"(?ms)^\| ランダム要素 \| イベント \|\n(.*?)\n\n", design)
    if table is None:
        fail("random-table", "DESIGN §10 の乱数イベント表を見つけられない")
        return
    listed = set(re.findall(r"`([A-Z_]+)`", table.group(1)))
    core_events = set()
    for source in CORE.glob("*.py"):
        core_events |= set(re.findall(r'type="([A-Z][A-Z_]+)"', read(source)))
    for name in sorted(core_events):
        if re.search(r"RANDOM|_SELECTED", name) and name not in listed:
            fail("random-table", f"乱数イベント `{name}` が DESIGN §10 の表に無い")


# --- 14. ROADMAP が参照する TEST_POLICY 節が存在するか ------------------------
def check_test_policy_refs() -> None:
    sections = set(re.findall(r"(?m)^## (\d+)\.", read(DOCS / "TEST_POLICY.md")))
    roadmap = read(DOCS / "ROADMAP.md")
    for line in roadmap.splitlines():
        if "TEST_POLICY" not in line:
            continue
        for ref in re.findall(r"§(\d+)", line.split("TEST_POLICY", 1)[1]):
            if ref not in sections:
                fail("test-policy-ref", f"ROADMAP が TEST_POLICY §{ref} を参照するが節が無い")


# --- 15. セッション入口を構成する文書のサイズ上限 ---------------------
# AGENTS.md と ai_status.py の出力元が伸びると、全セッションのコストが
# 恒久的に上がる。上限は引き上げず、古い記録を review_archive/ へ退避する。
SESSION_CONTEXT_LIMITS = {
    ROOT / "AGENTS.md": 8000,
    DOCS / "CURRENT_STATE.md": 12000,
    DOCS / "REVIEW_INBOX.md": 8000,
    DOCS / "ROADMAP.md": 10000,
    DOCS / "RUNBOOK.md": 16000,
    DOCS / "TASKS.md": 12000,
    DOCS / "WORKFLOW.md": 12000,
    DOCS / "ARCHITECTURE.md": 12000,
    DOCS / "OPERATIONS.md": 16000,
    DOCS / "MAIN_INTEGRATOR_PROMPT.md": 8000,
}


def check_session_context_size() -> None:
    for path, limit in sorted(SESSION_CONTEXT_LIMITS.items()):
        if not path.exists():
            fail("doc-size", f"{path.name} が見つからない")
            continue
        content = read(path)
        size = (
            sum(len(block) for block in ai_status.active_review_blocks(content))
            if path == DOCS / "REVIEW_INBOX.md"
            else len(content)
        )
        if size > limit:
            if path == DOCS / "REVIEW_INBOX.md":
                remedy = "OPEN / IN_PROGRESS の指摘を閉じて active ブロックを減らす"
            else:
                remedy = "セッション入口の出力元を分割または短縮する"
            fail(
                "doc-size",
                f"{path.name} が {size} 文字（上限 {limit}）。"
                f"{remedy}",
            )


def main() -> int:
    for check in (
        check_design_sections,
        check_decision_refs,
        check_open_questions,
        check_review_inbox,
        check_known_failures_have_active_review,
        check_runbook_sections,
        check_role_model_separation,
        check_task_board,
        check_design_target,
        check_design_gate_status,
        check_design_request_status,
        check_next_task_file,
        check_event_names,
        check_world_core_event_catalog,
        check_rule_keys,
        check_role_table,
        check_registry_ids,
        check_test_policy_phases,
        check_rule_paths,
        check_random_event_table,
        check_test_policy_refs,
        check_session_context_size,
    ):
        check()
    if problems:
        print(f"{len(problems)} 件の不整合:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("文書の不整合なし")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
