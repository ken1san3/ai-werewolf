# Phase 6 原因切り分けとモデル比較

Status: COMPLETE（調査・比較完了。製品採用・Phase6完了ではない）

測定期間: 2026-09-18〜19 JST。

## 結論

**モデル変更だけではPhase 6を通せない。** 全5モデルが32件ずつ完了したが、
QUESTION/ANSWER/REBUTTAL/OPINION_CHANGE等のラベルは一度も出ず、全160件がNONEだった。
本文の自然さや秘密自白にはモデル差がある。GemmaはHARD25/32・STYLE28/32、
Bonsaiは共通18質問の内容回答13/18で改善が見られたが、どのモデルにも必須違反が残った。

実入力最大は2062token、最大生成は360tokenであり、このsuiteで8192不足を示す証拠はない。
生成が悪くなる原因と、不適切な本文を受理境界で防げない問題を分ける。
共通prompt/複雑な出力契約の負荷は未分離で、enforcement不足だけを生成不良の原因とは呼ばない。
製品コード、既存provider設定、Logical Clockは変更せず、実game/Master Run/Phase7は実行していない。

## 基点と既知/未知

最新main `a3022a11b09d75e1bdec077ebde73e62389f6140` をfetchして照合。
調査branchは `investigate/phase6-model-comparison-20260918`。製品codeは変更しない。
CURRENT_STATE/TASKS、T406/T408/T409と20260917/20260918外部監査を確認した。
既存CI cleanupは取り込み済みだが、会話品質未達・Phase7未開始は別の状態として維持する。

| 分類 | 現在の証拠 | 確度・責務 |
|---|---|---|
| モデル能力 | 5モデル160生成でspeech_act NONEが160件 | 交換だけでは解消せず。能力上限やpromptとの主因分離は未確定 |
| prompt | 指示は存在するが違反が残る。T406完成例candidateではactが出た一方26/28例文コピー | 指示提示の影響は高確度。モデル能力の上限とは断定不可 |
| projection/context | alive/deathと本人ability result_idを明示投影。公開他者主張より優先する指示あり | 現code確認。T389の古い不足を現codeへそのまま帰属しない |
| token/proxy | T406/T408でactualに余裕、schema込みproxyとの単位差。履歴欠落と品質悪化の相関なし | 予算設計の独立課題。品質主因という仮説は非支持 |
| validator/enforcement | option/target/EvidenceRefと組合せは検査。自由文のrole secrecy、生死断定、act意味は包括検査なし | 高確度。モデル非依存の責務検討が必要 |
| Schema契約 | ClaimUpdateはPUBLIC evidenceのみ、提示schemaは同一制約を完全には表さない。T408で3件不整合 | 高確度。schema/validator整合責務 |
| fixture/evaluator | 32合成case、各1生成。期待actは戦略自由を制限しない。表層screenだけで意味PASS不可 | 比較可能だが母集団の性能推定ではない |
| runtime | stock10697/Prism10683で全5profileが32件完了 | このschemaで実行できた。汎用互換性や量子化単独効果の証明ではない |
| quantization | 各familyで代表1つのみ | family/サイズ/量子化が交絡し、量子化の単独原因は未確定 |

外部監査を無条件には採用しない。20260918文書の「本文に一切検査なし」は広すぎる。
現codeにはexact copyに加え、`_validate_generated_text` の190文字/570bytes付近の
非終止文をTEXT_BOUNDへ送る検査がある。自由文の意味検査不足という主要指摘は支持する。
またbaseline32＋structure32は64評価rowだが、structure内4rowは再利用なので
60 distinct generationである。QUESTION等0は観測だが、新規64生成という表現は訂正する。
T406の別candidateで非NONEが生成できた事実もあり、schemaがactを表現できないわけではない。

## 調査20項目の対応

既存host設定 `C:\AIagent\agent\config.toml` のgame profileは9B Q4_K_M、ngl99、
context8192、jinja、enable_thinking=false。既定profileはdevでありgameと混同しない。
provider共有1/backend concurrency1、Phase6 whole response上限512、温度0.2。
stock binaryはbuild10697/commit093adb242。調査時にlive providerは0。測定時のprops/slotsはprofileごとに保存する。

