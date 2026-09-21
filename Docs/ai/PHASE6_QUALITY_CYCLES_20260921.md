# Phase 6 有限品質改善 2026-09-21

## 運用

開始22:14:13Z、上限翌04:14:13Z（6 REAL時間）、最大6cycle。通常のtest/候補失敗は次の観測として継続。人間専有選択、秘密/所有/整合安全性、同根本原因2cycle進展なし、上限到達のみ停止条件。旧証拠再生成/再採点、通常game/Master Run/Phase7は0を維持する。

開始時main5a5635f、専用branch8b322d1。既存CI35518262838は全9job PASS。I1実装/所有修正/審査/32測定は前回完了、不一致24のため不採用。今回それを再実行しない。

## 比較概要

HARD/SEMANTIC/STYLEは別軸。主分母32、質問回答は固定18件。非NONE率だけでは採用しない。

| Cycle / 候補 | HARD fail | SEM pass | STYLE pass | act不一致 | 捏造 | 秘密/状態/能力 | 質問回答 | 判定 |
|---|---:|---:|---:|---:|---:|---|---:|---|
| 保存baseline | 14 | 7 | 15 | 23 | 0 | 5/3/0 | 9 | 比較基準 |
| 1 S1 | 30 | 4 | 16 | 27 | 0 | 5/3/0 | 10 | 不採用・隔離 |
| 2 P2 | 22 | 4 | 15 | 28 | 2 | 1/2/2 | 7 | 不採用・隔離 |
| 3 IC2 | 30 | 1 | 22 | 4 | 6 | 3/2/2 | 4 | 不採用・隔離 |
| 4 GC2 | 27 | 5 | 21 | 3 | 4 | 3/2/4 | 4 | 不採用・隔離 |
| 5 compiler互換修正 | — | — | — | — | — | — | — | tool採用、生成0 |
| 6 SC2 | 25 | 12 | 26 | 10 | 5 | 2/6/2 | 6 | 不採用・隔離 |

## Cycle 1: S1 single shape

- 仮説: speech_act objectのoneOf分岐を除くと、本文との整合がHARDを増やさず改善する。
- 変更: baselineのspeech_actだけ17field closed shape。kind以外はschema上nullable、strict mapで必須/適用nullable/非該当nullを区別し、旧validatorへ無損失変換。
- 非変更: prompt/messages/製品schema/model/profile/予算/旧証拠。
- 設計: T449、T450独立APPROVED。source_interpretationの2const衝突は設計段階で限定修正済み。
- focused: Python3.13関連340 PASS、3.10 focused104 PASS、Reviewer84 PASS。公式converter32 PASS、生成0。
- tool review: APPROVED、`handoffs/tasks/T450_SINGLE_SHAPE_TOOL_REVIEW.md`。
- 人工suite: T451で32生成完了、retry0/repair0。候補schema29/32、strict map2/29、旧受理2/32。構造不適合30によりHARD上限14を必ず超え、不採用。独立意味評価32/32完了。
- 構造違反の限定分類: 非該当非null25、必須null24、JSON不完結3（重複あり）。S1は隔離、製品rollback不要。次はcycle2の2-call詳細設計。

## Best known state

会話生成の製品baselineを維持。S1/P2/IC2/GC2/SC2はtest-only隔離し製品採用なし。独立承認済み検証基盤、C5 compiler互換修正、CI capture同期修正を保持。会話品質候補の製品rollbackは不要（製品未採用）。

## Cycle 2: 2-call Plan→Message（不採用）

S1では自由なnullable単一shapeがkindごとの制約を満たせず、strict adapterは30件を受理しなかった。validatorを緩めず、次は旧grounded planを厳密schemaで先に生成し、その確定planから本文を別callで生成する仮説へ進む。S1は再実行せず、独立意味評価と並行してT452詳細設計を行う。モデル/製品境界は不変。新予算/呼出回数/失敗原子性を設計し、T453独立承認後だけ実装する。

### Cycle 2 現在の証拠

