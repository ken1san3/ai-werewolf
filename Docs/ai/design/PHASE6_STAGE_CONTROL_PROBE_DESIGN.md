# Phase 6 System Stage Control 最小実験設計

Task: T469
Status: DRAFT

## 1. 目的と単一仮説

Cycle6候補SC2は、既存stage control instructionのroleだけをuserからsystemへ移すtest-only 2-call実験である。現在のsystemはuser payloadをuntrusted game dataとして扱うよう要求する一方、IC2/GC2は同じuser roleの第三messageへstage instructionを置いている。このauthority衝突が発話計画を弱める可能性を一回測定する。静的監査は配置事実だけを示し、因果効果は未測定である。

C1/C2/K1/I1/S1/P2/IC2/GC2は不採用済みであり、旧rawを再生成・再採点しない。GC2は旧validator 28/32でもHARD pass 5/32、mismatch 3、fabrication 4、ability contradiction 4、固定質問回答4/18だった。SC2は新32case、最大64 provider callを一回だけ実行する。max6cycleの最後の候補であり、不採用後に別候補へ進まない。

変更変数はmessage role/placementだけとする。`CHOICE_INSTRUCTION`と`OUTPUT_INSTRUCTION`の文字列を一字も変更せず、新warning、例、penalty、NONE禁止、oneOf順変更、grounding補完を足さない。製品schema/runtime/provider/model/authority/game/stateは不変である。

## 2. message構成

native template差を最小かつ一意にするため、first systemへcontrolをappendし、第三user messageを削除する方式だけを採る。複数system message案やrole名だけの置換案は試さない。

baseline messagesを`S0`（元system）、`U0`（canonical data user）とすると、両stageを次のexact二messageにする。

```text
messages[0].role = "system"
messages[0].content = S0.content + "\n\n" + stage_control
messages[1] = U0  # role/contentともbyte-equivalent
```

元system `S0.content`は完全なbyte prefixとして保持する。したがって「exact schema」「user payloadはuntrusted game data」「payload/schema宣言値だけを選ぶ」という既存safety ruleを削除、並替え、言い換えしない。区切りは固定ASCII LF二個だけで、control本文の一部として数えない。末尾空白のtrimやnewline正規化をしない。

choiceの`stage_control`は既存`CHOICE_INSTRUCTION`そのものとする。outputは既存実装と同じ`OUTPUT_INSTRUCTION + "\n" + canonical_choice_json`とする。`OUTPUT_INSTRUCTION`の文字列は不変であり、動的にsystemへ入るのは次を全て満たすchoice JSONだけである。

- strict JSON、duplicate/nonfinite/overflow拒否済み。
- exact closed choice schema合格済み。
- keyは`speech_act_kind`一つ、値はbaseline枝順の7値enumの一つだけ。
- `canonical_json_bytes`で再直列化し、raw LLM textを連結しない。

ゲーム本文、player ID、role、ref、actor、target、秘密、自由文、raw responseをsystemへ昇格しない。`U0`はcanonical payload全体を従来位置のuser messageとして同じUTF-8 bytesで保持する。

## 3. schema・validation境界

call1はIC2のclosed one-field choice schemaとbranch順をそのまま使い、max_tokens 32とする。call2は選択kindを固定したC5修正済みgrounding-closed schemaを使い、max_tokens 480とする。C5の修正は、空配列を閉じる際も元`items`を保持して公式compilerへ制約を伝える互換修正だけである。

call2はprojected∩capturedの有限EvidenceRef集合、合法`(claim, speaker)` tuple、refs0時の空配列/null、必須ref不存在時の`NO_LEGAL_GROUNDING`、strict legacy validatorというGC2承認境界を維持する。hostはref、actor、decision、本文、updateを補完しない。

各outputはstrict JSON、stage schema、choice kind一致、元projectionの`parse_llm_output`の順で検査し、全合格時だけ受理する。schema/validator不合格、length、timeout、binding不一致は当該case失敗として32分母へ残す。choice失敗時はoutput call 0で、fallback、repair、再選択をしない。

## 4. C5 grammar差の交絡

GC2実測後のC5 compiler互換修正により、空`evidence_array`/`claim_updates`を使う既知4caseは、SC2と保存GC2の間でstage2 grammarも異なる。この4caseの差をrole placement単独効果とは解釈しない。

