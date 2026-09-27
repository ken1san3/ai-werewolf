# Phase 6 生成契約 v2 詳細設計

Status: APPROVED

作成日: 2026-09-27
対象: `Docs/ai/tasks/T512_PHASE6_REWRITE_PROGRAM.md` のWP2
入力: `PHASE6_REWRITE_BASIC_DESIGN.md` §2・§7、`F009_PHASE6_DESIGN_TRAPS.md` K1〜K8
機械可読schema: `Docs/ai/design/PHASE6_GENERATION_CONTRACT_V2_SCHEMAS.json`

## 1. 決定の要約

v2は、chatを計画段Tと発話段Pへ直列分割し、`PRE_VOTE`、`CO_OPPORTUNITY`、`ABILITY`を
それぞれ専用の1段schemaにする。モデルが生成する値は、候補ID・有限enum・有限scoreと、Pの本文だけである。
候補の実値、権威イベント、送信先、状態revision、actionはhostが解決する。

次を基本設計から明確化または修正する。

1. speech actは**計画意図の記録**であり、本文の意味を証明しない。actと本文の一致、安全性、返信の実質は
   診断・安全検査に残す。
2. `OPINION_CHANGE.prior` はhostが本文から推測しない。capture時点でcommit済みの主観状態を
   `opinion_basis` として候補化し、モデルがそのIDと新値を選ぶ。hostは候補のpriorをそのまま複写する。
3. v2の記録から `source_interpretation` を除く。自分がANSWERを選んだことから、相手の発言がQUESTIONだったと
   推定しない。構造化された権威イベントに発話種別がある場合だけ別のevent metadataとして保持する。
4. `claim_updates` を、権威イベントから導出するclaimの**存在**と、`PRE_VOTE`で生成する主観的な
   `claim_assessments` に分離する。騙りの自己主張は権威ある能力結果と別namespace・別provenanceにする。
5. `relation_updates` と `strategy_update` はv2第一版では生成もstate更新もしない。v1 profileの既存値を
   v2が上書きしない。必要なら別trigger・別設計で戻す。
6. 仮の64/128〜160 tokenを採用しない。provider raw grammar、自由文字列、JSON escape、tokenizer全pipeline、
   EOS/specialを含む上限証明を§11に定める。

v1/v2はprofileで併存し、既定はv1のままとする。protocol v1.2、server/game規則、role定義、validator・
authority・privacyの緩和は対象外である。

## 2. データの分類と不変条件

| 種別 | 正本 | モデルの権限 | stateでの扱い |
|---|---|---|---|
| authority event | serverからcaptureされたCO宣言、CO報告、投票、能力結果、chat delivery等 | 候補から参照のみ | occurrenceとして追記。真偽評価と混ぜない |
| authorized fact | authority eventをclientの知識範囲へprojectionしたもの | ID選択のみ | EvidenceRefへ解決 |
| subjective assessment | suspicion、credibility、claim verdict | `PRE_VOTE`で有限値を選択 | revision付きでatomic更新 |
| generation intent | act、返信先、対象、選んだ根拠 | chat計画段で選択 | auditとintent record。意味の事実にしない |
| message text | 公開または私的channelへ送る本文 | 発話段だけが生成 | delivery結果とhashをaudit。本文はprivate保存 |

共通不変条件は次のとおり。

- 出力IDはそのcaptureで提示済みのcatalogに存在し、capture外へ持ち越さない。
- player、role、ability、teamの実IDはcatalogの4文字IDからhostが解決する。role名をPython分岐へ書かない。
- 段の入力はallowlistで新しく組み立て、canonical input全体をPへ渡さない。
- private情報を「書かないよう指示」して防がない。対象段の入力から除く。
- state revision・authority snapshot・channel recipient proofのどれかが変われば結果をcommitしない。
- schema合格は意味合格ではない。本文guardと独立意味評価を維持する。

## 3. Captureとcatalog

### 3.1 GenerationCaptureV2

1判断の全段は、開始時に作った同一のimmutable captureを使う。

| field | 内容 |
|---|---|
| `capture_id` | game/player/trigger内で一意 |
| `base_revision` | `DiscussionStateStore` のcommit済みrevision |
| `trigger` | kind、day、phase、source event ref |
| `world_version` / `last_applied_sequence` | capture時のserver worldと受信event列 |
| `fact_revision` | authorized fact projectionのrevision |
| `phase_identity` | game/day/phaseを一意にするidentity |
| `action_generation` | offered action集合のgeneration |
| `connection_generation` | 現在のserver connection所有世代 |
| `channel_authority` | server由来のchannel id、公開/私的種別、recipient集合、authority revision |
| `recipient_proof_sha256` | 上記recipient集合とauthority revisionのcanonical hash |
| `catalog_sha256` | §3.2の全catalogとpointerのhash |
| `option_catalog_sha256` | option、valid target、target count、abstain条件のhash |
| `input_sha256` | allowlist入力、schema profile、configのhash |
| `lease_id` / `expires_at_monotonic` | §8のadmission lease |

