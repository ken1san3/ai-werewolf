# Phase 6 会話品質: 強制層の不在に関する調査記録

Status: FINDINGS（未承認・参考情報）
作成: 2026-09-18（2026-09-19にT424–T428モデル比較の実測でF1/F2/F3を訂正）
作成者: Claude Code（調査のみ。製品変更0、テスト以外の実行0、provider操作0、実game0、新規生成0）

2026-09-19追記: `PHASE6_MODEL_COMPARISON_20260918.md`（5モデル160生成）の実測により、
本記録のF1表現・F2解釈・F3数値を訂正した。各節の「訂正」小節を参照する。
F2の能力境界の解釈は取り下げ、置き換えの仮説を同節に記載する。

## 調査範囲

1. 保存済み証拠 `Docs/ai/handoffs/tasks/T408_SAFE_SUITE_RESULTS.json`（32 row）の全件集計
2. 現HEAD `0e047f3` の製品コード（`ai_client/llm/`、`ai_client/brain/`、`ai_client/discussion/`）
3. 既存fixture `tests/fixtures/phase6_conversation_cases.py` を既存projectionへ通したローカル投影

3はプロンプトを構築するだけの同期計算であり、providerへは接続していない。
新規生成0、retry0、実game0。CURRENT_STATEのhold（通常9人smoke/長時間Master Run禁止、
追加runなし）に抵触する操作は行っていない。

本記録は未承認の参考情報である。採否・修正範囲・独立検証の要否はReviewerとユーザーが決定する。

## 測定方法

投影は既存公開APIだけを使い、`project_brain_input(case.request, config=LLMBrainConfig(short_chat=DiscussionChatConfig()))`
を32 caseへ適用した（`scripts/phase6_conversation_suite.py:81` と同じ呼び方）。
proxy内訳は `ai_client.discussion.projection.token_proxy_units` を
system / user / `decision_schema` の各文字列へ個別に適用して求めた。
実token数はT408保存rowの `prompt_tokens_actual` を使用し、再計数していない。

---

## F1（最重要）本文を検査する層が製品に存在しない

`ai_client/llm/types.py:693` の `DecisionValidationCode` は次で全部である。

```
JSON_SYNTAX / JSON_DUPLICATE_KEY / SCHEMA / OPTION_NOT_OFFERED / VALUE_NOT_OFFERED / TEXT_BOUND
```

すべて構造に関するものであり、`TEXT_BOUND` も長さの上限だけである。
**生成された本文の内容を検査するコードは1つもない。**

本文に効くガードは `ai_client/brain/controller.py:133` の `_invalid_or_repeated_self_text` と
`ai_client/brain/controller.py:150` の `_cross_player_public_copy` にある3条件だけである。

| ガード | 条件 |
|---|---|
| 空文字 | 拒否 |
| 自分の直前発言と同一 | 正規化後の完全一致のみ |
| 他者の公開発言の全文コピー | 50 code points以上かつ8 words以上の完全一致のみ |

T408で残った失敗と対照すると、強制層の穴と失敗の分布が一致する。

| 残存失敗 | 件数 | 構造で防げているか |
|---|---:|---|
| 秘密の陣営・役職の自白 | 5 | 防げない。該当ガードなし |
| 死亡playerへの投票提案 | 1 | 防げない。構造fieldはenumで守られ、本文だけ野放し |
| peer長文コピー | 2 | 完全一致のみ。1語の差異で通過する |
| 毎回の自己紹介 | 15/32 | 防げない。該当ガードなし |
| speech_actと本文の不一致 | 6 | offline screenでは検出。製品コードには無い |
| 中途切断 | — | 長さ上限のみ |

構造で表現できるルールは徹底して守られている一方、自由文だけが無防備であり、
残存失敗はすべてそこに集まっている。

### 訂正（2026-09-19 追記）

**「本文の内容を検査するコードは1つもない」は広すぎる表現であり、訂正する。**

`ai_client/llm/decision.py` の `_validate_generated_text` は、190 code points
または570 UTF-8 bytes付近の非終止文を `TEXT_BOUND` へ送る検査を持つ。
T427測定でこの経路は実際に発火しており、構造rejectの内訳は
`qw9` 3件、`gm12` 1件、`tb27` 2件である。検出経路は存在する。

