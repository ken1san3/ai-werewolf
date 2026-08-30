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
CORE = ROOT / "server" / "aiwolf_core"

problems: list[str] = []


def fail(check: str, detail: str) -> None:
    problems.append(f"[{check}] {detail}")


def docs_files() -> list[Path]:
    """検査対象。AGENTS.md はリポジトリ直下だが運用の規範なので含める。"""

    files = [p for p in DOCS.rglob("*.md") if "_to_delete" not in p.parts]
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
def check_review_inbox() -> None:
    text = read(DOCS / "REVIEW_INBOX.md")
    ids = re.findall(r"(?m)^## (R-\d{8}-\d+) \[", text)
    for rid in sorted({i for i in ids if ids.count(i) > 1}):
        fail("review-inbox", f"{rid} が重複している")
    states = set(re.findall(r"(?m)^## R-\d{8}-\d+ \[(\w+)\]", text))
    for state in sorted(states - {"OPEN", "FIXED", "REJECTED", "DEFERRED"}):
        fail("review-inbox", f"未定義の状態 [{state}] がある")
    for block in re.split(r"(?m)^(?=## R-)", text):
        m = re.match(r"## (R-\d{8}-\d+) \[FIXED\]", block)
        if m and "\nFix:" not in block:
            fail("review-inbox", f"{m.group(1)} が FIXED なのに Fix: 行が無い")


# --- 5. DESIGN が挙げるイベント名が実装に存在するか ---------------------------
def check_event_names() -> None:
    core_events = set()
    for path in CORE.glob("*.py"):
        core_events |= set(re.findall(r'type="([A-Z][A-Z_]+)"', read(path)))
    design = read(DESIGN)
    for name in set(re.findall(r"`([A-Z][A-Z_]{4,})`", design)):
        if name in {"PUBLIC", "PRIVATE", "SERVER", "AI"}:
            continue
        if name not in core_events:
            fail("event-name", f"DESIGN が `{name}` を挙げるが実装に無い")


# --- 6. DESIGN §5 のルールキーが RulesConfig と一致するか ---------------------
def check_rule_keys() -> None:
    design = read(DESIGN)
    block = re.search(r"# content/presets/standard_9\.yaml\nrules:\n(.*?)\n\nroles:", design, re.S)
    if block is None:
        fail("rule-keys", "DESIGN §5 の rules ブロックを見つけられない")
        return
    design_lines = block.group(1).splitlines()
    design_keys = {
        m.group(1)
        for line in design_lines
        if (m := re.match(r"^  (\w+):", line)) and "未実装" not in line
    }
    preset = read(ROOT / "content" / "presets" / "standard_9.yaml")
    preset_block = re.search(r"(?ms)^rules:\n(.*?)^roles:", preset)
    preset_keys = set(re.findall(r"(?m)^  (\w+):", preset_block.group(1)))
    for key in sorted(design_keys - preset_keys):
        fail("rule-keys", f"DESIGN §5 に `{key}` があるが standard_9.yaml に無い")
    for key in sorted(preset_keys - design_keys):
        fail("rule-keys", f"standard_9.yaml に `{key}` があるが DESIGN §5 に無い")


# --- 7. DESIGN §11 の役職一覧が content と一致するか --------------------------
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


# --- 8. DESIGN が挙げるチャネル / 死因が content registry にあるか ------------
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


# --- 9. TEST_POLICY の節が担当 Phase を宣言しているか -------------------------
def check_test_policy_phases() -> None:
    for line in read(DOCS / "TEST_POLICY.md").splitlines():
        if line.startswith("## ") and re.match(r"## \d+\.", line) and "［" not in line:
            fail("test-policy", f"担当 Phase の表記が無い: {line}")


# --- 10. 文書が挙げる rules.<path> が RulesConfig に存在するか ----------------
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


# --- 11. 実装の乱数イベントが DESIGN §10 の表にあるか -------------------------
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


# --- 12. ROADMAP が参照する TEST_POLICY 節が存在するか ------------------------
def check_test_policy_refs() -> None:
    sections = set(re.findall(r"(?m)^## (\d+)\.", read(DOCS / "TEST_POLICY.md")))
    roadmap = read(DOCS / "ROADMAP.md")
    for line in roadmap.splitlines():
        if "TEST_POLICY" not in line:
            continue
        for ref in re.findall(r"§(\d+)", line.split("TEST_POLICY", 1)[1]):
            if ref not in sections:
                fail("test-policy-ref", f"ROADMAP が TEST_POLICY §{ref} を参照するが節が無い")


def main() -> int:
    for check in (
        check_design_sections,
        check_decision_refs,
        check_open_questions,
        check_review_inbox,
        check_event_names,
        check_rule_keys,
        check_role_table,
        check_registry_ids,
        check_test_policy_phases,
        check_rule_paths,
        check_random_event_table,
        check_test_policy_refs,
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