`channel_authority` は表示名、本文、role推定から作らない。server-authorized projectionにrecipient集合の証明が
無ければ、私的channelのcapture作成を `PRIVATE_RECIPIENTS_UNPROVEN` で拒否する。

### 3.2 IDと上限

全IDはASCII 4文字の段内IDで、実値はpointerで参照する。

| catalog | ID | 最大 | 決定的な順序 |
|---|---:|---:|---|
| replies | `r000` | 6 | `PEER_CHAT`のtriggerを先頭、残りをday/channel一致の新しい順 |
| players | `p000` | 32 | canonical player order |
| public facts | `f000` | 48 | authority sequence、fact kind、source identity |
| disclosures | `d000` | 16 | authority sequence、fact kind、source identity |
| utterance claims | `c000` | 32 | self-asserted option identity |
| observed claims | `k000` | 32 | authority sequence、source identity |
| opinion basis | `u000` | 64 | player order、dimension |
| action options | `o000` | 32 | input option order |
| claimed-role options | `q000` | 32 | selected CO option内のcontent order |

上限超過時に必要候補を黙って落とさない。決定的priorityで切った後にPF2を実行し、期待行為またはtriggerの
必須候補が欠ければ `CATALOG_INCOMPLETE` で生成前に停止する。`PEER_CHAT`のtriggerは必ず`r000`であり、
権限検査に失敗したtriggerを代替の履歴で置換しない。

### 3.3 返信・事実・開示

reply entryはpointer、author player ID、channel ID、authority event identity、`is_trigger`、
構造化metadataが存在する場合だけそのspeech kindを持つ。本文解析で`addressed_to_me`やkindを推定しない。
protocol v1.2の宛先が無い間、`PEER_CHAT` triggerは返信候補にできるが「自分宛て」とは記録しない。

public factはserver-authorized projectionの公開情報だけである。能力結果のcanonical EvidenceRefは
`read_visibility=AUTHORIZED_PRIVATE`のまま保持し、公開既知factへ再分類しない。

明示開示はread authorityと別の`DisclosureAuthorityV2`で表す。第一版で作れるのは、serverから本人へ
authoritativeに通知されたABILITY_RESULTについて、actorがself、source event identity・対象・結果・
capture revisionが一致する`SELF_ABILITY_REPORT`だけである。これは本人が自分の能力結果をchatで明示報告できる
既存境界を候補化するもので、他者がその結果を既知であることを意味しない。default denyとし、role/team/仲間ID、
別playerのprivate result、任意private memoryを汎用開示へ入れない。

送信先recipient集合をR、候補が明示開示を許すaudienceをAとすると、私的channelでは `R ⊆ A` を全件で証明する。
public channelへの`SELF_ABILITY_REPORT`は明示的なPUBLIC送信authorityを持つ候補だけを解決する。
`DisclosureAuthorityV2`はclient projection層がserver-authorized personal ability recordと既存chat permissionから
決定論的に作る新しい型で、server protocolやEvidenceRef visibilityを書き換えない。authority kind、source event、
actor、audience、world/fact revisionをhashへ含める。prompt指示からauthorityを作らない。
未選択disclosureの値はPへ渡さない。
private teammateの表示identityは、server projectionの`knows_teammates`と全recipient proofが一致するchannelでだけ
channel contextとして渡せるが、汎用disclosure候補にはしない。team/roleの内部値は渡さない。

### 3.4 権威結果と騙り

真の結果開示と騙りの自己主張を同じcatalogへ入れない。

- `disclosure_catalog`の`AUTHORITATIVE_SELF_RESULT`: read visibilityはAUTHORIZED_PRIVATEのまま、
  `SELF_ABILITY_REPORT` disclosure authority、`d...`、authority event refを持つ。
- `utterance_claim_catalog`の`SELF_ASSERTED_CO_NARRATIVE`: 既存CO option/contentが許す自己主張。
  `c...`で選び、authority result refを持たず、
  claimed-role optionと表明対象を参照する。
- `observed_claim_catalog`の`PUBLIC_OBSERVED_CLAIM`: 他者の公開CO宣言・報告。`k...`で参照し、
  自分の新規claim候補ではなく、返信・事実・
  `PRE_VOTE`の評価対象としてだけ使う。