| 対象 | 実装/確認先 |
|---|---|
| path/build/template/jinja/thinking/context/sampling | host config、GGUF scalar、binary --version/--help、測定時props/slots/argv |
| max tokens/schema/grammar | backend._request_payload、generation512、response_format.json_schema strict |
| proxyへのschema算入 | discussion.projection._measure / token_proxy_units、messages＋schemaのserializeを計数 |
| repair | llm.brainのvalidation例外から上限1回、immutable元projection＋bounded repair datum。今回比較はrepair0 |
| prompt/context | llm.prompt._DISCUSSION_INSTRUCTION、discussion.projectionのgrounding.current/self_co/ability_results |
| semantic validator | llm.decision.parse_llm_output、discussion.model各型の整合検査。本文意味の完全判定ではない |
| repetition | brain.controllerの直前自己exact・長い公開peer exactのみ。near copyは製品で未検出 |
| secrecy / alive-dead / act | 指示とauthoritative contextはある。本文の明確な秘密自白、生死矛盾、act意味を包括強制する層はない |
| model依存 | tokenizer、native template、重み/量子化、runtime kernel、推論性能 |
| model非依存 | authorized state、dynamic candidates、public/private境界、構造契約、保存と評価、出力上限 |

## 固定比較契約

- 既存 `phase6_conversation_cases.cases()` の32case、baseline projectionを全modelへ使用。
  T408のstructure追加は不採用のため混ぜない。prompt/context/schemaのhashを共通化。
- A〜Nは既存G01〜G16で評価。近似copyはG12に対する生成を単語列similarityでもscreenし、
  focusedで1語変更と短文同意の対照を追加。実case/期待正解は改変しない。
- 代表: Qwen3.5-9B Q4_K_M、Llama3.1-8B Q4_K_M、Gemma3-12B Q4_K_M、
  Ternary-Bonsai-2-27B PTQ1_0、既存Qwen3.6-35B-A3B UD-Q4_K_XL。
  35Bはdense35BではなくMoEでありサイズだけの対照ではない。
- context8192、slot1、同時request1。temperature0.2/top_k40/top_p0.95/min_p0.05、
  repeat_penalty1/presence0/frequency0、seed4242026、max_tokens512、cache_prompt=false。
  thinking=falseをtemplate kwargsとruntime offで指定。native GGUF templateを使用。
- 32call/model、最大160call。case再生成0、retry0、repair0。各model専用claimは失敗時も保持。
  過去baselineはsampling/seed/cache/runtime slot条件が凍結比較と異なるため参考値に留め、
  今回の共通条件で一回測定する。旧証拠の意味判定はやり直さない。
- REAL request60秒、load180秒、model1200秒、cleanup所有子各最大25秒。外側1320秒。
  actual rendered tokens＋512＋1 <=8192を各request送信前に確認。
  usageとの不一致、runtime/入力hash差、transport failure、thinking不一致で当該model停止。
  同一条件を自動再試行しない。構造/意味不適合は測定対象として記録し他caseを継続。
- 専用loopback8082を使い、起動したprocess objectとlistener所有PIDを確認。
  既存config不変。モデルは逐次loadし、前provider/monitorの終了確認後に次へ進む。
- Qwen9/Llama8/Bonsaiはngl99、Gemma12はngl24、35Bはngl99+n-cpu-moe35。
  VRAM制約へのoffload差を記録し、速度差だけで候補を棄却しない。fit offでcontextの暗黙縮小なし。
- BonsaiはPrismML固定runtime10683/d8f26eec7を隔離使用。runtime差は交絡として明記。
  公式runtime253365422bytes/DLL391443627bytesのSHA256一致。モデルdownload0。
  取得根拠/公式digest: T425 handoff。既存C:\AIagentは変更しない。

## 評価・privacy

