# Roadmap

マスター仕様 §30 に対応。各Phaseは「一つの責務が完成しテスト可能になる」単位でサブ分割する。

## Phase 1 — Game Core（LLM不使用）

- 1.1 Role属性5軸 / Team / Ability / Passive / Effect / Modifier のデータモデル + YAMLローダー
- 1.2 GameState / Player / Event Bus
- 1.3 Phase Manager（昼 / 投票 / 夜 / 朝）
- 1.4 投票と処刑（同数時ルールを設定化）
- 1.5 夜行動と Action Resolver（priority順の解決）
- 1.6 WinCondition 評価
- 1.7 Dummy操作での9人村完走テスト

完了条件: 標準9人村をDummy操作だけで最後まで進行できる。

## Phase 2 — Network Server

- 2.1 WebSocket サーバ + プロトコル定義（バージョン付き）
- 2.2 Session / Join / Ready
- 2.3 公開ブロードキャストと private 送信の分離
- 2.4 Chat / Vote / Ability の受付と検証
- 2.5 `game.state_sync`（再接続用スナップショット）

完了条件: 複数Dummy Clientが別プロセスから接続し1ゲーム完走。

## Phase 3 — AI Client Skeleton

- 3.1 Network Client
- 3.2 World State / Memory
- 3.3 Dummy Brain（Brain Interface）
- 3.4 Reaction / Chat Controller
- 3.5 Vote Controller / Ability Controller

完了条件: RuleBased AI 9人でゲーム完走。

## Phase 4 — Local LLM

- 4.1 LLM backend interface（OpenAI互換）
- 4.2 Structured Output
- 4.3 発言生成
- 4.4 投票・能力選択

完了条件: 1体のAI ClientをLLMで動かせる。

## Phase 5 — 9 AI Agents

- 共有LLMサーバ / 生成キュー / 発言頻度調整 / 短文チャット

完了条件: AI 9人で自動ゲーム完走。

## Phase 6 — 議論品質

- Belief / Suspicion / Strategy / 重要イベント記憶 / 反応スコア
- 質問応答・反論・意見変更・ライン切り・CO判断・投票前再評価

完了条件: 前の発言を受けた会話が成立する。

## Phase 7 — UI

- Web UI（チャット / 生死 / 残り時間 / 投票 / 能力 / 入力中 / 結果 / 観戦）

## Phase 8 — Role Expansion / MOD

- 第三陣営 / Passive / Effect拡張 / WinCondition拡張 / MODローダー
- 具体的な Modifier（恋人 / 狐憑き / 手玉 / 呪い）
- Role Replacement（変化系・怪盗の交換）の設計と実装

## 将来候補（今は触らない）

観戦者、GM、部屋一覧、ランダムマッチ、BOT補充、再接続UI、Elo、戦績、
リプレイ再生、AI思考可視化、トーナメント、複数LLM比較、音声、Discord連携、
Web公開、デスクトップ化。
