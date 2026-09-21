# Phase 6 Intent Choice→Locked Full Output 最小実験設計

Task: T456
Status: DRAFT

## 1. 目的と境界

候補IC2は、speech act kindの選択だけをcall1へ分離し、call2でそのkind枝へ固定した旧完全出力を生成するtest-only 2-call実験である。P2は構造32/32合格後、speech actがNONE 32、HARD fail 22、act/text mismatch 28、捏造2、能力矛盾2、固定質問回答7/18で不採用済みであり、raw再読・再採点・同条件再実行をしない。P2のwire/adapter/bindingに限定欠陥は観測されていない。

仮説は、kind選択をbranch固有のgrounding/update field生成費用より前へ分離すると、選択後に旧strict branchを満たしながら本文とactを結合しやすくなる可能性がある、という限定的なものとする。NONE禁止、penalty変更、oneOf順変更、prompt例文、host補完は行わない。S1/I1/K1/C1/C2のschema変換を混ぜない。

製品schema/parser、model、sampling、context、concurrency、authority、privacy、game rule、admission、lifecycle、stateは不変。最大32判断/64 provider call、retry0、repair0。通常game、Master Run、Phase7、日本語版、model downloadは対象外である。

## 2. call1: 対称Intent Choice

call1 responseは次のsingle closed objectだけとする。

```json
{"speech_act_kind":"NONE|CLAIM|QUESTION|ANSWER|REBUTTAL|OPINION_CHANGE|RELATION_HYPOTHESIS"}
```

実schemaは`type=object`、propertyは`speech_act_kind`一つ、required一つ、`additionalProperties=false`、値は元`$defs.speech_act.oneOf`から順に抽出した7つのkind constを持つenumとする。元枝がexact 7種・一意・closed・既知shapeでなければ`SCHEMA_SHAPE_CHANGED`で停止する。enum順はbaseline oneOf順を保持し、NONEを移動・削除せず、順序変更を実験変数にしない。全kindは同じ一field・同じ型であり、branch固有fieldをcall1 schemaやinstructionへ出さない。

call1 messagesはbaseline system/userの先頭2件をbyte-equivalentに保持し、第三の固定user instructionを追加する。instructionは「既存canonical inputだけに基づき、後続の公開応答に最も適するspeech act kindを一つ選び、schemaどおり返す。field、根拠、本文はまだ生成しない」という範囲に限定する。各kindの説明、例、推奨、NONE penalty、role/target/ref/defaultを追加しない。

rawをprivate保存後、duplicate key、NaN/Infinity、float overflow、構文不正をstrict JSONで拒否し、exact schemaを検査する。call1値は製品proposal、decision、state、予約、部分admissionではなく、未適用のtest-only intentである。kind以外の値を持たず、send/state副作用は0。合格値をcanonical化し、raw hashとcanonical choice hashを別に束縛する。

## 3. call2: Kind-locked旧完全出力

call2はcall1が合格した場合だけ実行する。baseline旧出力schemaのroot、decision、discussion、全`$defs`をdeep-copyし、`$defs.speech_act.oneOf`から選択kindの元枝を一意に取り出し、`$defs.speech_act`をその枝そのものへ置換する。それ以外のschema値・array順はbaselineとJSON値同一にする。

この固定は次を意味する。

- call2は選択kindに必要なsubject/topic/stance/ref/actor/prior/new cause等を旧枝制約どおり自力生成する。
- decision、本文、reaction、CO、pre-vote、assessment/claim/relation/strategy updatesも旧完全schemaどおり自力生成する。
- hostはactor、topic、ref、role、target、option、updates、本文を補完・推測・修正しない。
- 選択kindが現groundingでは支持できない場合も別kind/NONEへfallbackせず、call2不適合を当該case失敗とする。

call2 messagesもbaseline system/userをbyte-equivalentな先頭2件として保持し、第三の固定user instructionを追加する。instructionは「locked speech act kindを変更せず、既存groundingとoffered actionだけを使い、そのkind枝を含む旧完全出力をschemaどおり返す」とし、canonical choice JSONを付ける。例文、追加事実、rationale、秘密、field値は足さない。

call2 rawをstrict JSONとlocked schemaで検査し、`discussion.speech_act.kind`がcall1選択とexact一致することをschemaとは別に再確認する。その完全rawを値変更なしで元baseline projectionの`parse_llm_output`へ渡す。offered action/option/target/role、EvidenceRef/visibility、actor/addressee/claim speaker、実prior/new cause、trigger/reaction、CO/pre-vote、updates、distinct evidence、text/proposal bytesを旧validatorに再検査させる。candidate schema合格、kind一致、旧validator合格の三つを別保存する。

