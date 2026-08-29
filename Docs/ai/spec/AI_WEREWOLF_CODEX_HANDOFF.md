# AI人狼ゲーム 実装引継ぎ仕様書

## 0. このファイルの目的

このファイルは、Codexに本プロジェクトの実装を引き継ぐための最上位仕様書である。

本プロジェクトの目的は、**人狼ジャッジメントのようなリアルタイムチャット形式で、複数のAIエージェントが自律的に議論・推理・欺瞞・投票・能力使用を行う人狼ゲーム基盤**を構築することである。

重要なのは「AI人狼のデモ」を作ることではなく、今後以下へ拡張可能な**汎用的な人狼ゲームプラットフォーム**を作ることである。

- AIのみの対戦
- 人間 + AI混成対戦
- 複数PCからの接続
- 異なるLLM同士の対戦
- ローカルLLM / API LLM切り替え
- 大量の追加役職
- オリジナル役職
- MOD形式での役職追加
- 将来的なWeb UI / デスクトップUI
- AI戦略・モデル性能比較実験

この仕様書に明記されていない細部については、**拡張性、疎結合、テスト容易性、サーバ権威性を優先して実装判断すること。**

---

# 1. 最重要設計思想

## 1.1 サーバとプレイヤーを完全分離する

ゲームサーバとプレイヤークライアントを別プログラムとして実装する。

サーバから見て、AIプレイヤーと人間プレイヤーは極力同一の「プレイヤークライアント」として扱える設計にする。

概念構成:

```text
┌──────────────────────────────────┐
│          Werewolf Server         │
│                                  │
│  ・ゲーム状態                    │
│  ・ルール                        │
│  ・役職                          │
│  ・能力                          │
│  ・昼夜進行                      │
│  ・投票                          │
│  ・勝敗判定                      │
│  ・チャット                      │
│  ・公開/非公開情報管理           │
└────────────────┬─────────────────┘
                 │
              WebSocket
                 │
        ┌────────┼────────┐
        │        │        │
        ▼        ▼        ▼
    AI Client AI Client Human Client
        │
        ▼
     LLM Server
```

サーバはAIの推論ロジックを知らない。

AIクライアントは人狼ゲームの正しい状態そのものを保持しない。

---

## 1.2 サーバを唯一の正しい情報源とする

Server Authoritative方式を採用する。

以下は必ずサーバが決定・保持する。

- プレイヤー一覧
- プレイヤー生死
- 役職
- 陣営
- 能力
- 昼夜フェーズ
- 残り時間
- 投票
- 処刑
- 襲撃
- 占い
- 護衛
- 霊能結果
- 能力使用可否
- 勝敗
- 秘密情報の公開範囲

クライアントから来た操作は必ずサーバ側で検証する。

例:

```text
AI: 「Bobを占う」
↓
Server:
・現在夜か？
・本人は生存中か？
・占い能力を持つか？
・今夜まだ未使用か？
・Bobは有効対象か？
↓
OKなら実行
NGなら拒否
```

クライアントを信用してはならない。

---

# 2. 初期ゲーム構成

最初の動作確認用の標準9人村:

| 役職 | 人数 |
|---|---:|
| 市民 | 3 |
| 人狼 | 2 |
| 占い師 | 1 |
| 狩人 | 1 |
| 狂人 | 1 |
| 霊能者 | 1 |

ただし、この役職構成をコードへハードコードしないこと。

設定ファイルからゲーム構成を変更できるようにする。

例:

```yaml
players: 9

roles:
  villager: 3
  werewolf: 2
  seer: 1
  guard: 1
  madman: 1
  medium: 1
```

---

# 3. リアルタイムチャット仕様

## 3.1 ターン制議論は禁止

本プロジェクトでは、

```text
A発言
↓
B発言
↓
C発言
```

のような順番制を採用しない。

人狼ジャッジメントのように、昼時間中は各プレイヤーが任意のタイミングで発言できるリアルタイムチャット形式とする。

想定:

```text
アリス:
占いCO、ボブ白

ケン:
対抗いる？

ミカ:
昨日ボブ疑ってたのにそこ占ったの？

ボブ:
俺もそこは聞きたい

アリス:
怪しいと思ったから占ったんだけど

ユウ:
それ自体は別に矛盾してなくない？
```

発言順は固定しない。

---

## 3.2 AIは全発言へ反応しない

9人全員が各チャットメッセージへ必ず回答する設計は禁止。

AIには「反応したい度」を持たせる。

例:

```text
+80 自分の名前が出た
+70 自分へ質問された
+70 自分が疑われた
+60 人狼仲間が疑われた
+50 自分の役職に関係するCO
+40 強く疑っている相手が発言
+30 自分と逆の意見
+20 長時間発言していない
-50 直前に自分が発言済み
```

これは完全固定ルールでなくてもよい。

最終的には、

- ヒューリスティック
- LLM判断
- 両者混合

を差し替えられる設計にすること。

---

## 3.3 発言キュー

各AIは発言したい場合、即座にサーバへ文章を送るのではなく、AI Client内で生成処理を行う。

想定:

```text
Chat Event
↓
Agent state update
↓
Reaction decision
↓
Generation Queue
↓
LLM
↓
Typing delay
↓
Chat send
```

複数AIが同時に発言生成していてよい。

---

## 3.4 発言キャンセル / 再考

AIが回答生成中に、重要な新規発言が来た場合、

- 現在生成を続行
- 生成を破棄
- 状況を再評価して再生成

を選択できる設計が望ましい。

例:

```text
Alice:
占いCO、Bob白

Mika:
（返答生成中）

Alice:
昨日怪しかったから占った

Mika:
旧返答を破棄
↓
再評価
↓
「なるほど、怪しかったから占ったならそこは一応筋通るか」
```

初期版では生成キャンセルを未実装でもよいが、拡張可能な構造にする。

---

# 4. ネットワーク

## 4.1 WebSocket

リアルタイム性のため、サーバ・クライアント通信にはWebSocketを第一候補とする。

HTTP RESTのみでリアルタイムチャットを実現しない。

REST APIを補助的に用いることは可。

---

## 4.2 通信メッセージ

通信データはJSONを基本とする。

すべてのイベントに、可能であれば以下を持たせる。

```json
{
  "type": "chat.message",
  "event_id": "uuid",
  "game_id": "uuid",
  "timestamp": 0,
  "payload": {}
}
```

イベント名は文字列で明確に分類する。

---

## 4.3 例: チャット

Server → Clients

```json
{
  "type": "chat.message",
  "payload": {
    "player_id": "alice",
    "display_name": "Alice",
    "message": "Bobちょっと怪しくない？"
  }
}
```

Client → Server

```json
{
  "type": "chat.send",
  "payload": {
    "message": "どの辺が怪しいと思った？"
  }
}
```

---

## 4.4 例: 投票

```json
{
  "type": "vote.cast",
  "payload": {
    "target_player_id": "alice"
  }
}
```

---

## 4.5 例: 能力使用

```json
{
  "type": "ability.use",
  "payload": {
    "ability_id": "inspect",
    "target_player_ids": ["bob"]
  }
}
```

---

## 4.6 エラー

```json
{
  "type": "action.rejected",
  "payload": {
    "action": "ability.use",
    "reason": "invalid_target"
  }
}
```

AIが失敗理由を理解できる内容にする。

---

# 5. サーバ内部アーキテクチャ

推奨:

```text
server/
├─ core/
│  ├─ game.py
│  ├─ player.py
│  ├─ event_bus.py
│  ├─ phase_manager.py
│  ├─ action_resolver.py
│  └─ game_state.py
│
├─ rules/
│  ├─ role.py
│  ├─ team.py
│  ├─ ability.py
│  ├─ passive.py
│  ├─ effect.py
│  ├─ target.py
│  └─ win_condition.py
│
├─ content/
│  └─ loader.py
│
├─ network/
│  ├─ websocket_server.py
│  ├─ protocol.py
│  └─ session.py
│
├─ services/
│  ├─ chat_service.py
│  ├─ vote_service.py
│  └─ game_service.py
│
└─ main.py
```

