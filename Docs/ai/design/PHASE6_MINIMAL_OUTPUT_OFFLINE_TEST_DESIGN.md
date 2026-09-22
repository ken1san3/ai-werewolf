# Phase 6 minimal output offline test 詳細設計

Status: APPROVED

T492承認範囲はpublic synthetic offline contract準備のみ。provider/製品採用は含まない。
承認本文SHAはT492 reportを正本とし、このStatus更新はMainによる判定反映である。

## 1. 目的・仮説・実行禁止

H61だけを対象にする。1回の候補出力から`assessment_updates / claim_updates / relation_updates / strategy_update`と、そのprivate state commitを除き、action、既存speech act、必要最小限のgrounding、発話文を機械検査できるかを公開syntheticで確認する。

今回は設計だけを作る。helper、casepack、test、runnerは実装せず、pytest、provider、model、network、Actionsを実行しない。本設計の独立承認前に実装しない。旧32件のbaseline、annotation、rawを読まず、再生成・再採点もしない。

論理stageは`tactic/action → grounding → utterance → deterministic validation`の4段だが、将来の候補生成は1 provider callである。旧完全`DiscussionProposal`を二回目のcallで生成せず、欠けたlegacy fieldをdefaultや空値で補完しない。

## 2. 所有物と一コマンド入口

承認後にMainが実装するtest-only所有物を次に限定する。

| path | 責務 |
|---|---|
| `scripts/phase6_minimal_output_probe.py` | strict parse、v0 schema生成、host sidecar binding、pure validation、coverage集計 |
| `tests/fixtures/phase6_minimal_output_cases.py` | 公開synthetic casepackと将来32件用metadata型。private本文を持たない |
| `tests/test_phase6_minimal_output_applicability.py` | positive/negative/coverage/non-mutation tests |

将来の唯一の入口は次とする。今回は作成・実行しない。

```powershell
python -m pytest tests/test_phase6_minimal_output_applicability.py -q
```

入口はofflineで、通信、socket/listener、provider/backend、game loop、network dispatch、`DiscussionStateStore`の構築・呼出し、旧raw/annotationのopenを0とする。data-only型の通常importで`ai_client/__init__.py`からnetwork/llm moduleがtransitiveにloadされること自体はruntime actionではなく、禁止対象にしない。

## 3. 再利用する現行契約

| 契約 | source | 再利用範囲 |
|---|---|---|
| Phase 6 projection/schema | `ai_client/discussion/projection.py::project_discussion_brain_input`, `_decision_schema`, `_speech_schema` | trigger-filtered option、player ID、speech act branch、max textの正本 |
| semantic validator | `ai_client/llm/decision.py::_validate_semantic_output` | trigger/action、EvidenceRef membership、ANSWER/REBUTTAL、OPINION_CHANGE、REACTION、CO、PRE_VOTEの既存関係を写したpure検査の比較元 |
| closed values | `ai_client/discussion/model.py` | `EvidenceRef`、visibility、speech enums、reaction、pre-voteのconstructor |
| decision types | `ai_client/brain/model.py::BrainDecision`, `brain_decision_identity` | action kind/option/targetの意味 |

`parse_llm_output`と`_parse_proposal`は完全legacy proposalを要求するので候補parseに使わない。これらを通すため旧fieldをfabricateしない。private methodを製品runtimeから呼ぶ設計でもなく、既存契約との対応確認sourceとして使う。

## 4. host sidecar

model output外に、test fixtureが次のimmutable sidecarを持つ。

```text
HostBindingV0
  case_id: 公開一意ID
  trigger: INITIAL_CHAT | PEER_CHAT | CO_OPPORTUNITY | PRE_VOTE | ABILITY
  actor_player_id: player_id
  current_player_ids: ordered tuple[player_id]
  offered_options: 既存projectionと同形の公開synthetic option tuple
  projected_evidence: tuple[AllowedEvidenceRecordV0]
  captured_evidence: tuple[AllowedEvidenceRecordV0]
  reaction_source: EvidenceRef | null
  prior_assessments: tuple[ReadOnlyAssessmentV0]
  base_revision / context_sha256: host値
  max_text: positive integer
  canonical_input_bytes / input_sha256: exact bytesとそのdigest
  canonical_private_view_bytes / captured_private_state_sha256: exact bytesとそのdigest
  max_text_utf8_bytes / max_candidate_utf8_bytes: positive integer
  requires_private_update: exact bool
```

