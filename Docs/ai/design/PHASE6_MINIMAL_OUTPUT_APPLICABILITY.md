# Phase 6 minimal output applicability（DRAFT）

Status: DRAFT
実装条件: 独立reviewで設計が承認されるまで、製品実装、provider実行、既存結果の再採点を行わない。

## 1. 目的と非目的

現行の一回の生成が、network decision、発話構造、根拠、主観的state更新を含む完全な`DiscussionProposal`を同時に作ることによる負荷を切り分ける。初回候補は`assessment_updates / claim_updates / relation_updates / strategy_update`による**captured private stateの永続更新とcommitをschemaから完全に除外**し、一回のprovider callでaction/発話とそのgroundingだけを出す。

1. tactic選択
2. minimal grounding選択
3. utterance生成
4. deterministic validation

この4項目は**論理stage**であり、4 provider callsを意味しない。候補診断は1 provider callの単一strict JSON outputを、純粋なvalidatorが順に検査する。validator通過後に旧完全出力をproviderで再生成しない。

本案は新製品schema、品質改善、legacy受理同値を確定しない。旧metadataを空配列、`None`、固定confidence、既定strategyなどで補い、legacy adapterへ通す案でもない。既存32件に対するtest-onlyな「主観更新と発話の結合を外したとき、何を保てて何を失うか」の適用可能性診断である。客観stateは現行でもhost由来であり、その再生成削減を新規効果として数えない。

## 2. 現行責務との対応

| 現行要素 | 客観/主観 | 現在の責務 | 最小候補での扱い |
|---|---|---|---|
| `BrainInput.snapshot`, `history`, `co`, `ability_results` | server event由来の客観的な受信状態 | world projectionが生成時点の入力を固定する | modelに再生成させず、validatorの照合sourceとして読む |
| `DiscussionCapture.context`, trigger, provenance | capture時点の客観的境界 | visibility、履歴完全性、triggerを固定する | request bindingとgrounding参照範囲の検査に使う |
| `EvidenceRef` | recordの存在、種類、順序、visibility | retained/public/private recordを参照する | typed grounding参照としてmodelが選び、hostは存在・visibilityだけ検査する |
| `PlayerAssessment` | 主観 | suspicion/credibility/confidenceを保持する | captured既存値をread-only入力として維持し、候補出力による更新は0。更新必須caseは`APPLICABILITY_UNRESOLVED` |
| `ClaimAssessment` | 主観的な公開claim評価 | public Chat/CO recordに`UNVERIFIED/SUPPORTED/CONTRADICTED`を付ける | captured既存値はread-only。CO truthへ変換せず、候補schemaから更新を除外 |
| `RelationHypothesis` | 主観 | public evidenceから関係仮説を保持する | captured既存値はread-only。host導出も候補更新も禁止 |
| `StrategyState` | 主観/戦略 | mode、focus、evidenceを保持する | captured既存値はread-only。tacticから導出せず、候補更新も禁止 |
| `DiscussionProposal` | decisionとprivate semantic transaction | stage/commit対象をまとめる | 診断候補から完全Proposalを偽造しない。初回候補が保持しない契約を対応表で明示する |
| `BrainDecision` | network action候補 | none/chat/vote/ability/COを表す | tactic payloadから既存option/action authorityに対して決定的に検査する |
| authoritative accepted observation | server受理後の客観事実 | `observe_authoritative`へaccepted/rejected evidenceを与える | private commitから推測しない。実送信を行わない診断では生成しない |

`WorldSnapshot`等の現在状態は既にserver eventから構築される。modelにalive、phase、action availability、能力結果を再宣言させ、その宣言を正本にしてはならない。

`ClaimAssessment`は、公開されたChat/CO recordに対するprivateな評価である。`CO_DECLARATION`を参照できても、宣言されたroleが事実であるとは意味しない。claimの存在、発言者、内容の真偽、modelの評価を分離する。

private semantic commitとauthoritative accepted observationも分離する。stage/commitされたassessment、relation、strategy、semantic turnはclient内部のtransactionであり、network actionがserverに受理された証拠ではない。accepted/rejectedはserver observationと対応する`EvidenceRef`が必要であり、candidate validatorが合成してはならない。初回候補はprivate stateをcommitせず、captured stateを変更しない。

### 2.1 対応する現行source