厳密にこの構成にする必要はないが、責務分離は維持すること。

---

# 6. Event Driven設計

ゲーム内部はEvent Busを中心に構成する。

想定イベント:

```text
GAME_CREATED
GAME_STARTED

PLAYER_JOINED
PLAYER_LEFT

DAY_STARTED
DISCUSSION_STARTED
DISCUSSION_ENDED

CHAT_MESSAGE

VOTING_STARTED
VOTE_CAST
VOTING_ENDED

PLAYER_LYNCHED

NIGHT_STARTED

ABILITY_SELECTED
ABILITY_USED
ABILITY_RESOLVED

PLAYER_ATTACKED
PLAYER_PROTECTED
PLAYER_INSPECTED
PLAYER_DIED

ROLE_REVEALED

MORNING_STARTED
DAY_ENDED

GAME_END_CHECK
GAME_ENDED
```

新役職追加時にゲームコアへの大量のif文追加が必要にならないようにする。

---

# 7. 役職システム

## 7.1 Roleを能力の集合として扱う

役職そのものへ処理を大量に持たせない。

概念:

```text
Role
├─ Team
├─ Abilities
├─ Passives
└─ WinCondition
```

---

## 7.2 役職定義を外部化

YAMLまたはJSONから読み込む。

例: 占い師

```yaml
id: seer
name: 占い師
team: village

description: >
  毎晩、生存者1人を占い、その人物の陣営情報を知る。

abilities:
  - id: inspect
    timing: night_action
    target:
      selector: alive_other
    uses:
      per_night: 1
```

---

例: 狩人

```yaml
id: guard
name: 狩人
team: village

abilities:
  - id: protect
    timing: night_action
    target:
      selector: alive_other
    uses:
      per_night: 1

restrictions:
  - type: no_same_target_consecutive
```

---

例: 人狼

```yaml
id: werewolf
name: 人狼
team: wolf

abilities:
  - id: wolf_chat
    timing: night

  - id: wolf_attack
    timing: night_action
    target:
      selector: alive_non_team
```

---

# 8. Ability / Passive / Effect

## 8.1 Effectを最小単位とする

役職追加の柔軟性を最大化するため、AbilityをさらにEffectへ分解できる設計を推奨する。

例:

```text
InspectAbility
↓
RevealAlignmentEffect
↓
PrivateNotificationEffect
```

---

## 8.2 初期Effect候補

最低限、以下のようなEffectを表現できる構造を目指す。

```text
Kill
Protect
InspectAlignment
InspectRole
Reveal
PrivateNotify
PublicNotify
RoleBlock
Silence
Revive
Poison
Heal
ExtraVote
VoteModifier
SwapRole
CopyRole
ChangeTeam
Immune
Redirect
RandomTarget
```

すべてを初期実装する必要はない。

まず標準9人村に必要なものを実装し、APIを拡張可能にする。

---

# 9. 第三陣営

村・狼の2陣営をハードコードしない。

例:

```yaml
teams:
  village:
    name: 村人陣営

  wolf:
    name: 人狼陣営

  fox:
    name: 妖狐陣営
```

妖狐のような第三陣営を後から追加可能にする。

例:

```yaml
id: fox
name: 妖狐
team: fox

passives:
  - type: immune
    source: wolf_attack

  - type: die_when_inspected

win_conditions:
  - type: survive_when_base_game_ends
```

---

# 10. 勝利条件

勝敗条件も役職・陣営から分離する。

例:

村:

```yaml
win_conditions:
  - type: eliminate_team
    target: wolf
```

狼:

```yaml
win_conditions:
  - type: parity
    against: village
```

妖狐:

```yaml
win_conditions:
  - type: survive_at_game_end
```

将来的には恋人・単独勝利・特殊勝利などへ対応できる構造にする。

---

# 11. 夜能力の解決順

複数能力が同じ夜に使用されるため、解決順を明示的に制御する。