`AllowedEvidenceRecordV0`はclosed dataで、`ref(record_kind, order, visibility)`、`actor_player_ids`、`channel_id`だけを持つ。本文、claim内容、private payloadを含めない。CHATはactor exact 1名とchannel必須、CO_DECLARATION/CO_REPORTはactor exact 1名・channel null、その他record kindはchannel nullとし、actorは既存captured descriptorのtupleをそのまま固定する。ANSWER/REBUTTALはactor exact 1名、actor≠self、addressee=actorを検査する。PEER_CHAT sourceはCHATで、offered chat optionのchannel=descriptor channelを要求する。

`OfferedOptionV0`は全branch共通の`action_kind, option_id`に加え、chatは`channel`、voteは`valid_targets, allows_abstain`、abilityは`valid_targets, target_count`、co_declareは`claimed_role_ids`だけを持つclosed unionとする。actorは`actor_player_id`一箇所、channelはchat optionとCHAT record descriptorの二箇所を照合し、candidateへhost値として複写させない。

`ReadOnlyAssessmentV0`は`subject_player_id`、`suspicion`、`credibility`、それぞれのprior値に共通する`prior_evidence_identities`だけを持つ。OPINION_CHANGEのsubject/dimensionからpriorを選び、candidateのprior一致と「causeの少なくとも1件がprior evidence集合外」を検査する。candidateから生成・更新しない。このviewは`canonical_private_view_bytes`の原像に含め、将来32件で公開metadataから得られなければ推測せずUNRESOLVEDにする。

候補に`case_id`、actor、revision、phase、capture/context/input hashを書かせない。`canonical_input_bytes`の原像は、HostBindingの`case_id, trigger, actor_player_id, current_player_ids, offered_options, projected_evidence, captured_evidence, reaction_source, prior_assessments, base_revision, context_sha256, max_text, max_text_utf8_bytes, max_candidate_utf8_bytes, requires_private_update`を持つclosed exact mapである。bytes/hash自身は循環を避けて原像から除く。

`canonical_private_view_bytes`の原像は`{"prior_assessments": [...]}`だけのclosed exact mapで、配列要素は`ReadOnlyAssessmentV0`の全fieldと完全一致する。helperは両bytesをstrict JSON decodeし、sidecarの各field/list/dataclass表現を同じschema adapterでplain exact mapへ変換して完全一致を検査する。別々の変換や推測で値を補わない。その後、`json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")`による再serializationが元bytesとbyte完全一致し、各SHA-256 digestとも一致することを要求する。

runnerはraw candidate bytesのSHA-256とsidecar hashを結果rowへ結合する。digestだけを比較して原像との結合を省略しない。validatorはsidecar bytesを変更せず、private view bytesのbefore/after digestを一致させる。公開syntheticのprivate viewは架空literalだけで、実private stateや旧rawをcasepackへ複製しない。両sidecar bytesはfixture由来のtrusted test inputであり、暗号署名、server authenticity、network acceptanceの証明ではない。

ref許可集合は`projected_evidence ∩ captured_evidence`だけである。identityだけでなくrecord_kind/order/visibilityの完全一致を要求し、descriptor metadataもcapture側と矛盾してはならない。captured-only、projected-only、visibility差替えを別々に拒否する。

将来32件metadataは公開fixture codeから上記の非本文fieldだけを構成する。全case IDがexact 1回、件数32、重複0であることを先に検査する。取得不能fieldを旧raw/annotationから補わず、そのrowを`APPLICABILITY_UNRESOLVED`へ残す。

## 5. MinimalOutputV0 exact shape

top-level required keyは次の6個だけ、`additionalProperties=false`とする。

```json
{
  "schema_version": "phase6.minimal-output.v0",
  "decision": {},
  "speech_act": {},
  "grounding": [],
  "utterance": null,
  "trigger_detail": null
}
```

### 5.1 strict parse

