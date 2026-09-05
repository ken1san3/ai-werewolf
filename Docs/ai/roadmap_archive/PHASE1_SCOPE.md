# Phase 1 スコープ（完了済み。ROADMAP から退避）

2026-09-04 に Reviewer / Claude が `ROADMAP.md` の文字数上限（10000）内へ
Phase 3.3〜3.5 を書くため、完了済み Phase 1 のサブPhase詳細をここへ移した。
**内容は当時のまま。** 参照が必要なときだけ開く。

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