例:

```text
10 RoleBlock
20 Redirect
30 Protect
40 Inspect
50 Attack
60 Poison
70 DeathResolve
80 Revive
```

実際の順序は設定可能にする。

AbilityまたはEffectがpriorityを持つ設計が望ましい。

例:

```yaml
priority: 40
```

---

# 12. AI Client

## 12.1 AI側の責務

AI Clientは以下を担当する。

- WebSocket通信
- 公開情報の記憶
- 自分だけの秘密情報の記憶
- チャット履歴
- 推理状態
- 疑惑度
- 信頼度
- 自分の戦略
- 発言判断
- 発言生成
- 投票判断
- 能力使用判断
- LLM呼び出し

---

## 12.2 推奨構造

```text
client/
├─ common/
│  ├─ network.py
│  ├─ protocol.py
│  └─ events.py
│
├─ ai/
│  ├─ agent.py
│  ├─ world_state.py
│  ├─ memory.py
│  ├─ belief.py
│  ├─ strategy.py
│  ├─ reaction.py
│  ├─ chat_controller.py
│  └─ action_controller.py
│
└─ main.py
```

---

# 13. AI Brainを差し替え可能にする

Brain Interfaceを作成する。

概念:

```python
class Brain:
    async def on_event(self, event):
        ...

    async def decide_reaction(self):
        ...

    async def generate_chat(self):
        ...

    async def choose_vote(self, candidates):
        ...

    async def choose_ability(self, abilities):
        ...
```

実装例:

```text
QwenBrain
GemmaBrain
LlamaBrain
RuleBasedBrain
RandomBrain
RemoteAPIBrain
```

---

# 14. AIに役職名をハードコードしない

AI Client側に、

```python
if role == "seer":
```

のような役職別ロジックを大量に書かない。

サーバからAIへ役職説明と利用可能能力を渡す。

例:

```json
{
  "role": {
    "id": "astrologer",
    "name": "占星術師",
    "description": "毎晩、生存者1名を調査できます。",
    "team": {
      "id": "village",
      "name": "村人陣営"
    },
    "abilities": [
      {
        "id": "inspect",
        "description": "対象1名の陣営を確認する",
        "available": true,
        "valid_targets": [
          "alice",
          "bob",
          "carol"
        ]
      }
    ]
  }
}
```

これにより未知のオリジナル役職でもLLMがルール文章を読んで行動できるようにする。

---

# 15. AIの内部状態

各AI Agentは最低限以下の情報を持つ。

```text
Self
├ role
├ team
├ abilities
├ alive
└ private_information

Players
├ alive/dead
├ claimed_roles
├ suspicion
├ trust
├ notable_statements
└ relationships

Memory
├ recent_chat
├ important_events
├ day_summary
└ own_statements

Strategy
├ current_goal
├ current_suspects
├ defend_targets
├ attack_targets
├ deception_plan
└ risk_level
```

---

# 16. LLMへ全履歴を丸投げしない

小型LLMでも人狼推論を行えるよう、外部記憶を活用する。

LLMへの入力例:

```text
【自分】
役職: 人狼
仲間: Mika

【現在の重要情報】
Alice: 占い師CO
Bob: Aliceから白判定
Mika: 昨日の発言矛盾を指摘され疑惑上昇

【疑惑度】
Alice 40
Bob 15
Carol 60
Mika 仲間
Yu 20

【自分への疑惑】
Alice → 自分: 低
Bob → 自分: 高

【重要イベント】
- Aliceが占いCO
- Bob白判定
- MikaがCarolから追及された

【直近チャット】
...

【現在の目的】
状況を評価し、必要なら短く発言する。
```

---

# 17. 思考と会話を分離する

AI処理は可能なら以下へ分ける。

```text
Situation Analysis
↓
Internal Strategy Update
↓
Action Decision
↓
Natural Language Generation
```

LLMから構造化結果を取得することを推奨。

例:

```json
{
  "should_speak": true,
  "urgency": 0.83,
  "target": "carol",
  "intent": "question",
  "reason": "昨日と今日の主張が変化している",
  "message_plan": "投票先変更理由を確認する"
}
```

その後、自然な発言へ変換する。

---

# 18. 小型モデル向け設計

初期ターゲット環境:

```text
GPU: RTX 3070 Ti
VRAM: 8GB
```

9つのモデルを同時ロードしない。

同一LLMを複数Agentから共有する。

概念:

```text
AI Agent 1 ─┐
AI Agent 2 ─┤
AI Agent 3 ─┤
...         ├─→ Local LLM Server
AI Agent 9 ─┘
```

候補:

- llama.cpp server
- Ollama
- その他OpenAI互換ローカル推論サーバ

LLMバックエンドは抽象化する。

---

# 19. Thinkingと高速応答

可能なら2種類の推論を使い分けられるようにする。

通常:

```text
短い発言
軽い反応
質問への回答
```

→ 高速推論

重要局面:

```text
占いCO
黒判定
仲間への疑惑急上昇
自分への強い疑惑
投票直前
夜行動決定
```

→ 深い戦略推論

バックエンドがThinking mode非対応でも動作可能にする。

---

# 20. 人格

Agentごとに人格設定を持つ。

例:

```yaml
name: Alice

personality:
  style: logical
  aggressiveness: 0.4
  talkativeness: 0.6
  caution: 0.8
  response_speed: 0.5
  message_length: short
```

これらはゲーム強度とは別に扱う。

将来人格プリセットを追加できる構成にする。

---

# 21. 「入力中」演出

将来UIでは発言生成中にtyping状態を通知できるようにする。

例:

```json
{
  "type": "player.typing",
  "payload": {
    "player_id": "alice",
    "typing": true
  }
}
```

AIごとに反応速度差を持たせられるようにする。

ただしLLM推論遅延そのものを完全に信用せず、演出用ディレイを別設定可能にする。

---

# 22. AI同士の秘密会話

人狼陣営チャットなどを実装可能にする。

Chat Channelという概念を導入することを推奨。

```text
public
wolf
lover
spectator
system
private:<player_id>
```

サーバが閲覧権限を管理する。

AI Clientは権限のないチャネルを取得できない。

---

# 23. MOD / Content Pack

最終的に以下のような構造を目指す。

```text
mods/
├─ standard/
│  ├─ roles/
│  ├─ teams/
│  ├─ abilities/
│  └─ game_modes/
│
├─ judgment_style/
│  └─ roles/
│
└─ custom_pack/
   ├─ roles/
   ├─ abilities/
   └─ config.yaml
```

最初から完全なMODシステムを作り込む必要はない。

ただし、役職データの外部読み込みを初期段階から採用し、後でMODローダーへ移行しやすくする。

---

# 24. ログ

このプロジェクトではAI挙動解析が重要なので、ログを非常に重視する。

最低限保存:

```text
game id
random seed
game config
player config
role assignment
全公開チャット
秘密チャット
投票
能力使用
ゲームイベント
死亡理由
勝敗
AI内部判断
LLM request metadata
LLM response
LLM latency
```

ただし、人間プレイヤーを将来参加させる場合、LLM内部ログ・秘密情報の扱いには注意する。

---

# 25. リプレイ

ゲームイベントを時系列ログとして保存し、後からリプレイ可能な構成が望ましい。

Game Stateを直接保存するだけでなく、

```text
Event 1
Event 2
Event 3
...
```

を保存する。

完全なEvent Sourcingまでは初期要件ではないが、イベントログからゲーム経過を確認できること。

---

# 26. テスト

## 26.1 AIなしでゲームエンジンをテストする

LLMがなくてもテスト可能にする。

RuleBased / Dummy Clientを用意する。

最低限テスト:

- 役職配布
- 昼夜遷移
- 投票
- 処刑
- 人狼襲撃
- 占い
- 護衛
- 霊能
- 勝敗判定
- 不正行動拒否
- 死亡者発言禁止
- 非公開情報漏洩防止

---

## 26.2 能力組み合わせ