- UTF-8 strict decode、JSON object 1個、前後whitespaceのみ許可。
- duplicate key、NaN/Infinity、unknown key、unknown enum、bool-as-int、surrogateを拒否。
- parse後の値を変更・正規化・default補完しない。
- error codeは`JSON_INVALID / SHAPE_INVALID / VALUE_NOT_OFFERED / BINDING_INVALID / TEXT_INVALID / PRIVATE_UPDATE_FORBIDDEN`の閉集合。

複数違反時のerror precedenceを次で固定し、最初の1 codeだけを返す。

1. raw candidate byte cap超過 → `TEXT_INVALID`
2. UTF-8 decode、JSON構文、duplicate key、NaN/Infinity、surrogate → `JSON_INVALID`
3. parse済みJSON objectの**keyだけ**を再帰走査し、禁止update keyを検出 → `PRIVATE_UPDATE_FORBIDDEN`
4. closed shape、required/unknown key、型、enum、array局所上限 → `SHAPE_INVALID`
5. trigger/action、option/target/player/prior等のoffered value → `VALUE_NOT_OFFERED`
6. sidecar bytes↔digest、projected∩captured、visibility、actor/channel、grounding完全一致 → `BINDING_INVALID`
7. utterance scalar/UTF-8 byte/cap exhaustion境界 → `TEXT_INVALID`

禁止語がutterance等の文字列**値**に現れても禁止keyとは扱わない。一変数negativeはこの順序で期待codeを固定する。

### 5.2 `decision`

既存`_decision_schema`のtrigger-filtered branchを使う。ただしchatの`message`とco_declareの`comment`は`utterance`へ一度だけ置き、candidate decisionから除く。

| kind | exact fields |
|---|---|
| `none` | `kind` |
| `chat` | `kind`, `option_id` |
| `vote` | `kind`, `option_id`, `target_player_id` |
| `ability` | `kind`, `option_id`, `target_player_ids` |
| `co_declare` | `kind`, `option_id`, `claimed_role_id` |

option ID、targets、target count、claimed roleはsidecarのoffered optionとexact一致させる。`co_report`は現Phase 6 validatorどおり常に負例である。trigger/action許可は次を固定する。

| trigger | allowed |
|---|---|
| INITIAL_CHAT / PEER_CHAT | none, chat |
| CO_OPPORTUNITY | none, co_declare |
| PRE_VOTE | none, vote |
| ABILITY | none, ability |

### 5.3 `speech_act`

`ai_client/discussion/projection.py::_speech_schema`の7 branchを変更せず使う。`kind`はここで一回だけ選び、別のtactic kindやdialogue purposeを置かない。

- `NONE`: kindのみ。
- `CLAIM`: subject/topic/stance/evidence。
- `QUESTION`: addressee/nullable subject/topic/nullable source。
- `ANSWER`/`REBUTTAL`: addressee/in_reply_to/source_interpretation/topic/stance/evidence。
- `OPINION_CHANGE`: subject/dimension/prior/current/causes。causesは1件以上。priorはcaptured read-only assessmentの同dimensionとexact一致し、少なくとも1 causeがprior evidence集合に無い既存条件を守る。assessment更新は作らない。
- `RELATION_HYPOTHESIS`: source/target/relation/confidence/evidence。これは発話分類であり、`RelationHypothesis`を永続更新しない。

全player IDはsidecarのcurrent players内、actorを使えない既存箇所は同じく拒否する。EvidenceRef fieldはすべて`grounding`にも同じidentity/purposeで現れることを要求する。

### 5.4 `grounding`

v0は新しいfact-ref namespaceを作らず、既存`EvidenceRef`だけを使う。current world値をEvidenceRefへ偽装しない。current-only groundingが必要なcaseは`UNRESOLVED`である。

各itemは`{"purpose": enum, "ref": EvidenceRef}`のclosed object。v0のpurposeは`UTTERANCE / OPINION_CURRENT / REACTION / PRE_VOTE`だけとする。`TACTIC / OPINION_PRIOR / CO`は対応するcandidate EvidenceRef fieldが現契約にないため追加しない。

