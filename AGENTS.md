# Project AI Rules

## このリポジトリの動かし方

ユーザーが打つ指示は原則4つだけ。**指示を受けたら対応する
`python scripts/ai_status.py <role>` を実行し、出力された RUNBOOK 節に従う。
ユーザーへ追加の指示を求めない。**

| 指示 | 送る先（役割 / model） | 手順 |
|---|---|---|
| 「レビューして」 | Reviewer / Claude または Sol | `python scripts/ai_status.py review` |
| 「Phase X.Y を詳細設計して」 | Detailed Design / Sol | `python scripts/ai_status.py design` |
| 「Phase X.Y を実装して」「次のフェーズを実装して」 | Implementer / Luna | `python scripts/ai_status.py implement` |
| 「レビュー内容を確認して修正して」 | Implementer / Luna | `python scripts/ai_status.py fix` |

**どれを送るかは Reviewer が決める。** 迷ったら「レビューして」を Reviewer へ送る。
Reviewer が Design Gate（D051）で次の1手を名指しし、`CURRENT_STATE.md` の
Next Task に残す。ユーザーはそれをそのまま送ればよい。

- **実装の指示は、まず Design Gate を通る**（D051 / RUNBOOK §1.2 / §2.5）。
  `DESIGN: REQUIRED` と判定されたサブPhaseは、承認済み詳細設計が無いかぎり実装しない
- フェーズ番号の指定が無ければ `ai_status.py` が出力する Next Task に従う
- スコープ（含む / 含まない / 完了条件）は `Docs/ai/ROADMAP.md` の該当サブPhaseにある
- 判断に迷ったら `Docs/ai/OPEN_QUESTIONS.md` へ起票し、避けて進められるなら続行する
- 現状の要約は `python scripts/ai_status.py` で取得できる

## Roles

**役割と、その役割に現在割り当てているモデルは別物である。**
モデルの変更は役割の定義を変えない。

| 役割 | default model | やること |
|---|---|---|
| Implementer / Local-LLM Orchestrator | Luna | 実装 / テスト / ローカルLLMの運用 / CURRENT_STATE・handoff・decision・failure の更新 |
| Detailed Design | Sol | Reviewer が必要と判定したタスクの詳細設計（D051） |
| Reviewer / Design Gate（通常） | Claude, Sol | 詳細設計の要否判定 / 詳細設計レビュー / 仕様整合レビュー / REVIEW_INBOX 起票 / 仕様と設計ドキュメントの整備（D053） |
| Reviewer（深掘り） | Sol | 同上。対象を絞って深く見る（D053） |

- **新しい実装タスクは Reviewer の Implementation Design Gate を通る**（D051 / RUNBOOK §2.5）。
  不要と判定すれば Implementer へ直行、必要なら Detailed Design → Reviewer 承認 → Implementer。
- Reviewer は原則コードを書かない。指摘は `Docs/ai/REVIEW_INBOX.md` へ残し、修正は Implementer が行う。
- **Reviewer は2 model いる（D053）。Sol の各レーンは別チャットで動く。**
  `_DESIGN.md` の `Status: APPROVED` だけは、設計を書いた model と別の model が付ける
  （Sol の設計は Claude が承認）。model の違いが見落としを捕まえた実績による（R-97）。
- Implementer は仕様を勝手に変更しない。疑問は `Docs/ai/OPEN_QUESTIONS.md` へ起票する。
- 仕様・ルールの最終決定権はユーザーにある。各役割とも決定を `Docs/ai/decisions/` へ記録する。

## Start of session

`AGENTS.md` を bootstrap / 恒久ルールとして毎セッション読む。次に作業役割の
`python scripts/ai_status.py <role>` を実行する。`ai_status.py` は動的コンテキストの
**唯一の入口**である。引数なしは状態確認専用で、RUNBOOK を出力しない。

`CURRENT_STATE.md` / `REVIEW_INBOX.md` / `RUNBOOK.md` を開始時に別途開かない。
`ai_status.py` の出力が追加で名指したファイルだけを読む。必要な文書の
詳細索引は `Docs/ai/INDEX.md` だけに置き、ここに複製しない。

Do not scan the whole repository unless necessary.

## Design invariants

1. Server is the single source of truth. Never trust the client.
2. Server and AI logic are separate programs. The game core has no AI dependency.
3. Realtime free chat. No fixed speaking order.
4. No role names hardcoded in the game core or in the AI client.
5. Keep `Role` / `Team` / `Alignment` / `Knowledge` / `Ability` / `Passive` / `Effect` / `WinCondition` / `ChatPermission` separated.
6. Roles, teams and game modes are loaded from YAML, never from Python literals.
7. Send each client only the information that client may know. Never filter secrets in the prompt.
8. The protocol must stay language-independent and versioned.
9. Must run on RTX 3070 Ti / 8GB VRAM: one shared LLM server, not one model per agent.
10. Game core must be fully testable without any LLM.

## Review checklist

レビュー時に必ず確認する。**この一覧が唯一の置き場所であり、各Decisionには複製しない。**

- ゲームコアに役職固有の分岐（`if role == "seer"`）が入っていないか
- 占い・霊能が `team` を参照していないか（`inspect_result` / `medium_result` を使うこと）
- 勝利条件の人数計算が `team` を数えていないか（`count_as` を使うこと）
- 判定が Role を直接読んでいないか（Role + Modifiers の実効属性を経由すること）
- 内部死因を含むイベントがクライアントへ送られていないか
- private 通知がブロードキャスト経路に乗っていないか
- ネットワーク層に行動の可否判定が書かれていないか
- ランダムな選択の結果がイベントとして記録されているか
- モジュールレベルの `random` を直接呼んでいないか
- ルールの既定値がコードへ埋め込まれていないか
- 役職の追加に Python の変更が必要になっていないか
- `python scripts/check_docs.py` が通るか（文書と実装の不整合の機械検査）

## Prohibitions

```
Do not reread the whole repository just to refresh context.
Use the AI documentation as the persistent project memory.

Do not repeat an investigation already documented in decisions or failures
unless current code contradicts that documentation.

Prefer targeted reads, searches, diffs, and batched independent operations.

Do not redesign a completed phase without an explicit request.
Do not change roles or the protocol on your own judgement; raise it in OPEN_QUESTIONS.md.

Before ending the session, externalize all information required by the next
agent into the repository.

Route bulk one-shot input through the local LLM before it enters the conversation:
test output, long diffs, and the large reference documents. Using it is the default;
skipping it needs a reason. Ask first whether a script would do the job for free.

Never conclude "no problem" from its output. Take pass/fail counts from the exit
code and the raw last line, never from a summary. Narrowing may reorder what you
read, never reduce it. See D034.
```

## End of session checklist

```
[ ] tests run
[ ] git diff reviewed
[ ] CURRENT_STATE.md updated (phase / next task / current problem)
[ ] REVIEW_INBOX.md OPEN items checked
[ ] new decision -> Docs/ai/decisions/
[ ] repeatable failure -> Docs/ai/failures/
[ ] phase finished -> Docs/ai/handoffs/PHASE<N>_HANDOFF.md
[ ] Next Task に次のエージェントが読む実在ファイルパスを書いた
```

## Commit convention

```
feat(core): ...
feat(network): ...
fix(core): ...
test(core): ...
docs(ai): ...
```

Phase または責務単位でコミットする。
