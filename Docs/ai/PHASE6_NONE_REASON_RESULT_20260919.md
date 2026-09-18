# Phase 6 C2・C1単独実験の結果

**C2、C1とも不採用。** 設計・実験toolは独立承認を受けたが、会話品質の採用条件を満たさなかった。製品は変更していない。

## 範囲と手順

Reviewer提案のC2（NONEの理由必須化）だけを人工32caseで測定した。製品schema、validator、prompt、fixture、モデル、sampling、context、Logical Clockは変更していない。T431設計・T432 toolを独立承認後、T433が一回だけ測定した。T427 qw9 baseline/T428判定は再利用し、再生成・再採点していない。

`NO_NEW_INFORMATION` / `WAITING_FOR_OTHERS` / `DELIBERATE_SILENCE` に、合法な独立発言を残す `INDEPENDENT_STATEMENT` を加えた閉enumを実験schemaのNONE枝にだけ追加した。候補全schemaを検査してからcopy上のNONE.reasonだけを除去し、既存validatorでも検査した。原文、候補schema適合、adapter後の旧契約、独立意味評価を分離している。

## 測定

| 指標 | 旧qw9 baseline | C2 |
|---|---:|---:|
| 新規生成 | 再利用（32件） | 32件 |
| NONE | 32 | 32 |
| 旧構造契約 PASS / FAIL | 29 / 3 | 29 / 3 |
| 候補schema PASS | 対象外 | 32 / 32 |
| 入力tokens合計 | 55,068 | 55,068 |
| 出力tokens合計 | 7,110 | 7,500 |
| REAL所要秒 | 237.322 | 249.044 |
| GPU VRAM最大 MiB | 6,701 | 6,301 |

C2理由はINDEPENDENT_STATEMENT 18、NO_NEW_INFORMATION 14。全件finish_reason=stop、retry/repair0、context超過なし。GPU228samples/errors0。VRAM差は今回の観測であり、schemaの性能改善とは主張しない。起動したprocess残存0、終了時8080〜8082 listener0、既存config不変。

## 判定

**C2は不採用。** 独立評価でact/text不一致とHARD違反がともに増加した。

| 独立意味評価 | baseline | C2 | C1 |
|---|---:|---:|---:|
| HARD PASS / FAIL | 18 / 14 | 15 / 17 | 15 / 17 |
| SEMANTIC PASS / FAIL | 7 / 25 | 6 / 26 | 6 / 26 |
| STYLE PASS / FAIL | 15 / 17 | 15 / 17 | 15 / 17 |
| act/text不一致 | 23 | 26 | 26 |
| 根拠捏造 | 0 | 0 | 0 |
| 秘密開示 | 5 | 5 | 5 |
| 質問に回答 / 固定18件 | 9 | 11 | 11 |

C2の理由と本文が整合するのは2/32件。NONE自体をFAILにせず、本文が質問・回答・反論等なら理由欄でラベル不一致を免除しなかった。回答が2件増えても必須条件の悪化を相殺しない。C2不採用後、提案の次手順C1も元baselineから単独で測定した。Phase6は未達。

## C1の追加観測と次の判断

discussion proposalのpropertiesだけを既存required順へ変更し、C2理由欄やoneOf枝順は混ぜなかった。32件すべてでcanonical JSON値/hashはbaseline同一、実wire hashだけが異なることを凍結・送信原本・独立測定で確認した。旧baselineの再生成・再採点は0。

独立Reviewerは、出力discussionの12項目が32/32で指定順となり、speech_actが5番目になったことを確認した。それでも値は全32件NONEで、上表のとおり品質は改善しなかった。したがって、この範囲の項目順変更だけでは問題を解決できない。

C1は235.047 REAL秒、32call、旧構造契約29 PASS/3 FAIL、入力55,068 tokens、出力6,945 tokens、GPU215 samples/errors0、VRAM最大6,301 MiB。retry/repair0、所有残存0、既存config不変。

単独で両候補が採用条件を悪化させたため、C1+C2の組合せを現時点で実行する根拠は弱いと判断し、追加callは行わない。C4の接地保証緩和、C5の2call化、C6の機械ラベル付けも採用しない。次の有力な限定検証としてspeech_act各枝内のkind位置があるが、今回は変えておらず、効果は未確認。別scope/設計/比較条件を定めてから扱う。

## 前提の扱いと限界

保存baselineの1件はspeech_actが12項目中11番目でalphabetical順だった。この一致からGBNFの順序強制やNONE固着の因果までは確定しない。複数モデルの共通失敗だけでモデル能力も原因から完全除外しない。32case・1seedの有限観測であり、実gameや母集団性能へ一般化しない。

## 証拠・検証

- [Main handoff](handoffs/tasks/T430_NONE_REASON_PROBE.md)、[独立レビュー](handoffs/tasks/T432_NONE_REASON_REVIEW.md)、[独立測定](handoffs/tasks/T433_NONE_REASON_MEASUREMENT.md)。
- freeze SHA `c7dbae39970fe8fdd3243e1a4639f909a9e4987bd996a00a10e825d01cad3e26`。
- result SHA `603f1c72527d3062a0c23a4c13e40ae2927d329ad7fd15dc78988772308a2879`。
- annotation SHA `4ef08d9ac0f5f4a0ce9a229632a302ff7c7492c1c5c5886781115daa1967df85`。[原文を含まない32件の結果](handoffs/tasks/T433_SAFE_RESULTS.json)。
- C1: [独立測定](handoffs/tasks/T435_SCHEMA_ORDER_MEASUREMENT.md)、[原文を含まない32件の結果](handoffs/tasks/T435_SAFE_RESULTS.json)。result SHA `066993c83d1db3196522119b9dda262358e97ca02908b2ce7a99c9fae682fda9`、annotation SHA `4d9adb48c5b681f6b7ec474e9fd3155b5814f0297384aa3acf4790f5a287ef54`。
- 最終focused110 PASS、Python3.10 84 PASS。製品不変のgrounding/memory84 PASS、check_docs/diff PASS。
- private原文はOwner専用rootに保全。GitHub/Actions/報告書へ原文は掲載しない。

branchは`fix/reviewer-followup-20260919`。mainへの直接commit/merge、通常game、Master Run、Phase7は行っていない。
