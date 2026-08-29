# Codex 低トークン実装運用ガイド

## 0. この文書の目的

この文書は、Codexを長期間のソフトウェア開発に利用する際に、

- 会話コンテキストの肥大化
- 同じファイルの再読
- 同じ設計説明の繰り返し
- 実装とレビューの混在
- 過去の失敗の再発
- レビュー指摘の見逃し
- 不要なツール往復
- 途中で利用枠が尽きた際の引継ぎコスト

を減らし、**できるだけ少ないトークン消費で継続的に開発を進めるための運用ルール**を定義する。

対象は特に、複数Phaseにまたがる中～長期プロジェクトである。

本ガイドは、AI人狼プロジェクトに限らず他のCodex開発でも利用できる。

---

# 1. 基本思想

Codexにプロジェクト全体を毎回思い出させない。

代わりに、

> **会話を記憶装置にしない。リポジトリを記憶装置にする。**

ことを基本方針とする。

会話履歴は一時的な作業領域として扱う。

長期的に必要な情報はすべてファイルへ残す。

---

# 2. 最重要原則

以下を最優先ルールとする。

1. 長期状態をチャット履歴へ依存しない
2. Codexに毎回リポジトリ全体を読ませない
3. 実装担当と評価担当を分離する
4. Phase単位でチャットを切り替える
5. 現状を `CURRENT_STATE.md` に集約する
6. 必要な文書への入口を `INDEX.md` に集約する
7. 設計判断を `decisions/` に残す
8. 失敗した方法を `failures/` に残す
9. レビュー指摘を専用ファイルに残す
10. 次のCodexが読むファイルを明示する
11. 独立した調査・読み取りは可能な限りまとめる
12. 一度確認した不変情報を何度も再取得しない
13. 大きなPhaseを一度に実装しない
14. 作業終了時には必ず引継ぎを書く

---

# 3. 推奨リポジトリ構造

プロジェクトルートに以下を用意する。

```text
project/
│
├─ AGENTS.md
│
├─ README.md
│
├─ src/
├─ tests/
│
└─ Docs/
   └─ ai/
      ├─ INDEX.md
      ├─ CURRENT_STATE.md
      ├─ ROADMAP.md
      ├─ REVIEW_INBOX.md
      ├─ REVIEW_STATUS.md
      │
      ├─ handoffs/
      │  ├─ PHASE1_HANDOFF.md
      │  ├─ PHASE2_HANDOFF.md
      │  └─ ...
      │
      ├─ decisions/
      │  ├─ D001_*.md
      │  ├─ D002_*.md
      │  └─ ...
      │
      └─ failures/
         ├─ F001_*.md
         ├─ F002_*.md
         └─ ...
```

必要に応じて追加:

```text
Docs/ai/
├─ ARCHITECTURE.md
├─ PROTOCOL.md
├─ TEST_POLICY.md
└─ OPEN_QUESTIONS.md
```

---

# 4. AGENTS.md の役割

`AGENTS.md` はCodexが毎回参照する可能性が高いため、**短く、安定したルールだけ**を書く。

巨大な仕様書をAGENTS.mdへ入れない。

悪い例:

```text
AGENTS.md
2000行
全設計
全履歴
全失敗
全ロードマップ
```

これでは毎回大量のコンテキストを消費する。

良い例:

```markdown
# Project AI Rules

1. Start by reading `Docs/ai/INDEX.md`.
2. Then read `Docs/ai/CURRENT_STATE.md`.
3. Read only the documents listed as relevant for the current task.
4. Do not scan the entire repository unless necessary.
5. Before implementation, check `Docs/ai/REVIEW_INBOX.md`.
6. Record durable design decisions in `Docs/ai/decisions/`.
7. Record failed approaches that should not be repeated in `Docs/ai/failures/`.
8. Update `CURRENT_STATE.md` before ending a work session.
9. At phase completion, create/update the corresponding handoff.
10. Prefer targeted searches and batched independent reads.
```

AGENTS.mdは「地図への入口」に留める。

---

# 5. INDEX.md

`INDEX.md` はAI文書群のナビゲーション専用にする。

Codexはまずこれを読む。

例:

```markdown
# AI Documentation Index

## Always read

- `CURRENT_STATE.md`
- `REVIEW_INBOX.md`

## Current phase

Phase 2: Network Server

Relevant:
- `handoffs/PHASE1_HANDOFF.md`
- `ARCHITECTURE.md`
- `PROTOCOL.md`

## Read only if needed

Role system:
- `decisions/D003_ROLE_EFFECT_MODEL.md`

LLM integration:
- not needed in current phase

Known failed approaches:
- `failures/F002_GLOBAL_ROLE_IF_CHAIN.md`
```

これによりCodexが

```text
Docs全部読む
↓
src全部読む
↓
tests全部読む
```

という動きをする必要を減らす。

---

# 6. CURRENT_STATE.md

このファイルが最重要。

「現在プロジェクトがどこまで進んでいるか」を短く保持する。

理想は数百行ではなく、可能なら100行以内程度を維持する。

例:

```markdown
# Current State

## Current Phase

Phase 2: Network Server

## Completed

- Game core
- Role loading
- Vote resolution
- Night actions
- Win condition
- Core unit tests

## In Progress

- WebSocket session management

## Not Started

- AI Client
- Local LLM
- Web UI

## Current Architecture

Server authoritative.
Game core has no AI dependency.
Roles loaded from YAML.

## Important Files

- `server/core/game.py`
- `server/network/session.py`
- `tests/test_game.py`

## Current Problem

Reconnect handling is not implemented.

## Next Task

Implement WebSocket join/ready flow.

## Known Constraints

- Do not introduce role-specific branches into Game.
- Protocol must remain language-independent.

## Latest Review

See `REVIEW_INBOX.md`.
```

---

# 7. CURRENT_STATE.md に書かないもの

以下は別ファイルへ分離する。

### 長い設計理由

→ `decisions/`

### 過去の失敗詳細

→ `failures/`

### 完了Phaseの詳細

→ `handoffs/`

### 大量のTODO

→ `ROADMAP.md`

### レビュー全文

→ `REVIEW_INBOX.md`

`CURRENT_STATE.md` はあくまで「現在地点」。

---

# 8. Phase単位でチャットを切り替える

1つのCodexチャットでプロジェクト完成まで進めない。

推奨:

```text
Chat A
Phase 1実装
↓
PHASE1_HANDOFF.md
↓
終了

Chat B
Phase 2実装
↓
PHASE2_HANDOFF.md
↓
終了

Chat C
Phase 3実装
...
```

新しいチャットは過去の巨大な会話履歴を持たないため、コンテキストを大きく節約できる。

---

# 9. Phase Handoff

各Phase終了時に引継ぎファイルを作る。

例:

```text
Docs/ai/handoffs/PHASE1_HANDOFF.md
```

内容:

```markdown
# Phase 1 Handoff

## Goal

Game core implementation.

## Completed

- Player
- Game
- Team
- Role loader
- Vote
- Night actions

## Files Changed

- `server/core/game.py`
- `server/rules/role.py`
- ...

## Tests

42 passed.

## Important Decisions

- Game core does not know concrete role names.
- Ability resolution uses Effect objects.

## Remaining Problems

- No reconnect support.
- No network layer.

## Do Not Repeat

- Do not add `if role == ...` to Game.

## Next Phase

Network Server.

## Files Next Agent Should Read

1. `Docs/ai/INDEX.md`
2. `Docs/ai/CURRENT_STATE.md`
3. This file
4. `server/core/game.py`
5. `server/network/protocol.py`
```

重要なのは最後の

> **Files Next Agent Should Read**

である。

次のCodexが何を読むかをこちらで制限する。

---

# 10. 実装担当とレビュー担当を分離する

以前検討した運用では、

```text
実装チャット
↓
実装
↓
レビュー依頼ファイル作成
↓
別の評価チャット
↓
レビュー
↓
REVIEW_INBOX.mdへ記録
↓
実装チャットまたは次チャットで修正
```

とする。

モデルを使い分ける場合も同様である。

例:

```text
Luna系:
主に実装

Sol系:
主に評価・レビュー
```

ただしモデル名そのものへ設計を依存させない。

役割は、

```text
Implementer
Reviewer
```

として定義する。

---

# 11. なぜ実装とレビューを分けるか

同じ長大チャットで、

```text
設計
↓
実装
↓
自己レビュー
↓
修正
↓
再レビュー
```

