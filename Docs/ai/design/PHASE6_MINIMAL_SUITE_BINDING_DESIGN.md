# Phase 6 minimal-output 32件接続・一回測定 詳細設計

Status: APPROVED

承認元: T497がSHA-256 `c40e2dc91fcc2bc1051d82866e0ea2b81376198807aa9a39c5219de876453bf7`を独立承認した。その後のT499 frozen-v1はprovider generation=0、row claim=0のまま`TEMPLATE_INVALID`で停止した。本改訂は、その未生成停止で確定したnative template互換性だけを直すdelta。T497が改訂本文SHA-256 `bfdd37e661b3ec2bef94071886fdf56ab753b466dd65bb4fc243ad3fb8bc92ca`を独立APPROVEDとした。実装後のtool delta reviewは別gateである。

## 1. 目的と許可境界

承認済みoffline契約`MinimalOutputV0`を固定32caseへ接続し、H61「永続private state更新/commitと公開発話生成の結合を外す」を一回だけ品質測定できるようにする。順序は詳細設計の独立承認、test-only実装、focused test、tool review、その後にprovider 32件各1回、独立意味評価である。

本設計は製品変更、legacy validator緩和、旧完全proposal補完、主観state推測、通常game、旧候補再生成、同条件retry、Actionsを許可しない。旧baseline raw/annotationを開かず、保存済みaggregateとhashだけを比較に使う。

## 2. 実装単位と公開API

承認後の所有fileを次に限定する。

| path | 責務 |
|---|---|
| `scripts/phase6_minimal_output_probe.py` | 内部core抽出、suite専用binding/schema/validator追加。既存公開契約は不変 |
| `scripts/phase6_minimal_suite_adapter.py` | 32case projectionから`SuiteBindingV1`、prompt、schema、公開metadataを純粋生成 |
| `scripts/phase6_minimal_suite_runner.py` | plan/verify/preflight/一回生成/private保存/finalize |
| `scripts/phase6_minimal_suite_quality.py` | 新annotationと固定baseline aggregateのsafe合成 |
| `tests/test_phase6_minimal_suite_adapter.py` | 32 mapping、schema/prompt、UNRESOLVED、交絡metadata |
| `tests/test_phase6_minimal_suite_runner.py` | freeze、call消費、process/private境界、failure matrix |
| `tests/test_phase6_minimal_suite_quality.py` | annotation binding、三値、固定gate、本文非漏洩 |
| `tests/test_phase6_minimal_output_applicability.py` | 既存HostBinding/bind/validate/output_schema exact bool・旧schema bytes非回帰 |

実行task/run identityは`task_id=T499`, `experiment=minimal_output_v1`, `runner=scripts/phase6_minimal_suite_runner.py`で固定する。T496は設計、T497は独立review、T498は実装であり、provider resultへT496/T498を実行taskとして記録しない。

既存`phase6_minimal_output_probe.py`はstrict parse/schema/bindingを再利用するが、既存`HostBinding`、`bind(authority)`、`validate(raw, HostBinding)`と`_host_shape`のexact bool契約を変更しない。T494 negativeもbool以外を引き続き拒否する。

suite専用に`SuiteBindingV1(binding_version="minimal-suite-binding.v1", authority_without_update, authority_bytes/hash, canonical_user_bytes/hash, private_bytes/hash, update_requirement)`、`bind_suite(...)`、`output_schema_suite(authority_without_update)`、`validate_suite(raw, SuiteBindingV1)`をopt-in追加する。`authority_without_update`は既存`HOST_KEYS - {requires_private_update}`のexact mapで、三値はSuiteBinding直下の`update_requirement`だけに存在する。`False | True | None`を許し、`None→APPLICABILITY_UNRESOLVED`, `False→COVERED`, `True→UNRESOLVED`、その他型→INVALIDとする。

probe内部は`_host_shape_core(authority_without_update)`と`_validate_core(...)`を抽出する。既存`_host_shape`はcoreにexact bool必須検査を加え、既存`HostBinding/bind/validate/output_schema`の公開挙動、bool以外拒否、合法入力のschema canonical bytesを不変にする。suite入口だけがcore mapと三値identityを受ける。`output_schema_suite`は仮boolを挿入せずcore mapからschemaを生成し、同じcommon fieldsを持つ合法な旧authorityに対する既存`output_schema`とcanonical schema bytesがexact一致することをfocused testで証明する。既存validateへSuiteBinding、validate_suiteへHostBinding、binding_version不一致を拒否する。