HARD: 構造、候補/参照、明確な無利益の真の敵対秘密開示、現stateとの矛盾、exact/nearコピー。
正当なCO、他者への役職推理、戦略的な騙り、死者についての結果報告まで一律禁止しない。
SEMANTIC: 実質問への回答、反論、質問、意見変更、受信根拠、claim継続、情報不足の扱い。
STYLE: 自己紹介の反復、現在の話題への応答、定型文・同構文の反復。
act出現だけで能力PASSにしない。非開示自体は合法で能力未観測になり得る。
全caseを入力/最終出力hashに結び付けて評価し、不明はUNKNOWNのまま保持。

private原文/入力/GPU/runtime logは既存Owner専用rootへ保存。通常log/成果物は
ID/hash/数値/固定理由のみ。内部推論は要求・保存・採点しない。
GPUは全GPU使用量であり単独process占有量とは呼ばない。process RAMとruntime buffer、
offload layer数も区別する。監視欠測は0へ置換しない。

## モデル比較（独立判定）

各モデル32件。P/F/UはPASS/FAIL/UNKNOWN、構造は既存parser/整合validatorの結果。
HARD/SEMANTIC/STYLEは別軸であり合計点にしない。質問回答は同じ入力ベース18件が分母。
本文が回答していても、act不一致や根拠違反があればSEMANTICはFAILになり得る。

| モデル | 構造P/F | HARD P/F/U | SEMANTIC P/F/U | STYLE P/F/U | 秘密自白 | 内容回答/18 |
|---|---:|---:|---:|---:|---:|---:|
| Qwen3.5-9B | 29/3 | 18/14/0 | 7/25/0 | 15/17/0 | 5 | 9/18 |
| Llama3.1-8B | 30/2 | 11/21/0 | 7/25/0 | 10/22/0 | 0 | 5/18 |
| Gemma3-12B | 29/3 | 25/7/0 | 6/26/0 | 28/4/0 | 0 | 11/18 |
| Bonsai2-27B | 30/2 | 19/13/0 | 6/26/0 | 22/10/0 | 0 | 13/18 |
| Qwen3.6-35B-A3B | 31/1 | 24/8/0 | 4/28/0 | 19/13/0 | 2 | 8/18 |

全モデルでNONE 32/32。非NONEラベルの改善は未観測。全callでretry0/repair0、
finish_reason=lengthは0。したがって本文途中切れは512token上限への到達だけでは説明できない。
TEXT_BOUNDによる構造rejectはqw9 3、gm12 1、tb27 2。製品の検出経路は存在し、今回はrepairを行わず原生成を測った。

A〜Nの対応: A=G03、B=G01、C=G02、D=G04、E=G08/G09、F=G15、G=G07/G13、
H=G05/G06、I=G11と自己copy、J=G12とpeer/near-copy、K=G16、L=G10、M=全caseの捏造、N=全caseのact整合。
G14はNONEの合法対照。case別3軸・理由・hashはsafe成果物に保存した。
短い正当な同意や任意の非開示を一律FAILにせず、合法な別戦略と違反を分けた。

## 失敗の種類とモデル別の弱点

以下は独立判定の固定reasonに該当するrow数。同じrowが複数理由へ該当するため足し合わせない。

| モデル | copy row | 能力矛盾 | 生死等state矛盾 | 根拠捏造 | act/text不一致 | 自己紹介反復 |
|---|---:|---:|---:|---:|---:|---:|
| Qwen3.5-9B | 4 | 0 | 3 | 0 | 23 | 11 |
| Llama3.1-8B | 18 | 2 | 0 | 0 | 23 | 6 |
| Gemma3-12B | 1 | 2 | 1 | 1 | 24 | 2 |
| Bonsai2-27B | 4 | 3 | 0 | 5 | 24 | 3 |
| Qwen3.6-35B-A3B | 1 | 2 | 2 | 3 | 27 | 4 |

- **Qwen9B**: 低遅延だが秘密自白5件、自己紹介反復11件。現baselineとして保持し、単独で品質達成とはしない。
- **Llama8B**: 秘密自白は0になったがcopy18件。最速でも今回の置換候補の優先度は低い。
- **Gemma12B**: コピー・反復が少なくSTYLEが改善。ただし能力/state矛盾とact不一致が残り、現offloadで約30秒/発言。
- **Bonsai27B**: 質問内容への応答とSTYLEは改善。一方、根拠捏造5件、能力矛盾3件があり、PTQの品質維持を保証できない。
- **Qwen35B MoE**: 構造31/32、copy1件へ改善したが、秘密自白2件・根拠捏造3件、SEMANTIC4/32。大型化だけの解決ではない。

