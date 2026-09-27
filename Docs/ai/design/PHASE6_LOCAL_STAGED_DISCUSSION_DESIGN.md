# Phase 6 ローカル段階発話設計（L1〜L3）

Status: SUPERSEDED — PHASE6_REWRITE_BASIC_DESIGN.md へ吸収（2026-09-27）。§4.1・§5.2・§5.3は同文書から参照して再利用する
作成: 2026-09-27
作成者: Claude Code
根拠: `Docs/ai/PHASE6_LOCAL_VS_API_ASSESSMENT_20260927.md`

## §1 目的と範囲

Qwen3.5-9B（製品モデル不変）のまま、次の3つの残存失敗を**モデルの判断力ではなく構造で**減らす。

| 失敗 | 現状（T506新baseline、96出力） | 本設計の手段 |
|---|---:|---|
| 秘密開示 | 14 | L1: 発話段に秘密を渡さない |
| 質問への応答 | 29/54 | L2: 宛先をプロトコルに載せる |
| 捏造・act/text不一致 | 捏造4、不一致78 | L3: 意図と根拠を前段で決め、発話段は本文だけを書く |

含まない: モデル変更、fine-tune、API利用、ルール変更、role構成変更、Logical Clock、実game、Phase 7。
信念更新（assessment/claim/relation/strategy）の生成は§6の第2段階で扱い、第1段階では空で出す。

## §2 現状の構造（変更前）

1回のprovider callが、本文と12項目のdiscussion proposalを同時に生成する。

- 入力: `ai_client/llm/prompt.py` の `project_brain_input` が作る canonical input。
  `context` に `role_id`・`team`・`inspect_result`・`medium_result`・`attack_result`・`knows_teammates`・
  `authorized_known_player_ids`、`grounding.ability_results` に能力結果、`memory` に私的channelの発言を含む。
- 出力: `decision`（kind/option_id/message）と `discussion`（schema_version、base_revision、decision_kind、
  option_id、speech_act、reaction、assessment_updates、claim_updates、relation_updates、strategy_update、
  co_judgment、pre_vote_reassessment）。
- 更新4項目は全てschema上空配列またはnullを許す（`assessment_updates`等は `maxItems` のみ、`strategy_update` はnull可）。
- test-onlyのGB1（`scripts/phase6_grounding_basis_probe.py`）は選択段と出力段に分けたが、
  両段に全入力を渡し、出力段で旧来の完全出力を生成する。事実候補集合（`fact_catalog`）に
  チャット発言は含まれない。

プロトコル（`protocol/aiwolf-v1.1.schema.json`）の `chat.send` は `channel_id` と `message` だけを持つ。
サーバは `server/network/session.py` → `GameState.submit_chat` → `PlayerInteractions.submit_chat`
（`server/aiwolf_core/interactions.py`）で `{player_id, display_name, message}` を作って配信する。
クライアントは `ai_client/world/reducer.py` で `ChatRecord`（`ai_client/world/model.py`）を作る。
反応機会は任意の他者発言から生じ（`ai_client/reaction_chat/controller.py` の `_discussion_trigger_for`）、
宛先の概念は無い。

## §3 目標構造

1回の発話判断を、計画段（T）と発話段（P）の2 callに分け、コードが組み立てる。

```text
canonical input ─┬─> 計画段T（全入力＋候補集合）─> 小さな計画JSON
                 │                                    │
                 └─> 公開入力の組み立て（コード）<─────┘ 計画を解決して渡す
                                    │
                                    v
                    発話段P（公開入力＋解決済み計画）─> {"message": "..."}
                                    │
                                    v
                    組み立て（コード）─> 旧decision＋旧discussion proposal ─> 既存validator
```

既存validatorと既存評価器をそのまま通すため、最終出力は旧来の形に組み立てる。

## §4 計画段T

### §4.1 入力

現行canonical inputに、次の2つの候補集合を加える。値を複製せず、GB1と同じくcanonical input内の
pointerで参照する。

**返答候補集合 `reply_catalog`**（新規）: 受信済み `ChatRecord` から決定的に作る。

| field | 内容 |
|---|---|
| `id` | `r000`〜 |
| `pointer` | canonical input内の当該記録へのpointer |
| `player_id` | 発言者 |
| `addressed_to_me` | §7の構造化宛先が自分なら true。本文からは推定しない |
| `is_trigger` | 今回の反応機会の起点なら true |

