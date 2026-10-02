# Phase 6 resolved subject plan-locked probe 詳細設計

Status: APPROVED
Task: T564
Contract: `PHASE6_RESOLVED_SUBJECT_PLAN_LOCKED_V1`

## 1. 目的・非目的

保存T550 v2のmessage planを固定し、message user payloadの `plan.subject_player_id` だけを、同じ選択bindingが指すcanonical player IDへ置換する一因子counterfactualを測る。opaque literalの有無は文字列観測にすぎず、意味整合、品質改善、原因をhostが断定しない。

製品presenter/schema/model/sampling/game/provider実装は変更しない。計画段、CO、非chatを再生成せず、旧raw/hash/annotationを変更・再採点しない。controlは保存T550 v2 realizationであり、canonical v1 baselineではない。

## 2. 固定母集団と状態

T550の96 `(case_id,seed)` を保存順で固定する。排他的状態は次のとおり。

| 状態 | 行数 | 新generation |
|---|---:|---:|
| `ELIGIBLE_PLAN_LOCKED` | message planあり77 | ordinal 1、必要時のみ2（最大154） |
| `PLAN_NOT_OBSERVED` | chatだがplanなし7 | 0 |
| `NOT_MESSAGE_DOMAIN` | non-chat12 | 0 |

旧message requestは88件（ordinal 1=77、ordinal 2=11）、旧outcomeはACCEPTED69 / GUARD_REJECT19として別集計する。これを新結果へ移植しない。新行statusは `ACCEPTED | GUARD_REJECT | STRUCTURE_INVALID | LENGTH | MISSING_RESPONSE | TRANSPORT_GENERATION_LOST | TRANSPORT_OR_OWNERSHIP | CONTEXT_INVALID | INPUT_NOT_CHANGED | PLAN_NOT_OBSERVED | NOT_MESSAGE_DOMAIN`。77行はordinal 1で受理時終了、非受理時だけordinal 2を送る。77結果を必ず作り、未送信ordinalを成功扱いしない。

## 3. custody入力とpreflight

新private containerへ、T550 source manifest/seal、保存request/outcome/plan/final locator、fixture source/bindings/catalog、runner/probe/product schema source snapshotをcreate-onlyコピーする。各fileはopaque `source_id`、original member name、size、whole SHA-256をmanifestへ保存する。Mainが別系統に固定したexpected manifest/preflight SHAを必須引数で受け、開始・各generation直前・終了に全fileとcurrent code SHAを再照合する。入力自身が算出したhashを承認値にしない。

preflightはexact counts `rows=96, eligible=77, plan_missing=7, non_chat=12, saved_message_requests=88, old_ordinal1=77, old_ordinal2=11, max_generation_calls=154, budget=237, context=8192`、model/profile/config/runtime identity、source/code SHA、旧T550 run/seal identity、新run identityを含む。重複case/seed、別row plan、欠測、hash/type差、非finite値は全体停止する。

各eligible行で次を機械照合する。

1. 保存planはaccepted `chat_plan` exact bytesで、現v2 schemaに通る。plan hashを固定する。
2. `subject_player_id=null` はnullのまま。それ以外はfixture binding exact一件、`source_kind=PLAYER`、pointer解決scalar nonempty player ID、source/projection/value SHA、actor/channel/authorityを既存 `verify_bindings` で照合する。
3. Main診断の88/88 dereferenceは参考値であり、実装が同じsourceから再証明する。
4. 保存requestは現 `q.body('message', fixture, derived_seed(...), 237, plan)` と88/88 exact一致する。保存ordinal 2はseedだけが既定差分である。

## 4. request変換

各行のcontrol rootは保存ordinal 1 request bytesであり、再生成しない。candidate ordinal 1はcontrolをstrict duplicate/nonfinite拒否でparseしsubject pointerだけを置換する。candidate ordinal 2は同じ保存ordinal 1 outer/inner bytesを基底に、subject pointerとouter `/seed` だけを置換する。seedは既存 `derived_seed(seed,'message',2)` のexact値であり、planを再生成しない。保存ordinal 2が存在する11行では、ordinal 1へseed差だけを適用したcontrol bytesが保存ordinal 2とexact一致しなければ全体停止する。残る66行はこの検証済み変換規則で初めてordinal 2を構成する。

```text
/messages/1/content  (JSON string) 内
/plan/subject_player_id
```

inner user payloadもstrict parseし、`schema_version="t550.p-input.v2"`、既存presenter exact keys、plan exact keysを検査する。非null opaque IDを§3で解決したcanonical scalarへ置換し、outer/innerの全他key、型、配列順、値をtyped recursive comparisonでexact一致させる。nullはbyte変更0であるため、因子差がない行は `INPUT_NOT_CHANGED` としてproviderへ送らずeligible母数内UNKNOWNへ保持する。canonical値がopaque値と偶然同じ場合も同様。

