# AI Documentation Index

AIエージェントが「どのファイルを読むか」だけを決める詳細索引。
内容そのものはここに書かない。セッションの動的入口は必ず
`python scripts/ai_status.py <role>` を使い、この索引を毎回は読まない。

## Session entry

- bootstrap / 恒久ルール: `../../AGENTS.md`
- 動的状態と役割別手順: `../../scripts/ai_status.py`

`ai_status.py` が `CURRENT_STATE.md` / `REVIEW_INBOX.md` / `OPEN_QUESTIONS.md` の
必要部分と、指定roleの `RUNBOOK.md` 節を直接出力する。

## 実装に入る前に読む

- `spec/DESIGN.md` — 設計の結論。**まずこれを読む**
- `TEST_POLICY.md` — 検証項目。各節の見出しが担当 Phase を示す

`decisions/` は「なぜそう決めたか」の記録。
設計を変えたくなったとき、または DESIGN.md の意図が読み取れないときだけ開く。

## Current phase

**ここには書かない。** 現在フェーズと次にやることは `CURRENT_STATE.md` にだけ置く。
2箇所に書くと必ず片方が古くなる。

どのフェーズでも参照するもの:

- `spec/DESIGN.md`
- `TEST_POLICY.md`
- `spec/JUDGMENT_REFERENCE.md`（参照実装の事実。**再調査せずここを見る**）
- `OPEN_QUESTIONS.md`

## Read only if needed

| 目的 | ファイル |
|---|---|
| 設計判断の理由 | `decisions/` |
| 過去のレビュー指摘の本文 | `review_archive/<年-月>.md` |
| 過去のレビューの判断と観察 | `review_archive/REVIEW_LOG.md` |
| 各 Phase で何を作ったかの経緯 | `review_archive/BUILD_LOG.md` |
| 元仕様への指摘 | `SPEC_REVIEW.md` |
| 元仕様の原文 | `spec/AI_WEREWOLF_CODEX_HANDOFF.md` |
| 運用ルールの根拠 | `spec/CODEX_TOKEN_EFFICIENT_WORKFLOW.md` |
| Qwen実装runner・利用枠・コスパ集計 | `spec/LOCAL_IMPLEMENTATION_RUNNER.md`（入口: `../../scripts/ai_status.py infra`） |
| 有限キューの無人開発・独立レビュー・再開 | `spec/AUTONOMOUS_DEVELOPMENT.md` / `handoffs/INFRA_AUTONOMOUS_HANDOFF.md` |
| 新しい設計判断までの連続実装 | `spec/OVERNIGHT_DEVELOPMENT.md` / `infra/OVERNIGHT_PACKAGE_GUIDE.md`（`../../Run-Overnight.cmd`） |
| 起動・停止・復旧・利用量（利用者向け） | `infra/OVERNIGHT_QUICKSTART.md` |
| D060基盤の検証・引継ぎ | `handoffs/EFFICIENT_OVERNIGHT_HANDOFF.md` |
| 詳細設計と DESIGN REQUEST（canonical ではない） | `design/` |
| ロードマップ全体 | `ROADMAP.md` |
| 文書と実装の不整合検査 | `../../scripts/check_docs.py` |
| Phase引継ぎ | `handoffs/` |
| 過去の失敗 | `failures/` |

`review_archive/` は**過去の記録**であり、当時の節番号・ファイル構成・ルール名を
そのまま保存する。現在の文書との一致は求めず、`check_docs.py` も検査しない。
**現在の状態を書き足さない。** FIXED / REJECTED / DEFERRED の指摘は
ここへ退避し、現在の状態は `CURRENT_STATE.md` にだけ置く。
ただし月別レビュー記録の `R-YYYYMMDD-NN` 採番だけは、重複防止のため
`check_docs.py` の検査対象になる。

## Phaseごとの主読込範囲

| Phase | 読むもの | 読まないもの |
|---|---|---|
| 1 ゲームコア | Role / Team / Ability / Effect / GameState / WinCondition / tests | AI Client 仕様 |
| 2 ネットワーク | CURRENT_STATE / PHASE1_HANDOFF / Protocol / Session / 公開・非公開状態 | LLM詳細 |
| 3 AI Skeleton | Protocol / Client / WorldState / Dummy Brain | Roleエンジン内部（必要時のみ） |
| 4 Local LLM | Brain Interface / LLM Backend / Structured Output | ゲームコア内部 |