例:

```text
狼がA襲撃
狩人がA護衛
↓
A生存
```

```text
占い師が狼を占う
↓
占い師だけに狼判定通知
```

```text
処刑された狼
↓
霊能者へ狼結果通知
```

---

# 27. セキュリティ / 情報漏洩防止

AIへプロンプトとしてゲーム全状態を渡してはならない。

特に以下は危険:

```text
Server GameState:
Alice = Seer
Bob = Werewolf
Carol = Villager
...
```

AI ClientにはそのAIが本来知り得る情報だけを送る。

「LLMへ秘密情報が一度でも渡ったが、プロンプトで忘れろと指示する」は禁止。

ネットワーク段階で情報を分離する。

---

# 28. 開発言語

初期実装はPythonを第一候補とする。

理由:

- LLM連携が容易
- WebSocketライブラリが豊富
- asyncioとの相性
- AIロジック開発速度
- テスト容易
- ローカルLLM API連携が簡単

ただし通信プロトコル自体は言語非依存にする。

将来:

- C++
- TypeScript
- Rust

などのクライアントが接続可能であること。

---

# 29. 推奨技術候補

必須ではない。

Server:

```text
Python
FastAPI
WebSocket
Pydantic
asyncio
```

AI Client:

```text
Python
asyncio
websockets / httpx
```

LLM:

```text
llama.cpp server
または
Ollama
```

Config:

```text
YAML
```

Test:

```text
pytest
pytest-asyncio
```

---

# 30. 初期実装ロードマップ

## Phase 1: ゲームコア

LLMを一切使用しない。

実装:

- Player
- Game
- Role
- Team
- Phase
- Event Bus
- Role Loader
- 基本役職
- 能力
- 投票
- 夜行動
- 勝敗判定

完了条件:

標準9人村をDummy操作だけで最後まで進行できる。

---

## Phase 2: Network Server

実装:

- WebSocket
- Client session
- Join
- Ready
- Chat
- Vote
- Ability
- Server event broadcast
- Private event

完了条件:

複数Dummy Clientが別プロセスから接続し、1ゲーム完走できる。

---

## Phase 3: AI Client Skeleton

実装:

- Network Client
- World State
- Memory
- Dummy Brain
- Reaction
- Chat Controller
- Vote Controller
- Ability Controller

完了条件:

RuleBased AI 9人でゲーム完走。

---

## Phase 4: Local LLM

実装:

- LLM backend interface
- llama.cpp / Ollamaのどちらか1つ
- Structured output
- 発言生成
- 投票
- 能力選択

完了条件:

1体のAI ClientをLLMで動かせる。

---

## Phase 5: 9 AI Agents

実装:

- 共有LLM Server
- 複数Agent
- Generation Queue
- 発言頻度調整
- 短文チャット

完了条件:

AI 9人で自動ゲーム完走。

---

## Phase 6: 議論品質

実装:

- Belief
- Suspicion
- Strategy
- Important event memory
- Reaction score
- 質問応答
- 反論
- 意見変更
- 人狼のライン切り
- CO判断
- 投票前再評価

完了条件:

単なるランダム発言ではなく、前の発言を受けた会話が発生する。

---

## Phase 7: UI

Web UI候補。

必要:

- チャット
- プレイヤー一覧
- 生死
- 残り時間
- 投票UI
- 能力UI
- 入力中表示
- ゲーム結果

AI観戦モードも用意したい。

---

## Phase 8: Role Expansion / MOD

- 第三陣営
- Passive
- Effect
- WinCondition拡張
- 追加役職
- MOD読み込み

---

# 31. 初期役職の仕様

## 市民

```text
Team: Village
Ability: なし
```

---

## 人狼

```text
Team: Wolf

知識:
- 他の人狼を知る

Ability:
- 夜に襲撃対象を決定
- 人狼専用チャット

Win:
- 村人陣営との人数条件を満たす
```

複数狼がいる場合の襲撃先決定方式は設定可能にする。

初期版では投票多数決でもよい。

---

## 占い師