candidate requestはsystem、response schema、temperature `0.7`、top_p `0.9`、max_tokens `237`、stream false、thinking falseをcontrolとexact一致させる。ordinal 1の変更pointerはsubject一件、ordinal 2はsubjectとseedの二件だけ。field追加・削除・並替え、plan hash差、subject object/provenance変更があれば停止する。各再構成request SHAと、Mainが保存ordinal 1、保存ordinal 2の11件、source bindingから別計算したexpected SHAをpinする。

subjectがnullまたはopaque値とcanonical値がtyped exact同値なら行statusは `INPUT_NOT_CHANGED`、reasonは同名、attempt列は空、generation calls 0、bindingにはplan/source/subject equality SHAだけを保存する。metricは `UNKNOWN / INVALID`、blind annotation対象外であり、77対象・全96分母とsafe countには残す。control本文をcandidate本文として補完しない。

## 5. native countと有限実行

generation前に所有runtimeを起動し、identity/profile/model/config/context `8192`、listener/process object/snapshot/source SHAを既存T550 runnerと同じ条件で固定する。utility `/tokenize` / `/apply-template` は有限preflightだけに使いgeneration countへ含めない。全candidate requestについてnative input countとrendered SHAを保存し、`input_tokens + 237 + 1 <= 8192` を要求する。count中の切断、identity/source/owner drift、非finite/不一致はgeneration前停止。

実行は77行×最大2 ordinal、generation call上限154。probe generation、planning、CO、retry runは0。`RequestCache` 相当のimmutable cacheを新contract内で作り、keyは `(case_id,seed,'message',ordinal,plan_sha256,contract)`。body送信済requestは再送しない。idle closeは次call前recheck、未送信callだけ新transportへ移すT550承認済み境界を再利用する。disconnectしたgenerationは専用failure attemptとし次ordinalへ進み、同requestを再送しない。deadline、generation count、row countの超過は停止する。

responseは現message schema parse、usage/native output count、budget、`text_guard`をそのまま使う。HARDはwire/schema/hash/context/ownership/transport integrity、SEMANTICはfresh reviewerによる意味評価、STYLEはcopy/completeness等に分離し、guard failureを意味FAILへ変換しない。

## 6. 保存、cleanup、公開値

新run ID、claim、private root、sealをT550と異なる固定identityにする。claimはapproved design/tool/source/preflight/model/profile/runtime、旧T550 seal、最大154、provider一回許可を束縛する。output path存在時は開始拒否する。

各attemptでrequest/input-count/response/outcome/clockをcreate-only保存し、全96行resultには非対象19行も明示する。例外はpayload/pathを含まないclosed codeへ変換する。終了時は成功・失敗を問わずruntimeを所有process objectで停止し、listener 0、owned process終了、snapshot/source/code再hash、seal作成を行う。cleanup失敗はintegrity falseでCOMPLETEにしない。

公開safe resultはstatus、hash、96/77/7/12、generation calls `0..154`、各closed status count、native count範囲、runtime cleanup、provider call countだけを含む。本文、subject ID/value、condition mapping、private path、seed別結果は公開しない。

## 7. 別version blind評価

新rubric IDは旧S4等と混ぜない `RESOLVED_SUBJECT_PLAN_LOCKED_V1`。controlは保存T550 v2 accepted realization、candidateは新accepted realizationだけをprofile-neutral blind IDへ変換する。condition/model/seed/run order/latency/token/raw plan/opaque mappingをEvaluatorへ渡さない。pair可能なのは両conditionで本文が観測された行だけ。candidate未観測や `INPUT_NOT_CHANGED` をcontrol本文で補完しない。

Evaluatorには両condition共通の `ChosenIntentObservationV1` を一度だけsealed提示する。JSON canonical wireはUTF-8、key辞書順、compact、nonfinite拒否。型は次のexact shapeで、記載外keyを拒否する。

```text
ChosenIntentObservationV1:
  case_alias: "case_" + lowercase hex 32文字
  act: "ANSWER"|"REBUTTAL"|"QUESTION"|"CLAIM"|"OPINION_CHANGE"|"NONE"
  subject: nonempty canonical player-id str|null
  reply: ReplyObservationV1|null
  public_trigger: PublicTriggerObservationV1
  current_public_state: CurrentPublicStateObservationV1
  observation_sha256: lowercase hex 64文字

ReplyObservationV1:
  source: {record_kind:"chat", order:strict int>=0, visibility:"PUBLIC"}
  actor_player_ids: [nonempty player-id str,...]  # source順、重複なし
  channel_id: nonempty str
  day: strict int>=0
  phase: nonempty str
  text_excerpt: str
  text_truncated: bool

PublicTriggerObservationV1:
  kind: "INITIAL_CHAT"|"PEER_CHAT"
  day: strict int>=0
  phase: nonempty str
  source: null|{record_kind:"chat",order:strict int>=0,visibility:"PUBLIC"}

CurrentPublicStateObservationV1:
  alive_player_ids: [nonempty player-id str,...]       # source順、重複なし
  day: strict int>=0
  phase: nonempty str
  players: [{player_id:nonempty str,alive:bool,
             death:null|{day:strict int>=0,public_cause:nonempty str}}, ...]
  vote_candidate_player_ids: [nonempty player-id str,...] # source順、重複なし
```

