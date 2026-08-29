# AI Werewolf

複数のAIエージェントが、人狼ジャッジメント風のリアルタイムチャットで
自律的に議論・推理・欺瞞・投票・能力使用を行う**汎用人狼ゲーム基盤**。

「AI人狼のデモ」ではなく、人間参加・複数PC接続・異種LLM対戦・MOD役職追加まで
拡張できるプラットフォームを目標とする。

## 構成（予定）

```
server/   ゲームサーバ（唯一の正しい状態を持つ）
client/   プレイヤークライアント（AI / 人間）
content/  役職・陣営・ゲームモード定義（YAML）
tests/
Docs/ai/  AIエージェント向け永続ドキュメント
```

## AIエージェント（Codex等）で作業する場合

1. `AGENTS.md`
2. `Docs/ai/INDEX.md`
3. `Docs/ai/CURRENT_STATE.md`

の順に読むこと。セッション開始用プロンプトは `Docs/ai/PROMPTS.md` にある。

## 現在の状態

`Docs/ai/CURRENT_STATE.md` を参照。
