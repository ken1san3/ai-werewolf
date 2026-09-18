# Roadmap

各サブPhaseは「含む / 含まない / 完了条件 / 参照」を持つ。
**実装セッションはここで自分のスコープを決める。**手順は `RUNBOOK.md`。

進行中のPhaseと次のタスクは `CURRENT_STATE.md` を見る。

---

# Phase 1 — Game Core（LLM不使用）✅ 完了

1.1 データモデルと content ローダー / 1.2 GameState・Player・Event Bus・ログ /
1.3 Phase Manager / 1.4 投票と処刑 / 1.5 夜行動の予約と Action Resolver /
1.6 WinCondition 評価と勝敗 / 1.7 get_available_actions /
1.8 13役職の動作確認と完走テスト

完了条件: 13役職すべてが LLM 無しの content-only 構成で完走し、
`standard_9`（9席・6種類の役職）でも勝敗が確定する。

サブPhaseごとの 含む / 含まない / 完了条件は
`Docs/ai/roadmap_archive/PHASE1_SCOPE.md` に退避した（内容は当時のまま）。

# Phase 2 — Network Server

## 2.1 プロトコル定義

含む: 共通メッセージ形、`protocol_version`、`seq`、エラー（`action.rejected`）
完了条件: 言語非依存のスキーマとして定義され、バージョン方針が決まっている
参照: DESIGN.md §9.2 / D031

## 2.2 WebSocket サーバと Session

含む: 接続、Join、Ready、切断、接続トークンの発行と照合（D028）、
      1秒 tick による `advance_if_due` の駆動（D028）
含まない: 再接続UI

## 2.3 公開と private の送信分離

含む: ブロードキャストと個別送信の経路分離、チャットチャネルの権限管理、
      死亡者の情報範囲（`rules.graveyard`、D028）
完了条件: 権限の無いチャネルの内容が届かないことをテストで確認できる。
          死亡者が生存者以上の情報を受け取らない
参照: DESIGN.md §4.6 / D028 / D031

## 2.4 Chat / Vote / Ability / CO の受付

含む: `co.declare` / `co.report`（D007）、行動の受理と拒否、
      発言数の集計と突然死（`rules.sudden_death`、D028）、
      **締切到達時の進行駆動**（投票解決と Execution の完了。ticker から呼ぶ）
完了条件: 偽COをサーバが拒否しない。回数制限は拒否する。
          突然死の解決直後に勝敗判定が走る。
          ticker だけで Vote と Execution を越えられる
参照: DESIGN.md §9.5 §6.1 / TEST_POLICY §3 §12 / D028

## 2.5 状態配信

含む: `player.list` / `player.deaths` / `player.action_state` / `game.state_sync`
完了条件: 再接続したクライアントが `game.state_sync` だけで状態を復元できる
参照: DESIGN.md §9.3 §9.4

## 2.6 完走

完了条件: 複数 Dummy Client が別プロセスから接続し1ゲーム完走。
          **手動の `advance_phase` / `resolve_votes` を呼ばずに完走すること。**
          テストドライバがサーバの代わりに進行を駆動していないことを確認する。
          Phase 2 handoff 作成

---

# Phase 3 — AI Client Skeleton（完了済み）

Phase全体の完了条件: RuleBased AI 9人でゲーム完走。
完了済み詳細は `Docs/ai/roadmap_archive/PHASE3_SCOPE.md` に全文保全。
SHA-256: `3c4077ce3737b759590e9dd849cdda2c2842a43d8148851b0d5d0d2f153b8d8f`。既存仕様・状態は変更しない。
以下の各節が必要な場合だけ、退避先の同名節を読む。

## 3.1 Network Client

## 3.2 World State・Memory

## 3.3 Brain Interface と Dummy Brain

## 3.4 Reaction・Chat Controller

## 3.5 Vote・Ability Controller

# Phase 4 — Local LLM

4.1 LLM backend interface / 4.2 Structured Output / 4.3 発言生成 / 4.4 投票・能力選択

完了条件: 1体のAI ClientをLLMで動かせる

# Phase 5 — 9 AI Agents

共有LLMサーバ / 生成キュー / 発言頻度調整 / 短文チャット

完了条件: AI 9人で自動ゲーム完走。OPEN_QUESTIONS Q8 の実測を行う

Status: **COMPLETE** — T154 exact canonical-9B standard Q8 PASS; T155 fresh closure review APPROVED.
This completion proves the `standard_9` nine-client transport/runtime, admission, audit, game-end,
and cleanup path. It does not prove responsive conversation or discussion quality.

The bounded external-review remediation is independently approved, and D069 records the user's
Q8 baseline selection. Phase 6 implementation begins only after its detailed design passes an
independent Design Gate. The Phase 5 checkpoint is already committed and pushed.

# Phase 6 — 議論品質

Belief / Suspicion / Strategy / 重要イベント記憶 / 反応スコア /
質問応答・反論・意見変更・ライン切り・CO判断・投票前再評価

完了条件（D072のユーザー決定。以下の5条件のみ）:

1. 9 AIで1ゲーム完走し、serverがgame endに到達、owned process残存0。
2. 他人の発言を受けたaccepted responsive chatが1件以上。
3. 質問→回答、主張→反論、意見変更のいずれかを独立Reviewerが原文で1件以上確認。
4. private channel本文・自分のprivate結果・tokenの公開境界への漏洩0。
5. 正規化後の同一文が同一playerの直前発言と一致せず、全ゲームで3回以上出現しない。

初期Phase6専用profileは200chars/600bytes/20–120 text token/whole-response512。
旧Phase5/Q8 profile、privacy/authorization/structured validationは維持する。
調整実行は受入証拠にせず、受入一回は直前ユーザー承認と独立Tester/Reviewerを要する。
製品scopeは `decisions/D072_PHASE6_REBASELINE.md`。最新運用はD073と
`PHASE6_MASTER_TEST_PLAN.md` に従い、現在はコード不変で計画/FREEZE候補を提示して停止。

入口証拠: T158 Investigator と T160 Reviewer が、T154 のprivate auditを本文非転載の
集計形式で内容監査した。transport/runtimeは成立している一方、発言は少数の反復的な
役職主張に偏り、実役職との不一致も観測された。Phase 6 Design Gate は、本人に許可された
役職・目的context、bounded memory summary + recent history、欠落表示、privacy、hard byte/
token bounds、およびprivate transcript quality reviewを定義する。800〜1,000 tokenやprefix
cache効果は未検証の仮説であり、acceptanceとして先に固定しない。

# Phase 7 — UI

Web UI（チャット / 生死 / 残り時間 / 投票 / 能力 / 入力中 / 結果 / 観戦 / 墓場）

# Phase 8 — Role Expansion / MOD

第三陣営の追加 / Passive・Effect・WinCondition の拡張 / MODローダー /
具体的な Modifier（恋人・狐憑き・手玉・呪い）/ Role Replacement（変化系・怪盗）

`standard_9` に含まれない妖狐・猫又・賢狼・狂人variant等について、呪殺、道連れ、
private knowledge、death masking、chat visibilityを強制する決定論的なnetwork completionを
追加する。単一の確率的presetだけで網羅したとは扱わない。

---

# 将来候補（今は触らない）

観戦者、GM、部屋一覧、ランダムマッチ、BOT補充、再接続UI、Elo、戦績、
リプレイ再生、AI思考可視化、トーナメント、複数LLM比較、音声、Discord連携、
Web公開、デスクトップ化。