モデル交換で秘密自白・コピー・自然さの一部は改善したが、act意味整合と根拠の問題は残る。
「同じ症状が残る」ことは、全モデルの内部原因が同一である証明ではない。

## 実性能（REAL時間）

profile全体時間はload/前処理/生成/cleanupを含むtool計測、latencyは各provider requestの経過時間。
p95はnearest-rank。VRAMは生成中の全GPU使用量の最大（desktop等を含む）、RAMはprovider processの
PeakWorkingSetSize。モデル重みサイズやprocess専有VRAMとは異なる。

| モデル | profile秒 | request p50/p95秒 | prompt最小〜最大 | completion総token | VRAM最大MiB | RAM最大GiB |
|---|---:|---:|---:|---:|---:|---:|
| Qwen3.5-9B | 237.3 | 5.61/6.11 | 1518〜1875 | 7110 | 6701 | 11.82 |
| Llama3.1-8B | 177.2 | 3.58/3.88 | 1454〜1796 | 5012 | 6979 | 10.02 |
| Gemma3-12B | 1008.6 | 29.62/35.11 | 1646〜2062 | 8072 | 5953 | 15.90 |
| Bonsai2-27B | 357.4 | 9.15/9.95 | 1518〜1875 | 4730 | 7403 | 14.10 |
| Qwen3.6-35B-A3B | 478.1 | 12.65/15.15 | 1518〜1875 | 8151 | 6253 | 21.63 |

GPU利用率の区間最大はqw35 85%、他4モデル100%。持続利用率とは呼ばない。
GPU sample合計2076、記録error0。runtime logにbuffer/offload行がなく、実offload layer数と
CPU/GPU別buffer量はUNKNOWN。要求argvは上の固定契約、per-case GPU/RAM実測はsafe成果物へ保存した。
全5profileでsource不変、owned process残存0、最終8082 listener0、config hash不変。
provider/monitorのWindows強制終了codeは各1、collectorは全profile exit0/timeoutなしであり区別する。

全160caseでschema有無のrendered prompt差は0、preflight actual tokenとprovider usageは一致した。
proxy/nativeの数値比はGemma3.81〜4.25、他4モデル4.18〜4.82。異なる単位の比であり、
actual tokenとしてそのまま使える値ではない。旧監査の約2.8倍は旧条件の参考値。

## 15問への回答

1. **Qwen9B固有の能力限界**: 自白等の挙動差はあるが、能力の上限までは未証明。モデル交換だけで全品質問題は解消しない。
2. **QUESTION/REBUTTAL/OPINION_CHANGEの改善**: ラベルは全5モデルで0。本文への応答には差があるが、メタデータとの整合は改善していない。
3. **大型化の効果**: Gemmaの自然さ、Qwen35の構造適合/コピー減少は観測。family/量子化/runtimeも違うためサイズだけの効果ではない。
4. **残る失敗**: act/text不一致、能力矛盾、根拠捏造等。全モデルにすべて同じ違反が出たという意味ではない。
5. **enforcementで防ぐか**: 有効候補・参照・権限・構造・定義済み出力制限はコード側。自由文の全意味を単純regexで保証しない。
6. **prompt改善対象**: 優先順位と発言目的、actと本文の対応。旧完成例はact生成を変えたがコピー副作用があり、例文追加だけの再実行は避ける。
7. **構造化対象**: 本人能力結果/公開根拠/時点/対象/claimの由来。既存canonical projectionを使い、二重stateを作らない。ClaimUpdateのPUBLIC契約をまず整合する。
8. **proxyは独立課題か**: 実tokenとの差とschema寄与は確認。安全側proxy自体を即バグとはせず、過剰切捨ての契約/影響を別に修正設計する。品質主因は未立証。
9. **8192不足か**: 今回の最大input2062、最大output360では不足していない。長い実game全局面の保証ではない。
10. **量子化劣化か**: 同familyの別量子化/非量子化対照がなくUNKNOWN。モデル間差からternary/Q4単独の劣化を断定しない。
11. **8GBで実用的か**: 全モデルが指定offloadで合成測定を完了。Qwen/Llamaは軽快、Gemmaは約30秒、Bonsai約9秒、35B約13秒の中央値。9人共有負荷の実用性は未測定。
12. **Bonsaiのternary品質維持**: 絶対的な違反と改善は測定できたが、同baseの非量子化比較なしでは維持率は不明。捏造5件等を無視して採用しない。
13. **Qwen35との差**: HARD24対Bonsai19、SEMANTIC4対6、STYLE19対22。35Bは約1.38倍のrequest中央値と約21.63GiB RAM。単一総合順位にできない。
14. **model変更だけで通す設計**: 不可。モデル非依存の保証と既存acceptanceを維持する。
15. **enforcementを先に作るか**: 既知の構造契約整合を先行し、本文向けは誤reject対照を備えた限定設計にする。質問/反論/投票戦略をルールベースへ置き換えない。