取り下げるのは上記の全称表現だけであり、**自由文の意味検査が無いという主要指摘は維持する。**
`PHASE6_MODEL_COMPARISON_20260918.md` も「自由文の意味検査不足という主要指摘は支持する」と
独立に判定している。本節のガード表と失敗対照表のうち、`TEXT_BOUND` が関与するのは
「中途切断」行だけであり、秘密自白・死者投票・自己紹介・act不一致に検査層が無い点は変わらない。

## F2 能力境界がカテゴリ単位で分離している

T408保存rowをcategoryで集計した（各category n=2）。

**semantic 2/2 PASS の2 category**

| category | 課題の性質 |
|---|---|
| `vote_candidates` | 与えられた候補列挙から選ぶ |
| `legitimate_disclosure` | 自分が保持する事実をそのまま述べる |

**semantic 0/2 の category**

`accusation_rebuttal` / `direct_question` / `claim_continuity` / `opinion_change` /
`cross_player_copy` / `intro_repetition` / `alive_dead` / `insufficient_information` /
`madman_secrecy` / `werewolf_secrecy` / `medium_grounding` / `seer_grounding`

境界は「列挙からの選択」「自己状態の記述」と、「相手の発言への噛み合わせ」「秘密の保持」の間にある。

裏付けとして、相手の発言を前提とする3 actの出現数を示す。

| speech_act | T406 baseline | T408 structure |
|---|---:|---:|
| QUESTION | 0 / 32 | 0 / 32 |
| REBUTTAL | 0 / 32 | 0 / 32 |
| OPINION_CHANGE | 0 / 32 | 0 / 32 |

提示方法2通り・計64生成で1件も出ていない。semantic PASSは全体で7/32。

### 訂正（2026-09-19 追記）

**生成数「64」は誤りであり、60が正しい。** T408 structure variantの32 rowのうち4 rowは
同一bytesのbaseline再利用であり、distinct generationは60である。
T408 handoffが「新規28件/再利用4件」と明記しており、本記録の集計時の誤りである。

**能力境界の解釈は取り下げる。** 本節は境界を「相手の発言への噛み合わせができない」と読んだが、
T424–T428の5モデル160生成はこの読みを支持しない。

| モデル | act/text不一致 | SEMANTIC | 非NONE | 内容回答/18 |
|---|---:|---:|---:|---:|
| Qwen3.5-9B | 23 | 7/32 | 0 | 9 |
| Llama3.1-8B | 23 | 7/32 | 0 | 5 |
| Gemma3-12B | 24 | 6/32 | 0 | 11 |
| Bonsai2-27B | 24 | 6/32 | 0 | **13** |
| Qwen3.6-35B-A3B | 27 | 4/32 | 0 | 8 |

Bonsai2-27Bは共通18質問のうち13件に**内容としては回答している**。
本文は相手の発言へ噛み合っており、`speech_act.kind` だけがNONEへ落ちている。
したがって失敗は会話能力の不足ではなく、本文とラベルの結合の失敗である。
5ファミリー・8B〜35B・3種の量子化・2種のruntimeを跨いで非NONEが160件中0という
モデル非依存性も、能力分布ではなく構造的性質を示す。

### 置き換え仮説（未測定）

取り下げた解釈の代わりに、次の仮説を記録する。**静的確認のみで、生成による検証はしていない。**

投影したschemaの `speech_act.oneOf` は7枝で、必須fieldは次のとおりである。

| 枝 | kind | required数 | kind以外の必須field |
|---:|---|---:|---|
| 0 | NONE | 1 | （なし） |
| 1 | CLAIM | 5 | subject_player_id, topic, stance, evidence |
| 2 | QUESTION | 5 | addressee_player_id, subject_player_id, topic, source |
| 3 | ANSWER | 7 | addressee_player_id, in_reply_to, source_interpretation, topic, stance, evidence |
| 4 | REBUTTAL | 7 | addressee_player_id, in_reply_to, source_interpretation, topic, stance, evidence |
| 5 | OPINION_CHANGE | 6 | subject_player_id, dimension, prior, current, causes |
| 6 | RELATION_HYPOTHESIS | 6 | source_player_id, target_player_id, relation, confidence, evidence |