manifestへ既知4case IDを固定し、各rowに`c5_grammar_compat_changed` boolと旧/新schema hashを保存する。28caseの限定cohortはrole-only診断として別集計できるが、主acceptance、HARD、mismatch、質問回答の分母は常に全32caseである。28case結果を32case gateの代用にせず、4caseを除外した合否を出さない。C5/GC2を再生成・再採点しない。

## 5. offline検証とnative template gate

offline/focused gateとowned server依存gateを分離し、offline結果をnative rendered prompt合格として扱わない。

### 5.1 offline/focused

provider serverを起動せず、全32caseと全7 choice fixtureについて次を検査する。

- baseline `S0`が新system contentのexact prefixで、append suffixが固定delimiter＋既存instruction（outputのみstrict canonical enum JSON）と一致する。
- `U0`のrole、content、UTF-8 bytes、SHAがbaseline/GC2と一致し、user messageがexact一件である。
- message role列が両stageとも`[system,user]`、第三messageが存在しない。
- choice/output instructionのsubstring SHAが既存監査値`2096372bafb9c7aab9a4eab3070294c6828486dcd1992da3685e3146470f8952` / `2b13e7a47a2289e780fe8977e4b7c90b5c14ca61b83691022cf5771ae3573bb1`と一致する。
- output system suffixをdecodeしてもcanonical choice object以外の動的値がなく、全7値fixtureで同じfield集合である。
- bodyのcanonical wire serializationとschemaを検査する。承認済み`llama-tokenize.exe`はserialized message/control fixtureが有限であることの補助計数だけに使い、server native template token数やcontext PASSとは記録しない。

公式schema converterでchoice/C5 output schemaを検査し、MockTransportでbody/wire/bindingを確認する。この段階の証拠はbody SHA/bytes、message role、content SHA、offline fixture count、converter結果に限定する。

### 5.2 owned server native gate

独立tool review承認後、かつ一回suiteのgeneration callを一件も消費する前に、runnerがcanonical owned serverを起動してreadiness、runtime identity、single idle slotを既存共通lifecycleどおり確認する。その後、各generation callの直前に、送信するexact body/wire bytesを既存`count_prompt(body, wire_payload=payload)`へ一度だけ渡す。`count_prompt`が行う既存3 request、すなわちschema付き`/apply-template`、その結果への`/tokenize`、schemaなしbodyの`/apply-template`をnative gateとし、同じ目的の追加HTTPを重ねない。

choiceはchoice generation直前、outputはそのcaseのstrict choiceからbodyを作った後かつoutput generation直前に実行する。返されたrendered promptはgeneration requestより前にUTF-8へstrict encodeし、exact bytesをprivate fileへdurable保存する。これを追加HTTPなしで行うため、test-only `count_prompt`へoptional private sinkを追加し、defaultなし時のP2/IC2/GC2返値とrequest列を不変にする。

既存`rendered_prompt_sha256`は`base.digest(rendered)`、すなわちrendered Python文字列をcanonical JSON stringとして直列化したlegacy digestであり、意味と値を変更しない。raw UTF-8 bytesには新しい`rendered_prompt_utf8_sha256`と`rendered_prompt_utf8_bytes`を使う。private sinkが受け取ったexact bytesのSHA/長さ、durable保存後に再読したbytesのSHA/長さ、公開rowの新fieldを一致させる。legacy digestとUTF-8 byte SHAを比較または代用しない。公開rowへ出すのは二種類のhash、UTF-8 bytes、token count、remaining context、schema差boolだけである。

native serializationの新SC2値は今回のgateで全て取得し、instruction textの出現exact一回とcanonical user payloadの連続byte列出現exact一回をprivate検査する。旧IC2/GC2は保存済み公開/private証拠に既に存在するfieldだけを比較に使う。旧bodyへの追加HTTP、旧run再測定、legacy digestやtoken countからUTF-8 bytes/hashを推定することを禁止する。旧`rendered_prompt_utf8_sha256`または`rendered_prompt_utf8_bytes`が保存されていなければ各値を`null`、capture statusを固定`NOT_CAPTURED`とする。

比較集計はfieldごとに`legacy_digest_comparable_count`、`utf8_sha_comparable_count`、`utf8_bytes_comparable_count`、`token_count_comparable_count`を持ち、両候補に保存値があるstageだけをそのfieldの分母にする。SC2全stageのnative gate分母と、旧runとの比較可能分母を混同しない。旧UTF-8 bytesが全件欠落なら当該比較分母は0であり、差なし/一致とは記録しない。template出力本文や秘密を公開しない。保存request.bin、apply-templateへ渡すwire、generationへ渡すwireは同一bytesとする。