| candidate field | required purpose |
|---|---|
| CLAIM `evidence[]` | UTTERANCE |
| QUESTION `source`（non-null） | UTTERANCE |
| ANSWER/REBUTTAL `in_reply_to`, `evidence[]` | UTTERANCE |
| OPINION_CHANGE `causes[]` | OPINION_CURRENT |
| RELATION_HYPOTHESIS `evidence[]` | UTTERANCE |
| PEER_CHAT reaction `trigger` | REACTION |
| PRE_VOTE reassessment `evidence[]` | PRE_VOTE |

同じrefを異なるfield/purposeで使うことは許すが、各`(purpose, record_kind, order, visibility)`はexact 1回とする。同一purpose内で複数fieldが同じrefを使ってもgrounding itemは1件へ集合化する。grounding arrayの順序は意味契約にせず、validatorは上表から得た期待multisetとの完全一致を検査する。

aggregate件数上限と独自canonical sortを追加しない。各元fieldの既存上限（各evidence/causes array最大8、reaction trigger 1、PRE_VOTE evidence最大8）をそのまま維持する。refは`projected_evidence ∩ captured_evidence`にrecord_kind/order/visibility完全一致しなければならない。

参照存在・visibility・purpose整合だけを機械PASSにする。claimの真偽、本文への含意、秘密開示の妥当性はPASSにしない。

### 5.5 `utterance`

- chat/co_declare: UTF-8 string、1..`max_text` Unicode scalarかつ`max_text_utf8_bytes`以下。現行`_validate_generated_text`のcap exhaustion境界を使うcaseでは、190 scalarsまたは570 bytes以上の未完結末尾も拒否する。decisionのmessage/commentはこの値を使うという診断上の対応だけを記録し、legacy objectを構築しない。
- none/vote/ability: null。
- empty、surrogate、scalar/byte上限超過、candidate全体のbyte上限超過を拒否する。現行契約にないwhitespace-only拒否は追加しない。

copy bound等の既存機械guardは別flagにできるが、secret disclosure、act/text一致、grounding supportはsemantic rubricへ送る。regexで意味PASS/FAILを決めない。

### 5.6 `trigger_detail`

`trigger_detail`は`{"reaction": ...}`等のwrapperを持たず、triggerごとに旧object自体をflatに置くclosed unionとする。

- INITIAL_CHAT: null。
- ABILITY: null。
- PEER_CHAT: flat ReactionAssessment shape `{"trigger", "score", "reason"}`を必須。既存`trigger` EvidenceRef、score 0..100、既存reasonだけを許可する。triggerはsidecar reaction sourceとexact一致し、REACTION groundingに同じrefを必須とする。channel整合はsidecarが指すcaptured chat descriptorで検査する。
- CO_OPPORTUNITY: flat CoJudgment shape `{"decision", "selected_option_id", "claimed_role_id"}`を必須。`SILENCE/DEFER`はoption/role null、`DECLARE`はdecisionのco_declare option/roleとexact一致。noneとDECLARE、co_declareとSILENCE/DEFERを拒否する。
- PRE_VOTE: flat PreVoteReassessment shape `{"option_id", "ranked_target_player_ids", "preferred_target_player_id", "evidence"}`をdecisionがnoneでも必須。option、unique ranking、preferred、evidenceを既存offerに照合する。voteではtarget=preferred、noneではabstention可能かつpreferred nullという現行条件を守る。全evidenceをPRE_VOTE groundingへ含める。

他trigger用shape、追加wrapper、unknown keyを拒否する。

## 6. private state不変と結果分類

candidateに次のkeyがどの深さでも現れたらshapeで拒否する: `assessment_updates`, `claim_updates`, `relation_updates`, `strategy_update`, `private_updates`。hostはcandidateからこれらを導出しない。

fixtureの`requires_private_update=true`なら、candidateが他の機械検査を通っても結果は`APPLICABILITY_UNRESOLVED`である。falseなら`APPLICABILITY_COVERED`になり得る。不正candidateは`APPLICABILITY_INVALID`。全分類でprivate state digest before/afterが同一でなければtest failureとする。

これはlegacy constructibility、legacy validator合格、semantic品質、network acceptanceを表さない。state storeの`stage/commit/observe_authoritative`を呼ばない。