```text
Team: Village

Night Ability:
生存者1名を占う

Result:
対象の陣営判定
```

「狂人をどう判定するか」などはルール設定化できると望ましい。

---

## 狩人

```text
Team: Village

Night Ability:
対象1名を護衛

初期ルール:
自分以外
```

連続護衛可否は設定化する。

---

## 狂人

```text
Team:
勝利判定上はWolf側

Knowledge:
狼の正体を知らない

Ability:
なし
```

TeamとInformation Sharingを分離すること。

つまり、

```text
team = wolf
knows_wolves = false
wolf_chat = false
```

を表現可能にする。

---

## 霊能者

```text
Team: Village

Passive/Ability:
処刑された人物の陣営結果を確認
```

結果通知タイミングは夜または翌朝。

設定で変更可能にするとよい。

---

# 32. 重要な抽象化

以下を混同しない。

## Role

プレイヤーに割り当てる役職。

## Team

勝利所属。

## Knowledge

開始時・途中で知っている情報。

## Ability

自発的に使う能力。

## Passive

イベントに応じて自動発動する効果。

## Effect

能力がゲーム状態へ与える最小処理。

## WinCondition

勝利判定。

## ChatPermission

参加可能なチャットチャネル。

これらを分離すると、

```text
「狼陣営だが狼を知らない」
「村陣営だが狼チャットが見える」
「死亡後だけ狼が見える」
```

などの特殊役職も表現しやすい。

---

# 33. AI向けServer State

サーバはAIへ「現在可能な操作」を具体的に返す。

AIにゲームルールを推測させない。

例:

```json
{
  "type": "player.action_state",
  "payload": {
    "phase": "night_action",
    "available_actions": [
      {
        "type": "ability",
        "ability_id": "inspect",
        "valid_targets": [
          "bob",
          "carol",
          "dave"
        ]
      }
    ]
  }
}
```

これにより存在しない相手への能力使用を減らす。

---

# 34. AI出力はStructured Outputを優先

LLMに最終操作を自由文だけで返させない。

例:

```json
{
  "action": "speak",
  "message": "Carolは昨日と意見変わってない？",
  "intent": "question",
  "target": "carol"
}
```

投票:

```json
{
  "action": "vote",
  "target": "carol",
  "reason": "占いCOへの反応と昨日の発言が不整合"
}
```

能力:

```json
{
  "action": "use_ability",
  "ability_id": "inspect",
  "target": "carol"
}
```

サーバ送信前にAI Client側でも検証する。

---

# 35. 発言長

リアルタイムチャットなので、長文生成を基本としない。

標準目安:

```text
短文: 5～30 tokens
通常: 15～60 tokens
長文: 必要な時のみ
```

AIごとの個性として差をつけてもよい。

---

# 36. 発言頻度

AIごとにtalkativenessを持つ。

ただし固定確率だけでなく、イベント重要度を考慮する。

同じAIが連投し続けないようクールダウンを設ける。

---

# 37. 避けるべき実装

以下は禁止または極力避ける。

## 37.1 巨大Gameクラス

すべてをGameクラスへ書かない。

---

## 37.2 役職if地獄

```python
if role == "seer":
elif role == "wolf":
elif role == "guard":
```

をゲームコアへ増殖させない。

---

## 37.3 AIに全ゲーム状態を渡す

秘密情報漏洩の原因になる。

---

## 37.4 全AIへ毎回LLM推論

1メッセージごとに9体すべてが長文推論する設計は禁止。

---

## 37.5 AIとサーバを同一責務にする

AIの誤判断でルールが変化してはならない。

---

## 37.6 役職名依存AI

AIは役職説明・能力説明から行動できるようにする。

---

# 38. 最初の成果物

最初の実装ではUIを急がない。

まずCLIログでよい。

最優先成果:

```text
Server起動
↓
9 Dummy AI接続
↓
役職配布
↓
昼
↓
チャット
↓
投票
↓
処刑
↓
夜
↓
能力
↓
死亡処理
↓
次の日
↓
勝敗決定
```