| 責務 | 現行source |
|---|---|
| canonical input projection入口 | `ai_client/llm/prompt.py::project_brain_input` |
| Phase 6 discussion canonical projection / 完全discussion output schema | `ai_client/discussion/projection.py::project_discussion_brain_input`, `_decision_schema` |
| strict parse / legacy proposal parse / semantic cross-check | `ai_client/llm/decision.py::parse_llm_output`, `_parse_proposal`, `_validate_semantic_output` |
| providerを含むBrain入口 | `ai_client/llm/brain.py::LLMBrain.decide` |
| network decision closed types / identity | `ai_client/brain/model.py::BrainInput`, `BrainDecision`, `brain_decision_identity` |
| discussion capture / stage / private commit / authoritative observation | `ai_client/discussion/state.py::DiscussionStateStore.capture`, `stage`, `commit`, `observe_authoritative` |
| dispatch前後の検査とcontroller-level copy guard | `ai_client/brain/controller.py::_validate_and_dispatch`, `_cross_player_public_copy` |

## 3. 1-call最小候補

### 3.1 候補envelope

テスト用の概念schemaを次に限定する。名前とenumはDRAFTであり、製品APIではない。

```text
MinimalDecisionV0
  schema_version = "phase6.minimal-output.applicability.v0"
  tactic
    decision_kind = none | chat | vote | ability | co_declare
    option_id / targets / claimed_role_id（kindごとの既存必須値のみ）
    kind = NONE | CLAIM | QUESTION | ANSWER | REBUTTAL | OPINION_CHANGE | RELATION_HYPOTHESIS
  grounding[]
    purpose = TACTIC | UTTERANCE | REACTION | OPINION_PRIOR | OPINION_CURRENT | PRE_VOTE | CO
    ref = 既存EvidenceRefまたはcurrent/abilityへの閉じたfact ref
  utterance
    text = UTF-8 textまたはnull
    OPINION_CHANGEの場合はsubject/dimension/prior/currentと対応groundingを明示
  pre_vote（PRE_VOTE triggerで必須）
    option_id / ranking / preferred / grounding purpose=PRE_VOTE
```

`tactic.kind`は既存`speech_act.kind`と同じenumを一回だけ選ぶ。別の`dialogue_purpose`や`utterance.kind`を置かず、二重labelを作らない。`decision_kind`はnetwork action種別であり、speech actとのcross-field関係をvalidatorが検査する。

`capture_id`、`context_sha256`、actor/player ID、epoch/revision、day/phase等のhost由来bindingをLLMに生成させない。runnerがsidecarへ保存し、`raw output bytes + exact input bytes/hash + capture/context/revision binding`を一つの診断rowへ結合する。model outputにhost値を複写させてbinding検査の成功数を水増ししない。

最小とは「全caseで全fieldを出す」ことではなく、選んだkindに必要な値だけを出すことを指す。初回候補schemaには`private_updates`を存在させない。captured assessment/claim/relation/strategyはread-onlyで更新0とし、更新が必要なcaseは32件の分母に残したまま`APPLICABILITY_UNRESOLVED`として止める。

### 3.2 なぜgroundingを一種類に潰さないか

- authoritative fact refはserver由来stateを指す。
- `EvidenceRef`は発言やeventが存在したことを指し、その内容の真偽を自動で保証しない。
- tacticの私的理由とutterance中の主張supportは同じ用途ではない。
- `OPINION_CHANGE`はprior/current/causesの関係を持ち、単なるspeech labelではない。
- `PRE_VOTE`はranking/preferredと根拠を持ち、通常発話のgroundingで代替できない。
- CO groundingは宣言という公開event、privateなrole knowledge、戦略的なCO判断を区別する。

よって`grounding[].purpose`を必須とし、参照の存在だけで支持関係をPASSにしない。0件が合法なtacticも保持するが、必要groundingの欠落を空配列で補完しない。

## 4. deterministic validationの責務

validatorは次を順に検査し、値を補正しない。

1. strict JSON、閉じたkey/enum、request binding、UTF-8/size境界。
2. `decision_kind`と既存available optionの一致。role名やaction defaultをhardcodeしない。
3. kindごとの必須/禁止field、`none`とoption nullability、targetの既存authority。現契約どおり`co_report`は拒否し、新しく許可しない。
4. grounding refの存在、source、visibility、captureへの所属、重複、上限、purpose適合。
5. tactic kindとutterance text、decisionとの組合せ、合法NONE、CO tuple、`OPINION_CHANGE`のprior/current/causes、PRE_VOTE rankingの構造整合。`PEER_CHAT`ではreaction trigger/channelとcaptureの一致を必須にする。
6. captured assessment/claim/relation/strategyが入力から変更されていないこと。主観値の出力、host導出、暗黙更新を拒否する。
7. chat/CO textの既存bounded/copy等の機械検査を、意味評価とは別結果で記録する。private disclosure、act/text一致、groundingによるclaim支持は文字列regexや機械screenだけで完全判定せず、独立した意味評価対象として残す。