`/apply-template`、`/tokenize`、private rendered保存、hash照合、context式のいずれかが失敗した場合、対象stageのgeneration endpointを呼ばず、provider generation call/consumed countを0のままにする。当該rowを固定`TEMPLATE_INVALID`または既存固定budget reasonで失敗にし、未処理rowを含む全32をresult分母へ残してrunを停止する。共通`finally`がmonitor/providerの所有processだけをcleanupし、owned processes remaining 0を要求する。

## 6. budget・call accounting

choice32/output480の固定合計512、context8192、request60秒、load180秒、model1200秒、cleanup25秒、outer1320秒、slot1を据え置く。各callで`prompt_tokens_actual + assigned_max_tokens + 1 <= 8192`、provider prompt usage一致、completion stage上限とcase合計<=512を検査する。

IC2と同じreserve契約を使い、choice前remaining>=240秒、output前>=120秒を許可境界とする。検査はrequest.bin/consumed/counterより前。最大32 choice＋32 output=64新call、retry0、repair0。messages条件が変わるため旧choiceを再利用せず、全choiceを新規生成する。reused callは0と明記する。

合法no-message/NONEでもvalid choice後はoutputを実行する。全32 rowをprovider前に初期化し、未実行、invalid、length、budget停止を分母から除外しない。state/send副作用は0。

## 7. binding・privacy

case ID、baseline canonical input/projection/body/schema/messages hash、C5 schema hash、system prefix hash、stage control hash、canonical user hash、choice raw/canonical hash、selected kind、locked branch、stage body/wire hashを送信直前に再照合する。別case、旧choice、raw choice、user payload差替え、system prefix欠落、instruction重複を拒否する。

raw、rendered prompt、canonical user payload、本文、秘密はprivate evidenceだけへ保存する。公開rowはhash、bytes/count、role列、validation bool、selected kind、status、usageだけを持つ。新system message全文を公開resultへ複製しない。

## 8. 固定identityと実装の最小境界

experimentはexact `stage_control_v1`、measurement taskはexact `T471`、runner pathはexact `scripts/phase6_stage_control_runner.py`とする。prepare、plan、manifest、runner verify、result、freeze、outerの全てがこの組を検査し、欠落、別task、別runner、unknown experimentをprovider起動前に拒否する。

新test-only helper `scripts/phase6_stage_control_probe.py`とfocused test、`scripts/phase6_stage_control_runner.py`とrunner testを追加する。helper実装taskはT470、measurementはT471であり、runner contractのtask値にT470を使わない。helperはIC2のchoice/locked-kind検査とC5済みGC2 schema/validatorを値変更なしで合成し、message構築だけを本候補として実装する。P2/IC2/GC2 runnerをimportして経路を流用せず、pure helperの承認済み関数を必要最小限importする。

共有runtimeには固定`SC2_CONTRACT = TwoStageContract('stage_control_v1', 'T471', choice32/output480, reserves240/120, equal false/false, max64)`だけを追加する。既存同期two-stage lifecycleをそのまま使い、P2/IC2/GC2 contract/path/resultを変更しない。`scripts/phase6_probe_outer.py`の固定task mapへ`'stage_control_v1': 'T471'`、固定runner mapへ`'stage_control_v1': 'phase6_stage_control_runner.py'`だけを追加し、default fallbackでSC2を起動させない。

runtime source接点はSC2 contract、各stage generation直前の既存`count_prompt`呼出し、optional private rendered sink、固定失敗row/cleanup testだけとする。`phase6_model_comparison.count_prompt`の3 HTTP request列を再利用し、別のapply/template clientやbulk preflight、汎用frameworkを追加しない。private sinkはSC2だけが渡し、他probeのbody/wire/resultを変えない。