を繰り返すと、過去のコード・説明・ログがすべてコンテキストへ残る。

また、実装したAI自身が自分の設計前提を引きずる。

別チャットのReviewerなら、

- 必要な仕様
- diff
- テスト結果
- 重要ファイル

だけを読ませられる。

そのためコンテキスト量を削減しやすい。

---

# 12. Reviewerにリポジトリ全体を読ませない

レビュー対象を明示する。

悪い依頼:

```text
プロジェクト全体をレビューしてください。
```

良い依頼:

```text
Phase 2の変更のみレビューする。

まず以下を読む:
- Docs/ai/CURRENT_STATE.md
- Docs/ai/handoffs/PHASE2_HANDOFF.md

その後、git diffでPhase 2の変更を確認する。

重点:
- protocol compatibility
- information leakage
- async race conditions

変更されていないファイルは、
必要性が生じない限り読まない。
```

---

# 13. REVIEW_INBOX.md

レビュー指摘の見逃し防止用に専用ファイルを作る。

これが以前問題になっていた「レビューコメントを実装側が見落とす」対策になる。

例:

```markdown
# Review Inbox

## R-023 [OPEN] Critical

File:
`server/network/session.py`

Problem:
Private events may be broadcast to all clients.

Required:
Separate private send from room broadcast.

Reviewer:
Phase 2 review

---

## R-024 [FIXED]

File:
`server/core/game.py`

Problem:
Dead players could vote.

Fix:
Validation added in commit ...
```

---

# 14. REVIEW_INBOX.md のルール

状態を必ず付ける。

```text
OPEN
IN_PROGRESS
FIXED
REJECTED
DEFERRED
```

Codex実装担当は作業開始時に、

```text
REVIEW_INBOX.md
```

を確認する。

未解決Critical / Highがある場合は、新機能より先に対応する。

---

# 15. REVIEW_STATUS.md

Inboxが増えてきたら要約ファイルを分けてもよい。

例:

```markdown
# Review Status

Open Critical: 0
Open High: 2
Open Medium: 3

Next:
R-031
R-034
```

ただし小規模なうちは `REVIEW_INBOX.md` のみでよい。

---

# 16. decisions/

後から何度も同じ設計を考え直さないための記録。

例:

```text
D001_SERVER_AUTHORITATIVE.md
D002_WEBSOCKET_PROTOCOL.md
D003_ROLE_EFFECT_MODEL.md
```

テンプレート:

```markdown
# D003 Role / Ability / Effect separation

## Status

Accepted

## Context

Adding roles directly to Game would increase branching.

## Decision

Separate:
- Role
- Team
- Ability
- Passive
- Effect
- WinCondition

## Why

Allows content-driven role expansion.

## Consequences

More abstractions initially, lower future modification cost.
```

---

# 17. decisions/ の効果

Codexが新しいチャットになるたびに、

```text
なぜこうなっている？
↓
再考
↓
別設計を提案
↓
既存設計を壊す
```

ことを防ぐ。

判断済み事項については、必要時にDecisionだけ読めばよい。

---

# 18. failures/

失敗した方法を記録する。

例:

```text
F001_FULL_HISTORY_PROMPT.md
F002_ROLE_IF_CHAIN.md
F003_NINE_SIMULTANEOUS_MODEL_LOADS.md
```

テンプレート:

```markdown
# F002 Role-specific branching in Game

## Attempt

Added:

if role == "seer":
if role == "guard":
...

## Problem

Game core became role-dependent.

## Result

Reverted.

## Do Not Repeat

Concrete role behavior must use Ability / Effect.
```

---

# 19. failures/ が重要な理由

AIは新しいチャットでは過去の試行錯誤を知らない。

記録がなければ、

```text
Chat 1で失敗
↓
Chat 4で同じ案を再提案
↓
再び失敗
```

が起きる。

失敗記録は、長い会話履歴を保持せずに「経験」だけ残す方法である。

---

# 20. 読む順番を固定する

Codex開始時の標準順序:

```text
1. AGENTS.md
2. Docs/ai/INDEX.md
3. Docs/ai/CURRENT_STATE.md
4. REVIEW_INBOX.md
5. 現在PhaseのHANDOFF
6. 現在タスクに必要なソースだけ
```

原則としてこれ以外を最初から全部読ませない。