公開APIは次で閉じる。

```text
adapter.bind_case(case, projection) -> SuiteCase[SuiteBindingV1]
adapter.candidate_body(suite_case, model_name) -> dict
adapter.candidate_wire(body) -> bytes
adapter.shadow_without_grounding(body) -> dict       # schemaのgrounding field/requiredとappended instructionのgrounding句だけ削除。tokenize-only、生成禁止
probe.bind_suite(authority_without_update, canonical_user_bytes, private_bytes, *, update_requirement) -> SuiteBindingV1
probe.output_schema_suite(authority_without_update) -> dict
probe.validate_suite(raw, suite_binding) -> ProbeResult
runner.prepare(out, baseline_aggregate_path) -> plan
runner.verify(plan) -> RunContext
runner.run(out) -> safe result
quality.combine(result_bytes, annotation_bytes, baseline_bytes, exact hashes) -> safe report
```

adapterとrunner間の最低data APIを次で固定する。

```text
SuiteCaseV1(
  case,                 # read-only canonical Case。runnerは内部を変更しない
  projection,           # read-only PromptProjection
  binding: SuiteBindingV1,
  schema: dict,         # output_schema_suiteのdeep-frozen plain value
  public_metadata: SuitePublicMetadataV1,
)

SuitePublicMetadataV1(
  case_id, ordinal, category, trigger,
  expected_acts, hard_rule_ids,
  is_fixed_question_case, is_legal_none_control,
  update_requirement, unresolved_reason,
)
```

runnerの`candidate_body`は`SuiteCaseV1.projection/binding/schema`だけを使い、schemaを再生成しない。`public_metadata`にrole、semantic rule自由文、prompt本文、EvidenceRef値を入れない。`unresolved_reason`は閉じたenum（初期値`PRIVATE_UPDATE_REQUIREMENT_UNKNOWN`）で、自由文を許さない。adapterはtuple/deep-frozen mapとして返し、Implementer間で別名fieldやdict推測を作らない。

## 3. 32case metadataとsidecar mapping

正本は`tests/fixtures/phase6_conversation_cases.py::cases()`の固定順`G01-1..G16-2`である。adapterは各caseを一度だけ`project_brain_input(..., CONFIG)`し、providerやstate mutationを呼ばずread-onlyに抽出する。fixture内部のinert `DiscussionStateStore.capture`構築は許すが、接続後の`stage/commit/observe_authoritative`は0をspyで確認する。

期待分布をplanへ固定する。

- 32件、16 category×2、ID重複0。
- trigger: `INITIAL_CHAT=4`, `PEER_CHAT=24`, `CO_OPPORTUNITY=2`, `PRE_VOTE=2`, `ABILITY=0`。
- 合法NONE control: `G14-1`, `G14-2`。
- fixed question cohort: `G01/G05/G06/G07/G08/G09/G10/G11/G16`の各2件、計18。

### 3.1 mapping

| SuiteBinding/authority field | source |
|---|---|
| case/category/role/expected acts/rule IDs | `Case`の公開metadata。自由文ruleはprivate promptへだけ保持しpublic resultへ出さない |
| trigger/actor/players/base revision/context | `projection.discussion_capture`とcanonical input |
| full offered options | `case.request.action_context.options`とdecoded canonical input `action_context.options`の全field exact一致を正本にする。新optionを合成しない |
| projected decisions | canonical input `grounding.allowed_decisions`とbound response schemaが実際に表現するdecision branchを照合 |
| projected evidence | canonical input `memory.records[].source`とdescriptor metadata |
| captured evidence | `DiscussionCapture.evidence`のread-only descriptor |
| reaction source/channel | capture trigger sourceと対応descriptor/offered chat channel |
| prior assessments/evidence | captured state read-only assessments |
| max text/UTF-8/proposal bytes | projection limits/config |
| suite authority bytes/hash | `HOST_KEYS - {requires_private_update}` exact mapのcanonical bytes/hash。既存HostBinding bytesと同名・同一値とは主張しない |
| canonical user bytes/hash | `projection.messages[1].content.encode("utf-8")`とhash。decode JSONが`projection.canonical_input`とexact一致 |
| private view bytes/hash | prior assessment viewだけのcanonical bytes |

