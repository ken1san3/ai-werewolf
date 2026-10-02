# Phase 6 plan-locked public rubric 詳細設計

Status: REQUESTED  
Task: T569  
Responsibility: Architect  
Contract: `PHASE6_PLAN_LOCKED_RUBRIC_V1`

## 1. 範囲と固定母集団

本設計はT564 `RESOLVED_SUBJECT_PLAN_LOCKED_V1` の公開結果だけを別versionで意味評価する。provider実行、旧annotation・score・rawの参照、一般HARD、ability、秘密、製品採用は対象外である。

母集団はT550順の96 `(case_id,seed)` を固定する。排他的区分はplan対象77、非対象19（`PLAN_NOT_OBSERVED=7`、`NOT_MESSAGE_DOMAIN=12`）。77にはcontrol本文観測69、control本文欠測8、candidateの欠測・失敗・`INPUT_NOT_CHANGED`を残す。欠測を本文や0へ補完しない。

固定質問集合は18 question case × 3 seed = 54 opportunityである。case identityとseedはcustodian-private mappingに置き、評価者へ出さない。質問回答率の分母は常に54とし、PASS、FAIL、UNKNOWN、MNOを別countで合計54にする。candidateだけ観測できた行はpaired改善へ入れず、candidate-only countへ残す。

## 2. 入力packetとfreeze

評価開始前にsource manifest、T564 final freeze、96行partition、77 target、19 non-target、69/8 control body、54 question opportunity、packet/rubric/mappingをMainとは別に再計算し、外部expected SHAへ一致させる。

```text
PlanLockedBlindPacketV1 exact keys:
  contract: "PHASE6_PLAN_LOCKED_RUBRIC_V1"
  rubric_sha256, population_sha256, packet_binding_sha256: Digest
  rows: tuple[PlanLockedBlindRowV1,...]  # random blind順、exact 96

PlanLockedBlindRowV1 exact keys:
  blind_id: non-empty opaque str
  population_status: "TARGET" | "PLAN_NOT_OBSERVED" | "NOT_MESSAGE_DOMAIN"
  side_observations: tuple[BlindSideObservationV1,BlindSideObservationV1]
  chosen_intent: ChosenIntentObservationV1 | null
  question_opportunity: bool

BlindSideObservationV1 exact keys:
  side: "A" | "B"
  observation: PublicMessageObservationV1 | null

PublicMessageObservationV1 exact keys:
  result_status: "ACCEPTED" | "GUARD_REJECT" | "STRUCTURE_INVALID" |
    "LENGTH" | "MISSING_RESPONSE" | "TRANSPORT_GENERATION_LOST" |
    "TRANSPORT_OR_OWNERSHIP" | "CONTEXT_INVALID" | "INPUT_NOT_CHANGED"
  text_observation: "OBSERVED" | "NOT_OBSERVED"
  text: str | null
  text_binding: PublicTextBindingV1 | null
  observation_sha256: Digest

PublicTextBindingV1 exact keys:
  parsed_result_sha256, member_sha256, locator_sha256: Digest

ChosenIntentObservationV1 exact keys:
  case_alias: "case_" + lowercase hex 32文字
  act: "ANSWER" | "REBUTTAL" | "QUESTION" | "CLAIM" | "OPINION_CHANGE" | "NONE"
  subject: nonempty canonical player-id str | null
  reply: ReplyObservationV1 | null
  public_trigger: PublicTriggerObservationV1
  current_public_state: CurrentPublicStateObservationV1
  observation_sha256: Digest

ReplyObservationV1 exact keys:
  source: {record_kind:"chat",order:strict int>=0,visibility:"PUBLIC"}
  actor_player_ids: tuple[nonempty player-id str,...]
  channel_id: nonempty str
  day: strict int>=0
  phase: nonempty str
  text_excerpt: str
  text_truncated: bool

PublicTriggerObservationV1 exact keys:
  kind: "INITIAL_CHAT" | "PEER_CHAT"
  day: strict int>=0
  phase: nonempty str
  source: null | {record_kind:"chat",order:strict int>=0,visibility:"PUBLIC"}

CurrentPublicStateObservationV1 exact keys:
  alive_player_ids: tuple[nonempty player-id str,...]
  day: strict int>=0
  phase: nonempty str
  players: tuple[{player_id:nonempty str,alive:bool,
                  death:null|{day:strict int>=0,public_cause:nonempty str}},...]
  vote_candidate_player_ids: tuple[nonempty player-id str,...]
```