`claim_id`はself-assertedだけ、`disclose_id`はauthority resultだけなので、モデルに両者の整合を生成させない。
hostは選択disclosureからauthority claim表現を導出し、騙りには実結果のEvidenceRefを付けない。
Pには選ばれたclaim/disclosure surfaceとprovenanceだけを渡し、未選択の真の結果は渡さない。audit/stateでも
`SELF_ASSERTED`を`AUTHORITATIVE`へ昇格させない。

## 4. 正確な生成schema

機械可読な代表解決済みschemaは companion JSON を正本とする。製品のschema factoryは同じbranch templateへ
captureのenum/constを束縛する。opinion basis、CO option、ability optionは候補ごとにbranchを反復する。
全objectは`additionalProperties:false`、全fieldを`required`とし、非該当値は省略でなく`null`または空配列にする。

### 4.1 Chat計画段T

キー順は次の10個で固定する。

`reply_to, act, subject_player_id, topic, stance, opinion_basis_id, opinion_current, claim_id, fact_ids, disclose_ids`

| 分岐 | `reply_to` | 到達可能act | 追加条件 |
|---|---|---|---|
| 応答 | catalog ID | ANSWER, REBUTTAL, QUESTION, CLAIM, OPINION_CHANGE | 返信先actorはhostが導出 |
| 自発 | null | QUESTION, CLAIM, OPINION_CHANGE, NONE | ANSWER/REBUTTALへ到達不可 |

QUESTIONはsubjectを必須、stance/claim/discloseをnull/空にする。CLAIMはsubject・topic・stanceを必須にする。
ANSWER/REBUTTALのsubjectは任意だが、reply actorとaddresseeはhostが固定する。NONEは他の全値をnull/空にする。

OPINION_CHANGEは`opinion_basis_id`を必須にし、そのbasisに記録されたsubjectを
`subject_player_id`のconstへ束縛する。basisはcommit済みpriorを持つ。`opinion_current`は
`{0,25,50,75,100} - {prior}` のenumで、hostがpriorやcurrentを意味推定しない。応答分岐ではreply eventを
新しいcauseにできる。自発分岐では`fact_ids`を1件以上必須にする。validatorは選択causeの少なくとも1件が
priorの既存evidenceに無いことを検査する。

### 4.2 Chat発話段P

出力は `{"message": string}` の1 fieldだけで、順序もこれだけである。本文は現行契約どおり1〜200文字、
UTF-8で600 bytes以下とする。quote、reverse solidus、改行等へ新しい禁止を追加しない。既存TEXT_BOUNDが
拒否する文字だけを拒否し、provider rawのescape表現とparse後の本文値を§11の別artifactとして測る。

P入力allowlistは次だけである。

- actorとcurrent playerの表示名、day、phase、生死、公開死因、自分の公開CO。
- 同じchannelの直近発言6件。私的channelではrecipient proofが同じ履歴だけ。
- 解決済みplanのact、reply本文、reply actor、subject、topic、stance、opinionのprior/current。
- 選択したpublic fact、disclosure、utterance claimのsurfaceとprovenance。

role/team/count_as/能力定義/win condition/未選択能力結果/別channel履歴/主観state全体は渡さない。
私的channelのteammate表示identityだけは§3.3のrecipient proofを満たすchannel contextとして扱う。
system指示は「解決済みplanに従う」「返信先がある場合はその内容へ応答する」「短く書く」の3規則と、
選択値の意味だけで構成し、完成例・例文を入れない。

Pのschema合格後もTEXT_BOUND、自己反復、長文peer copy、private原文/token照合、選択していない
claim/disclosureの表出、act/messageの意味一致を検査する。後者はschemaだけでは証明できないため、
診断と独立意味評価から削除しない。

### 4.3 PRE_VOTE

キー順は
`vote_option_id, decision, ranked_player_ids, assessment_updates, claim_assessments, fact_ids`。

- vote optionごとにVOTE branchを作り、そのoptionのvalid targetだけをrankへ許す。rankは重複なし、1件以上、
  optionのtarget数以下である。hostはrank先頭をpreferred targetへ解決する。
- `allows_abstain=true`のoptionだけABSTAIN branchを作り、rankを空配列へ固定する。abstain不可optionの空rank、
  optionをまたぐtarget、空rankからのvote actionは到達不能である。
- assessmentは最大2件。playerはpeerだけ、scoreは0/25/50/75/100、根拠factは最大2件。
- claim assessmentは最大2件。対象は権威イベントから導出済みのclaimだけで、verdictは
  UNVERIFIED/SUPPORTED/CONTRADICTED、confidenceは有限score、根拠factは最大2件。
- 同じplayer/claimの重複はvalidatorが拒否する。空更新は合法である。
- VOTEでは`decision_kind=vote`と実player ID、ABSTAINではNoDecisionをhostが一意に導出する。

claimの存在、speaker、内容、authority event refをモデルに生成させない。能力結果と矛盾する主張の評価も、
clientが知るauthorized factだけを根拠候補にする。

