"""Classify every tracked file of a git tree into KEEP / EDIT / DELETE for the rebuild.

Usage:
  python make_manifest.py <repo_dir> <git_ref> <out_tsv>          # write the manifest
  python make_manifest.py --apply <manifest.tsv> <tree_dir>       # delete DELETE rows inside an exported tree (verification only)
"""
from __future__ import annotations

import csv
import fnmatch
import subprocess
import sys
from pathlib import Path

# Game/server/protocol rule decisions that DESIGN.md still cites.  Everything else in decisions/ is process history.
KEEP_DECISIONS = {f"D{n:03d}" for n in list(range(1, 34)) + list(range(35, 43)) + list(range(44, 51))}

KEEP_TESTS = {
    "tests/__init__.py",
    "tests/test_action_resolver.py",
    "tests/test_available_actions.py",
    "tests/test_content_models.py",
    "tests/test_game_state.py",
    "tests/test_network_review_regressions.py",
    "tests/test_network_sessions.py",
    "tests/test_phase1_completion.py",
    "tests/test_phase2_completion.py",
    "tests/test_phase_manager.py",
    "tests/test_player_interactions.py",
    "tests/test_protocol_schema.py",
    "tests/test_rejections.py",
    "tests/test_runtime_capabilities.py",
    "tests/test_state_delivery.py",
    "tests/test_voting.py",
    "tests/test_win_evaluator.py",
    "tests/fixtures/network_dummy_client.py",
}

# (pattern, disposition, reason) -- first match wins
RULES: list[tuple[str, str, str]] = [
    # ---- root ----
    (".editorconfig", "KEEP", "エディタ設定"),
    (".gitattributes", "KEEP", "改行コード固定"),
    (".gitignore", "EDIT", "ゲーム出力先(games/)を追加"),
    ("AGENTS.md", "EDIT", "1ページの新ルールへ全面置換"),
    ("README.md", "EDIT", "現状・起動方法・構成を書き直し"),
    ("pyproject.toml", "EDIT", "packagesとpytest markerを新構成に合わせる"),
    ("CONTRIBUTING.md", "DELETE", "旧6役割の運用説明"),
    ("EXTERNAL_REVIEW_*.md", "DELETE", "旧運用の外部レビュー記録"),
    ("PHASE6_*.md", "DELETE", "旧Phase6の方向転換・修正提案"),
    (".github/*", "DELETE", "Actionsは停止中。旧運用のテンプレートとCI。必要になったら簡単なCIを新設"),
    # ---- code ----
    ("server/*", "KEEP", "ゲームコア・ネットワークサーバ（テスト通過を確認済み）"),
    ("protocol/*", "KEEP", "プロトコル定義（v1.1が現役、v1は旧版として保持）"),
    ("content/*", "KEEP", "役職・プリセットのデータ"),
    ("ai_client/*", "DELETE", "AI側は作り直す（必要ならタグから network/world を復元）"),
    ("scripts/*", "DELETE", "旧runner・Phase6実験・運用スクリプト"),
    # ---- tests ----
    ("tests/conftest.py", "EDIT", "completion markerをPhase2完了テストだけに縮小"),
    ("tests/*", "DELETE", "AIクライアント・旧runner・旧運用ツールのテスト"),
    # ---- docs ----
    ("Docs/ai/spec/DESIGN.md", "KEEP", "ゲーム仕様の正本"),
    ("Docs/ai/spec/JUDGMENT_REFERENCE.md", "KEEP", "人狼ジャッジメントの参照ルール"),
    ("Docs/ai/TEST_POLICY.md", "EDIT", "サーバ側の検証方針。冒頭のPhase 6節だけ削除"),
    ("Docs/ai/spec/AI_WEREWOLF_CODEX_HANDOFF.md", "DELETE", "初期構想書。製品要件はREADMEへ移す（AI設計の節は旧前提）"),
    ("Docs/ai/spec/*", "DELETE", "旧運用・開発補助環境の記録"),
    ("Docs/ai/decisions/*", "DECISION", ""),
    ("Docs/ai/*", "DELETE", "旧運用文書・handoff・task・設計・履歴"),
    ("Docs/*", "DELETE", "公開手順チェックリスト（旧運用）"),
]


def classify(path: str) -> tuple[str, str]:
    if path in KEEP_TESTS:
        return "KEEP", "サーバ・プロトコルのテスト"
    for pattern, disposition, reason in RULES:
        if fnmatch.fnmatchcase(path, pattern):
            if disposition == "DECISION":
                name = Path(path).name
                if name[:4] in KEEP_DECISIONS:
                    return "KEEP", "ゲーム・サーバ・プロトコルの決定理由（DESIGN.mdが参照）"
                return "DELETE", "旧運用・AIクライアント・Phase6の決定記録"
            return disposition, reason
    raise SystemExit(f"unclassified path: {path}")


def write_manifest(repo: str, ref: str, out: str) -> None:
    files = subprocess.run(["git", "-C", repo, "ls-tree", "-r", "--name-only", ref],
                           check=True, capture_output=True, text=True, encoding="utf-8").stdout.splitlines()
    rows = []
    for f in files:
        blob = subprocess.run(["git", "-C", repo, "show", f"{ref}:{f}"], check=True, capture_output=True).stdout
        lines = blob.count(b"\n")
        disposition, reason = classify(f)
        rows.append((f, disposition, lines, reason))
    with open(out, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["path", "disposition", "lines", "reason"])
        w.writerows(rows)
    summary: dict[str, list[int]] = {}
    for _, d, n, _ in rows:
        summary.setdefault(d, [0, 0])
        summary[d][0] += 1
        summary[d][1] += n
    print(f"{ref}: {len(rows)} files")
    for d, (count, lines) in sorted(summary.items()):
        print(f"  {d:7s} files={count:5d} lines={lines:7d}")


def apply(manifest: str, tree: str) -> None:
    removed = 0
    with open(manifest, encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            if row["disposition"] == "DELETE":
                p = Path(tree) / row["path"]
                if p.exists():
                    p.unlink()
                    removed += 1
    for d in sorted((p for p in Path(tree).rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()
    print("removed", removed)


if __name__ == "__main__":
    if sys.argv[1] == "--apply":
        apply(sys.argv[2], sys.argv[3])
    else:
        write_manifest(sys.argv[1], sys.argv[2], sys.argv[3])