## 本番に向けた候補A/B/C（未採用）

| 候補 | モデル | 次の検証で残す理由 | 採用までの条件 |
|---|---|---|---|
| A・品質優先 | Gemma3-12B Q4_K_M | HARD25/32、STYLE28/32、秘密自白0、copy1。別familyの品質対照 | act/groundingを改善し、約30秒とCPU分担の実用性を別測定 |
| B・応答性との両立 | Bonsai2-27B PTQ1_0 | 約9秒、質問回答13/18、秘密自白0、8GB上で完走 | 捏造/能力矛盾を解消。専用forkを隔離維持し、ternary維持率は未確定 |
| C・大型MoE対照 | Qwen3.6-35B-A3B | 構造31/32、copy1、約13秒で完走。Gemmaより低遅延 | 自白2/捏造3とact不一致、約21.63GiB RAMを許容できるか検証 |

これは追加検証の候補であり、現時点の本番採用を推奨する判定ではない。
現行Qwen設定は保持。Llamaは速度だけなら有利だがcopy18件のため今回優先採用しない。
速度だけで候補を除外せず、どの候補も必須品質を通るまで実gameへ進めない。

## Phase6の次の最小作業順

1. 既知のClaimUpdate PUBLIC参照等、schema/prompt/semantic validatorの契約整合を限定修正しfocusedで確認。
2. 共通act不振を、同じ少数caseで「自然文のみ→最小構造→現行構造」の対照へ分離する計画を作る。
   A/B等最大2候補から開始し、モデル交換とprompt/projection修正を同時に混ぜない。今回の160生成の自動再試行はしない。
3. 明確な本文違反への保証範囲と誤reject対照を設計する。秘密自白と正当な騙り、死者投票と死者の結果報告を区別。
   interface/authority/acceptanceを変える場合だけArchitect gateを使用する。
4. 限定修正→focused/regression→必要な独立Reviewer→人工suite。改善後だけ許可された短いbounded smokeを検討。
5. token accountingは別変更としてnative実測と予算の契約を整合。長時間gameや時間予算拡張で品質違反を隠さない。

今回、追加製品修正・短game・Master Runは実施しない。Phase6は未完了、Phase7未開始。

## 証拠・検証・限界

- [全160caseのsafe測定・独立注釈・集計](handoffs/tasks/T427_SAFE_MODEL_RESULTS.json)
  SHA256 `664fb03a61ba528809ec915b665f209551d4bc372ed2f9dd85c48aa5d9ebfaa6`。
- [独立測定T427](handoffs/tasks/T427_MODEL_COMPARISON.md)、[独立内容判定T428](handoffs/tasks/T428_MODEL_COMPARISON.md)、
  [tool承認T426](handoffs/tasks/T426_MODEL_COMPARISON.md)、[専用runtime一次資料T425](handoffs/tasks/T425_MODEL_COMPARISON.md)。
