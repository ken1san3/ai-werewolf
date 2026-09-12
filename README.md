# AI Werewolf

複数のAIエージェントが、リアルタイムチャットで議論・推理・欺瞞・投票・能力使用を行う
**汎用人狼ゲーム基盤**。

「AIに人狼を1回プレイさせるデモ」ではなく、人間参加・複数PC接続・異種LLM対戦・
MOD役職追加まで拡張できるプラットフォームを目標にしています。

> 個人開発プロジェクトです。人狼ジャッジメント（そらいろ株式会社）をルールの参照実装として
> 参照していますが、非公式であり同社とは一切関係ありません。

---

## 設計の中心にあるもの

### 1. サーバが唯一の正しい状態を持つ

ゲーム状態・役職・生死・能力可否・勝敗・秘匿範囲はすべてサーバが決定し、
クライアントの申告は必ず検証されます。AIの誤判断でルールが変わることはありません。

```
Werewolf Server        ゲーム状態・ルール・進行・勝敗・公開範囲
      │  WebSocket (JSON)
AI Client / Human Client
      │
   LLM Server          全エージェントで共有（RTX 3070 Ti / 8GB 想定）
```

### 2. 秘匿情報はネットワーク段階で落とす

「LLMに全部渡してから、プロンプトで忘れろと指示する」方式を禁止しています。
各クライアントには、そのクライアントが知ってよい情報だけを送ります。

たとえば死因は内部値（`cursed` / `attacked` / `retaliation` …）と
公開値（`lynched` / `died_in_day` / `died_in_night`）を分離し、
**公開値は死亡解決時のフェーズから導出**します。対応表を持たないので、
新しい死因を追加しても自動的にマスクされます。

呪殺された妖狐と襲撃された村人は、村側から区別できません。

### 3. 役職はコードではなくデータ

役職追加に Python の変更が必要になったら設計の失敗、という基準で作っています。

役職は5つの独立した軸を持ちます。

| 軸 | 例 |
|---|---|
| `team` | 勝利判定上の所属 |
| `count_as` | 人数カウント上の扱い |
| `attack_result` | 襲撃を受けたとき |
| `inspect_result` | 占い結果 |
| `medium_result` | 霊能結果 |

狂人は `team: wolf` かつ `count_as: village`、大狼は占い結果と霊能結果が食い違う——
これらを1つの「陣営」フィールドでは表現できません。

```yaml
id: madman
name: 狂人
team: wolf
count_as: village
inspect_result: not_wolf
medium_result: not_wolf
knows_teammates: false
chat_channels: [public]
```

**狂人 / 狂信者 / 囁く狂人**は5軸がすべて同一で、`knows_teammates` と
`chat_channels` だけが違います。この3つをゲームコアの分岐なしに表現できることを
Phase 1 の合格条件にしています。

---

## 現在の状態

| | |
|---|---|
| Phase | 3.4 Reaction Chat（実装着手前） |
| 完了 | Phase 1、Phase 2、Phase 3.1〜3.3 |
| 実装済み役職 | 13種（すべて YAML 定義） |
| 通常テスト | 310 passed（2026-09-09 Local Windows） |

詳細は [`Docs/ai/CURRENT_STATE.md`](Docs/ai/CURRENT_STATE.md)。

---

## 動かす

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

Python 3.10 以上。実行時の依存は PyYAML のみです。

content の読み込みだけを試す場合:

```python
from server.aiwolf_core.content import load_content, load_preset

content = load_content("content")
preset = load_preset("content/presets/standard_9.yaml", content)

print(len(content.roles))                    # 13
print(content.roles["madman"].attributes)    # team=wolf, count_as=village, ...
```

---

## リポジトリ構成

```
server/aiwolf_core/   ゲームコア
  models.py           データモデル（役職・陣営・能力・ルール）
  content.py          YAML ローダーと厳格なバリデーション
  game.py             権威状態の保持と公開 API
  phase.py / voting.py / actions.py / death.py
                      フェーズ遷移・投票・夜行動・死亡処理
  events.py           可視性つきイベントバスとログ
content/              役職・陣営・ルールプリセット（YAML）
tests/
scripts/ai_status.py  現在の状態を1画面で表示
Docs/ai/              設計ドキュメントと開発運用の記録
```

---

## 開発の進め方

このプロジェクトは、Integrator が作業を分解し、短命な Worker が実装・詳細設計・
調査を担当し、独立した Reviewer が検証する体制で進めます。

| Responsibility | Purpose |
|---|---|
| Integrator | 現在地・critical path・task分解・統合・次waveの管理 |
| Architect | Design Gate対象のinterface・lifecycle・acceptance設計 |
| Implementer | 承認済みcontract内の実装とテスト |
| Reviewer | design / implementation / tests / diff の独立確認 |
| Tester | focused / integration / completion / regression の独立実測 |
| Investigator | 原因不明・E2E・並行性問題の再現と原因特定 |
| 仕様の決定 | 人間が最終決定 |

具体的な実行モデルは責務と分離され、現在の運用設定だけを
[`Docs/ai/MODEL_ASSIGNMENTS.md`](Docs/ai/MODEL_ASSIGNMENTS.md) に置きます。

会話履歴を長期記憶にせず、**リポジトリを記憶装置として使う**のが方針です。
入口は [`AGENTS.md`](AGENTS.md) → `python scripts/ai_status.py <entry>` →
task packetです。現在地は [`Docs/ai/CURRENT_STATE.md`](Docs/ai/CURRENT_STATE.md)、
task boardは [`Docs/ai/TASKS.md`](Docs/ai/TASKS.md)、複数チャット運用は
[`Docs/ai/WORKFLOW.md`](Docs/ai/WORKFLOW.md) を参照してください。

`decisions/` には却下した案と理由も残し、別セッションが同じ失敗を繰り返すのを防ぎます。

詳しくは [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## ライセンス

未定です。現時点では全権利を留保しています。