### 4.4 CO_OPPORTUNITY

キー順は `decision, co_option_id, claimed_role_option_id, comment, fact_ids`。SILENCE/DEFERではoption、role、
commentをnullにする。DECLAREでは提示されたoption branch、そのoptionがcontentから提示したclaimed-role ID、
1〜200文字かつUTF-8 600 bytes以下の非空commentを同じ1 callで生成する。commentは既存TEXT_BOUNDと
CO decision validatorで検査し、固定文・role名hardcodeで補わない。role名のenumはschema factory入力のcontentから作る。
`decision_kind`、実option/role、commentを既存`CoDeclareDecision`へlosslessに写す。

### 4.5 ABILITY

キー順は `decision, ability_option_id, target_player_ids, fact_ids`。USE branchはoptionごとに作り、
`target_player_ids`をarrayとして、そのoptionの`target_count`を`minItems=maxItems`へ、valid targetをitems enumへ
束縛し、重複を禁止する。target_count 0とNを同じtemplateで保持する。NONE branchはそのoptionがNoDecisionを
許す場合だけ作り、option IDを保持した空arrayにする。既定動作もoptionのtarget_count、valid targets、
abstain条件から導出し、role/ability種別による分岐をコードへ追加しない。

## 5. Provider wireとPF1

次のartifactを混同しない。

| artifact | owner | 用途 |
|---|---|---|
| logical JSON value | schema validator | parse後の型・候補・条件 |
| guard-accepted final | product validator | TEXT_BOUND・authority・意味guard後の採用候補 |
| provider completion raw | provider/native grammar | whitespace・escape・EOSを含む受信bytes |
| host canonical copy | audit serializer | parse済みvalueの比較・hash。provider rawの上限証明には使わない |
| provider request bytes | backend serializer | messages/schema/seed/max tokenを実際に送るwire |

現行backendのrecursive `sort_keys=True` はschema propertiesを並べ替え、K1と両立しない。また現行
`StructuredGenerationRequest`はstage別seed/max outputを持たず、request IDはproviderへ送られない。
U2後のv2実装では次を明示的な製品変更とする。

- `StructuredGenerationRequest`へ`generation_profile="phase6_v2"`、`max_output_tokens`、`seed`を追加する。
  profileはbackend serializer選択、request IDはlocal audit identityに使い、いずれもprovider bodyへ入れない。
- admission client/brainがstage設定を作り、broker/backendは同じ値を上書きせず転送する。
- `GenerationSettings.max_output_tokens`の現行hard cap 512は維持し、request値を1〜512で検査する。
- v2 configは`chat_plan_max_output_tokens`、`message_max_output_tokens`、`pre_vote_max_output_tokens`、
  `co_max_output_tokens`、`ability_max_output_tokens`を別fieldで持つ。PF3 freeze前はUNSETでadmission拒否、
  freeze後だけ1〜512の整数を許す。brainはstage名で1つを選び、broker/backendが同じ値をauditとwireへ転送する。
- seedは`SHA256(UTF8(capture_id) || 0x00 || UTF8(stage) || 0x00 || uint32_be(attempt))`の先頭4 bytesを
  unsigned big-endian整数として使う。attemptは1または2、seedは0〜2^32-1で検査し、同じ値をauditとwireへ渡す。
- providerがseedを受け付けないmodel/versionならv2 profileをadmitしない。
- v2 provider bodyは挿入順を
  `max_tokens, messages, model, response_format, seed, stream, temperature`へ固定する。
  `response_format`は`type, json_schema`、`json_schema`は`name, schema, strict`、各messageは`role, content`の順とする。
- UTF-8、`ensure_ascii=False`、`sort_keys=False`、compact separatorで1回だけserializeする。v1 wireの変更は
  別回帰で管理し、v2 schema subtreeを途中でcanonicalize/sortしない。

backend直前のprovider request bytes、schema subtree bytes、messages bytesをprivate auditへ保存し、各SHA-256を
public auditへ記録する。host canonical copyは別hashを持ち、actual wire hashと同一だと仮定しない。

PF1はcompanion dictだけでなく、変更後backendが作る**actual provider request bytes**をparseし、
`response_format.json_schema.schema`の全oneOf/$ref branchを展開する。branch先頭field、到達act/decision、field順を
companionと比較し、schema factory出力とwire内schema subtreeがexact bytes一致すること、transitive mappingが
途中でsortされていないこと、provider/model/schema compiler/serializer identityがfreezeと一致することを検査する。