- T452設計 SHA `c2f46835bcb13e7f6008b0fb5e74e3cda0817b722d24030aca72b7497ce1db0c` はT453独立APPROVED。
- T455がpure helper/受理同値testsを担当し、Mainがrunner/outer接点・呼出数/失敗経路testsを実装。製品schema/code不変。
- Python3.13関連237 PASS、追加outer routing1 PASS。Python3.10 focused118 PASS。check_docs/diff-check PASS。
- 公式converter32/32 PASS、native tokenizer71有限fixture、生成0。多byte最大文字数fixture1件は128 tokens超過したが、診断値として保存しschema受理集合やtoken予算を変更しない。
- 2call合計512 tokens（384/128）、最大32 decisions/64calls、retry/repair0。新freeze `logs/t448-quality-cycles/frozen-p2/plan.json`、tool審査対象5filesは `p2-review-sources.json` に固定。
- Windowsの置換保存でPermissionError5を観測。dispatch前の消費をexclusive個別ファイルへ記録し、結果snapshotは既存方式を維持した。通常のoffline fixture/保存不具合として修正し継続。
- tool最終APPROVED後にT454で32判断/60callを一回測定し、T453独立意味評価まで完了。

### Cycle 2 採否

| 軸 | baseline | P2 |
| --- | ---: | ---: |
| HARD FAIL | 14 | 22 |
| SEMANTIC PASS | 7 | 4 |
| STYLE PASS | 15 | 15 |
| act/text不一致 | 23 | 28 |
| fabricated evidence | 0 | 2 |
| 秘密 / 状態 / 能力矛盾 | 5 / 3 / 0 | 1 / 2 / 2 |
| UNKNOWN | 0 | 0 |
| 固定18質問への回答 | 9 | 7 |

P2不採用。全32構造PASSでも全actがNONE、本文28件はすべて不一致。peer copy9/self copy7、合法NONE control0/2。32判断は302.679 REAL秒、prompt108,772/completion7,507、GPU peak6,322MiB/100%、終了process残存0。品質と性能は別評価。原本は保存し、製品rollback不要（製品未変更）。

### Cycle 3 scope

P2は本文を別callへ分離したが、intent kind選択時にgrounding/state update全体の生成costが残っていた。次はIC2として、call1でkindだけを等しいshapeのenumから選び、call2でそのkindを固定した旧完全出力を生成する設計をT456へ割当。NONEを禁止せず、旧validator/authority/512 tokens合計/32caseを維持する。field順やoneOf枝順の再調整ではない。設計独立承認前は実装・生成しない。

### Cycle 1 最終比較

| 指標 | 保存baseline | S1 |
|---|---:|---:|
| HARD fail | 14 | 30 |
| SEMANTIC pass | 7 | 4 |
| STYLE pass | 15 | 16 |
| act/text mismatch | 23 | 27 |
| fabricated evidence | 0 | 0 |
| secret disclosure | 5 | 5 |
| state contradiction | 3 | 3 |
| ability contradiction | 0 | 0 |
| UNKNOWN | 0 | 0 |
| 固定18質問の回答 | 9 | 10 |

G14無発話controlは2件とも発話を選択。JSON-valid29件の実17key順は全件設計どおり、3件はJSON不正で順評価不能。tool初期falseを順序違反と数えない。候補schema29/32→strict2/29で拒否しており、受理境界は守られたが生成品質は悪化した。

独立判定 `handoffs/tasks/T450_SINGLE_SHAPE_QUALITY.md`、annotation SHA `383fa99eada92e769397c7a88c7d2c6a76661ad8e0e6016e0e6248cb5e797a4f`。全体採否は不採用。候補隔離、製品rollbackなし。次scopeは2-call詳細設計で、人間判断待ちにしない。

### Cycle 3 実装gate

T456設計はT457独立APPROVED。kind-only choice32tokens→元kind枝locked full480tokens、全valid choiceで必ず2call。Main/T459がtest-only実装。Python3.13合同310＋freeze7 PASS、3.10 181 PASS。P2既存10mock条件の抽出前後hash一致、converter224/native263fixture合格。製品境界不変、tool審査中、実生成なし。