`observation_sha256` は自身を除く外側6 fields `{case_alias,act,subject,reply,public_trigger,current_public_state}` のcanonical hash。`case_alias` はcryptographic randomで全96一意、condition、case ID、seed、順序から導出しない。association private recordは `{case_alias,row_identity_sha256,plan_sha256,source_sha256,subject_binding_sha256,reply_binding_sha256|null,observation_sha256}` exact型で、同じalias/observationをcontrol/candidate双方へ一度だけ結ぶ。

`act` は保存plan `/act` の明示値。`subject` は `/subject_player_id` がnullならnull、それ以外はそのIDのexact binding pointerが解決するcanonical scalar。`reply` は `/reply_to` がnullならnull、それ以外はexact bindingが指すPUBLIC chat recordから上記7 fieldsだけをsource順で写す。`public_trigger` はfixture `/capture/trigger/{kind,day,phase,source}`、`current_public_state` は `/grounding/current` の上記5 fieldsを型・順序・null込みでlosslessに写す。これらのsource pointer集合は固定で、実装者がrubricごとにfieldを選ばない。

全binding/source/plan hashをcustodian-privateに保持し、Reviewer viewへraw plan、opaque ID、owner private truth、strategy、ability、非公開recordを出さない。hostは本文、alias、strategyからrefや意図を生成しない。subject/reply/trigger/stateのmissing、複数binding、private visibility、型・順序・hash不一致は関連metricをUNKNOWNにし、別sourceから補完しない。

全96分母を維持し、77対象の `PASS/FAIL/UNKNOWN/MEASUREMENT_NOT_OBSERVED`、planなし7とnon-chat12の `NOT_APPLICABLE` を明示する。`INPUT_NOT_CHANGED` はUNKNOWN/INVALIDとしてannotation対象外のまま数える。act/message整合、本文回答、source state矛盾、copy、text completenessを別metricにし、subject literal出現や非NONE数をPASSにしない。chosen intentで観測できないfabrication、ability、秘密、権威一般を採点せず、本結果を総合HARD改善や製品採用へ拡張しない。単純literal regexで採点しない。annotationは一回freeze後にunblindする。旧annotationを読まず、旧69 ACCEPTEDを新candidate成功へ変換しない。

## 8. testと実行gate

公開syntheticで以下を検査する。

- 96行partitionと88保存request metadata、77×2最大154、ordinal 1受理時のcall抑制。
- null、opaque→canonical正例、同値で `INPUT_NOT_CHANGED`、missing/duplicate/wrong-kind/cross-row binding。
- 保存ordinal 2あり11件のordinal 1＋seed変換exact一致、未保存66件の同規則構成、seed/subject以外の一byte差拒否。
- outer/inner duplicate/nonfinite、唯一pointer以外の差、順序差、plan/source/code/seal/Main pin tamper。
- native count全candidate、8192境界、budget237、seed ordinal、cache drift、送信済再送0。
- ACCEPTED/各reject/切断/owner drift/deadline、全96結果、create-only、例外非漏洩、cleanup listener/process 0。
- blind pair欠測、UNKNOWN/MNO/N/A、condition漏洩、annotation一回freeze。
- `INPUT_NOT_CHANGED` のattempt 0/call 0/UNKNOWN INVALID/blind除外/safe countと、誤送信・control本文補完拒否。
- chosen intentのsource-bound subject/reply/public state正例、missing/ambiguous/private refのUNKNOWN、raw plan/condition漏洩拒否。
- 各nested型のextra/missing/null/type/order/literal、self-hash、alias重複・condition由来、control/candidateで異なるobservationの拒否。

Python 3.13/3.10 focusedとT550 generation/reconnect関連回帰を通し、独立design APPROVED、独立tool APPROVED、Main source/preflight pin、owned runtime空、private ACL、有限utility検証が全て成立した後だけproviderを一回実行する。一つでも不成立ならprovider 0で停止し、保存artifactを変更しない。

## 9. counterfactual限界

このprobeは保存planの選択を固定した発話表現だけを比較する。planningとの相互作用、全pipeline採用、一般的な代名詞解決、モデル能力、Phase 6総合品質を推定しない。結果が良くても製品presenter変更は別のproduct design/acceptanceを要する。

## 独立承認記録

T564 content SHA `191123e823b5a0472e16ec81bf5d017fc97538a2d127ff78594aca21116f35e5` を別ReviewerがAPPROVED。report SHA `1ac17c1db26fcba3ee442880512a7f294c107236bbf2e04e8ae31b848c691f3b`。Mainはstatus/承認記録のみ更新し、設計自己承認していない。
