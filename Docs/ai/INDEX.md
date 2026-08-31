# AI Documentation Index

AIエージェントが「どのファイルを読むか」だけを決めるためのファイル。
内容そのものはここに書かない。

## Always read

- `RUNBOOK.md` — 指示に対応する手順。**まずこれ**
- `ROADMAP.md` — 対象サブPhaseのスコープ
- `CURRENT_STATE.md`
- `REVIEW_INBOX.md`（OPEN の Critical / High があれば新機能より先に対応）

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

`review_archive/` は**過去の記録**であり、当時の節番号・ファイル構成・ルール名を
そのまま保存する。現在の文書との一致は求めず、`check_docs.py` も検査しない。
**追記しない。** 現在の状態は `CURRENT_STATE.md` にだけ置く。

## 文書のサイズ

`Always read` の文書は毎セッション必ず文脈へ入る。伸びると全セッションの
コストが恒久的に上がるため、`check_docs.py` が上限を検査する。
上限に当たったら**上限を上げるのではなく、古い記録を `review_archive/` へ退避する。**
| ロードマップ全体 | `ROADMAP.md` |
| 文書と実装の不整合検査 | `scripts/check_docs.py` |
| Phase引継ぎ | `handoffs/` |
| 過去の失敗 | `failures/` |

## Not needed in the current phase

Phase 1（ゲームコア）では以下を読まない。

```
AI Client 仕様          spec master §12〜§21, §33〜§36
LLM / 人格 / typing演出  spec master §16〜§21
ネットワーク詳細         spec master §4（プロトコル境界の把握のみで可）
Web UI                  spec master §Phase 7
MOD                     spec master §23
```

## Phaseごとの主読込範囲

| Phase | 読むもの | 読まないもの |
|---|---|---|
| 1 ゲームコア | Role / Team / Ability / Effect / GameState / WinCondition / tests | AI Client 仕様 |
| 2 ネットワーク | CURRENT_STATE / PHASE1_HANDOFF / Protocol / Session / 公開・非公開状態 | LLM詳細 |
| 3 AI Skeleton | Protocol / Client / WorldState / Dummy Brain | Roleエンジン内部（必要時のみ） |
| 4 Local LLM | Brain Interface / LLM Backend / Structured Output | ゲームコア内部 |
