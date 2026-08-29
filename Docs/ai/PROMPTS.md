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