対象は、今回の起点の発言と、同じ日・同じchannelの直近の他者発言最大6件。

**事実候補集合**: GB1の `fact_catalog` を公開と私的に分ける。

| 集合 | 内容 |
|---|---|
| `public_fact_catalog` | 日・phase、生死、公開死因、投票候補、他者と自分の公開CO宣言・報告 |
| `private_fact_catalog` | 自分の能力結果、仲間の身元（`knows_teammates` の場合） |

### §4.2 出力schema

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["intent", "reaction_reason", "reply_to", "addressee_player_id",
               "subject_player_id", "stance", "public_fact_ids", "disclose_fact_ids"],
  "properties": {
    "intent": {"enum": ["ANSWER", "QUESTION", "REBUTTAL", "CLAIM", "OPINION_CHANGE", "NONE"]},
    "reaction_reason": {"enum": ["DIRECT_QUESTION", "DIRECT_MENTION", "CLAIM_CONFLICT",
                                 "VOTE_PRESSURE", "NEW_INFORMATION", "OTHER_AUTHORIZED"]},
    "reply_to": {"enum": ["<reply_catalogのid>", null]},
    "addressee_player_id": {"enum": ["<現在の他playerのid>", null]},
    "subject_player_id": {"enum": ["<現在のplayerのid>", null]},
    "stance": {"enum": ["SUSPECT", "TRUST", "NEUTRAL"]},
    "public_fact_ids": {"type": "array", "maxItems": 2, "uniqueItems": true,
                        "items": {"enum": ["<public_fact_catalogのid>"]}},
    "disclose_fact_ids": {"type": "array", "maxItems": 1, "uniqueItems": true,
                          "items": {"enum": ["<private_fact_catalogのid>"]}}
  }
}
```

enumは各requestの候補集合から生成する。**参照は全て候補集合からの選択であり、LLMが参照を生成しない。**
GB1で捏造が0だった理由をそのまま全参照へ広げる。`RELATION_HYPOTHESIS` は第1段階では出さない。

### §4.3 検査

- schema適合、enum所属、`intent=ANSWER/REBUTTAL` なら `reply_to` 必須、`QUESTION` なら `addressee_player_id` 必須。
- `disclose_fact_ids` を選べるのは公開channelへの発話だけ。私的channelでは空。
- 不適合は既存の有限K（最大3）で再生成し、使い切れば `intent=NONE` の沈黙として扱う。

### §4.4 予算

`max_tokens` は64を上限候補とし、実装前にT507と同じ方法（native tokenizer、compactとpretty両方）で
全32 caseの最大出力tokenを測って確定する。計画JSONに本文は含めない。

## §5 発話段P

### §5.1 入力の原則

**Pへ渡す情報は、発話先channelの受信者全員が知ってよい情報と、Tが明示的に開示を選んだ事実に限る。**
禁止リストで秘密を消すのではなく、許可リストで入力を組み立てる。設計不変条件7
（各clientには知ってよい情報だけを送り、promptで秘密を隠させない）をclient内部の段の間にも適用する。

### §5.2 公開channelへの発話の許可リスト

| 項目 | 出典 |
|---|---|
| 自分のplayer_idと表示名、現在のplayerの表示名 | `context.player_id`、`grounding.current.players` |
| 日・phase・生死・公開死因 | `grounding.current` |
| 自分の公開CO宣言・報告 | `grounding.self_co` |
| 直近の公開channelの発言 最大6件 | 履歴を公開channelで絞る |
| 解決済みの計画 | intent、stance、`reply_to` の発言全文、宛先、主題player |
| 解決済みの事実 | `public_fact_ids` と `disclose_fact_ids` の値 |

`context.role_id`・`team`・`count_as`・`inspect_result`・`medium_result`・`attack_result`・`knows_teammates`・
`authorized_known_player_ids`・`abilities`・`win_conditions`、開示されていない `grounding.ability_results`、
私的channelの発言は**渡さない**。

人狼が占い師を騙る場合、騙りは既存のCO system操作（D007の `co.declare` / `co.report`）で公開記録になる。
Pはそれを自分の公開COとして受け取り、真偽を知らないまま話す。騙るかどうかの判断はTがLLMとして行い、
ルールで固定しない（T396 packetの方針を維持）。

### §5.3 私的channelへの発話

人狼チャットのような私的channelでは、受信者全員が知る情報としてそのchannelの発言と仲間の身元を加える。
それ以外の私的情報（例: 狂人の役職を人狼チャットへ）は§5.2と同様に渡さない。

### §5.4 出力と検査

- 出力schemaは `{"message": string}` の1項目だけ。形式制約による推論劣化（Tam et al.）を最小にする。
- 検査は既存の `ai_client/llm/decision.py` の `_validate_generated_text`（TEXT_BOUND）と、
  `ai_client/brain/controller.py` の自己反復・長文peerコピーのguardをそのまま使う。
- 不適合は有限K（最大3）で再生成し、使い切れば沈黙として扱う。
- `max_tokens` は160を上限候補とし、§4.4と同じ方法で確定する。

### §5.5 system指示

現行のsystem指示（`ai_client/llm/prompt.py` の `_SYSTEM_MESSAGE` と `_DISCUSSION_INSTRUCTION`、計206語）はPへ渡さない。Pには次の3点だけを与える。
計画の意図に従うこと、`reply_to` があればその発言に答えること、1〜2文の短い英語で書くこと。
具体的な文言は実装時に決め、例文は入れない（T406の例文模倣を避ける）。

## §6 組み立てと信念更新

コードがTの計画とPの本文から旧出力を組み立てる。

| 旧field | 組み立て方 |
|---|---|
| `decision` | `{kind: chat, option_id, message: Pの本文}`。`intent=NONE` かつ沈黙なら `{kind: none}` |
| `speech_act` | `intent` と、候補集合から解決した `in_reply_to`・`addressee_player_id`・`evidence` で作る |
| `reaction` | `trigger` は既存の定数、`reason` はTの `reaction_reason` |
| 更新4項目 | 第1段階は空配列とnull |

**第2段階（別測定）**: GREE 2026の非同期内省と同じく、発話とは別のcallで信念更新を行う。
発話の頻度と独立に、日やphaseの区切りでだけ走らせる。第1段階の結果を見てから設計する。

## §7 プロトコル v1.2: 宛先フィールド（L2）

| 対象 | 変更 |
|---|---|
| schema | `chat.send` payloadに任意の `addressee_player_id`（string）を追加し、`urn:aiwolf:protocol:1.2` とする |
| core検査 | `PlayerInteractions.submit_chat` で、宛先が自分以外・生存・当該channelの受信者であることを検査し、違反は `invalid_addressee` で拒否する。network層は判定しない（Review checklist） |
| 配信 | chat messageの辞書に `addressee_player_id` を加える。値が無いときはkeyを出さず、v1.1と同じbytesを保つ |
| client | `ChatRecord` に `addressee_player_id: str | None = None`、reducerで型検査して格納、`send_chat` に任意引数 |
| 反応機会 | 自分宛ての他者発言を受けたら、既存の発言上限の範囲で優先して反応機会を作る |

人狼知能大会の `>>Agent[01]` アンカーと同じ役割で、「向けられた発話には応答が期待される」を構造で保証する。
Tは `reply_catalog.addressed_to_me` で自分宛ての質問を本文推定なしに知る。

版の扱い: 1.1のclientは宛先を送らず、宛先の無いメッセージは1.1と同じ形になる。
AI clientは本リポジトリ内で同時に更新する。人間clientのUIは Phase 7 の範囲とする。

## §8 検証計画

### §8.1 段取り

一度に一つずつ効果を分けるため、2段階で測る。

| 段 | 内容 | 製品変更 |
|---|---|---|
| A | L1＋L3 をtest-only probeで測る。宛先は無し | 無し |
| B | L2を実装し、宛先付きfixtureでAとの差を測る | プロトコルv1.2 |

L1とL3はPの入力を分けること自体がL1なので、同時に入れる。

### §8.2 offline試験（生成なし）

1. **漏洩不可能性**: 32 case全てで、公開channel向けPの入力に§5.2の禁止項目のfieldが構造上存在しないこと。
   人狼・狂人のcaseで、Pの入力の非チャット部分に真の `role_id` とteam値が現れないこと。
2. 候補集合の決定性: 同じ入力から同じ候補集合とidが出ること。pointerが全て解決できること。
3. 組み立て: Tの計画とPの本文から組み立てた旧出力が、既存validatorを通ること。
4. native tokenizerでT・Pの最大出力と最大入力を測り、§4.4・§5.4の予算を確定すること。
5. v1.2: 宛先の検査（自分・死者・非受信者・未知idの拒否）、宛先無しの配信bytesがv1.1と一致すること。

### §8.3 実測と判定

- 既存の `scripts/phase6_recovery_runner.py` の paired 3-seed（seed `4242027/4242028/4242029`、32 case、96出力）。
- 比較対象は T506の新baselineとGB1。旧結果は再生成・再採点しない。
- 独立意味評価はT508と同じく独立Reviewerが行う。Main評価で代用しない。

事前登録する指標:

| 区分 | 指標 | 基準 |
|---|---|---|
| 主 | 固定18問×3 seedの質問回答 | 新baseline 29/54 に対し非劣性、段Bでは優越を目標 |
| 安全 | 秘密開示 | 1/96以下（構造上0が期待値） |
| 安全 | 捏造 | 0/96（構造上0が期待値） |
| 安全 | 状態矛盾・HARD失敗 | GB1（状態2、HARD失敗10）以下 |
| 副 | SEMANTIC、STYLE、意図と本文の一致 | 非劣性 |

判定規則はT506のbootstrapを使い、**下限だけでなく上限も計算して「劣性」を判定できるようにする**。
T506は下限だけを計算したため、質問回答の −24ポイントもINCONCLUSIVEになった。

### §8.4 段階1の停止条件

段Aと段Bの後、次のどれかなら段階1を打ち切り、`PHASE6_LOCAL_VS_API_ASSESSMENT_20260927.md` の
診断（上位モデルでの32件1回）を経て段階2を判断する。

- 質問回答が新baselineに対し劣性と判定される。
- 秘密開示または捏造が、構造上の期待値（0）から外れ、原因が構造でなくPの生成にある。
- T＋Pの1判断あたりREAL時間のp95が、現行1 callのp95（5.95秒、T506 baseline）の2倍を超える。

## §9 時間とcallの見積り

| 構成 | 1判断のcall | 出力token上限 | 1 callのp50（実測） |
|---|---:|---:|---:|
| 現行 | 1 | 512 | 5.45秒（T506 baseline） |
| GB1 | 2 | 32＋480 | 1.92秒（選択段と出力段が混在した全callの値） |
| 本設計 | 2 | 64＋160（予定） | 未測定 |

出力上限の合計は512から224へ下がり、schemaも小さくなる。T＋Pの合計は現行1 callと同程度になる
可能性があるが、GB1の値は段別の実測ではないため根拠にしない。§8で段別に測る。
9 agentが同時1 slotを共有する実gameでは、call数の増加で待ち行列が伸びる。
実gameでの確認は§8の合格後に、許可された短gameで行う。

## §10 作業分割

| 順 | 作業 | 責務 | gate |
|---|---|---|---|
| 1 | 本設計の独立review | Reviewer | D051 |
| 2 | test-only probe（候補集合、T/P投影、組み立て） | Implementer | 1のAPPROVED |
| 3 | §8.2の1〜4のoffline試験 | Main | 2の完了 |
| 4 | 段Aの実測 | Tester | 実行許可 |
| 5 | 段Aの独立意味評価 | Reviewer | 4の完了 |
| 6 | プロトコルv1.2の詳細設計 | Architect | 5の結果と独立review |
| 7 | v1.2実装（core・client・反応機会）と§8.2の5 | Implementer | 6のAPPROVED |
| 8 | 段Bの実測と独立評価 | Tester・Reviewer | 実行許可 |

6以降はプロトコル・component境界の変更なので、D075によりArchitectと独立reviewを要する。

## §11 リスク

- 更新4項目を空にすると、多turnで信念の蓄積が失われる。第1段階の32 caseは単発判断なので影響が見えない。
  実gameの前に§6の第2段階が要る。
- Pが真の役職を知らないため、人狼が仲間をかばう等の戦略的な発話はTの `subject_player_id` と `stance`
  が表す範囲に限られる。表せない戦略は第1段階では失われる。
- 1 slotのGPUでcallが2倍になるため、実gameでは待ち行列が伸びる。
- 宛先の優先反応が、発言上限の中で他の反応機会を押し出す可能性がある。
- 本設計はcase単位の人工suiteで判定する。9人の実gameでの自然さは別途確認が要る。

## §12 ユーザー判断が必要な事項

1. プロトコルv1.2で宛先フィールドを加えること（§7）。
2. 自分宛て発言への優先反応という製品挙動の変更（§7）。
3. 段A・段Bのprovider実測の実行許可（各96判断、最大でT/P各K=3）。
4. §8.4の停止条件の数値。