---

# 21. リポジトリ全体走査を避ける

Codexにはまず、

```text
tree
git status
git diff
対象ディレクトリ
```

などを使わせる。

その後、必要な対象だけ読む。

避けたい行動:

```text
find . 全ファイル
↓
全ファイルcat
↓
全テストcat
↓
全Docs cat
```

これは大きなプロジェクトほど無駄が大きい。

---

# 22. diff中心のレビュー

既存コードを全部読み直すのではなく、

```text
git diff
git diff --stat
git status
```

を入口とする。

Review対象は基本的に、

```text
今回変更されたコード
+
変更の影響を受ける周辺コード
```

に限定する。

---

# 23. 独立した読み取り処理はまとめる

独立した調査を1件ずつ外側のモデル往復として実行するより、可能なら1回の実行内でまとめる。

過去に保存したメモでは、独立した読み取り処理を逐次実行するより、実行ツール内で並列化・バッチ化する方針が利用枠節約に有効とされている。

特に、

```text
Aを読む
↓
モデルへ戻る
↓
Bを読む
↓
モデルへ戻る
↓
Cを読む
```

より、

```text
A/B/Cは互いに独立
↓
まとめて取得
↓
モデルへ戻る
```

を優先する。

---

# 24. 並列化してよい処理

例:

```text
複数の独立ファイル読み取り
独立したgrep
独立したテスト
複数ログの確認
複数の静的解析
```

ただし、

```text
Aの結果によってBを決める
```

ものは逐次実行する。

---

# 25. 並列化してはいけない例

以下は無理に並列化しない。

- 依存関係がある処理
- 同一ファイルへの競合編集
- 承認が必要な処理
- 最初の結果によって次の調査対象が変わる処理
- 前工程が成功しないと意味がないテスト

---

# 26. 実装を小さな単位へ切る

悪い例:

```text
Phase 3全部を実装してください。
```

良い例:

```text
Phase 3.1
AI ClientのWebSocket接続だけ

Phase 3.2
WorldState

Phase 3.3
Dummy Brain

Phase 3.4
Vote Controller
```

大きな一括依頼は、

- 読むコード量
- 変更量
- 推論量
- レビュー量
- 再作業量

を増やす。

---

# 27. ただし細かく切りすぎない

1行修正ごとに別チャットにすると、引継ぎコストが増える。

1つの作業単位は、

> **一つの明確な責務を完成させ、テスト可能になる程度**

を目安とする。

---

# 28. セッション開始プロンプト

実装担当Codexへの推奨テンプレート:

```text
このセッションでは現在Phaseの実装のみ行う。

最初に以下だけを読む:
1. AGENTS.md
2. Docs/ai/INDEX.md
3. Docs/ai/CURRENT_STATE.md
4. Docs/ai/REVIEW_INBOX.md
5. INDEXに指定された現在Phaseの引継ぎ

その後、現在タスクに必要なコードだけ確認する。

禁止:
- 必要性なしにリポジトリ全体を読む
- 完了済みPhaseを再設計する
- 役職や仕様を勝手に変更する
- 長い進捗説明を繰り返す

作業:
- 実装
- 必要なテスト
- CURRENT_STATE更新
- 必要ならdecision/failure記録
- Phase完了時のみhandoff更新
```

---

# 29. レビュー担当プロンプト

```text
あなたは実装担当ではなくReviewer。

まず以下を読む:
1. Docs/ai/CURRENT_STATE.md
2. 対象PhaseのHANDOFF
3. Docs/ai/REVIEW_INBOX.md

その後git diffを確認する。

レビュー対象は今回の変更と、
その影響を直接受けるコードのみ。

確認重点:
- 仕様違反
- バグ
- 情報漏洩
- 競合状態
- テスト不足
- 拡張性を壊す実装

レビュー結果は
Docs/ai/REVIEW_INBOX.md
へ追記する。

修正実装は原則行わない。
```

---

# 30. 実装担当とReviewerの責務を混ぜない

Implementer:

```text
作る
直す
テストする
```

Reviewer:

```text
疑う
壊れるケースを探す
仕様との差を見る
```

Reviewerにそのまま大量修正させると、次回はReviewerの変更まで再レビューする必要が出る。

原則、指摘をファイルへ残し、Implementerが修正する。

