# Phase 6 会話品質: 強制層の不在に関する調査記録

Status: FINDINGS（未承認・参考情報）
作成: 2026-09-18
作成者: Claude Code（調査のみ。製品変更0、テスト以外の実行0、provider操作0、実game0、新規生成0）

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

## 限界

- T408は各category n=2、1 caseあたり1生成である。T408自身が統計的優劣を主張しない旨を
  明記しており、本記録も同じ制約に従う。件数比較を有意差として扱わない。
- 強い主張をしているのはF1（検証コードに内容検査が存在しない）と
  F2の「64生成でQUESTION/REBUTTAL/OPINION_CHANGEが0」の2点であり、
  いずれも標本比較ではなくコードの全定義と保存rowの全件集計から確定している。
- F3の実token値はT408保存rowの再利用であり、本調査でtokenizerを実行していない。
- private生成本文は参照しておらず、本記録にも含まれない。
- 本調査はpytest・実game・provider操作・製品変更を行っていない。