| stage | branchの先頭field | 到達集合 |
|---|---|---|
| chat T 応答 | reply_to | ANSWER, REBUTTAL, QUESTION, CLAIM, OPINION_CHANGE |
| chat T 自発 | reply_to | QUESTION, CLAIM, OPINION_CHANGE, NONE |
| chat P | message | messageのみ |
| PRE_VOTE | vote_option_id | 提示vote optionのみ |
| CO | decision | SILENCE, DEFER, 提示されたDECLARE branch |
| ABILITY | decision | USE、optionが許す場合だけNONE |

positive fixtureはcompanionをv2 backendでbody化したactual bytesである。negativeは(1) branch先頭をactへ変える、
(2)現行recursive sortを使う、(3)replyなしANSWERを加える、(4)CO comment欠落/roleをoption外へ広げる、
(5)ABILITY target_countを0/1/Nで違える、(6)PRE_VOTEでcross-option target、abstain不可の空rankを作る、
(7)factoryとwire schemaを1 byte違わせる、である。negativeが1つでもPF1を通れば製品実装へ進めない。
PF1は生成確率や品質を証明しない。

## 6. speech act、reaction、現行12 fieldの行き先

### 6.1 SpeechActV2

Chat planからhostが次を導出する。

| field | 出所 |
|---|---|
| kind | plan.act |
| addressee | reply actor。自発QUESTIONはsubject |
| in_reply_to | reply pointer。自発はnull |
| subject/topic/stance | planまたはopinion basis |
| evidence | 選択fact/disclosureのEvidenceRef |
| prior/current/causes | opinion basisのcommit済みprior、選択current、新しいreply/fact |
| claim provenance | 選択claim catalog。authority/self-assertedを保持 |

`source_interpretation`と`RELATION_HYPOTHESIS`はv2第一版に無い。v1の旧型はQUESTION/CLAIMの二値を
必須にするため、情報が無いsourceを旧型へ変換しない。v2 consumerはreply event refと自分のresponse modeを読む。
authority eventに構造化kindがある場合もevent metadataとして保持し、自分のactから相手の意味を生成しない。

### 6.2 現行proposal 12 field

| v1 field | v2の行き先 |
|---|---|
| schema_version | hostがprofileから固定 |
| base_revision | captureから固定 |
| decision_kind | triggerと選択結果から導出 |
| option_id | action catalogから解決 |
| speech_act | chatだけSpeechActV2を導出。非chatには生成しない |
| reaction | trigger refと自分のresponse modeだけを導出。source意味は推定しない |
| assessment_updates | PRE_VOTEだけ |
| claim_updates | occurrenceはauthority event、主観評価はPRE_VOTEのclaim_assessmentsへ分離 |
| relation_updates | v2第一版では廃止。既存v1 stateを上書きしない |
| strategy_update | v2第一版では廃止。発話ごとの更新をしない |
| co_judgment | CO専用stage |
| pre_vote_reassessment | PRE_VOTE専用stage |

## 7. trigger別の組立てと既定動作

| trigger | call順 | 成功時 | schema消尽・入力不備時 |
|---|---|---|---|
| INITIAL_CHAT | T→必要ならP | chat/NoDecision | silence（NoDecision） |
| PEER_CHAT | T→必要ならP | chat/NoDecision | silence（NoDecision） |
| PRE_VOTE | 1段 | assessment commit＋option-bound vote/abstain | 現行optionから導出する安全な既定動作。主観更新なし |
| CO_OPPORTUNITY | 1段 | declare＋comment/defer/silence | 現行のsilence/defer既定動作 |
| ABILITY | 1段 | option＋target array | optionのtarget_count/valid targetから導出。許可時だけNoDecision |

TがNONEならPを呼ばない。Tがacceptedになった後はPだけを再サンプルし、planを引き直さない。
P本文がplanと意味不一致でもplanからspeech actを「正解」とみなさない。選択外ID・返信原文copy・
未選択disclosureなど機械判定可能な不一致はinvalidとしてPを再サンプルし、意味判定だけが必要な不一致は
本文を改変せず診断と独立評価へ記録する。
transport/timeout/cancel/staleをschema invalidや合法NONEへ変換しない。

## 8. LLMBrain lifecycle、回数、lease

### 8.1 直列実行と回数

1つの`LLMBrain`は現行どおりactive invocationを1件に限定する。chatはTを完了しdurable audit ackを得てから
Pを開始する。同じ判断内で並列callをしない。

- T: 最大2 sample。
- P: 最大2 sample。T accepted時だけ。
- PRE_VOTE/CO/ABILITY: 各最大2 sample。
- repair promptは使わない。invalid rawや例文を次promptへ入れない。
- sampleは同一immutable input/schemaで、`capture_id/stage/attempt`から導出した異なるseedを使う。
- chat最大4 backend call、他trigger最大2。schema projection失敗はbackend call 0。