call2が別kind、schema不合格、旧validator不合格、length、timeout、usage/binding不一致なら全体を破棄する。call1 intentだけを意味評価・最終出力・stateへ使わない。修復、再選択、NONE fallback、同case再callは0。

## 4. 全action・triggerとno-message

speech act kindはdecision kindと別概念であり、call1は全triggerで同じ7値を選べる。call2の旧完全schemaがaction/triggerの合法集合を決める。

| trigger | 旧decision候補 | call2 |
| --- | --- | --- |
| INITIAL_CHAT | none / chat | 必ず実行 |
| PEER_CHAT | none / chat、reaction必須 | 必ず実行 |
| CO_OPPORTUNITY | none / co_declare、CO整合必須 | 必ず実行 |
| PRE_VOTE | vote、許可時none、pre-vote整合必須 | 必ず実行 |
| ABILITY | none / ability | 必ず実行 |

decisionがnone/vote/abilityで本文fieldを持たないno-message actionでも、speech act、grounding、updatesを含む完全出力はcall2で生成する。したがってcall1合格32件なら常に64callであり、P2のno-messageによるcall省略を再利用しない。合法NONEは選択・最終出力の両方で維持するが、NONEを選ぶことも避けることも指示しない。

選択kindが最終action/triggerと意味的に不自然でも、hostは規則で書き換えない。schema/旧validatorが許す組合せは独立意味評価で本文/act、質問回答、秘密、state/abilityを判定する。

## 5. token・REAL時間・call accounting

旧baselineのmax_tokens512をcall1=`32`、call2=`480`へ固定分割する。未使用tokenの移動、動的配分、上限拡大はしない。call1の全7値JSON fixtureを固定offline tokenizerで計数し、32以内の診断を保存する。call2は固定32入力のlocked schema/messagesについてprompt actualとcontext式を事前確認する。最大合法全形が480以内であることは要求せず、実runのlengthは当該case失敗として32件分母へ残す。

offline tokenizerはP2で承認済みの`C:/AIagent/llama-tokenize.exe` version/build/commit `0.3.0-dev/10697/093adb242`、SHA-256 `a0fbd34a8a3f25fc0f41cbac1ec67e8395a5ef940db33bd07307b5e3dc8cd6a1`、同canonical GGUF、`--stdin --ids --no-bos --no-escape`を固定する。stdout/stderr/token IDs/入力本文は公開せず、fixture ID、count、status、tool/model hashだけを保存する。

各callで`rendered_prompt_tokens_actual + assigned_max_tokens + 1 <= 8192`、provider prompt usage一致、completionがstage上限以下、case合計completion<=512を検査する。call別prompt/schema/messages/wire hash、bytes、usage、latencyを保存し、proxyとactualを混同しない。

HTTP timeout各60秒、load180秒、model全体1200秒（load含む）、cleanup各25秒、outer1320秒、slot1/同時1を据え置く。1 stageについて、apply-template/tokenize等の3 requestを合計最大60秒、generationを最大60秒として、送信前reserveを120秒とする。IC2はchoice前にmodel deadline残時間`>=240`秒、choice合格後のoutput前に`>=120`秒を必須とする。境界値240/120秒は実行可、それ未満は不可とする。この検査は各stageの`request.bin`作成、consumed record作成、provider call count加算より前に行う。

choice前reserve不足はprovider call 0の`MODEL_TIME_BUDGET`で全体停止する。choiceが合格してもoutput前reserve不足なら`output_provider_calls=0`、outputの`request.bin`/consumedなし、当該rowを固定状態`OUTPUT_NOT_STARTED`かつ理由`MODEL_TIME_BUDGET`として全体停止する。いずれも事前生成した32 rowをresultへ残し、意味評価の分母を32から減らさない。P2の既存plan前120秒/message前120秒検査は変更せず、IC2のchoice前240秒をP2へ適用しない。dispatch許可後だけcall consumed recordをdurable保存する。最大32 decision/64call、retry0/repair0をmanifestとresultで照合する。

状態は`CHOICE_NOT_STARTED → CHOICE_STARTED → CHOICE_VALID → OUTPUT_STARTED → COMPLETE`とする。choice invalid/error/lengthではOUTPUT call 0、case failure。output失敗では最終候補なし。runner再起動時はclaim/result/consumedを上書きせず再開しない。

## 6. wire・binding・prompt公平性

call1とcall2 bodyを別々にcanonical化し、新規schema propertiesだけ宣言順へ戻したimmutable bytesをprivate保存してから、同じbytesをapply-templateとgenerationへ渡す。MockTransportで保存・template・generationの三者一致を検査する。

次を別hashでfreezeする。

- baseline先頭2 messages、canonical input、projection、baseline body/schema。
- call1固定instruction、choice schema/body/messages/wire。
- call1 raw、canonical choice。
- call2固定instruction template、locked kind branch、locked schema/body/messages/wire。
- model/runtime/argv/template/config、32case順、source allowlist。