照合は二層に分ける。

1. **full descriptor層:** request optionsとdecoded canonical input `action_context.options`を全field exact照合し、そのfull mapをhost authorityへ束縛する。chat=`channel`、vote=`valid_targets/target_count/allows_abstain`、ability=`valid_targets/target_count`と既存parameter、CO=`claimed_role_ids`を含むclosed descriptorで、欠落・余剰・値差を拒否する。
2. **decision projection層:** canonical input `grounding.allowed_decisions`と`output_schema_suite`が実際に表現するbranchを照合する。全branchの`kind/option_id`、voteのtarget enum、abilityのtarget enum/count/unique、COのclaimed role enumをexact比較する。MinimalOutputV0 chat branchにchannelは存在しないため、channelをschema照合済みと記録せず、full descriptor層とPEER reaction channel検査だけで保証する。

いずれかの層に不一致があれば`CASE_BINDING_CHANGED`でprovider前停止する。schemaに存在しないfieldを検査済みと偽装しない。

許可EvidenceRefはprojected∩capturedの完全一致だけ。current world値をEvidenceRefへ偽装しない。32caseでcurrent-only grounding、prior assessment、update必要性などが公開metadataから決まらない場合も生成対象から落とさず、`update_requirement=null`と理由enumをSuiteBindingV1へ記録し`APPLICABILITY_UNRESOLVED`にする。

全32件の初期applicabilityは、承認済みpublic metadataだけでfalseを証明できない限りUNRESOLVEDを維持する。provider出力が良好でもCOVEREDへ昇格しない。quality評価とapplicability分類は別列・別分母である。

## 4. exact candidate schemaとgrounding交絡

response schemaは各`SuiteBindingV1`に対してsuite専用`output_schema_suite(authority_without_update)`が返す`MinimalOutputV0`そのものとする。仮のupdate boolを入れない。top-levelは`schema_version/decision/speech_act/grounding/utterance/trigger_detail`だけ。全7 speech、trigger/action matrix、co_report拒否、PEER reaction、CO judgment、PRE_VOTE、OPINION_CHANGE、text boundを変更しない。common fieldsが同じ合法旧authorityに対する既存`output_schema`とのcanonical schema bytes一致をfreezeする。

候補はEvidenceRefをspeech/trigger fieldと`grounding[]`へ重複して出す。この重複はH61以外の生成負荷交絡であるため、次を守る。

- 一回測定schemaでは削除・自動補完・別variant生成をしない。
- 各rowへ`grounding_item_count`, `distinct_grounding_ref_count`, `duplicate_ref_occurrences`, candidate schema bytes/hashを保存する。
- provider前に、同じbodyからresponse schemaのtop-level `grounding` propertyと`required`内の`grounding`、およびfirst systemへ連結した`MINIMAL_V1_INSTRUCTION`のgrounding説明句だけを除いた`shadow_without_grounding`を`/apply-template`と`/tokenize`へ渡し、`full_prompt_tokens`, `shadow_prompt_tokens`, `grounding_prompt_token_delta`を測る。schemaの他部分、original system、user、その他instruction、body parameterはcandidateとbyte同一に保つ。shadowは`/v1/chat/completions`へ送らない。
- token差は表現負荷の記述値で、品質差やH61因果の証明にしない。baselineとの品質差をprivate更新除去だけへ帰因しない。

grounding順序非意味、aggregate上限なし、元fieldごとの既存上限を維持する。

## 5. exact prompt/body/wire

### 5.1 messages

各caseのprovider bodyは2 messagesで固定する。model template SHA-256 `c17a933c26907f0982a96e5cb3b6a5ef393f1722f13558ebda7be039649cb4cd`はsystem roleを先頭messageにだけ許し、後続systemを`System message must be at the beginning.`で拒否するためである。