### Cycle 3 最終結果

| 指標 | baseline | IC2 |
|---|---:|---:|
| HARD FAIL | 14 | 30 |
| SEMANTIC PASS | 7 | 1 |
| STYLE PASS | 15 | 22 |
| act/text不一致 | 23 | 4 |
| 捏造 / 秘密 / 状態 / 能力 | 0 / 5 / 3 / 0 | 6 / 3 / 2 / 2 |
| 固定18質問回答 | 9 | 4 |

IC2不採用。kind選択はCLAIM27/QUESTION5でNONE固定を脱したが、意味・安全性は回帰した。旧validator拒否26はCLAIM_ACTOR16/参照9/option1。これは訂正済みexact診断で、初版PRIOR_STATE分類は使わない。原本保存、製品rollbackなし。

### Cycle 4 GC2

生成時の参照集合とclaim/actor対応を既存authority受理制約へ閉じる限定仮説。IC2 choiceは厳密hashで再利用し、変わったoutput schemaだけ新生成する設計をT460へ割当。旧候補は再生成しない。未承認実装・製品変更なし。


GC2設計はT461独立承認、実装/focusedは完了してtool review中。保存choice32照合、grammar221PASS、参照不成立3種を生成前拒否、native32 fixture、新provider0。主な関心は25件のgrounding拒否を生成契約で防げるかであり、kind固定だけを会話改善と見なさない。旧IC2 choiceの合法NONE不成立は既知の残存制約で、GC2でも品質gateから除外しない。

### Cycle 4 最終結果

GC2は旧choice32を再利用し、新output32だけ測定した。candidate/旧validator28 PASS、HARD5 PASS/27 FAIL、SEMANTIC5 PASS、STYLE21 PASS。不一致3、捏造4、秘密3、状態2、能力4、UNKNOWN0、固定18質問回答4。参照制約で構造受理6→28まで改善したが、自由文の能力矛盾と捏造は残り不採用。独立判定は`handoffs/tasks/T461_GROUNDING_CLOSED_QUALITY.md`。260.894 REAL秒、output prompt56508/completion7095、GPU peak6536MiB/100%、所有process残存0。原本隔離、製品rollbackなし。

### Cycle 5 compiler互換修正

4件のschema不合格は、空配列の`items`を除去すると公式converterが`maxItems=0`を反映しない限定原因だった。元itemsを保持してmaxItemsだけを閉じる修正で受理集合を変えない。3.13関連194 PASS、3.10 focused41 PASS、独立7 PASS。旧32schemaの28件同一/4件のみ互換修正を確認。独立tool APPROVED、生成0/再採点0。品質改善とは判定せず、検証基盤として採用。

### Cycle 6 SC2 scope

既存stage controlがuntrusted user dataと同じroleにあるauthority衝突仮説だけを検証する。本文instructionとcanonical user bytesは変えず、制御部分をfirst systemへ配置する限定案。C5修正済みschema、choice32/output480、最大64call、全32主分母。実装開始前にT469独立設計承認、tool gate後だけ一回測定。6cycle枠の最後であり、結果保存後は別候補へ進まない。

### 並行CI修正

Linuxの10mock golden不一致はmock argvのWindows区切りをportable化し、全比較項目を維持した。completionのcapture raceは別worktreeで元deadline内のauthoritative catchup待機を修正。constructor受理契約の追加修正を含む最終4sourceを独立承認し、T473 offline completion1 PASS、0skip、66.117秒。専用CI branch commit b425cd1に保存後、SC2測定・意味評価完了時点で承認4fileをexact hash照合してrootへ統合した。新CIはpush承認待ちのため未実施。