現行の「初回＋invalid rawを含むrepair」はv2 profileで無効化する。再サンプルはrepairではなく、同じ合法空間からの
新しいsampleである。backendが受理したか不明なtimeout/transport errorは同じattemptを再送せず、その判断を終了する。

### 8.2 Admission lease

projection前にstate storeからleaseを1件取得する。leaseはcapture ID、base revision、world version、
last applied sequence、fact revision、phase identity、action generation、connection generation、input/catalog/schema hash、
option catalog hash、recipient proof hash、owner invocation ID、authority phase/action deadline由来のexpiryを束縛する。
state lockをprovider待ちの間保持しない。

各callの直前、TからPへの遷移、state stage、commit、action dispatch直前にcurrent snapshotとCAS照合する。

1. lease ownerが同じで未release。
2. authority phase/action deadline由来のmonotonic lease expiry前。
3. state/world/sequence/fact/phase/action/connectionの全revision・generationがcaptureと同じ。
4. authority/channel/recipient proof、option catalog hashが同じ。
5. audit上そのattempt request IDが未消費。

不一致は次のtruth tableで停止し、出力を別captureへ流用しない。input hashが同じだけではfreshとしない。

| 変化 | status | 理由 |
|---|---|---|
| world version、last sequence、fact revision、authority event追加 | STALE | 判断根拠が古い |
| phase identity、action generation、option/target catalog | STALE | action/targetが古い |
| connection generation、lease owner | LEASE_INVALID | 所有connectionが変わった |
| channel authority、recipient proof | LEASE_INVALID | privacy authorityが変わった |
| authority deadline/lease expiry | LEASE_INVALID | admission権が失効 |

reconnectはconnection generationを、phase change/action refreshは対応identity/generationを必ず進める。leaseは延長しない。

### 8.3 cancel

cancelを受けたら新段・新attemptを開始しない。dispatch済みrequestは消費済みとし、backend取消しの成否から
再送可能とは推定しない。owned audit writeだけをshieldして`CANCELLED`をdurableに記録し、leaseをreleaseする。
state commit後のcancelは既存の「committed proposalをabortしない」を維持し、delivery finalizationを完了する。

## 9. State transactionとaudit

### 9.1 stateの変更

- chat: generation intentとmessage hashをauditへ記録する。OPINION_CHANGEだけは全段成功時に、選択basisの
  dimensionについてcommit済みpriorをbefore、opinion_currentをafter、新しいreply/factをcausesとして、
  同じstate transactionでsubjective assessmentへcommitする。Tだけ成功、P invalid/exhausted、stale/cancelでは更新0。
  dispatch failureでも形成済みの主観更新はrollbackせず、delivery statusを別auditへ残す。
  claim occurrenceや公開発話の存在は、送信意図でなくserverのdelivery/CO event受信時にauthority stateへ追加する。
- PRE_VOTE: assessmentとclaim assessmentを同じtransactionで更新する。
- CO/ABILITY: 選択意図だけをstageし、結果・claimはserver authority eventまで作らない。
- relation/strategy: v2から更新しない。

`RecentSemanticTurnV2`はauthority eventから作る。自分の配信済みchatにはlinked intent hashを付けられるが、
未配信本文をsemantic turnにしない。保持するのはevent identity、delivery revision、act intent kind、reply/subject、
selected evidence、message hash、claim provenanceに加え、OPINION_CHANGEのdimension/prior/current/causesである。
次captureはsubjective assessmentをpriorの正本として読み、RecentSemanticTurnはlosslessな短期記憶と照合に使う。
act intent kindは本文の意味判定結果ではない。

### 9.2 transaction

1. `capture`: committed stateからimmutable input/catalogを作りlease取得。
2. `generation audit`: attemptごとにSTARTEDをdurable記録してからbackend dispatch。
3. `stage`: 全段成功後、全freshness field・lease・hashを再検証し、pending decision/state deltaを作る。
4. `commit`: audit ack、plan/message hash、before/after state hashを1 transactionでcommitしrevisionを1増やす。
5. `dispatch/finalize`: dispatch直前にfreshnessを再照合し、action identityをstage結果と照合して
   DELIVERED/NO_ACTION/FAILEDを記録する。
6. `authority ingest`: server event受信後にclaim occurrence・RecentSemanticTurnを作る。

stale/cancel/invalid/transport failureはstage前ならstate delta 0である。commit後はrollbackで履歴を消さず、
final statusを追記する。次captureはdelivery finalization完了まで開始しない現行不変条件を維持する。

### 9.3 audit record

