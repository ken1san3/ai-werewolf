# Roadmap

各サブPhaseは「含む / 含まない / 完了条件 / 参照」を持つ。
**実装セッションはここで自分のスコープを決める。**手順は `RUNBOOK.md`。

進行中のPhaseと次のタスクは `CURRENT_STATE.md` を見る。

---

# Phase 1 — Game Core（LLM不使用）

## 1.1 データモデルと content ローダー ✅ 完了

## 1.2 GameState / Player / Event Bus / ログ

含む:
- `GameState`（プレイヤー、生死、日番号、現在フェーズ、pending actions の保持枠）
- `Player`（player_id / display_name / Role / Modifiers / 生死）
- Event Bus（イベントの発行と購読）
- イベント定義と、`public` / `private` / `ai` の3系統へのログ出力
- 乱数を `game.rng` に一元化し、テストで注入可能にする
- 役職配布（`role_missing` を含む）と `ROLE_ASSIGNED` イベント

含まない:
- フェーズ遷移そのもの（1.3）
- 投票・夜行動・勝敗判定
- ネットワーク

完了条件:
- 13役職と standard_9 プリセットから1ゲーム分の初期状態を構築できる
- 役職配布の結果がイベントとして記録される
- `public.jsonl` に秘匿情報が出ないことをテストで確認できる

参照: DESIGN.md §3 §4 §10 / TEST_POLICY §13

## 1.3 Phase Manager

含む:
- `Setup → Night0 → Dawn → Day → Vote → [Runoff] → Execution → Night → …` の状態機械
- 条件つきフェーズ（Night0 / Runoff）の仕組み
- 日番号の採番（Night0 → Dawn 1 → Day 1）
- `phase_ends_at` と `chat_enabled_at` の算出（15秒ルール、延長）
- `available_from_night` による能力の有効／無効

含まない:
- 投票の集計（1.4）、夜行動の解決（1.5）
- 実時間のタイマー駆動（テストは論理時刻で進める）

完了条件:
- Night0 から GameEnd まで、フェーズだけを空回しで一巡できる
- Night0 で人狼の襲撃能力が無効になる
- `runoff: true/false` で Runoff の有無が変わる

参照: DESIGN.md §6.1 §6.2 / TEST_POLICY §3

## 1.4 投票と処刑

含む:
- 投票の予約と締切での確定
- 決選投票 / 同数時のランダム処刑 / 処刑見送り / 無効票 / 自己投票
- `VoteResult`（処刑あり / 処刑なし / 決選投票へ）
- 処刑による死亡（`DeathCause.lynched`）
- `TIE_RESOLVED_RANDOM` などランダム結果のイベント記録

含まない:
- 猫又の道連れ（1.5 の Passive 側で扱う）
- 勝敗判定（1.6）

完了条件:
- D006 の投票設定がすべて動き、それぞれ両方の値でテストが通る
- 投票同数の4パターンが再現できる

参照: DESIGN.md §5 §6.3 / TEST_POLICY §11

## 1.5 夜行動の予約と Action Resolver

含む:
- `submit_action`（予約）と `resolve_pending_actions`（解決）の分離
- 予約の上書き。使用回数は解決時に消費
- priority 順の解決（DESIGN.md §7.1 の表）
- Effect の実装（Protect / Inspect / Attack / Kill / InspectRole / PublicNotify / MediumInspect）
- Passive の実装（`retaliate_on_death` / `on_inspected` / `public_notify_if_alive`）
- `DeathCause` と公開死因の導出、死亡連鎖（深さ上限は設けない。D027）
- 能力結果の通知範囲（DESIGN.md §7.4）
- 未選択フォールバック `no_selection`、`PUBLIC_NOTIFY`、`wolf_attack.random`、
  `no_self_target` restriction（D027）

含まない:
- 勝敗判定（1.6）
- Modifier の具体実装（Phase 8）

完了条件:
- TEST_POLICY §4 §5 §6 §7 §9 が通る
- 呪殺・護衛・襲撃・道連れの相互作用がすべてテストされている
- 内部死因がクライアント向けイベントに出ない

参照: DESIGN.md §7 §6.3 §4.2 §5 / TEST_POLICY §4 §5 §6 §7 §9 §10 / D024 / D027

## 1.6 WinCondition 評価と勝敗

含む:
- 3型（`eliminate_role_tag` / `count_parity` / `survive_when_others_win`）の評価
- `win_evaluation_order` に従う評価と便乗型の適用
- 生存者0人 → `draw`（全員敗北）
- `GAME_ENDED` イベントと `GameResult`
- 判定を走らせる位置（Execution 内・Night 内の各1回）
- 評価は `GameState` へ追記せず、専用サービス（`WinEvaluator` 等）として置く（D025）

含まない:
- レーティング、戦績

完了条件: TEST_POLICY §8 が通る

参照: DESIGN.md §8 / TEST_POLICY §8 / D025

## 1.7 get_available_actions

含む:
- `game.get_available_actions(player_id)`
- 制約宣言（`target` / `restrictions` / `uses` / `available_from_night`）から
  検証と列挙の両方を導く
- プレイヤー視点で組み立てる（そのプレイヤーが知ってよい情報のみ）
- 各フェーズの行動（チャット / CO / 投票 / 夜能力）
- 列挙と検証も `GameState` へ追記せず、専用サービスとして置く（D025）。
  `_phase_action_kinds` はここで公開 API へ置き換える

含まない:
- ネットワーク送信（Phase 2）

完了条件: TEST_POLICY §10 が通る