1. role=`system`、content=`projection.messages[0].content + "\n\n" + MINIMAL_V1_INSTRUCTION`。original system bytesはprefixとしてexactに一回保持し、delimiterはUTF-8のLF×2で固定する。instructionもexactに一回だけ連結する。
2. `projection.messages[1]`のrole=`user`とcanonical user contentをbyte不変で置く。

第三message、後続system、original systemの置換・要約・削除を禁止する。adapterは`build_messages(projection, instruction_bytes) -> tuple[dict, dict]`を純粋関数として一度だけ用い、candidate body、message hash、native検査は同じ戻り値を使う。

固定instructionは次の意味だけを持つ。完全な文字列bytes/hashは実装reviewでfreezeする。

- 今回は`phase6.minimal-output.v0`だけを返す。
- response schemaにないlegacy discussion/update fieldを出さない。
- state/option/player/evidenceはuser canonical data内の値だけを使う。
- assessment/claim/relation/strategy stateを更新・推測しない。
- `grounding`はspeech/trigger内refのpurpose別mirrorで、真偽や公開妥当性を保証しない。
- JSON以外を出さない。

元systemのlegacy完全出力指示との競合は既知交絡としてplanに`legacy_instruction_present=true`を固定する。original systemを文字列置換で除去せず、first system内のprefixとして保つ。candidateの別prompt variantを生成しない。

### 5.2 body

```text
model = fixed Qwen profile basename
messages = 上記2件
response_format = json_schema(strict=true, name="phase6_minimal_output_v0", schema=bound schema)
temperature=0.2, top_k=40, top_p=0.95, min_p=0.05
repeat_penalty=1.0, presence_penalty=0.0, frequency_penalty=0.0
seed=4242026, max_tokens=512, cache_prompt=false
```

serializerは`wire_bytes`（UTF-8、canonical separators、allow_nan false）を一度だけ使う。candidate request bytes/hash、schema bytes/hash、messages bytes/hash、common input hashをplanでfreezeし、run時に再計算一致を必須とする。

### 5.3 native template preflight

prepare/verifyはofflineで全32 bodyのmessage数=2、role順=`system,user`、original system prefix + LF×2 + instruction、user bytes、candidate/shadow request hashと構造だけを検査し、serverへ通信しない。

run claimのdurable取得後、runnerがowned providerを起動してruntime/listener ownershipを確認する。従来の各row順序を維持し、そのrowのgeneration claim取得前にcandidate/shadowを`/apply-template`と`/tokenize`へ渡す。成功、rendered prompt内のoriginal system prefix・該当instruction・canonical user bytesが各exact 1回、token/context gateを検査してからrow claimへ進む。後続system、欠落、重複、順序差、template errorは`TEMPLATE_INVALID`として当該row generation=0・row claim=0でrunを停止する。全32キャッシュpreflight、plan後書き、新artifactは追加しない。

frozen-v1の停止はcall許可を消費していない。同一bytesでretryせず、本deltaを反映した新request/message/rendered hashes、新commit、新freezeに対するdesign reviewとtool delta review承認後だけ初回32 generationへ進める。instruction内容、model、schema、32case、sampling、token上限は変更しない。

## 6. provider上限・token/context gate

- model profile: `qw9`一つだけ。
- provider generation: 32件×1回、最大32 calls。retry=0、repair=0、fallback=0。
- 一度call slotを消費記録してからnetwork dispatchする。timeout、HTTP、parse、validation失敗も消費済みで再試行しない。
- completion上限: 各512、総上限16,384 tokens。
- context: 8,192。`actual_prompt_tokens + 512 + 1 <= 8192`を全rowで満たす。
- request 60秒、model run 1,200秒、load 180秒。run deadlineで未開始rowは`MODEL_TIME_BUDGET/NOT_STARTED`だが分母32に残す。
- `/apply-template`, `/tokenize`, health/runtime確認はgeneration callに数えない。endpoint allowlistを固定する。
- 各rowのnative rendered prompt bytes/hashとactual token countを既存private evidenceへ保存し、usage prompt/completion tokensと照合する。token mismatchはrow FAIL、retryなし。

### 6.1 排他的run/row claim