call2 bindingはcase ID、projection hash、baseline messages hash、call1 wire/raw/canonical choice hash、selected kind、locked branch/schema/body/wire hashを送信直前に再照合する。case/kind/raw/schemaの差替え、古いchoice、別projectionを拒否する。rawはbinding用hash以外に再解釈せず、strict parseしたchoiceとの一致を再検査する。

baseline system/userは変更せず、追加instructionだけを明示的な実験差とする。P2との差は、stage1 schema/instruction、stage2 locked branch/instruction、32/480配分、全case2callである。model/sampling/canonical input/groundingは同一だが、P2 rawや出力を対照入力に使わない。

## 7. 同期共通lifecycleと実装境界

新`phase6_intent_choice_probe.py`とfocused test、新IC2 runner/testを追加する。P2 helperのplan/message adapterは呼ばない。P2 runnerからstage dispatchだけでなく、実測済みのrun ownership lifecycleも新しいtest-only共通module `phase6_two_stage_probe_runtime.py`へ機械的に抽出する。これはP2とIC2という二つの固定probeを一回同期実行する関数に限定し、daemon、scheduler、workflow engine、model router、汎用実験frameworkにはしない。

共通入口は概念上次の小さい契約とする（実装時のdataclass名変更は可、意味変更は不可）。

```python
run_two_stage_probe(
    out: Path,
    *,
    contract: TwoStageContract,
    callbacks: TwoStageCallbacks,
) -> int
```

`TwoStageContract`はexperiment/task、順序固定の2 stage名、stage別`max_tokens`、stage前reserve秒、stage別`reject_when_remaining_equal`、最大provider call数、request/load/model/cleanup/outer上限、model/profile/config/tokenizer識別子をimmutable値で受ける。P2は`plan/message`、`384/128`、`120/120`、`true/true`とし、既存どおりremaining `<=120`秒を拒否する。IC2は`choice/output`、`32/480`、`240/120`、`false/false`とし、remaining `<240`/`<120`秒だけを拒否する。共通関数は未知/重複stage、stage key集合の不一致、bool以外の等号policy、合計token 512超過、非正reserve、上限不一致をclaim前に拒否する。

`TwoStageCallbacks`がrunner固有処理として渡せるのは次だけとする。

- `verify_source_and_inputs(plan) -> VerifiedInputs`: source/config/input/case順をread-only検査し、32case projectionを返す。
- `initial_row(case, projection) -> dict`: 公開可能な固定rowを作る。共通側が全32 rowをcall前に確定保存する。
- `process_case(context, case, projection, row) -> CaseOutcome`: P2のplan/message意味、またはIC2のchoice/locked-output意味を処理する。network送信は`context.dispatch(stage_name, canonical_body)`だけを使う。
- `verify_final_source(verified_inputs) -> bool`: cleanup後のsource/config不変を照合する。

callbackはprocess起動/停止、listener、deadline、request/response I/O、private path、`request.bin`/consumed/raw/usage保存、provider call counter、result書込み、claim、cleanupを直接扱えない。`context.dispatch`はstage順、reserve、token上限、body/wire bindingを検査する唯一の送信口であり、成功時だけprivate stage resultを返す。P2はno-message skipを`process_case`内の既存規則として保ち、IC2はvalid choice後に必ずoutputを要求する。

共通lifecycleはplan読込み、既存result/claim拒否、claim取得、private evidence locator、32 row初期化、model process/readiness/runtime identity/listener ownership、monitor、単一model deadline、stage reserve検査、canonical request保存、durable consumed、同期HTTP call、reasoning/usage/finish/budget検査、逐次case loop、固定例外分類、各遷移のdurable result保存、provider/monitor cleanup、所有process確認、performance付加、final source/config照合、最終status/exit codeを所有する。preflight失敗はclaim/process作成前に停止する。claim後の全成功・拒否・例外は共通`finally`でcleanupとresult保存を行う。reserve不足はstage artifact/counter作成前、callback例外は当該caseの次call前に固定失敗へ写像し、final source/config差はcleanup後に`STOPPED`とする。private raw/本文/秘密を公開rowへ流さない。

P2 runnerはこの共通関数へ既存callbackと固定contractを渡す薄いadapterにし、IC2 runnerもIC2 callbackと固定contractだけを渡す。IC2はP2 runner/helperをimportせず、P2固有plan binding、adapterを再利用しない。outerは新experiment/task/runner mappingだけを追加し、既存P2 mapping・ownership・timeoutを変えない。

