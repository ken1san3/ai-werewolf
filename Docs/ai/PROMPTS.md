# Session Prompts

各セッション開始時に貼るテンプレート。
同じ内容を AGENTS.md や CURRENT_STATE.md へ複製しない（置き場所はここだけ）。

役割分担は `AGENTS.md` の Roles を参照。実装は Codex、レビューは Claude (Cowork)。

---

## Implementer（実装セッション） — 担当: Codex

```
このセッションでは現在Phaseの実装のみ行う。

最初に以下だけを読む:
1. AGENTS.md
2. Docs/ai/INDEX.md
3. Docs/ai/CURRENT_STATE.md
4. Docs/ai/REVIEW_INBOX.md
5. Docs/ai/spec/DESIGN.md（設計の結論）
6. Docs/ai/TEST_POLICY.md（検証項目）

その後、現在タスクに必要なコードだけ確認する。

禁止:
- 必要性なしにリポジトリ全体を読む
- 完了済みPhaseを再設計する
- 役職や仕様を勝手に変更する（OPEN_QUESTIONS.mdへ起票する）
- 長い進捗説明を繰り返す

作業:
- 実装
- 必要なテスト
- CURRENT_STATE.md更新（Phase / Next Task / Current Problem / Test Status）
- 必要ならdecision/failure記録
- Phase完了時のみhandoff作成

終了前に、次のセッションが必要とする情報をすべてリポジトリへ書き出すこと。
会話内だけに残して終了しない。
```

---

## Reviewer（レビュー専用セッション） — 担当: Claude (Cowork)

```
このチャットはレビュー専用。実装は原則行わない。

読む順番:
1. Docs/ai/CURRENT_STATE.md
2. 対象PhaseのHANDOFF
3. Docs/ai/REVIEW_INBOX.md
4. git diff（対象範囲を明示すること）
5. 必要な周辺コードのみ

プロジェクト全体を最初から読み直さない。
変更されていないファイルは、必要性が生じない限り読まない。

確認重点:
- 仕様違反
- バグ
- 情報漏洩（privateイベントがブロードキャストされていないか）
- 競合状態 / asyncの取り扱い
- テスト不足
- 拡張性を壊す実装（ゲームコアへの役職固有分岐など）

指摘は Docs/ai/REVIEW_INBOX.md へ、
ID(R-YYYYMMDD-NN) / 重要度 / 対象ファイル / 問題 / 必要な修正 / 検証条件
付きで追記する。既存OPENとの重複を避ける。

長い解説は書かない。
```

---

## Phase終了時 — 担当: Codex

```
このPhaseを終了する。次のPhaseの実装には入らない。

1. Phaseに必要なテストを実行（結果は成功数・失敗数・重要エラーのみ報告）
2. git diff を確認
3. CURRENT_STATE.md を更新（Test Status に commit hash も記録）
4. Docs/ai/handoffs/PHASE<N>_HANDOFF.md を作成
5. 新しい設計判断があれば Docs/ai/decisions/ へ記録
6. 再発しそうな失敗があれば Docs/ai/failures/ へ記録
7. 次のエージェントが読むべきファイルをHandoff末尾へ列挙
```

---

## 利用枠が残り少なくなったとき

```
実装を止め、状態の外部化のみ行う。優先順位:
1. CURRENT_STATE.md
2. git diff（未コミット分をコミットまたは明記）
3. Handoff
4. REVIEW_INBOX の未処理
5. decision / failure
```

---

## 現在Phase用（そのまま貼る）— Phase 1.1