`ChosenIntentObservationV1` はT564 §7のexact型・source順・self hashをそのまま再利用する。固定planとPUBLIC reply/trigger/current stateだけから作り、private context、owner truth、condition、model、seed、run orderを含めない。body literal regexやsubject literalの変化からact、answer、source consistencyを生成しない。`NONE` はplanが明示する合法値で、欠測sentinelではない。

各 `observation_sha256` は自身を除く当該observationのexact fieldsのcanonical SHAである。各sideのmessage観測は保存済みpublic parsed resultからlosslessに作り、chosen intentとは別hashにする。

`ACCEPTED` は `text_observation=OBSERVED`、text=非空str、binding非nullを必須とする。`INPUT_NOT_CHANGED|MISSING_RESPONSE|TRANSPORT_GENERATION_LOST|TRANSPORT_OR_OWNERSHIP|CONTEXT_INVALID` はNOT_OBSERVED/null/nullである。`GUARD_REJECT|STRUCTURE_INVALID|LENGTH` は保存済みpublic parsed-resultに本文memberとlocator/hashが存在し全hash一致する場合だけOBSERVED、存在しなければNOT_OBSERVEDとする。hostはstatusやraw bytesから本文を再構成しない。空文字はNOT_OBSERVEDでありOBSERVEDへ数えない。

message observationにact fieldを置かない。計画actの唯一のsourceはsource-bound `ChosenIntentObservationV1.act` であり、plan pointer/hashとの一致はT564 observation bindingで検証する。評価者だけがchosen plan actと本文の自然意味を比較し、hostは本文からactを分類しない。

```text
PlanLockedPacketFreezeV1 exact keys:
  contract, version
  source_manifest_sha256, t564_final_freeze_sha256,
  population_sha256, mapping_sha256, rubric_sha256,
  packet_sha256, packet_binding_sha256: Digest
  rows: 96
  target_rows: 77
  non_target_rows: 19
  side_a_body_rows, side_b_body_rows: strict int
  both_body_rows, side_a_only_body_rows,
  side_b_only_body_rows, neither_body_rows: strict int            # 合計77
  question_opportunities: 54
  question_target_rows, question_plan_missing_rows,
  question_non_message_rows: strict int                           # 合計54
  freeze_sha256: Digest
```

packet、rubric、mapping、freezeはcreate-onlyで評価前に閉じる。blind ID、本文、chosen intent、public source以外の対応情報をpacketへ出さない。

各TARGET rowでcontrol/candidateを暗号学的random bitによりA/Bへ割り当て、row間で独立に決める。packetは常にA,B順で、実condition対応はprivate mappingの `{blind_id,A_condition,B_condition,condition_permutation_sha256}` だけに置く。alias、本文有無、statusからconditionを導出できるside規則を作らない。評価者とannotation freezeにはcontrol/candidate名、改善方向を渡さない。

評価者向け本文countはTARGET 77だけについて `both + A_only + B_only + neither = 77`、`both + A_only = side_a_body_rows`、`both + B_only = side_b_body_rows` を要求する。実conditionのcontrol 69/欠測8とcandidate observed/missing countはprivate mapping freezeだけに置き、unblind後に再照合する。各rowのtext invariantとpublic locator/hashから別再計算し、self-consistentなstatus/text差を拒否する。

## 3. 5 metricと判定境界

各metricの値は `PASS | FAIL | UNKNOWN | MEASUREMENT_NOT_OBSERVED | NOT_APPLICABLE` である。意味判断に必要な公開contextが不足すればUNKNOWNを優先する。観測自体が無ければMNO、metric対象外ならN/Aとし、両者を混同しない。