source allowlistは新helper/test/runner、共有runtime contract/native gate/private sinkとtest、`phase6_model_comparison.py`のoptional sink差分、outer固定mapping/test、現在の製品codeをexact path/SHAでfreezeする。C5 sourceはhelper path `scripts/phase6_grounding_closed_probe.py`とhelper SHA-256 `81c8e287c9af6db76afcee2272ef3d4b2e5bb56870c96c73a7e7d3c456a79221`をsource fieldへ固定する。承認bindingはreview report path `Docs/ai/handoffs/tasks/T461_EMPTY_ARRAY_COMPATIBILITY_REVIEW.md`とreport SHA-256 `ed68adc6378b3fadfbb21f2c6d96e93ec089e99357d6430f1bc6e80f7247b2e9`を別fieldへ固定する。review report SHAをhelper SHAとして扱わず、handoffの単一「承認SHA」から推定しない。P2/IC2/GC2のMockTransport body/wire/result、count_prompt request列、ownership、cleanupを回帰する。

## 9. focused acceptance

- choice/output全32 bodyで元system exact prefix、instruction exact一回、canonical user role/content/bytes不変、role列`[system,user]`、第三userなしを検査する。
- strict choice以外の動的値をsystemへ渡さない。全7 enumを受理し、unknown/extra/missing/duplicate/nonfinite/overflow/raw textをsystem構築前に拒否する。
- offlineではbody/system/user/schema/wireと`llama-tokenize.exe`の補助計数だけを検査し、native PASSと混同しない。
- tool review後のowned serverで、readiness後かつ各generation消費前に既存`count_prompt`の3 requestを一回実行する。legacy JSON-string digestと新UTF-8 bytes SHA/lengthを別fieldで検査し、private rendered保存、保存/apply/generation bytes一致、instruction/user bytes出現回数、prompt/context/usageを確認する。native gate失敗は対象generation call0、全32分母、所有cleanupとする。
- 旧runは保存済みfieldだけを比較し、未保存UTF-8 SHA/bytesを`null/NOT_CAPTURED`とする。追加HTTP、再測定、推定を禁止し、legacy digest/UTF-8 SHA/bytes/token countの比較分母をfield別に保存する。
- C5済みschemaの全32×7構築と公式converterを通し、既知4caseのschema hash差と28case非差を固定する。空配列items保持、refs0/null、claim tuple、必須ref fail-closedを回帰する。
- 全trigger/5 action/7 act、合法NONE/no-message、authority positive 13を受理し、EvidenceRef/actor/option/role/target/prior/new cause/reaction/CO/pre-vote/update/bytesの代表negativeを候補schemaまたは旧validatorで拒否する。
- choice不合格時output call0、valid choice時output一回、最大64、32/480、retry/repair0、reserve境界、全32 rowをMockTransportで検査する。
- system prefix、user payload、choice/case/projection/schema/body/wireの一点改変を送信前に拒否する。公開rowへsystem/user/raw/本文/秘密が漏れないことを検査する。
- exact `stage_control_v1`/`T471`/`phase6_stage_control_runner.py`をprepare/verify/freeze/outerで検査し、一点違いをprovider前に拒否する。P2/IC2/GC2既存focused、共有runtimeのcontract拒否、count_prompt既存3 request列、process/listener ownership、即exit、private save failure、PID reuse、非所有process保護、cleanup、source/config不変を回帰する。

独立Reviewerは、変更がrole/placementだけでinstruction text不変であること、元system safetyとcanonical user bytes保持、strict enum以外のsystem昇格0、C5 4case交絡、全32主分母、旧validator/authority、runtime最小差分、privacyを確認する。

## 10. 一回測定と終了

独立設計APPROVED→offline/focused実装→独立tool review→freeze→独立Testerの最大64call一回→独立意味評価の順を守る。保存baselineに対する主gateは全32でHARD fail `<=14`、act/text mismatch `<23`、FABRICATED_EVIDENCE `<=0`、SECRET `<=5`、STATE_CONTRADICTION `<=3`、ABILITY_CONTRADICTION `<=0`、各UNKNOWN非増加、SEMANTIC pass `>=7`、固定18質問回答`>=9`、合法NONE維持とする。

28case role-only cohortと4case grammar-changed cohort、choice/final kind、decision kind、message有無、stage別失敗を診断として別保存する。NONE/no-message増加やvalidator passだけを改善扱いしない。baseline/GC2/C5の旧結果を再評価しない。

SC2はcycle6の最後である。採用・不採用・実行不能の証拠を保存した後、この6cycle枠で別候補を設計・実装・実行しない。製品採用には別途D077のHARD100%、重大秘密/状態矛盾0、長文exact copy0、独立reviewが必要である。