---

# 31. レビュー往復を短くする

Reviewerの指摘は、

```text
問題
根拠
対象ファイル
重要度
必要な修正条件
```

だけを書く。

長大な解説を毎回残さない。

例:

```markdown
## R-041 [HIGH]

File:
`server/network/session.py`

Problem:
Private role assignment is sent through room broadcast.

Required:
Use direct session send.

Verification:
Add test ensuring another client cannot receive the event.
```

---

# 32. テスト結果を引継ぎに残す

新しいCodexが、

```text
今テスト通るの？
```

を調べ直さなくて済むようにする。

例:

```text
Last verified:
2026-08-29

pytest:
126 passed
2 skipped

Known failing:
none
```

ただし古くなった結果はCURRENT_STATEで明示する。

---

# 33. 出力ログを大量に会話へ貼らない

テスト出力が長い場合は、

```text
成功数
失敗数
重要エラー
```

だけを会話へ返す。

必要ならログファイルへ保存する。

悪い例:

```text
pytest -vv の数千行を全部会話へ返す
```

---

# 34. Codex自身の説明を短くさせる

作業中に毎回、

```text
私はこれから○○を行います。
次に○○を...
```

と長い説明を生成させる必要はない。

重要なのはコードと状態ファイル。

推奨:

```text
作業中の報告は短く。
詳細はファイル変更と最終サマリに残す。
```

---

# 35. 仕様書を毎回全文プロンプトへ貼らない

既にリポジトリ内に、

```text
AI_WEREWOLF_CODEX_HANDOFF.md
```

のような仕様書が存在する場合、

毎回その全文をユーザーが貼り直す必要はない。

AGENTS/INDEXから必要時に参照させる。

---

# 36. 大きな仕様書を「常時読むファイル」にしない

最上位仕様書が大きい場合、

```text
常に全文読む
```

より、

```text
INDEX
↓
CURRENT_STATE
↓
必要な仕様章だけ確認
```

とする。

仕様書に見出しを明確に付けること。

必要なら将来的に、

```text
SPEC/
├─ GAME_CORE.md
├─ NETWORK.md
├─ AI_CLIENT.md
└─ ROLE_SYSTEM.md
```

へ分割する。

---

# 37. コンテキストの「固定部分」と「可変部分」

長期間変わらない情報:

```text
コーディング規約
設計原則
禁止事項
ドキュメント入口
```

→ AGENTS.md

頻繁に変わる情報:

```text
現在Phase
現在の問題
次の作業
直近レビュー
```

→ CURRENT_STATE.md

履歴:

```text
完了内容
判断
失敗
```

→ handoffs / decisions / failures

この分離を崩さない。

---

# 38. 途中でトークンが尽きても困らない状態にする

理想は、Codexチャットが突然終了しても、

別チャットが

```text
AGENTS.md
INDEX.md
CURRENT_STATE.md
git status
git diff
```

を読めば作業を再開できる状態。

つまり、

> 「前のチャットを読めないと続きが分からない」

状態を禁止する。

---

# 39. 作業終了チェックリスト

各作業セッション終了前:

```text
[ ] コード保存
[ ] テスト実行
[ ] git diff確認
[ ] CURRENT_STATE更新
[ ] REVIEW_INBOX確認
[ ] 新しい重要判断があればdecision追加
[ ] 再発防止すべき失敗があればfailure追加
[ ] Phase完了ならHANDOFF更新
[ ] 次に読むべきファイルを明示
```

---

# 40. チャットを切り替える基準

以下のどれかなら新チャットを推奨。

### Phase終了

最も明確。

### コンテキストがかなり長くなった

過去の実装ログが多くなった。

### タスクの責務が変わった

例:

```text
Game Core
↓
Network
```

### 実装からレビューへ移る

別Reviewerチャットを使う。

### 同じ説明を再度参照し始めた

状態ファイルへ移してチャットを切る。

---

# 41. チャットを切り替えない方がよい場合

以下は同じチャットでよい。

```text
同じバグの修正
↓
テスト失敗
↓
原因調査
↓
再修正
```

途中でチャットを切ると、直近のエラー状態まで引き継ぐ必要がある。

---

# 42. 実装用チャットの長期化対策

同じPhaseが大きい場合、