- adapter＋既存suite44 PASS、Python3.10 adapter18 PASS、関連grounding/memory84 PASS。製品差分0。
- 全160件のcommon/input/output hashを結合確認。既存T406/T408の出力は再採点していない。
- Qwen35の質問回答null範囲に初稿の適用ミスがあり、Reviewerが同列だけを入力共通18件へ訂正した。
  HARD/SEMANTIC/STYLEや原生成は変更なし。旧集計もlocalに保全し、上記safe成果物は訂正版hashへ結合した。
- private本文の内部読取は一度自動承認レビューで拒否された後、ユーザーが意味評価限定のツール表示を明示許可。
  GitHub・Actionsログ・報告書に原文/入力/locator値を掲載しない条件は維持した。
- 32固定case、各1seed、repairなし。ゲーム勝率、自然な多turn、9人負荷、多seed再現性、量子化の単独効果は未測定。
  注釈は一人の独立Reviewerによる有限評価で、母集団性能や完全な因果分離の保証ではない。

## 実行gate証拠

新adapter＋既存suite44 PASS（4.99秒）、Python3.10 adapter18 PASS（2.28秒）、
grounding/memory関連回帰84 PASS（39.65秒）。docs/diff/compile PASS。
T426初回の2blocking（任意値のsafe出力、例外cleanup）を修正し新delta APPROVED。
凍結plan v2 SHA256 `c878456dc15b00b87549a8b91d29eba8ef64e63cb7cc972da1eff40942f4c1c4`。
未実行v1は保全。source/model/runtime hashをplanへ固定し、T427が一回ずつ完了。
config保全hash `43e509956d96492cace8393bdc3fd598ab2418315ccdfd82e144b876241f3bab`。
意味判定はT428の独立最終出力照合を使い、Mainが集計/hashと原因分析を統合する。

## 修正責務の分離（製品未変更）

| 分類 | 最小候補 | 必要な証拠・境界 |
|---|---|---|
| A model | 同一caseの意味・実行性能を基に候補を絞る | モデル変更とprompt変更を混ぜない。単発32件で一般能力を断定しない |
| B prompt | 優先順位を短くし、発言目的と根拠を結ぶ | 完成例文の追加はT406のコピー副作用がある。自然言語の反論/質問そのものはLLMに残す |
| C projection | 既存canonical情報を参照しやすくする必要性をcase別に検証 | 既にある能力結果を別stateで二重管理しない。受信権限外の情報は渡さない |
| D tokens | deterministic proxyとnative rendered tokensの単位・schema寄与を分離 | 予算/切捨て契約の変更は設計gate。contextを増やして品質成功と見なさない |
| E enforcement | ClaimUpdateのPUBLIC参照契約、実state/候補/参照、コピー/長さの保証を整合 | 自由文の全意味を単純regexで保証しない。合法な騙り・死者の能力結果報告・非開示を維持 |
| F fixture | direct response/合法な別戦略/非開示/UNKNOWNを分け、必要な不足対照だけ追加 | 今回32caseは入力固定。複数turnの自然さ・ゲーム勝率・多seedの再現率は測っていない |
| G runtime | native templateと実token、schema受付、offload、thinkingをprofile別固定 | PTQ専用forkと他stockの差をモデル能力だけに帰属しない |

製品コードが保証する対象は受信権限、構造契約、有効候補/根拠参照、明確に定義された出力制限。
誰を疑うか、いつCOするか、どんな偽情報を戦略的に述べるか、質問や反論の内容はLLMの責務。
明確な秘密自白/事実矛盾への生成後の対処は必要だが、自由文からの抽出精度と誤rejectを含む
別の限定設計が必要。今回のモデル比較だけで新しいsemantic validatorの安全性は証明しない。

## 計測値の読み方

`structural_pass`は既存parser/整合検査の結果、`product_text_guard_rejects`は既存追加guardの結果。
前者PASSだけで製品が受理したとは呼ばない。今回は合成provider単発生成なので、実製品の
retry/repairを適用せず原出力を採点する。repair/retryは0で固定し、品質差を修復回数で隠さない。
`finish_reason=length`はJSON応答全体の512token上限到達であり、本文200文字の途中切断とは別。
HARD/SEMANTIC/STYLEは各々分母とUNKNOWNを表示し、3軸を合成した「賢さ順位」は作らない。