run開始時に`<out>/minimal-output-v1.run.claim`をcreate-new (`open(..., "xb")`)で取得する。markerは`task_id, experiment, runner, plan_sha256, run_id`のcanonical JSON。write→flush→`os.fsync`→close→readback byte一致の後だけprovider processを起動できる。既存run marker、readback不一致、fsync失敗はdispatch 0で全体停止し、上書き・削除・再利用しない。

各caseはdispatch直前に`<private>/<case_id>.generation.claim`を同じcreate-new手順で取得する。内容は上記run identityに加え`case_id, ordinal, request_sha256, wire_sha256, schema_sha256`を束縛する。readback一致後にpublic safe rowへ`call_consumed=1`をdurable保存し、その後だけ`/v1/chat/completions`を呼ぶ。既存markerなら当該row dispatch 0でrunを停止する。失敗・cancel・timeout後もmarkerを削除せず、同じrun/caseを再callしない。

focused raceは同一out/privateへ二processを同時進入させ、run claimまたはrow claimのexact一方だけが成功し、generation transport観測が最大1であることを確認する。markerはplan/run/case/request hashが異なる再開にも流用せず、新out/run identityなしの再開を禁止する。

## 7. freeze・runtime・process所有

新runnerはGB1の過去CI/approval chainを流用しない。`phase6_model_comparison`から限定再利用するのはlaunch args、file identity、request/tokenizer/runtime、private evidence、owned process cleanup primitivesである。baseline annotationを読む`baseline_binding`は呼ばない。

既存`phase6_two_stage_probe_runtime.stage`はP2/IC2/SC2/GB1/GC2 contract完全列挙を正本とし、minimal contract追加のために変更しない。minimal runner内へ一回candidate専用のbounded stageを局所実装し、順序を`consume durable marker → /v1/chat/completions 1回 → raw private write → strict validate → safe row`へ固定する。既存frozen observable bytesへ触れない。`phase6_probe_outer.main`のtask列挙も変更せず、minimal runner自身の`--supervise`入口が既存`phase6_probe_outer.supervise`を呼ぶ。base transport/count_prompt/runtime/cleanupとouter superviseは未変更で再利用し、専用stageだけをtool review対象に含める。

planは次をexact freezeする。

- HEAD commit、clean relevant source diff、runner identity、task/experiment=`minimal_output_v1`。
- adapter/runner/quality/probe、fixture、focused tests、projection/decision/model types、suite source、context/wire/monitor helpersの明示source path/hash。wildcardなし。
- config path/hash=`C:/AIagent/agent/config.toml` / `43e509956d96492cace8393bdc3fd598ab2418315ccdfd82e144b876241f3bab`、tokenizer path/hash=`C:/AIagent/llama-tokenize.exe` / `a0fbd34a8a3f25fc0f41cbac1ec67e8395a5ef940db33bd07307b5e3dc8cd6a1`。
- model absolute path/size=`C:/models/Qwen3.5-9/Qwen3.5-9B-Q4_K_M.gguf` / `5,680,522,464` / SHA-256 `03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8`、quantization=`Q4_K_M`。
- llama-serverと全runtime DLLのpath/size/hash。server SHA-256=`3c21330df1049f49a11e08e695be84c1138886a5effe5fc702a3b08c8f0e5b8a`、runtime/build=`b10697-093adb242`、template SHA-256=`c17a933c26907f0982a96e5cb3b6a5ef393f1722f13558ebda7be039649cb4cd`、slot/context=`1/8192`をplan時実体と照合する。
- launch argv、context、sampling、timeout、endpoint allowlist、template/runtime identity、32 request/wire/schema/message hashes。
- T492/T495/tool review/design reviewのpath/hash。
- fixed baselineは`C:/AIwolf/logs/t424-model-comparison/frozen-v2`に固定する。`plan.json` SHA-256=`c878456dc15b00b87549a8b91d29eba8ef64e63cb7cc972da1eff40942f4c1c4`、`t427-safe-summary.json` SHA-256=`4557f7a43069b8cf1a3c4990590e828ef337f35599551fb7646f8ed51fda93a0`、集計正本`Docs/ai/handoffs/tasks/T478_GROUNDING_BASIS_QUALITY.md` SHA-256=`f4c17f6e148d6df8df215318ba686d81587491269d28a1bc6083f132883ce34e`と期待数値を照合する。旧`qw9-results.json`/annotation/rawをopenしない。