これが安定してからLLMを接続する。

---

# 39. Codexへの実装方針

作業開始時にまずこの仕様を読み、既存リポジトリがある場合は現在構造を調査すること。

その後、

1. 現状確認
2. アーキテクチャ案作成
3. 最小コア実装
4. 自動テスト
5. ネットワーク
6. Dummy AI
7. Local LLM
8. 議論改善

の順で進めること。

一度に全機能を実装しない。

各Phase終了時に、

- 実装内容
- テスト内容
- 未実装
- 次Phase
- 設計変更理由

をドキュメントへ残すこと。

---

# 40. 将来候補

初期スコープ外だが、設計上妨げないこと。

- 観戦者
- GM
- 部屋一覧
- パスワード部屋
- ランダムマッチ
- BOT補充
- 再接続
- AIモデル別レート
- Elo
- 戦績
- リプレイ
- AI思考可視化
- Tournament
- 複数LLM比較
- 音声会話
- Discord連携
- Web公開
- Steam等デスクトップ化

---

# 41. プロジェクトの成功条件

最終的に目指す状態:

```text
人間またはAIが
標準クライアントとしてサーバへ接続
↓
サーバから自分に必要な情報だけ受信
↓
リアルタイムチャットで自由に議論
↓
能力・投票を実行
↓
サーバがルールを厳密に処理
```

さらにAIについては、

```text
相手の発言を読む
↓
必要なら反応
↓
質問
↓
回答
↓
疑惑更新
↓
反論
↓
説得
↓
欺瞞
↓
投票
```

という継続した議論が成立すること。

---

# 42. 最終的な設計イメージ

```text
                         ┌─────────────────────┐
                         │   Werewolf Server   │
                         │                     │
                         │ Game Core           │
                         │ Event Bus           │
                         │ Rules / Roles       │
                         │ Action Resolver     │
                         │ Win Conditions      │
                         └──────────┬──────────┘
                                    │
                                 WebSocket
                                    │
          ┌─────────────────────────┼──────────────────────────┐
          │                         │                          │
          ▼                         ▼                          ▼
   ┌──────────────┐          ┌──────────────┐          ┌──────────────┐
   │ AI Client A  │          │ AI Client B  │          │ Human Client │
   │              │          │              │          │              │
   │ Memory       │          │ Memory       │          │ UI           │
   │ Belief       │          │ Belief       │          │ Input        │
   │ Strategy     │          │ Strategy     │          │              │
   │ Reaction     │          │ Reaction     │          │              │
   │ Brain        │          │ Brain        │          │              │
   └──────┬───────┘          └──────┬───────┘          └──────────────┘
          │                         │
          └──────────────┬──────────┘
                         ▼
                 ┌──────────────┐
                 │  LLM Server  │
                 │              │
                 │ Qwen / Gemma │
                 │ llama.cpp    │
                 │ Ollama       │
                 └──────────────┘
```

---

# 43. Codexへの最終指示

この仕様書の中で最も重要なのは以下である。

1. **サーバとAIを分離する**
2. **サーバを唯一の真実とする**
3. **リアルタイム自由チャットを前提とする**
4. **AIは必要な時だけ発言する**
5. **役職をハードコードしない**
6. **Role / Team / Ability / Passive / Effect / WinConditionを分離する**
7. **AI側も役職名へ依存しない**
8. **LLMへ全履歴や秘密情報を雑に渡さない**
9. **3070 Ti 8GBでも動かせる構成を維持する**
10. **最初にゲームコアを完成させ、その後AIを載せる**
11. **テスト可能性を優先する**
12. **将来の人間参加、分散実行、MOD追加を阻害しない**

実装上の細部で迷った場合は、

> 「この選択は後から役職、AIモデル、クライアント、ゲームモードを追加しやすいか？」

を判断基準とする。

このプロジェクトは「9人のAIを一度動かして終わり」ではなく、

**汎用的なリアルタイム人狼ゲーム基盤の上でAIエージェントをプレイヤーとして動かすこと**

を最終目標とする。