**NONEは先頭枝であり、かつ `kind` 以外に何も要求しない唯一の枝である。**
他6枝は4〜6個の追加fieldを要求し、その多くは接地が必要な相互参照である。
生成は `ai_client/llm/backend.py:293` の `response_format.json_schema` / `strict: True` による
制約付きdecodeで行われ、`kind` を出した時点で枝が確定する。
さらに `_DISCUSSION_INSTRUCTION` は接地できない参照を書かないよう明示的に指示しており、
ANSWER/REBUTTALが要求する `in_reply_to` / `source_interpretation` / `evidence` と方向が一致する。

この仮説は、モデル非依存性、本文だけが正しくラベルが落ちること、
T406の別candidateでは非NONEが生成できたことを同時に説明する。

検証は `PHASE6_MODEL_COMPARISON_20260918.md` の次手順2の対照へ、次の条件を足せば足りる。
NONEへ必須の理由fieldを1つ与えて無償の枝でなくする、actが明確に必要なcaseでNONEを枝から外す、
枝順を入れ替える。いずれも既存suite内で完結し、「actを出せない」と「構造的costを払わない」を分離する。

## F3 token proxy予算が誤較正されている（品質の原因ではない）

投影実測とT408保存rowの対照。

| 指標 | 値 |
|---|---|
| 実prompt token 最大 | 2,939 / context 8,192 = 36% |
| `token_proxy_units` | 7,002〜7,949 / 上限 8,192 |
| proxy と実tokenの比 | 約2.8倍の過大評価 |

proxy内訳（G01-1）は `total=7792 / system=795 / user=2181 / schema=4211` であり、
**schemaがproxy予算の54%を占める。**
一方 `PHASE6_ACTUAL_CONTEXT_BUDGET_DESIGN.md` §1 のT406実測は、現buildでは
structured-output schemaがrendered promptのtoken列に入らないことを確認している。
実tokenを消費しない値が、履歴を残すか捨てるかを決める予算の過半を占めている。

上限は `ai_client/discussion/projection.py:746` の `min(8192, config.max_token_proxy_units)`
でハードコードされており、設定から引き上げられない。
この天井だけをプロセス内で外して再投影すると、履歴の欠落が解消する。

| 条件 | included | omitted | max proxy |
|---|---:|---:|---:|
| proxy上限8192（現行製品） | 39 | 12 | 7,949 |
| 天井をプロセス内で解除 | 51 | 0 | 8,185 |

**ただし、これを品質失敗の原因として扱ってはならない。**
履歴が欠落したcaseと欠落しなかったcaseを対照した結果は次のとおりである。

| 群 | n | INTRO_OPENING | semantic PASS |
|---|---:|---:|---:|
| 履歴が欠落 | 12 | 4 (33%) | 3 (25%) |
| 履歴が完全 | 20 | 11 (55%) | 4 (20%) |

欠落した側が悪化していない。本調査は当初これを主因と仮説したが、対照の結果その仮説は棄却された。
proxy誤較正は独立した実装欠陥として扱い、修正による品質改善を見込まない。

### 訂正（2026-09-19 追記）

**「約2.8倍」は旧条件の参考値であり、現条件の実測値ではない。**
T427が固定条件で測ったproxy/native比は、Gemma3-12Bが3.81〜4.25、
他4モデルが4.18〜4.82である。本記録の2.8倍はT408保存rowから求めた旧条件の値であり、
モデルのtokenizerごとに異なるため単一の定数として扱えない。

**「実装欠陥」という断定も訂正する。** proxyは安全側へ倒した決定的heuristicであり、
`PHASE6_MODEL_COMPARISON_20260918.md` は「安全側proxy自体を即バグとはせず、
過剰切捨ての契約/影響を別に修正設計する」と判定している。
本記録が指摘すべきは、proxyとnative tokenの単位差とschema寄与が混在している点、
および過剰切捨ての契約が明示されていない点であり、proxyの存在自体ではない。

**context不足でないという判断は維持される。** T427の実測は全160生成で
最大input 2062 token、最大生成360 tokenであり、
「このsuiteで8192不足を示す証拠はない」と独立に結論づけられている。

## F4 打ち手が最も強制力の弱い手段に集中している