参照: DESIGN.md §9.4 §6.3 / TEST_POLICY §10 / D025

## 1.8 13役職の動作確認と完走テスト

含む:
- Dummy 操作による標準9人村の完走。夜行動も `get_available_actions` から選ぶ
- 妖狐入り構成、猫又入り構成、狂人系3種を含む構成での完走
- Phase 1 が担当する TEST_POLICY 節（§1〜§11 §13 §14。§12 は Phase 2.4）の通過確認

完了条件:
- 標準9人村を Dummy 操作だけで最後まで進行できる
- 13役職すべてが content の YAML だけで動作する
- 狂人 / 狂信者 / 囁く狂人 がコアの分岐なしに区別される
- **Phase 1 完了。`handoffs/PHASE1_HANDOFF.md` を作成する**

参照: TEST_POLICY 全体

---

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

# Phase 3 — AI Client Skeleton

Phase 3 全体の完了条件: RuleBased AI 9人でゲーム完走

3.2 World State・Memory / 3.3 Brain Interface と Dummy Brain /
3.4 Reaction・Chat Controller / 3.5 Vote・Ability Controller の
含む / 含まない / 完了条件は未確定（R-20260901-82）。3.1 の完了後に書く。

## 3.1 Network Client

サーバの WebSocket プロトコルだけで席に着き、切断をまたいで
本人視点の状態を保ち続ける単独プロセスのクライアント基盤を作る。
**このサブPhaseは「繋がり続けて、受け取って、送れる」までを担当し、
何を送るかは決めない。**

含む:
- 入室トークンによる Join と、private に受け取る接続トークンの保持
- 接続トークンと受信済み `last_seq` による Resume
- 受信ループと、封筒（`type` / `protocol_version` / `event_id` / `game_id` /
  `seq` / `timestamp` / `payload`）の検証
- `protocol_version` のメジャー不一致で接続を打ち切る
- プレイヤー単位 `seq` の欠番検出と、そこからの状態回復
- `game.state_sync` / `player.list` / `player.deaths` / `player.action_state` の
  受信と、本人視点の最新状態としての保持。**保持するだけで解釈しない**
- `phase_ends_at` からクライアント側の締切タイマーを起こす
- 上位（Brain / Controller）が使う送信 API。組み立ての入力は
  受信した `player.action_state` の列挙だけ
- `action.rejected` の受理
- 切断検知と再接続。諦めるときはプロセスとして明示的に終わる
- LLM を使わずに動く Dummy 操作での結合テスト

含まない:
- World State の構造化と記憶（3.2）
- Brain interface、Dummy Brain（3.3）
- 発言内容・反応制御（3.4）、投票や能力の選択方針（3.5）
- ゲームコアの import、および可否判定のクライアント側での再実装
- 役職名・チャネルIDのクライアントへの直書き
- 再接続 UI、観戦（将来候補）

完了条件:
- 別プロセスの Network Client 9個が、サーバ tick だけで `GAME_ENDED` へ到達する。
  行動の選択は Dummy でよいが、**選択肢は受信した列挙からのみ導く**
- 1個を任意のタイミングで落として再起動しても、保存済み接続トークンで
  同じ席へ Resume し、取りこぼした範囲を回復して完走する
- 受信列に欠番が生じたことをクライアントが検出したと、テストから観測できる
- `protocol_version` のメジャーが異なるサーバへは接続しない
- `action.rejected` を受け取ったことがテストから観測でき、握り潰されない
- `server.aiwolf_core` と `server.network` を import していない
- LLM 無しでテストが完走する

**Phase 3.1 の詳細設計で決定する事項。ROADMAP では決めない（D051）:**
- `seq` 欠番時の回復方式
- 接続トークンの保存責務
- receive / send の asyncio 構造
- `action.rejected` の責務境界
- reconnect backoff と終了条件

参照: DESIGN.md §2 §9.1 §9.2 §9.3 §9.4
参照: D048 / D049 / D050 / D051、`handoffs/PHASE2_HANDOFF.md`
参照: TEST_POLICY「ネットワーク Phase の検証の所在」（節番号は持たない）

# Phase 4 — Local LLM

4.1 LLM backend interface / 4.2 Structured Output / 4.3 発言生成 / 4.4 投票・能力選択

完了条件: 1体のAI ClientをLLMで動かせる

# Phase 5 — 9 AI Agents

共有LLMサーバ / 生成キュー / 発言頻度調整 / 短文チャット

完了条件: AI 9人で自動ゲーム完走。OPEN_QUESTIONS Q8 の実測を行う

# Phase 6 — 議論品質

Belief / Suspicion / Strategy / 重要イベント記憶 / 反応スコア /
質問応答・反論・意見変更・ライン切り・CO判断・投票前再評価

完了条件: 前の発言を受けた会話が成立する

# Phase 7 — UI

Web UI（チャット / 生死 / 残り時間 / 投票 / 能力 / 入力中 / 結果 / 観戦 / 墓場）

# Phase 8 — Role Expansion / MOD

第三陣営の追加 / Passive・Effect・WinCondition の拡張 / MODローダー /
具体的な Modifier（恋人・狐憑き・手玉・呪い）/ Role Replacement（変化系・怪盗）

---

# 将来候補（今は触らない）

観戦者、GM、部屋一覧、ランダムマッチ、BOT補充、再接続UI、Elo、戦績、
リプレイ再生、AI思考可視化、トーナメント、複数LLM比較、音声、Discord連携、
Web公開、デスクトップ化。