抽出前後のP2を同じ決定的MockTransportで比較し、stage body/messages/schema/max_tokens、canonical wire bytes/hash、private request/consumed/raw/usage、公開row/result/status、call数、停止理由が値同一であることを要求する。即spawn exit、PID再利用、非所有process保護、所有processだけのcleanup、例外時cleanupも回帰する。これは既存P2 artifactの再生成やprovider再実行を要求しない。IC2 testはstage名、32/480、240/120、最大64call、全case2callの固定値を直接検査する。

既存review済みrunnerをcopyして二重実装しない。source allowlistはIC2 helper/test/runner、共通stage+lifecycle moduleとtest、P2 runnerの薄いadapter差分、outer/test、現在の製品codeだけをexact freezeする。P2/S1/I1 helperをIC2 source deltaへ含めない。

## 8. strict acceptance

- call1全7 kind、enum順、unknown/missing/extra/duplicate/nonfinite/overflow/構文不正、32-token fixtureを検査。choice不合格時OUTPUT endpoint call 0、副作用0。
- call2 locked schemaが選択枝とJSON値同一で、他schema/messages/canonical input/grounding/limitsがbaseline同一。全32×7の静的schema構築と公式converterを検査する。
- 全5 action、全trigger、全7 act、合法NONE、PRE_VOTE棄権、CO、ability 0/1 target、no-message actionを旧validatorとaccept/reject一致させる。
- authority-closed positive matrix13件を、対応kind choice＋locked full output経路で旧parser結果と完全一致させる。option/target/role、alive/dead、owner-only ability result、EvidenceRef/visibility、actor/addressee/claim speaker、prior/new cause、reaction/CO/pre-vote、updates、distinct evidence、text/proposal bytesの代表negativeを両経路で拒否する。
- stage2別kind、wrong choice/case/projection/raw/branch/schema/body/wire hash、古いchoice、extra/missing fieldを送信前またはschemaで拒否する。host補完・fallbackが0であることを検査する。
- 32/480、各context式、call別usage、合計<=512、常時2call、最大64、retry/repair0、lengthの32分母保持をMockTransportで検査する。IC2のchoice前240秒/output前120秒は境界値を許可し、それぞれ240秒未満/120秒未満を拒否する。P2はplan/messageとも120秒ちょうどを拒否し、120秒超だけを許可する。両contractの`reject_when_remaining_equal`値、stage key完全一致、非bool拒否をclaim前testで固定する。reserve拒否時に対象stageのrequest/consumed/counterが0で、output前拒否でも32 rowと分母を維持することを検査する。
- 共通lifecycleのcallbackがprocess/network/private/result/cleanupを所有できず、`dispatch`だけが送信できることを検査する。抽出前後P2のmock body/wire/private records/public result等価性とownership/cleanup回帰、IC2固定contractを検査する。
- private save failure、claim再利用、source/input/runtime/config mismatch、apply-template/generation bytes差、HTTP error、timeout、即spawn exit、PID再利用、非所有process保護、cleanupを回帰する。
- reasoning_contentはnull/空文字列だけ不在扱い、それ以外の非null値は保存せず停止。raw/choice/final本文/秘密を公開rowへ出さず、state/send 0。

独立Reviewerは、call1が本当にkind以外を持たないこと、locked branchが元枝exact copyであること、全case2call、kind一致、host補完0、P2共通stage+lifecycle抽出の等価性、IC2/P2別reserve、ownership、binding、予算、秘密境界を確認する。

## 9. 人工suiteと意味gate

独立設計承認→実装→独立tool審査→freeze→独立Testerの64call一回→独立意味評価の順を守る。32case全件を分母にし、choice/output未生成、schema不合格、kind不一致、旧validator不合格を除外しない。旧baseline/C1/C2/K1/I1/S1/P2を再生成・再採点しない。

保存baselineに対する相対条件はact/text mismatch `<23`、HARD fail `<=14`、FABRICATED_EVIDENCE `<=0`、SECRET `<=5`、STATE_CONTRADICTION `<=3`、ABILITY_CONTRADICTION `<=0`、各UNKNOWN非増加、合法NONE維持とする。SEMANTIC passを7未満、固定18質問への本文回答を9未満へ落とさない。STYLE、stage1 choice kind、final speech act kind、decision kind、message有無、choice/final不一致、stage別失敗を別保存する。NONEやno-messageの増加だけでmismatchが下がっても改善扱いしない。

製品候補には別途D077のHARD100%、重大秘密/状態矛盾0、長文exact copy0、独立reviewが必要である。1 seed/32caseの有限結果であり、intent-choice一般やmodel能力の結論にしない。

通常の候補不採用やfocused失敗は6時間/6cycle全体の停止条件ではない。同条件retryなしで証拠を保存し、残り上限内で独立承認済みの次の安全なcycleへ進む。重要な製品規則選択、権限衝突、破壊操作、外部認可欠落だけを停止条件とする。