## 7. 公開synthetic casepack

### 7.1 positive cases

最小有限集合は次の13件とする。各caseは公開の架空player/evidence/optionだけを使う。

| ID | trigger/action | speech | 主な境界 |
|---|---|---|---|
| P01 | INITIAL_CHAT/none | NONE | 合法NONE、utterance null、grounding 0 |
| P02 | INITIAL_CHAT/chat | CLAIM | chat text bound、public evidence |
| P03 | PEER_CHAT/chat | QUESTION | reaction/channel、nullable source |
| P04 | PEER_CHAT/chat | ANSWER | in_reply_to/addressee/reaction |
| P05 | PEER_CHAT/chat | REBUTTAL | CLAIM interpretation、grounding |
| P06 | PEER_CHAT/chat | OPINION_CHANGE | prior/current/新cause、state不変 |
| P07 | PEER_CHAT/chat | RELATION_HYPOTHESIS | relation発話、relation update 0 |
| P08 | CO_OPPORTUNITY/none | NONE | SILENCE、合法NONE |
| P09 | CO_OPPORTUNITY/co_declare | CLAIM | DECLARE tuple、comment text |
| P10 | PRE_VOTE/vote | CLAIM | reassessment必須、target=preferred |
| P11 | PRE_VOTE/none | NONE | abstention可、reassessment必須 |
| P12 | ABILITY/ability | NONE | exact target count、utterance null |
| P13 | ABILITY/none | NONE | capability不使用、state更新0 |

全7 speech kind、全5 trigger、全許可action kind、合法NONE、CO、PRE_VOTE、OPINION_CHANGE、REACTIONを少なくとも1回覆う。casepack coverage関数が集合一致を検査し、件数だけで代替しない。

### 7.2 negative cases

各negativeは一変数だけを変え、期待error codeを固定する。

- JSON: duplicate/unknown/trailing/NaN/surrogate。
- action: trigger外kind、unknown option、target外、target count、co_report、none forbidden条件。
- actor/player: unknown player、自分宛を禁じる既存branch、sidecar actor差替え。
- evidence: unknown identity、captured-only、projected-only、visibility改変、private refをpublic-only箇所へ使用、元field局所上限9件、purpose欠落/余剰。同じgrounding itemsの並べ替えは拒否しない。
- speech: 7 branchごとのrequired/forbidden field、ANSWER/REBUTTAL source type・actor・addressee不一致。
- OPINION_CHANGE: prior不一致、current範囲外、cause空、全causeがprior集合内、更新key混入。
- PRE_VOTE: detail欠落、option/ranking/preferred/target不一致、duplicate rank、abstention不可none。
- CO: detail欠落、DECLARE tuple不一致、none+DECLARE、co_declare+SILENCE、CO recordをtruth扱いする追加field。
- REACTION: PEER_CHATで欠落、他triggerで存在、trigger/channel差替え、REACTION grounding欠落。
- text: chat/co null・空・scalar max+1・byte max+1・surrogate、non-chat非null。whitespace-onlyは現行どおりshape/text-boundで拒否せず、意味的適切さを機械PASSにもしない。
- state: forbidden update key、before/after digest差、commit/observe call spyが1以上。

## 8. semantic caseとrubricの分離

機械testのPASSは、modelが正しいtacticを選んだ、質問へ答えた、秘密を守った、groundingがclaimを支持した、CO/騙りが戦略的に妥当だった、という意味ではない。

将来provider測定を別承認で行う場合に備え、casepackは機械期待値と別に次の公開rubric IDだけを持てる: `ACT_TEXT`, `QUESTION_RESPONSE`, `GROUND_SUPPORT`, `PRIVATE_DISCLOSURE`, `CO_STRATEGY`。rubric本文と判定は機械validatorへ入れず、regexを使わない。今回annotationや期待semantic結果は作らない。

## 9. 将来32case applicability metadata

公開fixture codeからのみ、case ID、trigger、offered action kinds、expected legal speech kinds、NONE合法性、PRE_VOTE/CO/REACTION/OPINION_CHANGE必要性、`requires_private_update`を収集するadapterを将来設計する。次を固定する。

