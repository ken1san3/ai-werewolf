# D051 Implementation Design Gate

## Status

Accepted（ユーザー決定 2026-09-01）

## Context

実装タスクの規模が Phase ごとに大きく振れる。局所的な bug fix と、
新しい subsystem の新設が同じ経路で Implementer へ渡っていた。
後者は実装方法が複数あり、選択を誤ると手戻りが複数モジュールへ広がる。

一方で、すべてのタスクに詳細設計を挟むと、
自明な修正にまで設計書という二重管理を作ることになる。

## Decision

**Reviewer（Claude）は、新しい実装タスクへ進む前に
「詳細設計が必要か」を判定する門を持つ。** 判定は2値。

```
Reviewer が判定
├─ 不要 → Implementer へ実装指示
└─ 必要 → Detailed Design → Reviewer が設計レビュー
          → Implementer が実装 → Reviewer が実装レビュー
```

役割と、その役割に現在割り当てているモデルは別物とする。
モデルを差し替えても役割の定義は変わらない。

| 役割 | default model |
|---|---|
| Reviewer / Design Gate | Claude |
| Detailed Design | Sol |
| Implementer / Local-LLM Orchestrator | Luna |

**不要**（Implementer へ直接）: 局所的な bug fix / Reviewer 指摘への明確な修正 /
validation 追加 / テスト追加 / 既存 pattern に従う実装 /
canonical design から実装方法がほぼ一意 / public API と state 構造を新設しない /
component 間の責務変更が無い / 小規模な既存機能拡張。

**必要**（Detailed Design へ）: 新しい subsystem・module / 複数 component の責務分担 /
新しい state machine / lifecycle / async・concurrency / queue /
timeout・reconnect / network protocol との複雑な相互作用 / public API の新設 /
変更影響が複数モジュールへ広がる / 実装方法が複数あり選択を誤ると手戻りが大きい /
canonical design が目的だけを定め実装構造を定めていない / Phase の中核となる新機能。

出力の形は `RUNBOOK.md` のレビュー節に置く。

**レビュー報告は必ず「次に誰へ何を送るか」で締める**（ユーザー決定 2026-09-01）。
そのまま送れる文面を1つ名指しし、同じ内容を `CURRENT_STATE.md` の Next Task へ残す。
ユーザーが送り先を判断しなくてよい状態にすることが、この門の出口である。

**詳細設計の粒度。** 完成コードを書かせない。関数内部を1行ずつ指定しない。
必要なのは Purpose / Files / Responsibilities / Public interfaces / Data flow /
State・lifecycle / Main control flow / Failure handling / Concurrency assumptions /
Out of scope / Acceptance criteria / Required tests。
目的は「Implementer が重要な設計判断をせずに安全に書ける状態」を作ることだけである。

**実装レビューの優先順位は変えない。** 承認済み詳細設計を正しい前提として扱わず、
1. canonical source 2. 実コード 3. schema 4. tests 5. approved detailed design
の順に確認する。**設計どおりでも canonical specification に反していれば指摘する。**

## Why

設計判断の場所を Implementer の中から外へ出すため。
実装中に下された設計判断は、コードを読むまで誰にも見えず、
レビューの時点では手戻りが最も高くつく状態になっている。

門を2値にしたのは、判定そのものが重い工程になるのを避けるためである。
判定リストは「どちらか迷ったら必要」ではなく、
**列挙に当たれば必要、当たらなければ不要**として運用する。

承認済み詳細設計を canonical source より上に置かないのは、
設計が仕様を上書きできてしまうと、Reviewer が仕様違反を
「設計どおり」として通してしまうためである。

## Consequences

- Phase の中核（Phase 3.1、4.1、5、7 など）は原則 Detailed Design を通る
- Reviewer 指摘の修正は従来どおり Implementer へ直行する。門で滞留させない
- 詳細設計は承認後も canonical ではない。DESIGN.md と矛盾したら DESIGN.md が勝つ
- 詳細設計と DESIGN REQUEST は `Docs/ai/design/` へ置く。
  最初の1件は `design/PHASE3_1_NETWORK_CLIENT_REQUEST.md`。
  ここは canonical ではない。`DESIGN.md` / `ROADMAP.md` と矛盾したらそちらが勝つ
- `AGENTS.md` の Roles 表は「役割 / default model」の2列になる。
  Codex を通常の Implementer として併用する予定は無い（ユーザー確認済み）