validatorが決定できないものは、戦略的に最良なaction、claimの真偽、suspicion、relation、strategy、発話が質問に十分答えたか、groundingが本文を意味的に含意するかである。これらをdeterministic PASSへ偽装しない。

結果は少なくとも次を分ける。

- `STRUCTURE_PASS/FAIL`
- `AUTHORITY_PASS/FAIL`
- `GROUNDING_BINDING_PASS/FAIL`
- `APPLICABILITY_COVERED/INVALID/UNRESOLVED`
- `SEMANTIC_REVIEW_REQUIRED`

初回scopeでは`LEGACY_CONSTRUCTIBLE`を判定しない。候補は主観更新を意図的に持たず、legacy全contractを保持しない。必要な旧Decision・参照・OPINION_CHANGE等の検査だけを次表で対応付け、旧validator受理同値、品質同値、network受理を主張しない。

| 必要な旧契約 | 初回候補の検査 | 保持しないもの |
|---|---|---|
| `BrainDecision` kind/option/target | `tactic.decision_kind`と既存available optionを照合。`co_report`は負例として拒否 | dispatch、server acceptance |
| `speech_act.kind` | `tactic.kind`へ同じenumを一回だけ出す | legacy proposal全field |
| EvidenceRef/current/ability参照 | `grounding[]`のsource/visibility/purposeを照合 | 参照内容の真偽・含意の機械判定 |
| `OPINION_CHANGE` | subject/dimension/prior/currentとpurpose別groundingを必須化 | assessmentの自動更新 |
| PRE_VOTE | trigger時にpre_voteを必須とし、option/ranking/preferredとPRE_VOTE groundingを検査 | vote strategyのhost導出 |
| CO | action tuple、公開CO参照、private knowledge参照を別検査 | CO宣言をtruthにする変換 |
| PEER_CHAT reaction | trigger ref、channel、reply groundingのcapture一致を検査 | 発言内容の真偽、応答の意味的十分性 |
| assessment/claim/relation/strategy updates | captured値のread-only不変だけを検査 | 全更新。必要caseは`UNRESOLVED` |

## 5. 32case適用可能性matrix

全32caseを黙って一分母にせず、既存fixtureの公開metadataから次の軸を**事前に**記録する。過去rawやannotationを読み直さない。

| 軸 | 必須確認 |
|---|---|
| trigger | 32件全件を既存trigger enumごとに数え、0件のtriggerも明示する |
| action | none/chat/vote/ability/co_declareをtrigger別に確認し、co_reportを現契約どおり拒否する負例にする |
| NONE | 合法NONE controlを少なくとも既存2件とも保持し、NONEをchat空文へ変換しない |
| PRE_VOTE | trigger時にranking、preferred、option、PRE_VOTE groundingを必須で保持する。発話だけで代替しない |
| CO | declaration/report recordの存在、claimed role/result、private knowledge、strategy判断を分離する。network `co_report`生成は拒否する |
| OPINION_CHANGE | subject/dimension/prior/current/causesを保持し、kindだけ残してgroundingを落とさない |
| PEER_CHAT | reaction trigger/channel、reply対象、REACTION groundingの整合を保持する |
| subjective update | assessment/relation/strategyの更新が必要なら`UNRESOLVED`。初回候補へ明示updateを戻さず、host導出も禁止 |
| grounding 0件 | 合法な質問、保留、騙り、NONEを許す一方、必要根拠欠落と同一PASSにしない |

各rowは`covered / candidate-invalid / applicability-unresolved`のいずれかに必ず分類する。`unresolved`を除外して成功率を出さない。全32件、全trigger/actionのcoverage表と、候補が保持したgrounding purpose一覧を出力する。

現行`ai_client/llm/decision.py::_validate_semantic_output`のtrigger/action境界を正本として再利用する。

| trigger | 許可するdecision kind |
|---|---|
| INITIAL_CHAT / PEER_CHAT | none / chat |
| CO_OPPORTUNITY | none / co_declare |
| PRE_VOTE | none / vote |
| ABILITY | none / ability |

`co_report`は現validatorが拒否するため、全action coverageの負例に含める。「schemaを小さくしてJSONが通った」だけでは適用可能PASSにしない。

## 6. test-only計画