- 32 ID exact一致、各1回、未知/欠落/重複を拒否。
- 全trigger/actionのcountを0を含め出す。
- co_reportは生成候補でなく負例countへ置く。
- `requires_private_update`を推定できなければfalseにせずUNRESOLVED。
- unavailable metadataを旧raw、annotation、baseline、LLM判断で補わない。
- covered/invalid/unresolvedの合計を常に32にし、unresolvedを分母から除かない。

現行`tests/fixtures/phase6_conversation_cases.py::cases`の静的定義に対する期待値は、16 category×2、`INITIAL_CHAT=4 / PEER_CHAT=24 / CO_OPPORTUNITY=2 / PRE_VOTE=2 / ABILITY=0`、合法NONE control=`G14-1/G14-2`である。将来adapter testは正本case metadataとこの期待を照合する。ABILITYが0であることを隠さず、ABILITY契約は公開synthetic P12/P13でのみ覆う。`cases()`自体を今回実行して得た値ではない。

今夜のcase値は新規`tests/fixtures/phase6_minimal_output_cases.py`内のpublic literal `HostBindingV0`だけで構築する。`tests/test_phase6_semantic_output.py`、`tests/test_phase6_quality_grounding.py`、`tests/fixtures/phase6_conversation_cases.py`等のprivate test helperをimportせず、`DiscussionStateStore`も構築しない。data-only closed typeやspeech branchを閉じるpure functionは通常importで再利用できるが、full proposal parser/constructorは使わない。

将来32件adapterは別の未実装interfaceに留める。runtime actionなしで公開metadataを取得する詳細契約が承認されるまで、`cases()`、`scripts.phase6_conversation_suite.project`、provider/run/evaluate、`model_comparison`を入口からimportまたは実行せず、`32case ready`と呼ばない。

公開fixtureに必要metadataが存在しない場合、本実装単位はsynthetic casepackとadapter interfaceまでに止める。candidate実測ready、32case ready、品質改善とは呼ばない。

## 10. focused test一覧

将来のtest fileは次のtest groupを持つ。

1. `test_strict_json_and_closed_shape`
2. `test_trigger_action_matrix_and_co_report_rejection`
3. `test_all_seven_speech_kinds_and_grounding_membership`
4. `test_opinion_change_uses_prior_without_private_update`
5. `test_pre_vote_required_for_vote_and_abstention`
6. `test_co_judgment_correlates_without_claim_truth`
7. `test_peer_reaction_trigger_channel_and_grounding`
8. `test_text_bounds_and_non_text_actions`
9. `test_private_state_digest_unchanged_and_no_commit`
10. `test_casepack_exact_coverage`
11. `test_legacy_fields_are_never_fabricated`
12. `test_semantic_rubric_is_not_mechanical_pass`
13. `test_public_32_metadata_is_total_or_unresolved`
14. `test_sidecar_bytes_match_digests_and_allowed_ref_intersection`
15. `test_error_precedence_and_forbidden_keys_ignore_text_values`

test doublesはpure function引数だけにし、provider/network/state storeを構築しない。spyは「呼ばれない」ことの検査専用である。

## 11. 受入条件と独立review

詳細設計の受入条件は次のとおり。

- H61以外の介入を混ぜず、永続private update/commitが0。
- schema/parse/validationがclosedで、値を補正しない。
- 全7 speech、5 trigger、許可action、co_report負例、NONE、PRE_VOTE、CO、OPINION_CHANGE、REACTIONを有限caseで覆う。
- host binding、actor、option、visibility、text boundを機械検査する。
- semantic rubricを機械PASSから分離する。
- legacy full proposal、provider、network、state commitを作らない。
- 将来32件の不足をUNRESOLVEDとして分母に残す。

独立reviewは、v0 shapeの過不足、現行projection/validatorとの対応、EvidenceRef visibility、OPINION_CHANGE/PRE_VOTE/CO/REACTIONのcross-field条件、private state不変、casepack coverage、semantic境界、旧field補完禁止、一コマンドのoffline閉包を確認する。承認は製品採用、provider測定、32case ready、legacy受理同値を意味しない。