SC2詳細設計は独立APPROVED。実装/focusedはPython3.13/3.10各159件相当PASS（154＋fixture修正後5）、offline253 body/grammar/39 serialized native fixture、新provider0。9file exact hashをT474独立tool reviewへ提出。旧rendered bytes未保存をnull/NOT_CAPTUREDとして保存し、JSON digestとの混同を禁止した。

SC2一回測定は339.305 REAL秒、32decision/64call、candidate32/legacy30（TEXT_BOUND2）、choice CLAIM25/QUESTION6/ANSWER1。新prompt112857/completion6998、GPU314samples/error0/peak6320MiB・100%。各callのwire/consumed/raw/rendered UTF8/output hash64/64一致、source78/config/plan不変、所有process/listener0。T471測定完了、T474独立意味評価も最終r3まで完了。


### Cycle 6 最終結果と訂正範囲

T474独立tool APPROVED（185 focused PASS）後に一回測定。最終r3はoverall HARD7 PASS/25 FAIL、SEMANTIC12 PASS、STYLE26 PASS、不一致10、捏造5/秘密2/状態6/能力2、UNKNOWN0、固定18質問回答6、合法NONE0/2。不採用。意味改善とHARD回帰を相殺せず、候補を隔離した。

初版で表示省略されたG02-2だけ保存済みbytesの未読部分を読み、UNKNOWNを解消した（r2、他31件再採点0）。その後TEXT_BOUNDによる構造拒否2件がHARDに未合成と判明したため、意味判定を保全したまま全行のoverall HARDを`semantic_hard_pass AND structural_pass`へ機械的に合成した（r3）。新raw取得・再生成・意味再採点なし。Main照合は`frozen-sc2/final-hard-gate-audit.json`、独立最終判定は`handoffs/tasks/T474_STAGE_CONTROL_QUALITY_R3.md`。旧baseline/候補の再採点は0。

### 終了時の統合検証

CI同期修正の承認4fileはT473測定SHAとrootで一致。Python3.13 141 PASS＋56subtests（13.60秒）、Python3.10同件数（13.65秒）。既存の同bytes独立completion1 PASSを再利用し、同条件再実行は行わない。製品schema/prompt/model/ruleは変更しない。check_docsとdiff確認は最終handoffへ記録する。

### 最も強い仮説と次の最小scope

参照集合を生成schemaへ限定すると構造受理は改善したが、自由文の捏造・状態/能力矛盾は残った。actと本文の一致だけではauthoritative contextの正しい利用を保証できないことが今回の強い観測である。巨大structured outputやrole配置だけを唯一の根本原因と断定しない。

次は自由文groundingを対象に、authoritative stateと発話内容の対応を分離して検証する最小test-only設計を行う。機械的構造判定と意味HARD判定の合成漏れを防ぐ検証も必要。モデル変更やgame runへは進めない。今回6cycle到達のため次候補は未着手。

累計新provider252call。通常game/Master Run/Phase7は0。独立審査を通過したtoolは保持し、品質候補は全件不採用。外部pushだけが明示確認待ちで、新HEAD CI未実施・Phase6未達を維持する。

## 後続T476の有限2cycle（完了）

旧6cycleは再実行せず、後続の新cycle1でGB1（kind＋authoritative basis選択）を独立設計/tool承認後に32case/64call一回測定。新cycle2ではCO tupleの既知構造矛盾だけを閉じるoffline helperを実装・独立承認し、新provider0。

T478初回評価は内部表示の追加許可後に全32件完了。不採用。HARD fail14→12、SEMANTIC7→12、STYLE15→24、不一致23→3でも、秘密5→6、質問回答9→5、合法NONE0/2、長文peer copy3で個別gate未達。捏造0/状態1/能力0/UNKNOWN0。詳細とexact hashはT478_GROUNDING_BASIS_QUALITY.mdとT476_PHASE6_CONTINUATION.md、採否はD078。

内部表示blockerは解消。旧時間枠終了により新provider cycleは始めず、会話baselineを保持。次はchoice適合とbasis支持関係を分ける最小設計。Actionsはユーザー追加指示で停止し、通常の完了gateはローカル検証とする。