```
このセッションは実装担当（Implementer）。Phase 1.1 のみを行う。

## 最初に読むもの（これ以外は必要になってから読む）

1. AGENTS.md
2. Docs/ai/INDEX.md
3. Docs/ai/CURRENT_STATE.md
4. Docs/ai/REVIEW_INBOX.md
5. Docs/ai/spec/DESIGN.md          ← 設計の結論。実装はこれに従う
6. Docs/ai/TEST_POLICY.md          ← 検証項目

DESIGN.md が唯一の設計仕様。
Docs/ai/spec/AI_WEREWOLF_CODEX_HANDOFF.md は元になった旧仕様であり、
DESIGN.md と矛盾する箇所がある。矛盾したら DESIGN.md を優先する。
Docs/ai/decisions/ は判断の理由。DESIGN.md の意図が読み取れないときだけ開く。

## Phase 1.1 のスコープ

DESIGN.md §4 と §5 のデータモデルおよび content ローダーを実装する。

含む:
- Team / Role（5軸属性）/ Ability / Passive / Effect / Modifier /
  WinCondition / ChatChannel / DeathCause のデータモデル
- 実効属性の解決（Role + Modifiers）
- content の YAML ローダー
- ルール設定スキーマとバリデーション
- DESIGN.md §11 の13役職を content の YAML として記述し、
  ローダーが全件読み込めること
- 上記のユニットテスト

含まない（Phase 1.2 以降）:
- フェーズ進行、投票、夜行動の解決、勝敗判定、ネットワーク、AI

Effect の実行ロジックは Phase 1.5 以降なのでまだ書かない。
ただし Effect / Passive の「名前の登録簿」は Phase 1.1 で持ち、
未登録の名前を参照する定義は起動時にエラーにすること。

## 守ること

- レビュー観点は AGENTS.md の Review checklist にある。実装前に一読する
- 役職の追加が Python の変更を伴う設計にしない
- ルールの既定値をコードに埋め込まない。content から読む
- 仕様に疑問が出たら勝手に決めず、Docs/ai/OPEN_QUESTIONS.md へ起票する
- 必要性なくリポジトリ全体を読まない

## 終了時にやること

1. テストを実行し、成功数・失敗数・重要エラーだけを報告する（全出力を貼らない）
2. git diff を確認
3. Docs/ai/CURRENT_STATE.md を更新（Completed / Next Task / Test Status）
4. 新しい設計判断があれば Docs/ai/decisions/ へ追加
5. 再発しそうな失敗があれば Docs/ai/failures/ へ追加

Phase 1 全体はまだ終わらないので handoff は作らない。
会話内だけに次のセッションが必要とする情報を残して終了しない。

## 報告

作業中の説明は短く。詳細はコードと状態ファイルに残す。
最後に「実装したもの / テスト結果 / 未実装 / 次にやること」を各数行でまとめる。
```

---

## レビュー修正セッション用（そのまま貼る）

```
このセッションは実装担当（Implementer）。レビュー指摘の修正のみを行う。新機能は実装しない。

## 最初にやること

前回セッションの成果物が未コミットのままなので、まず現状をコミットする。
（content/ server/ tests/ pyproject.toml が untracked）
コミットしないと次回の差分レビューができない。

## 読むもの

1. AGENTS.md
2. Docs/ai/CURRENT_STATE.md
3. Docs/ai/REVIEW_INBOX.md   ← 対応対象はこのファイル。SPEC_REVIEW.md ではない
4. Docs/ai/spec/DESIGN.md の、指摘が参照している節だけ
5. 指摘された実装ファイル

Docs/ai/SPEC_REVIEW.md は元仕様への指摘履歴であり、今回の対象ではない。

## 対応順

1. High: R-20260829-01, R-20260829-02
2. Medium: R-20260829-03, R-20260829-04
3. Low: R-20260829-06, R-20260829-07, R-20260829-08

R-20260829-05 は Reviewer（DESIGN.md の担当）が対応する。**触らないこと。**
medium の priority 75 もそのまま残す。

R-20260829-04 は Reviewer 推奨（グローバル rules.first_night_seer を正とし、
role option を削除）で進める。異論があれば実装せず OPEN_QUESTIONS.md へ起票する。

## 設計変更を伴う場合（R-20260829-01）

Ability の effect 参照に priority を持たせる形は Codex が決めてよい。
ただし決めたら Docs/ai/decisions/ へ D018 として記録する。
**DESIGN.md は書き換えない**（Reviewer が反映する）。

## 各指摘の完了時

Docs/ai/REVIEW_INBOX.md の該当項目を編集する。

- [OPEN] を [FIXED] に変える
- 直下に「Fix:」の1行を足し、何をどう直したかを書く

対応しないと判断した場合は [REJECTED] または [DEFERRED] にし、理由を書く。
勝手に消さない。

## テスト

各指摘に Verification がある場合はその内容をテストにする。
TEST_POLICY.md に無い項目を追加したときは TEST_POLICY.md にも追記する。

## 終了時

1. テストを実行し、成功数・失敗数・重要エラーだけを報告する
2. git diff を確認してコミット
3. Docs/ai/CURRENT_STATE.md を更新（Test Status に commit hash を入れる）
4. Docs/ai/REVIEW_INBOX.md に [OPEN] が残っていないか確認する

## 報告

最後に「対応した指摘ID / 変更したファイル / テスト結果 / 未対応と理由」を各数行でまとめる。
```