`ai_client/llm/prompt.py` の `_SYSTEM_MESSAGE` + `_DISCUSSION_INSTRUCTION` は
合計1,703文字・206語に42個の制約節を含む単一段落である。
`_DISCUSSION_INSTRUCTION` は `4b1e922` で1,576 bytesとして導入され、現HEADまで同一bytesである。

この指示文が要求している秘密保護・自己紹介回避・会話継続・コピー禁止は、
いずれもT408で破られた項目と一致する。要求は記述されており、遵守されていない。

対象モデルは `PHASE6_MASTER_TEST_PLAN.md` のcanonicalどおり9B級の4bit量子化modelを
9 clientで共有する構成である。42制約の同時自己遵守をmodel側に期待する設計になっており、
T396からT408までの反復は、この手段の有効性を支持する結果を出していない。

---

## 検討すべき方向

本リポジトリは「役職追加にPythonの変更が必要になったら設計の失敗」「AIの誤判断でルールが
変わることはない」を基準とし、構造化できる規則をコードで強制している。
その原則が自由文にだけ適用されていない。

1. 秘密開示ガードをコード側に置く。clientは自分のrole/teamを保持しているため、
   公開channelへ出る本文の機械的検査は既存copy guardと同じ形で実装できる。
2. 死者・不正対象への言及を拒否する。権威あるalive集合はclientが保持している。
3. `speech_act` と本文の整合を製品の検証コードへ昇格する。
   offline screenが `QUESTION_LABEL_MISMATCH` を6件検出できている。
4. 上記を `OUTPUT_INVALID` から分離する（`PHASE6_CODE_REVIEW_20260917.md` F3と同じ論点）。
   現在このcounterを見て修正対象を決めているため、混入は判断を歪める。
5. proxy/actual予算は品質改善ではなく実装欠陥として修正する。

コード強制の導入後もQUESTION/REBUTTAL/OPINION_CHANGEが0のままであれば、
それはmodel側の能力天井を示す。人工suiteを別modelで1回流す測定は実gameも製品変更も伴わず、
この変数だけを切り分けられる。

### 実施済み（2026-09-19 追記）

上記のmodel切り分けはT424–T428として5モデル160生成で実施され、
`PHASE6_MODEL_COMPARISON_20260918.md` に結果がある。結論は次のとおり。

- **model交換だけではPhase 6を通せない。** 5モデル全てで非NONEは0。
- ただし空振りではなく、model依存と非依存の失敗が分離された。
  秘密自白（qw9 5 / qw35 2 / 他0）、コピー（ll8 18 / gm12・qw35 1）、
  自己紹介反復（qw9 11 / gm12 2）、STYLEはmodel依存であり、交換で改善する。
  act/text不一致（23〜27）とSEMANTIC（4〜7/32）はmodel非依存である。
- したがって上記の「0のままなら能力天井」という判定基準は成立しない。
  非NONEが0のままでも、本文は噛み合っている（Bonsai 13/18）。
  能力天井ではなく、本文とラベルの結合の問題として扱う。

本節の推奨1〜5のうち、1〜4（コード側の強制）は依然有効である。
5はF3訂正のとおり「実装欠陥」ではなく単位差と切捨て契約の問題として扱う。
順序は `PHASE6_MODEL_COMPARISON_20260918.md` の「Phase6の次の最小作業順」に従う。

## 限界

- T408は各category n=2、1 caseあたり1生成である。T408自身が統計的優劣を主張しない旨を
  明記しており、本記録も同じ制約に従う。件数比較を有意差として扱わない。
- 強い主張をしているのはF1（自由文の意味を検査する層が無い）と
  F2の「60 distinct generationで非NONEが0」の2点であり、
  いずれも標本比較ではなくコードの全定義と保存rowの全件集計から確定している。
  2026-09-19訂正: F1の全称表現は`TEXT_BOUND`検査の存在により限定した。
  F2の生成数は64ではなく60である。非NONE 0はT424–T428の160生成でも再現した。
- F2の能力境界の解釈は2026-09-19に取り下げた。置き換え仮説（NONEが唯一の無償枝である）は
  schemaとbackendの静的確認のみで、生成による検証をしていない。
- F3の実token値はT408保存rowの再利用であり、本調査でtokenizerを実行していない。
- private生成本文は参照しておらず、本記録にも含まれない。
- 本調査はpytest・実game・provider操作・製品変更を行っていない。
