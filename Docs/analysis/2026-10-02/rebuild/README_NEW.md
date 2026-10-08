# AI Werewolf

複数のAIエージェントが、リアルタイムチャットで議論・推理・欺瞞・投票・能力使用を行う
**汎用人狼ゲーム基盤**。

「AIに人狼を1回プレイさせるデモ」ではなく、人間参加・複数PC接続・異種LLM対戦・
MOD役職追加まで拡張できるプラットフォームを目標にしています。

> 個人開発プロジェクトです。人狼ジャッジメント（そらいろ株式会社）をルールの参照実装として
> 参照していますが、非公式であり同社とは一切関係ありません。

## 現在の状態（2026-10）

| 部分 | 状態 |
|---|---|
| ゲーム本体（`server/aiwolf_core`） | 完成。13役職をYAMLで定義。LLMなしでテスト可能 |
| ネットワークサーバ（`server/network`） | 完成。WebSocket、サーバ権威、private配送 |
| AIエージェント（`ai_agent/`） | 作り直し中。進め方は `ROADMAP.md` |

2026-09〜10 の旧AIクライアントと旧運用は、Gitタグ `archive/2026-10-02-*` に保存しています。
作り直しの理由と根拠は `Docs/analysis/2026-10-02/`。

## 設計の中心

### サーバが唯一の正しい状態を持つ
ゲーム状態・役職・生死・能力可否・勝敗・秘匿範囲はすべてサーバが決定し、
クライアントの申告は必ず検証されます。AIの誤判断でルールが変わることはありません。

```
Werewolf Server        ゲーム状態・ルール・進行・勝敗・公開範囲
      │  WebSocket (JSON)
AI Agent / Human Client
      │
   LLM Server          全エージェントで共有（RTX 3070 Ti / 8GB 想定）
```

### 秘匿情報はネットワーク段階で落とす
各クライアントには、そのクライアントが知ってよい情報だけを送ります。
死因は内部値と公開値を分け、公開値は死亡時のフェーズから導出します。

### 役職はコードではなくデータ
役職は `team` / `count_as` / `attack_result` / `inspect_result` / `medium_result` の5軸を持ち、
すべて `content/` のYAMLで定義します。役職追加にPythonの変更が必要なら設計の失敗です。

## 製品としての要件（初期構想から引き継ぐもの）

- 会話はリアルタイムの自由発言。ターン制にしない。
- AIは全発言に反応しない。話すかどうか、いつ話すかをAI自身が決める。
- 生成中に状況が変わって古くなった発言は、送らずに取り消せる。
- AIごとの性格（話し方・積極性）を設定できる。
- 人狼同士の秘密の会話（wolf channel）。
- 将来: 人間の参加、複数PCからの接続、異なるLLM同士の対戦、MOD役職。

## 動かす

```bash
python -m pip install -e ".[dev]"
```

```bash
python -m pytest
```

AIでゲームを回すには、ローカルの llama-server（OpenAI互換API）が必要です。

```bash
llama-server -m <models-dir>/Qwen3.5-9/Qwen3.5-9B-Q4_K_M.gguf --host 127.0.0.1 --port 8090 -c 8192 -np 1 --jinja --reasoning off -ngl 99
```

```bash
python -m ai_agent.play --seed 1
```

結果は `games/` に書き起こし（transcript.md）と機械チェック（checks.json）として出力されます。
（`ai_agent` は Stage 1 で作成します）

## リポジトリ構成

```
server/aiwolf_core/   ゲームコア（ルール・進行・投票・夜行動・死亡・勝敗・イベント）
server/network/       WebSocketサーバ（セッション・配送・プロトコル検証）
protocol/             プロトコルのJSON Schema（v1.1が現役）
content/              役職・陣営・ルールプリセット（YAML）
ai_agent/             AIエージェント（作り直し中）
tests/                テスト
Docs/ai/spec/         ゲーム仕様（DESIGN.md）と参照ルール
Docs/ai/decisions/    ゲームルールの決定理由（D001〜D050）
Docs/analysis/        2026-10 の停滞分析と試作
```

## 開発の進め方

`AGENTS.md`（AI向けの作業ルール）、`ROADMAP.md`（計画）、`WORKLOG.md`（作業記録）を参照してください。

## ライセンス

未定です。現時点では全権利を留保しています。