```text
Phase 4A
LLM backend

Phase 4B
Structured Output

Phase 4C
Vote integration
```

のようにサブPhase化する。

---

# 43. 調査結果をドキュメント化する基準

一度しか使わない情報:

→ 会話だけでもよい。

今後も何度も使う:

→ ファイルへ残す。

判断基準:

> 次のチャットでも必要になるか？

YESなら保存。

---

# 44. コードから自明な情報はドキュメントへ重複させない

悪い例:

```text
CURRENT_STATE.md に全関数一覧
READMEにも全関数一覧
ARCHITECTUREにも全関数一覧
```

コード変更のたびに全部更新が必要になる。

ドキュメントは、

- 意図
- 制約
- 現在状態
- 判断理由

を中心にする。

---

# 45. 重複指示を減らす

同じルールを、

```text
AGENTS.md
README
CURRENT_STATE
HANDOFF
ユーザープロンプト
```

全部へコピーしない。

原則:

```text
AGENTS = 恒久ルール
CURRENT_STATE = 現状
HANDOFF = Phase結果
Decision = 判断
Failure = 禁止された失敗経路
```

として一意の置き場所を決める。

---

# 46. AGENTS.mdを定期的に整理する

長期開発ではAGENTS.mdへ指示を足し続けがち。

しかし、常時読み込まれる指示が増えるほど固定コンテキストが大きくなる。

定期的に、

- 重複
- 既にコードで保証されるルール
- 古い暫定指示
- 完了Phase限定指示
- 過剰に細かい手順

を削除する。

---

# 47. 安定したプロンプト前半を維持する

Codex / API側でキャッシュが利用される構成では、安定した共通指示部分を頻繁に書き換えない方が有利な場合がある。

そのため、

```text
恒久ルール
↓
安定

現在タスク
↓
可変
```

と分ける。

この意味でもAGENTSとCURRENT_STATEの分離は有効。

---

# 48. ツール呼び出し回数を減らす

必要なファイルが明確なら、

```text
read A
read B
read C
```

を個別に何往復もするより、可能ならまとめて取得する。

ただし「とにかく並列にする」のではなく、独立性がある場合のみ。

過去メモでは、独立処理のバッチ化によって利用量が大きく低減した事例も記録されているため、Codex運用では特に意識する。

---

# 49. Adaptive Investigation

最初から大量ファイルを読むのではなく、

```text
1. INDEX/CURRENT_STATE
2. 対象コード
3. 必要性が発生した周辺コード
4. 必要ならさらに深掘り
```

という適応的調査を行う。

つまり、

> 広く浅く全部読む

のではなく、

> 狭く読み、必要になった方向だけ広げる

---

# 50. Gitを状態記憶として使う

Codexに変更点を覚えさせない。

Gitから取得する。

```text
git status
git diff
git log
```

で、

- 未コミット変更
- 最近の変更
- 現在の差分

を確認する。

---

# 51. Commit単位

可能ならPhaseまたは責務単位でコミットする。

例:

```text
feat(core): add role loader
feat(network): add websocket sessions
test(core): cover night action resolution
```

そうするとReviewerが対象コミットだけ調べられる。

---

# 52. AI人狼プロジェクトでの具体運用

今回のAI人狼では以下を推奨する。

```text
Phase 1
Game Core
Implementer Chat 1

↓ Handoff

Reviewer Chat 1

↓ Review Inbox

Implementer Chat 2
修正 + Phase 2 Network

↓ Handoff

Reviewer Chat 2
...
```

ただしPhase修正量が多い場合は、

```text
レビュー修正専用Implementer
```

を挟んでもよい。

---

# 53. AI人狼で常時読む必要がないもの

例えばPhase 1 Game Core中に、

```text
LLM人格
Thinking mode
Chat typing演出
Web UI
```

の仕様を毎回読む必要はない。

INDEXで、

```text
Phase 1では不要
```

と明示する。

---

# 54. AI人狼のPhaseごとの主読込範囲

## Phase 1 Game Core

```text
Role
Team
Ability
Effect
Game State
Win Condition
Tests
```

AI Client仕様は読まない。

---

## Phase 2 Network

```text
Current State
Phase 1 Handoff
Protocol
Session
Game public/private state
```

LLM詳細は読まない。

---

## Phase 3 AI Skeleton