各attemptは `capture_id, request_id, stage, attempt, derived_seed, lease_id, base_revision, input/catalog/schema/
messages/request/response hash, max_tokens, actual tokens, latency, terminal_status` を持つ。
statusは少なくともSTARTED、ACCEPTED、SCHEMA_INVALID、MECHANICAL_INVALID、BACKEND_FAILED、TIMEOUT、CANCELLED、
STALE、EXHAUSTEDを区別する。自由形式例外、prompt、response、private candidate値はprivate auditだけに保存する。
public auditは固定codeとhash・件数だけにする。attempt terminal auditのack前に次attemptへ進まない。

## 10. Privacyとauthorityの失敗時閉鎖

- plan/Pの両方で同じ`recipient_proof_sha256`を検査する。P直前の変化はstale。
- private channelの全recipientを証明できない、または候補audienceとの包含を証明できない場合はproviderを呼ばない。
- private prompt/responseはowner-only保存を要求する。ACL設定・検査失敗時は生成を開始しない。
- public auditへ実player/role/private fact/messageを出さない。短いcatalog IDもcapture外では意味を持たせない。
- unselected disclosure、別channel原文、private tokenの照合は機械guardで行い、違反本文を送らない。
- 騙りは合法なself-asserted optionであっても、authority result、inspection result、claim truthへ変換しない。
- validator、option authority、target legality、network action admissionは既存の権威境界で再検証する。

## 11. Token、context、時間の有限証明

### 11.1 completion rawとcapability ceiling

provider completion rawはhostがserializeする値ではない。JSON Schemaへ適合するlogical value、guard-accepted final、
provider raw、parse後のhost canonical copyを別に測る。provider rawはgrammarが空白、escape、number表現、EOSを
有限に拘束すると証明するまで最大bytes/tokenをUNKNOWNとする。

参考として、parse後valueをhostがcompact JSONへ再serializeした**capability ceiling**は、固定4文字IDと現fieldで
T 221 bytes、P 1214 bytes、PRE_VOTE 737 bytes、CO 1316 bytes、ABILITY 318 bytes以下である。
P/COは200文字が全て6-byte escapeを要する場合も含む。これらはaudit copyの容量見積りであり、provider rawの
token budgetでも、採用する`max_output_tokens`でもない。

PF3はprovider/version/schema compiler/grammar identityをfreezeし、次をすべて証明する。

1. grammarがJSON whitespaceと各string/numberのraw表現を有限にし、全schema branchのraw上界を与える。
2. tokenizerのvocabularyだけでなくnormalizer、pre-tokenizer、model、decoderを含むpipelineについて、
   raw 1 byteがtoken列を何個まで増やすかを求める。全256 byte tokenの存在だけを根拠にしない。
3. native negative fixtureにcontrol、quote、reverse solidus、non-ASCII、最長enum、最大array、最大whitespace/escapeを含める。
4. completion content上限にEOSとprovider固有special token reserveを別加算する。

各stageの`max_output_tokens`はこの証明結果から初めてfreezeする。現行hard capは1 call 512であり、全stageを
512以下に収める。収まらない、grammar/pipeline上界が不明、EOS reserveが不明なら値はUNSETのままprovider 0で停止し、
schema・件数・本文boundを再設計する。512超への拡張は本設計から採用せず、U2で影響を示す明示的な製品契約変更を要する。

K込み総予約は、freeze後にchat=`2*T + 2*P`、他=`2*stage`で算出する。これは有限停止量であり、
1 call上限や時間成立性の代用にしない。attemptごとの設定値、actual tokens、EOS/specialをauditする。

### 11.2 context

各stageでactual messagesとactual schemaをnative apply-templateした入力token、freeze済みcompletion content上限、
EOS/special reserveを測り、`input + completion + reserve <= 8192`をcall前に満たす。catalog上限までの最大fixtureでも
同式を満たすことをPF3のpositiveにする。1 tokenでも超過、identity不一致、自由文字列/raw上限を証明不能なら
providerを呼ばない。
上限に収めるため必要候補を削る場合はPF2を再実行し、必須候補欠落を許さない。

### 11.3 wall time

runtime hard deadlineとlease expiryはserver-authoritativeなphase/action deadlineから導出する。11.9秒をcancel、silence、
lease失効へ転用しない。残りのauthoritative時間が凍結済みstage見積りとaudit margin未満なら次attemptを開始しない。

PF4は次のmonotonic clock区間を別々に保存する。

- admission queue: enqueueからlease取得。
- stage provider: backend dispatchからresponse受領。T/P/sample別。
- validation/audit: response受領からterminal audit ack。
- generation service: lease取得後から最終audit ack。全stage・再sampleの実時間を合成。
- end-to-end: enqueueから最終audit ack。