1. `ACT_MESSAGE_ALIGNMENT`: chosen intentのactと本文の自然な発話行為が整合すればPASS、明確に矛盾すればFAIL。合法NONEは本文が特定actを約束しないため、本文だけで明確な別actを強制しない限りPASSへ推定せずUNKNOWN。QUESTION/ANSWER/REBUTTAL/OPINION_CHANGEの必要な会話先行文脈がpublic viewに無ければUNKNOWN。
2. `BODY_ANSWER`: `question_opportunity=true` の行だけ対象。公開質問に本文が実質的に答えればPASS、観測可能な本文が質問を避ける・無関係ならFAIL。質問または回答関係を確定できなければUNKNOWN。非question opportunityはN/A。
3. `PUBLIC_SOURCE_CONSISTENCY`: 本文の主張とpacket内のpublic source stateが両立すればPASS、同じdimensionについて明確に矛盾すればFAIL。source IDだけ、欠測、別dimension、private truthが必要ならUNKNOWN。subject literalがopaqueからpublic IDへ変化しただけではPASSにしない。
4. `COPY_INDEPENDENCE`: 各side本文と既存 `chosen_intent.reply.text_excerpt` の公開関係だけを判定する。replyの長い固有表現・節構造・誤りを出典表現なく明白に丸写しした場合だけFAIL、公開文脈上明白に独立ならPASS。reply欠測・truncated・actor不足、正当な明示引用、短い同意・定型句、判別不能はUNKNOWNまたは合法であり、推測でFAILにしない。A/B本文同士の一致・overlapは一因子不変の記述値にすぎずCOPY FAILへ使わない。
5. `TEXT_COMPLETENESS`: 観測本文が文として完結し、chosen actを評価できるだけの内容を持てばPASS。明確な途中切断、空、構文上の未完結はFAIL。guard/statusだけで本文が無い場合はMNO、意味的十分性が判断不能ならUNKNOWN。

A/Bの意味annotationはside別に独立保存する。評価者はpairのどちらが改善側か知らず、`IMPROVED|REGRESSED` を返さない。condition別pair比較はannotation freeze後のunblindでMainが純粋写像としてのみ作る。片側だけのPASSを改善と数えない。

## 4. annotation exact契約

```text
MetricValue = "PASS" | "FAIL" | "UNKNOWN" |
              "MEASUREMENT_NOT_OBSERVED" | "NOT_APPLICABLE"

SideMetricObservationV1 exact keys:
  value: MetricValue
  reason: "ALIGNED" | "CONTRADICTED" | "ANSWERED" | "NOT_ANSWERED" |
          "SOURCE_CONSISTENT" | "SOURCE_CONTRADICTION" |
          "INDEPENDENT" | "OVERLAPPING_COPY" |
          "COMPLETE" | "INCOMPLETE" | "CONTEXT_INSUFFICIENT" |
          "OBSERVATION_MISSING" | "METRIC_NOT_APPLICABLE"
  cited_observation_paths: tuple["subject"|"reply"|"public_trigger"|"current_public_state",...]

MetricObservationV1 exact keys:
  metric: "ACT_MESSAGE_ALIGNMENT" | "BODY_ANSWER" |
    "PUBLIC_SOURCE_CONSISTENCY" | "COPY_INDEPENDENCE" | "TEXT_COMPLETENESS"
  side_a: SideMetricObservationV1
  side_b: SideMetricObservationV1

PlanLockedAnnotationRowV1 exact keys:
  blind_id: str
  status: "ANNOTATED" | "NOT_REQUIRED"
  metrics: tuple[MetricObservationV1,...]  # ANNOTATEDは上記順でexact 5

PlanLockedAnnotationEnvelopeV1 exact keys:
  contract: "PHASE6_PLAN_LOCKED_ANNOTATION_V1"
  packet_sha256, rubric_sha256, packet_freeze_sha256: Digest
  rows: tuple[PlanLockedAnnotationRowV1,...]  # packet順exact 96
```