```text
Protocol
Client
World State
Dummy Brain
```

Role engine内部実装は必要時のみ。

---

## Phase 4 Local LLM

```text
Brain Interface
LLM Backend
Structured Output
```

Game core内部は原則不要。

---

# 55. REVIEW_INBOXを作業キューとして使う

レビューコメントは会話だけに残さない。

Reviewerが、

```text
「ここ直してください」
```

と会話で言って終わると、別Implementerが見落とす。

必ずファイルへ保存する。

Implementerは、

```text
OPEN
```

を検索すれば未修正を確認できる。

---

# 56. 自動化できるもの

将来的にはスクリプト化してよい。

例:

```text
scripts/
├─ ai_status.py
├─ ai_handoff_check.py
└─ ai_review_check.py
```

`ai_status.py`:

```text
Current Phase
Open Reviews
Last Test Result
Dirty Files
```

を表示。

これによりCodexが複数ファイルを個別に読む必要を減らせる。

---

# 57. 将来的なSESSION_START.md生成

必要なら、

```text
Docs/ai/SESSION_START.md
```

を自動生成してもよい。

内容:

```text
Current Phase
Next Task
Relevant Files
Open Reviews
Known Failure Warnings
```

ただしCURRENT_STATEとの二重管理にならないよう、自動生成に限定する。

---

# 58. 「まとめファイル」の肥大化に注意

最初は便利でも、

```text
CURRENT_STATE.md
```

が1000行になると意味がない。

古い情報は、

```text
handoffs
decisions
failures
```

へ移す。

CURRENT_STATEは常に現在だけを保持する。

---

# 59. Codexへの禁止事項として入れたい文

```text
Do not reread the whole repository just to refresh context.
Use the AI documentation as the persistent project memory.

Do not repeat an investigation already documented in decisions or failures
unless current code contradicts that documentation.

Prefer targeted reads, searches, diffs, and batched independent operations.

Before ending the session, externalize all information required by the next
agent into the repository.
```

英語のままAGENTS.mdへ入れてもよい。

---

# 60. 低トークン運用の本質

最も重要なのは、

```text
モデルを小さくする
```

ことでも、

```text
返答を短くする
```

ことでもない。

本質は、

```text
前回までの会話を
次回もう一度理解するためのトークン
```

を減らすことである。

そのため、

```text
会話履歴
↓
必要情報を抽出
↓
リポジトリへ保存
↓
新チャット
↓
必要部分だけ取得
```

という循環を作る。

---

# 61. 推奨運用フロー

最終形:

```text
              ┌─────────────────────┐
              │    MASTER SPEC      │
              └─────────┬───────────┘
                        │
                   AGENTS / INDEX
                        │
                        ▼
              ┌─────────────────────┐
              │   CURRENT_STATE     │
              └─────────┬───────────┘
                        │
                        ▼
                 Implementer Chat
                        │
                        │ code + test
                        ▼
                    Handoff
                        │
                        ▼
                  Reviewer Chat
                        │
                        ▼
                 REVIEW_INBOX
                        │
                        ▼
                 Implementer Chat
                        │
                        ▼
                CURRENT_STATE更新
                        │
                        ▼
                   次Phase
```

---

# 62. 最小構成

最初から大量ドキュメントを作りすぎる必要はない。

最低限:

```text
AGENTS.md
Docs/ai/INDEX.md
Docs/ai/CURRENT_STATE.md
Docs/ai/REVIEW_INBOX.md
Docs/ai/handoffs/
Docs/ai/decisions/
Docs/ai/failures/
```

これだけで開始できる。

---

# 63. Codexに最初に与える運用指示

以下を最初のプロンプトとして使用できる。

```text
このプロジェクトではチャット履歴を長期記憶として利用しない。
リポジトリ内の Docs/ai を永続的なAI引継ぎ領域として利用する。

作業開始時:
1. AGENTS.md
2. Docs/ai/INDEX.md
3. Docs/ai/CURRENT_STATE.md
4. Docs/ai/REVIEW_INBOX.md
を読み、INDEXが現在タスク用に指定するファイルだけ追加で読む。

必要性なくリポジトリ全体を読み直さないこと。
独立した読み取り・検索・検証は可能な範囲でまとめること。

実装完了時:
- テスト
- CURRENT_STATE更新
- 必要ならdecision/failure記録
- Phase完了時はhandoff更新
を必ず行う。

会話内だけに、次のセッションが必要とする重要情報を残して終了してはならない。
```