5.95秒目標と11.9秒許容上限は、queueを除くchatの`generation service`へ適用する性能判定である。
2行probeでは各区間のraw値と最大値を事前見積りとして報告し、p95と呼ばない。96行実行時は全chat判断を分母に、
失敗・再sampleも含むnearest-rank p95を出す。判定前にvalidation/audit marginを10%として固定し、
`p95 + margin <= 5.95`を目標達成、`p95 + margin <= 11.9`を許容、超過を再設計とする。
queueとend-to-endは別にp95を報告する。11.9秒を製品hard deadlineにする案は、既存queue/phase deadlineとの影響を
U2の別選択肢として提示しない限り採用しない。

## 12. K1〜K8照合

| trap | 設計対策 | positive / negative |
|---|---|---|
| K1 | branch内キー順固定、sort禁止、wire bytesをPF1 | companion通過 / act-first・sortedを拒否 |
| K2 | trigger・expected actの必須catalogをPF2 | G01 replyあり / reply欠落で停止 |
| K3 | §11のraw grammar/tokenizer全pipeline/EOS/context/call/性能証明 | 最大合法raw通過 / UNKNOWN・512超・context超過を拒否 |
| K4 | 全field required、branchでnull/必須、host導出 | NONE/OPINION合法 / prior欠落・replyなしANSWER拒否 |
| K5 | P指示3規則、例文なし | prompt scan通過 / 完成例混入を拒否 |
| K6 | safetyは行為有無と別の絶対条件 | silenceも安全検査 / 違反0を方式合格にしない |
| K7 | audit/reportで成立性・品質・再試験許可を分離 | valid run判定 / 交絡時はQUALITY判定を出さない |
| K8 | `reply_to`を先頭、trigger r000を必須化 | 応答act全到達 / reply非提示で応答生成不可 |

## 13. 実装対象とテスト影響（U2後）

製品実装はU2後の別packetで行う。想定変更点は次であり、この設計作業では変更しない。

| file | 変更 |
|---|---|
| `ai_client/discussion/projection.py` | profile v2 catalog/input/schema factory、順序保存serializer、P allowlist |
| `ai_client/llm/brain.py` | stage直列化、2回ずつのsample、repair無効、stage max/seed、lease/stale/cancel/audit |
| `ai_client/llm/types.py` | `StructuredGenerationRequest`のprofile/per-stage max output/seedと1〜512 validation |
| `ai_client/llm/backend.py` | v2 provider body、順序保存serializer、seed/max転送、actual wire audit |
| admission/broker/configの既存file | stage別設定の転送、現行512 hard cap、authority deadline由来lease |
| `ai_client/discussion/state.py` | 全freshness CAS、opinion subjective transaction、authority claim/turn導出 |
| discussion model/audit型の既存file | SpeechActV2、catalog、stage/attempt status。正確な配置は実装packetで既存境界に合わせる |
| config/profileの既存file | v1既定を維持したv2 opt-in、token/call/deadline |

既存projection/state/brain testはv1回帰として全件維持する。追加testはschema fixture一致、全trigger、全branch、
キー順、catalog外ID、OPINION prior/currentのstate/turn保存、claim provenance、private recipient proof、T→P直列、
CO非空comment、ABILITY target_count 0/1/N、option別PRE_VOTE/abstain、各段消尽、per-stage max/seed wire、
recursive sort拒否、transport/timeout/cancel/stale truth table、audit ack、state atomicity、default actionを含める。
role/ability追加でPython変更が不要なfixtureも置く。

WP3はcompanion JSONを直接読み、PF1/PF3 fixtureとして使う。WP3のtest-only scriptがschema生成、catalog束縛、
validator、prompt projectionを再実装してはならない。製品v2実装前に検査できるのはcompanion artifactの構造であり、
U2後は同一artifactと製品factory出力のexact bytes一致を追加gateにする。

## 14. 受入条件と未解決gate

本設計のreview受入条件は次のとおり。

1. companionがJSONとして妥当で、全branchのfield/required/orderが本文と一致する。
2. K1〜K8のpositive/negativeをReviewerが照合する。
3. authority event、主観評価、generation intent、騙りを相互に昇格させる経路が無い。
4. private recipient proof、lease、stale、cancel、transaction、auditが失敗時閉鎖である。
5. token上限がprovider raw grammar、自由文字列・escape、tokenizer全pipeline、EOS/special、K回を含み、
   UNKNOWNや現行512超をPASSにしない。

未解決なのは製品選択ではなく、後続gateで実測して固定する次の2点である。

- PF3: provider/native grammarとtokenizer全pipeline、EOS/special、512 hard cap、8192 context式。
  失敗時はschema/件数/文字数を再設計する。cap拡張はU2の明示選択なしに行わない。
- PF4: queueを除くgeneration serviceのp95 5.95秒目標、11.9秒許容上限。runtime deadlineではない。
  provider利用はU3まで禁止する。

独立Reviewer承認はU2の必要条件だが、承認だけで製品実装、provider利用、v1既定切替えを許可しない。