TARGET 77はANNOTATED、非対象19はNOT_REQUIREDかつmetrics空とする。observation path citationはsideごとに重複なしで、実際に判断へ使った既存pathだけを許す。評価者はsource ID、condition名、改善方向を保存せず、自由文理由、private ID、condition推測を保存しない。

各sideのvalue/reason合法組合せを固定する。PASSはmetric固有の肯定reason、FAILは否定reason、UNKNOWNはCONTEXT_INSUFFICIENT、MNOはOBSERVATION_MISSING、N/AはMETRIC_NOT_APPLICABLEだけを許す。COPYの肯定reasonはINDEPENDENT、否定reasonはOVERLAPPING_COPYであり、後者は公開replyとの明白な丸写しだけを表す。A/B間overlapをreasonへ使わない。

```text
PlanLockedAnnotationFreezeV1 exact keys:
  contract, version
  packet_sha256, rubric_sha256, packet_freeze_sha256,
  annotation_sha256, annotation_rows_sha256: Digest
  rows: 96
  annotated_rows: 77
  not_required_rows: 19
  side_a_metric_value_reason_counts: exact 5-metric closed count object
  side_b_metric_value_reason_counts: exact 5-metric closed count object
  both_body_rows, side_a_only_body_rows,
  side_b_only_body_rows, neither_body_rows: strict int
  fresh_independent_evaluator: true
  freeze_sha256: Digest
```

## 5. unblindと集計

annotation freeze完了後だけcustodianがprivate side mappingを一回再結合する。MainはA/Bの意味fieldを変更せずcontrol/candidateへ戻し、case/seed、77/19、69/8、54 question opportunityを再照合する。全96、対象77、非対象19を主分母として表示し、metricごとのPASS/FAIL/UNKNOWN/MNO/N/Aを全件示す。

`question_target_rows + question_plan_missing_rows + question_non_message_rows = 54` をpublic fixture metadataから機械的に固定する。TARGET内questionだけを評価者がBODY_ANSWER判定する。非対象question opportunityはNOT_REQUIREDを維持し、control/candidateとも `MEASUREMENT_NOT_OBSERVED / OBSERVATION_MISSING` をMainが機械付与する。これとTARGET question値を合計した54 opportunityを分母とし、PASS/FAIL/UNKNOWN/MNOを合計54にする。非question 42行はN/Aで54分母へ入れない。

Mainはunblind後、COPYを含む各metricでcontrol/candidate値からpair outcomeを純粋再計算する。両方PASS/FAILの場合だけ `FAIL→PASS=IMPROVED`、`PASS→FAIL=REGRESSED`、同値=UNCHANGED、どちらかUNKNOWN/MNOならCOMPARISON_UNKNOWN、対象外ならNOT_APPLICABLEである。A/B本文overlapは任意のdescriptive countとして意味metric外に保存できるが、FAIL、改善、HARD、製品採用へ転用しない。candidate-only、control-only、both-observed、neitherをfreeze値と再照合し、condition別metric値/reason/countとpair outcome countを分離する。条件名、seed、model別の意味結果はsafe summaryへ出さない。

## 6. 正負例とgate

正例は合法NONE、QUESTIONへの実回答、公開source整合、独立表現、完結本文、candidate-only UNKNOWNを含む。負例はact明確矛盾、質問回避、同dimension public source矛盾、実質copy、途中切断、未知observation path、cross-row ID、本文regexからのact生成、missing controlの改善扱い、54分母変更を拒否する。公開context不足、REBUTTAL/OPINION_CHANGEの先行発話欠測、copy判別不能はUNRESOLVED例としてUNKNOWNに固定する。

実装前に独立design APPROVEDを要求する。実装、fresh annotation、unblind、集計は別taskとし、それぞれsource/code/hash、denominator、create-only freeze、独立評価者を再検証する。provider、game、Actions、旧suite再生成は行わない。