prepare時とrun直前にsource/config/model/runtime hashを再照合する。port 8082 free、対象server/tokenizer processなしを要求する。runnerが起動したprovider/monitor PIDだけを所有し、listener PID一致、slot=1、context=8192、idleを確認する。外部processへattach/killしない。finallyで所有processを停止し、listenerなし・owned remaining=0を必須とする。cleanup不明はrun全体FAIL。

## 8. private/public境界と結果row

private evidence containerへだけ保存するもの:

- raw candidate bytes、request/wire、canonical input、rendered prompt、server/monitor logs。
- candidate text、private role/state/evidence values、provider error detail。
- 独立評価者が読む新candidate本文。

public result rowは固定safe fieldだけ:

```text
case_id/category/trigger, status, fixed error enum,
call_consumed/provider_calls, input/schema/wire/raw hashes and byte counts,
prompt/completion tokens, grounding counts/token delta,
mechanical structure/authority/binding/text flags,
applicability = COVERED|INVALID|UNRESOLVED,
semantic fields = null until independent evaluation
```

自由文、秘密値、private path、tracebackをpublic JSON/handoffへ出さない。locatorは既存private container契約に従う。run終了後もrawは再表示せず、独立評価の一回読取へ引き渡す。

## 9. independent semantic evaluation interface

評価者は新minimal candidate 32件だけを一回読み、旧baseline/annotation/rawを読まない。annotation schemaはcase IDとresult/raw hashへ束縛し、本文を保存しない。

各rowは既存D077/T478と同じ層を分ける。

- HARD: structural mechanical resultとsemantic HARDの三値AND。
- SEMANTIC、STYLE、act/text mismatch。
- fixed reasons: fabricated evidence、secret disclosure、state contradiction、ability contradiction、UNKNOWN、question response、self/peer copy等の閉集合。
- fixed 18 question cohort、G14-1/G14-2 NONE controlをexact IDで集計。
- grounding supportとgrounding存在を別booleanにする。
- applicability UNRESOLVEDをsemantic UNKNOWNへ自動変換しない。逆も同じ。

annotationは`PASS/FAIL/UNKNOWN`、固定reason、booleanだけ。strategyの好みをHARDへ追加せず、合法な騙り/CO/非開示を維持する。quality composerはresult/annotation/case IDs/hashがexact一致しない限りfail closedする。

## 10. fixed baselineと採否gate

baselineは保存済みsafe aggregateだけをfreezeし、再計算しない。

| metric | fixed baseline | candidate gate |
|---|---:|---:|
| HARD FAIL | 14 | <=14 |
| SEMANTIC PASS | 7 | >=7 |
| STYLE PASS | 15 | >15 |
| act/text mismatch | 23 | <23 |
| fabricated evidence | 0 | 0 |
| secret disclosure | 5 | <=5 |
| state contradiction | 3 | <=3 |
| ability contradiction | 0 | 0 |
| quality UNKNOWN | 0 | 0 |
| fixed 18 question answers | 9/18 | >=9/18 |
| legal NONE controls | 2/2 | 2/2 |
| independent meaning-evaluation copy rows | 4 | <=4 |
| mechanical peer long exact copy | baseline未測定 | absolute candidate gate=0 |

採用候補となるには全個別gate、32 generation slotsの終端記録、raw/hash完全性、source/runtime不変、owned cleanup、annotation 32/32を同時に満たす。改善指標でsecret/NONE/copy等のFAILを相殺しない。

baseline copy row=4は`Docs/ai/PHASE6_MODEL_COMPARISON_20260918.md`のQwen3.5-9B固定比較表にある独立意味評価reasonであり、機械peer exact screenとは別指標である。baselineの機械peer exact値は既存safe artifactで分母付き観測を証明できないため0と記載しない。candidateは`screen_executed_count`と`peer_long_exact_copy_count`を別保存し、未実施rowを0へ数えず、実施分に1件でもあればabsolute gate FAILとする。