---

# 64. Reviewerに最初に与える運用指示

```text
このチャットはレビュー専用。

実装は原則行わない。

読む順番:
1. Docs/ai/CURRENT_STATE.md
2. 対象Phase Handoff
3. Docs/ai/REVIEW_INBOX.md
4. git diff
5. 必要な周辺コードのみ

プロジェクト全体を最初から読み直さない。

指摘は必ず Docs/ai/REVIEW_INBOX.md に、
重要度・対象ファイル・問題・修正条件・検証条件付きで記録する。

既存のOPENレビューとの重複を避ける。
```

---

# 65. Phase終了時にCodexへ与える指示

```text
このPhaseを終了する。

以下を行う:
1. 全テストまたはPhaseに必要なテストを実行
2. git diffを確認
3. CURRENT_STATE.mdを更新
4. Phase Handoffを作成
5. 新しいdesign decisionがあればdecisionsへ記録
6. 再発しそうな失敗があればfailuresへ記録
7. 次のCodexが読むべきファイルをHandoff末尾へ列挙

次のPhaseの実装には入らない。
```

---

# 66. 利用枠が少なくなったとき

無理にそのチャットで次の機能まで実装しない。

まず状態を外部化する。

優先順位:

```text
1. CURRENT_STATE
2. git diff
3. Handoff
4. Open Review
5. Decision / Failure
```

これが残っていれば次チャットへ安全に移行できる。

---

# 67. この運用で期待する効果

- 新チャットでも再説明が少ない
- 仕様全文の再投入が不要
- 過去ログの再読が減る
- レビュー対象を限定できる
- 同じ失敗を繰り返しにくい
- 実装側のレビュー見逃しが減る
- 別モデルへ容易に担当交代できる
- Codexの利用枠が尽きても復旧しやすい
- 長期プロジェクトでもコンテキストが制御可能

---

# 68. 運用上の注意

この仕組み自体を複雑にしすぎない。

ドキュメント管理のためにCodexが大量のトークンを使うようになれば本末転倒である。

原則:

```text
短いCURRENT_STATE
短いINDEX
必要なときだけDecision
必要なときだけFailure
Phase終了時だけHandoff
```

---

# 69. AI人狼プロジェクトへの適用開始案

AI人狼リポジトリを作成した直後に、Codexへ以下を作らせる。

```text
AGENTS.md

Docs/ai/
├─ INDEX.md
├─ CURRENT_STATE.md
├─ ROADMAP.md
├─ REVIEW_INBOX.md
├─ handoffs/
├─ decisions/
└─ failures/
```

その後、既に作成済みの

```text
AI_WEREWOLF_CODEX_HANDOFF.md
```

をMaster Specificationとして登録する。

INDEXから参照できるようにする。

---

# 70. 最終ルール

Codex運用で迷った場合は、次の質問で判断する。

> この情報は次のチャットでも必要か？

YES:
ファイルへ残す。

NO:
会話だけでよい。

---

> このファイルを今読む必要があるか？

YES:
読む。

NO:
読まない。

---

> この調査は他の調査と独立しているか？

YES:
可能ならまとめる。

NO:
逐次実行する。

---

> この作業は現在Phaseに必要か？

YES:
実装する。

NO:
ROADMAPへ残して今は触らない。

---

> このレビュー指摘は次のImplementerが知る必要があるか？

YES:
REVIEW_INBOXへ必ず残す。

---

# 71. 本ガイドの要約

Codexのトークン節約は、

```text
短い返答
```

だけでは不十分。

本プロジェクトでは、

```text
チャット = 一時作業領域

リポジトリ = 永続記憶

CURRENT_STATE = 現在地

INDEX = 読む場所の地図

HANDOFF = Phase間引継ぎ

decisions = 設計判断の記憶

failures = 失敗経験の記憶

REVIEW_INBOX = ReviewerとImplementerの通信路

Git diff = 変更点の記憶
```

として扱う。

そして、

```text
実装
↓
状態外部化
↓
新チャット
↓
必要部分だけ読む
↓
実装
```

を繰り返す。

これをプロジェクト標準運用とする。