### 6.1 23:00 JSTまでに固定する内容

- 上記責務対応表と1-call仮説。
- 客観state、主観state、network受理の三境界。
- 32case coverage軸と`UNRESOLVED`の扱い。
- provider call数は1、論理stageは4という区別。
- legacy default補完、完全出力再生成、受理同値主張の禁止。

### 6.2 23:00 JST以降に作成するtest case（今回は未実施）

独立review承認後、製品codeを変えず、pure helperとfocused testだけを別taskで作る。

1. strict JSON、unknown key、重複key、unknown enum、request binding差替えを拒否。
2. 全decision kindのrequired/forbidden fieldと全既存action option照合。trigger/action matrix外と`co_report`を負例として拒否。
3. 32caseを1回ずつ列挙し、欠落・重複・分母除外を拒否。
4. 全trigger/action coverage、合法NONE 2件、PRE_VOTE必須再評価、CO、OPINION_CHANGEのgrounding保持。
5. PEER_CHATのreaction trigger/channel/reply groundingをcaptureへ結合し、差替えを拒否。
6. private/current/public refのvisibility/source混同、EvidenceRef存在とclaim支持の混同を拒否。
7. 候補schemaにprivate update keyが存在しないこと、captured assessment/claim/relation/strategyがread-onlyで不変なこと、`ClaimAssessment`をCO truthへ変換しないこと。
8. private commitからaccepted observationを作らず、accepted/rejected evidenceなしで受理扱いしないこと。
9. missing legacy fieldをdefault補完せず、主観更新が必要なcaseを`UNRESOLVED`にすること。
10. validatorがinputを変更せず、provider/network/state commitを呼ばないこと。

予定する一コマンド入口は次だけとする。現時点ではtest fileを作成せず、実行もしない。

```powershell
python -m pytest tests/test_phase6_minimal_output_applicability.py -q
```

この入口はoffline synthetic/既存公開fixture metadataだけを使い、provider、network、旧private raw、旧annotation、再採点へ接続しない。

## 7. 仮説と反証条件

**H61:** 完全なlegacy proposalを一度に生成する代わりに、1-callのtactic/grounding/utteranceだけを生成し、captured主観stateをread-onlyにすると、永続private state更新/commitと公開発話の結合を外した条件を診断できる。客観stateは既にhost由来であり、客観state再生成の削減を効果とはしない。

この候補にもtactic選択、`OPINION_CHANGE`のprior/current、PRE_VOTEのranking/preferred等の主観的推論は残る。「主観判断の負荷を除いた」とは主張せず、除く負荷をcaptured private stateの永続更新とcommitに限定する。

**反対仮説:** 32caseで主観updateが必要なcaseが広く、更新0では`UNRESOLVED`が支配する。または最小化によってPRE_VOTE、CO、OPINION_CHANGE、合法NONEの意味が`UNRESOLVED`へ偏り、診断価値がない。初回候補へoptional updateを戻して反対仮説を隠さない。

次のいずれかなら候補は不適用と判定する。

- 全32件をclosedに分類できない。
- 既存action authorityを再実装しないとvalidatorが決定できない。
- 主観assessment/relation/strategyをhostで推定しないと旧必須情報を保持できない。
- legacy default補完または二回目の完全出力生成が必要になる。
- private commitとauthoritative accepted observationを同一視する。
- NONE、PRE_VOTE、CO、OPINION_CHANGEのいずれかを黙って除外する。

## 8. 残る設計判断と独立review範囲

残る判断は次のとおりである。

1. EvidenceRefとauthoritative fact refを一つのtagged unionにするか、別配列にするか。
2. `tactic.kind`とnetwork `decision_kind`の既存cross-field制約をどこまで再利用するか。
3. 主観更新が必要なcaseを判定する公開fixture metadataと、`UNRESOLVED`へ止める境界。
4. host sidecarがraw/input hash/capture/revisionを結合するexact形式。
5. 既存32件の公開metadataだけで全trigger/action coverageを証明できるか。

加えて、PRE_VOTE再評価のexact closed shape、PEER_CHAT reactionの必要field、OPINION_CHANGEとnetwork decisionの合法な組合せは詳細契約が未確定である。独立review後の詳細設計で解決し、small JSONがparseできることを意味契約の代替にしない。

独立reviewは、authority/privacy、subjective state ownership、CO/ClaimAssessmentの意味、transactionとaccepted observationの分離、全32 coverage、legacy default補完禁止、1-call性、offline-only性を確認する。承認前にhelper、test、runner、provider測定を作らない。