これはtest-only candidateの採否であり、製品採用ではない。grounding重複、final system instruction、legacy instruction残存、主観update非推測の交絡があるため、PASSしてもH61単独因果を確定しない。UNRESOLVEDは32分母に残し件数を報告するが、品質UNKNOWNと混同しない。

## 11. failureと一回消費

固定error enum: `PLAN_EXISTS, SOURCE_CHANGED, CONFIG_CHANGED, MODEL_CHANGED, RUNTIME_CHANGED, CASE_BINDING_CHANGED, CONTEXT_OVERFLOW, REQUEST_TOO_LARGE, TEMPLATE_INVALID, TOKEN_MISMATCH, MODEL_TIME_BUDGET, HTTP_ERROR, RESPONSE_TOO_LARGE, OUTPUT_INVALID, PRIVATE_EVIDENCE_ERROR, CLEANUP_ERROR`。

- plan/verify/preflight失敗はprovider call 0で全体停止。
- row dispatch前にatomic consume保存。その後の全失敗はそのrow call=1、retryなし。
- parse/strict validation失敗もraw/hashをprivate保存し、publicは固定errorのみ。
- process crash/timeout後も未開始rowをNOT_STARTEDとして32 rows保持する。
- provider起動後の不変性/cleanup失敗は品質値に関係なくrun不採用。

## 12. focused matrix

| group | 必須検査 |
|---|---|
| adapter totality | 32 IDs/order、trigger count、NONE/question cohort、no drop、SuiteBindingV1 null→UNRESOLVED、既存HostBinding null拒否 |
| mapping | authority bytesとcanonical user bytesの別identity、user decode↔canonical input、full action options↔allowed decisions↔schema、chat/vote/ability/CO descriptor正負、player/ref intersection、actor/channel、prior、limits、input非変更 |
| schema/prompt | MinimalOutputV0 exact keys、7 speech、5 trigger contracts、2 message `system,user`、original system prefix + LF×2 + fixed instruction、user byte不変、各要素exact 1回、後続system拒否 |
| native template | prepare/verify通信0、全32の2-message構造offline検査、run claim後owned serverで各row claim前にcandidate/shadow apply-template/tokenize、rendered要素exact 1回、template error時当該row generation 0・row claim 0、全32 cache/artifactなし |
| grounding confound | duplicate counts、order independence、shadowはschemaのgrounding property/requiredと連結instructionのgrounding句だけ削除、schema他部分・original system prefix・user・他body parameter byte不変、tokenize only、generation endpoint禁止 |
| freeze | source/config/model/server/DLL/tokenizer/design/review/baseline hashes、wire/schema/message hashes |
| lifecycle | exclusive run/row create-new claim、fsync/readback、二process race最大1 dispatch、port/process ownership、call consume before dispatch、32 max、retry/repair 0、deadline/partial rows |
| token | full/shadow template+tokenize、8192 gate、512 cap、usage一致 |
| private | raw/text/path非漏洩、locator、error sanitization、private write failure |
| quality | annotation hash binding、32/32、三値AND、18 question、NONE2、全個別gate |
| negative | legacy completion/default、state推測、co_report許可、validator緩和、old raw/annotation openを拒否 |

focused後のtool reviewは、runner source/hash、fake transportでgeneration最大32、failure consume、private/public出力、freeze差替え拒否を独立確認する。実providerはtool review承認済み同一SHAだけを一回実行する。

## 13. 独立review範囲と未解決

独立Reviewerは、本deltaの2-message native互換、original system prefix/user byte不変、shadow差分限定、pre-claim停止、三値sidecar拡張、32 mapping、prompt競合、grounding重複shadow、one-call/token上限、freeze/process/private境界、baseline gate、評価interface、failure consumeを確認する。

未解決は、公開metadataだけではprivate update不要を証明できない32 rowのapplicabilityである。これはUNRESOLVEDのまま測定可能だが、製品採用判断には使えない。詳細reviewが別の公開・決定的分類規則を承認しない限りfalseへ補完しない。

設計承認だけではprovider実行を許可しない。改訂SHAの独立design review後に限定実装し、focused testと独立tool delta reviewが新source/request/freeze SHAを承認した後だけ、未消費の初回provider 32件を実行できる。
