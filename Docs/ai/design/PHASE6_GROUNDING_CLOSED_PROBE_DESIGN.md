# Phase 6 Grounding-Closed Locked Output 最小実験設計

Task: T460
Status: DRAFT

## 1. 目的と境界

候補GC2は、IC2で選択済みのspeech act kindを再生成せず、変更したstage2だけを最大32回生成するtest-only実験である。IC2は旧validator 6/32、HARD pass 2/32、act/text mismatch 4、固定質問回答4/18で不採用済みであり、確定拒否26件は`CLAIM_ACTOR` 16、`EVIDENCE_REFERENCE` 9、`OPTION_CONSISTENCY` 1だった。旧rawの再採点、IC2の再開、同条件retryは行わない。

検証する単一仮説は、生成schemaが許すEvidenceRefとclaim speakerの組合せを、canonical projectionとcaptureに基づく旧validatorの受理集合へ閉じれば、grounding拒否を減らせるというものとする。LLMのkind選択、疑い、role claim、質問、本文、verdict、confidence、relation、strategy、decisionは固定しない。`OPTION_CONSISTENCY`を含む他の失敗を同時に修正しない。

製品schema/parser、旧validator、model、sampling、context、authority、privacy、game rule、stateは不変。hostはactor/refや出力値を補完・修復しない。新provider callはstage2だけ最大32、各480 tokens、retry0、repair0、slot1とする。

## 2. grounding集合の導出

caseごとにIC2と同じbaseline `PromptProjection`から、次の二集合を独立に構築する。

1. `projected_refs`: `canonical_input.memory.records[*].source`をstrictに読み、`(record_kind, order)`をidentity、`(record_kind, order, visibility)`を完全値とする。
2. `captured_refs`: `discussion_capture.evidence[*].source`を同じidentityと完全値で読む。

いずれも各側に存在する要素の型、既知enum、非負整数、identity一意性を要求する。片側だけに存在する正形式refは、projectionの保持上限やcapture保持との差として合法であり、`allowed_refs`から除外するだけでrunを停止しない。片側内の同一identity重複、malformed値、または両側に同じidentityがあるのに完全値、とくにvisibilityが異なる場合だけ`GROUNDING_INPUT_INVALID`としてprovider前にfail-closedとする。有限`allowed_refs`は、identityが両方に存在し、完全値が一致する参照だけの積集合である。順序は既存`evidence_sort_key`相当の`record_kind, order, visibility`固定順とし、入力順を意味に使わない。32入力では集合数0〜2を記録するが、具体値はprivate evidenceだけへ保存する。

`allowed_claim_tuples`は`allowed_refs`の各refについて、その積集合refに対応するcapture eventだけを参照し、`actor_player_ids`がexact 1、actorがselfでなく、actorがcanonical captureの`current_player_ids`に存在する場合だけ作る`(ref完全値, actor_player_id)`である。片側だけのrefからtupleを作らない。actorが0人、複数、self、current外ならtupleを作らない。tuple重複や一つのrefに異なるactorが導かれた場合は入力不整合として停止する。これは旧`_validate_semantic_output`のclaim authority条件を狭めず写すもので、発言の真偽やverdictを判定しない。

schema構築後にも、列挙した全ref/tupleを元projectionへ組み込んだ有限fixtureが旧`parse_llm_output`に受理され、集合外ref、visibility変更、actor変更、self/multi/no actorが旧parserと候補schemaの双方で拒否されることを確認する。旧validatorが受理しない値を候補集合へ入れない。

## 3. Grounding-closed stage2 schema

IC2の`locked_schema(legacy_schema, reused_choice)`をdeep-copyした後、次だけを変更する。root、decision、選択speech branch、その他の値・key順はIC2とJSON値同一にする。

### 3.1 EvidenceRef

`allowed_refs`が1件以上なら、`$defs.evidence_ref`を完全なclosed object branchの`oneOf`へ置換する。各branchは`record_kind`、`order`、`visibility`をそれぞれ`const`とし、required三field、`additionalProperties=false`とする。したがってspeech、assessment、claim evidence、relation、strategy、pre-voteで到達する全EvidenceRefは有限集合内に限られる。

`allowed_refs`が0件なら、converter非対応のboolean `false` schemaや`not`、空enumは使わない。代わりに共有`evidence_array`を`type=array, maxItems=0`へ閉じ、空配列だけを許す。選択speechがQUESTIONなら`source`を`const:null`へ置換する。`claim_updates`も空配列だけにする。ANSWER/REBUTTALの`in_reply_to`、OPINION_CHANGEの非空`causes`のように必須refを持つ選択kindは合法値がないため、schemaを捏造せず`NO_LEGAL_GROUNDING`のcase failureとしてprovider前に終了する。GC2で実際に再利用するchoiceがこの条件に当たる場合も同じ扱いとし、分母から除外しない。

空配列化は`assessment_updates[*].evidence`、`claim_updates[*].evidence`、`relation_updates[*].evidence`、`strategy_update.evidence`、`pre_vote_reassessment.evidence`、CLAIM/RELATION_HYPOTHESISの`evidence`にも一貫して効く。nullが旧schemaで合法なQUESTION `source`と`strategy_update`はnullを維持する。PEER_CHAT `reaction.trigger`はbaselineが既にtrigger refのexact `const`であり、その必須trigger refが片側保持差により`allowed_refs`から除外されたcaseだけを`NO_LEGAL_GROUNDING`、output call 0とする。必須位置でない片側refの除外はcase failureにしない。

### 3.2 claim/actor tuple

`allowed_claim_tuples`が1件以上なら`$defs.claim`をtupleごとのclosed `oneOf`へ置換する。各branchは`claim`をref完全objectの`const`、`speaker_player_id`をactorの`const`とし、baseline claim branchから`verdict`、`confidence`、`evidence` schemaを値同一で保持する。required、field集合、max `claim_updates=4`も維持する。LLMは許可tuple、verdict、confidence、根拠を選ぶ。

tupleが0件なら`$defs.claim`へ不可能schemaを作らず、`$defs.claim_updates`を`type=array, maxItems=0`へ閉じる。合法な`claim_updates:[]`を維持し、hostはtupleや空要素を追加しない。非empty集合でも`[]`は合法のままとする。

候補schemaは既存keywordだけで構成し、公式converterで全32caseを変換する。生成値はstrict JSON、GC2 schema、reused kind一致、元projectionの`parse_llm_output`の順で検査し、全合格時だけ受理する。旧validatorは最終境界のままである。

## 4. IC2 choiceの厳密replay binding

GC2は新claim/freezeを持つ別候補であり、IC2のchoice provider callを一切行わない。freezeするrun証拠は訂正版公開安全集計SHA-256 `43acb2a1fa7887063d7a74a753eb4eb9f9de355b8106455d3ec9467cc91a2134`、IC2 result SHA-256 `cf1924995825431a6797486a528468a25ac2f337d70e5bfa7da3fb2b7dc3c3e2`、private binding safe SHA-256 `a54b72270a8b217845c8520254cff0f0ae183aee0eba154b15d33bbdbe3a7df8`とする。IC2実行時sourceは`logs/t448-quality-cycles/ic2-approved-source/manifest.json`と同directoryの保存原本9filesをpath/SHA/bytesで正本とする。private locator/path/raw本文は公開manifestへ書かない。

preflightで32件のcase順・集合がexact一致し、各caseについて次を再計算・照合する。

- case ID、baseline canonical input SHA、projection SHA、baseline body/messages/schema SHA。
- IC2 choice request wire SHA、保存request.bin SHA、consumed recordのcase/stage/input/wire/max_tokens。
- private choice raw recordのcase/stage、raw SHA、公開`choice_output_sha256`。
- rawのstrict JSON、IC2 choice schema合格、canonical choice SHA、公開selected kind。
- IC2保存原本manifestと9filesのpath/SHA/bytes、result/safe/binding fileの全体SHAと相互参照、元config/model/runtime識別子。

32件すべてが合格するまでclaim取得、model起動、新request作成をしない。欠落、余分、重複、順序差、任意cache探索、最新版fallback、別run混入は`REPLAY_BINDING_MISMATCH`で停止する。現在tree全体が旧IC2 sourceと一致することは要求しない。保存原本9filesからの許可差分は次の二群だけとし、各entryをexact path・旧SHA・新SHA・変更理由・承認または独立tool review artifact SHAへ束縛したmanifestでfreezeする。wildcardやdirectory単位の許可はしない。

1. 既承認CI可搬性差分: `tests/test_phase6_two_stage_probe_runtime.py`と`tests/fixtures/phase6_p2_runtime_golden.json`の2filesだけ。mock argvのWindows/Linux表現差を正規化するtest/fixture変更に限定し、provider inputやruntime挙動を変えない。
2. GC2実装差分: `scripts/phase6_two_stage_probe_runtime.py`、`scripts/phase6_probe_outer.py`、必要な共有回帰である`tests/test_phase6_two_stage_probe_runtime.py`と`tests/test_phase6_probe_outer.py`だけ。GC2固定同期入口、outer mapping、その非回帰testに限定し、GC2独立tool review artifact SHAへ束縛する。新規GC2 helper/runner/focused testは別の新source allowlistへexact path/SHAで列挙する。

両群にない旧9file差分、入力fixture、製品code、`scripts/phase6_intent_choice_probe.py`、`scripts/phase6_intent_choice_runner.py`の差分を拒否する。重複対象`tests/test_phase6_two_stage_probe_runtime.py`はCI変更とGC2追加の各patch/hashを別entryで連鎖させ、最終SHAだけによる由来の省略を許さない。現在のcanonical 32入力からbody/projectionを再計算し、保存IC2各hashとの一致を別に必須とするため、許可差分でreplay入力は変化できない。検証後はrawを再採点せず、strict parse済みの一field choiceだけをcase-local immutable値としてstage2 schemaとinstructionへ渡す。replayed choice、raw、秘密値を公開rowへ流さず、公開rowはhash、selected kind、検査boolだけを持つ。

## 5. messages・wire・予算

GC2 output messagesはIC2 outputと同じbaseline system/user二件および同じ固定output instruction＋canonical reused choiceをbyte-equivalentに保つ。sampling、model、context8192、max_tokens480、request60秒、load180秒、model1200秒、cleanup25秒、outer1320秒を据え置く。実験差はgrounding-closed schemaだけであり、IC2 output bodyとの差分hashを別保存する。

各caseで`prompt_tokens_actual + 480 + 1 <= 8192`、provider usage一致、completion<=480を検査する。output前reserveはIC2と同じ120秒、`reject_when_remaining_equal=false`とし、120秒ちょうどを許可、未満をrequest.bin/consumed/counter作成前に拒否する。length/timeout/budget不足は当該caseまたはrunの既存固定失敗として32分母へ残す。

resultは`reused_choice_count=32`、`reused_provider_calls=32`、`new_output_provider_calls<=32`、`new_provider_calls<=32`を別に持つ。reused choiceのprompt/completion usageと既存latencyはsource IC2の固定集計として別欄に引用し、GC2のcurrent usage、call budget、REAL durationへ加算しない。各rowは`choice.status=REUSED`とsource hashes、`output_provider_calls` 0/1、新output usage/latency/statusを分離する。合計を64 new callsと表示しない。

## 6. runtime・失敗原子性

共有runtimeにはGC2専用の同期入口`run_replayed_choice_output_probe(out, *, contract, callbacks)`だけを最小追加する。既存`run_two_stage_probe`、`P2_CONTRACT`、`IC2_CONTRACT`、P2/IC2 runner pathと結果契約は変更しない。新contractは固定experiment/task、単一stage `output:480/reserve120/equal false`、max new calls32、replay source hashesをexact値として持ち、未知contractをclaim前に拒否する。

既存ownership lifecycleの内部処理を機械的に共有し、plan/result/claim、全32 row初期化、private container、model/listener/runtime/monitor、deadline、唯一のoutput dispatch、request/consumed/raw/usage、durable result、cleanup、source/config再確認を共通側が所有する。GC2 callbackはreplay preflight、grounding schema/body構築、case結果検査だけを行い、process/network/private path/result保存を所有しない。大きな汎用runtime、cache service、daemon、routerを作らない。

replay preflight失敗はclaim/process/provider call 0。claim後の全経路は既存共通`finally`で所有processだけをcleanupし、公開resultを保存する。必須ref kindまたは必須PEER triggerが積集合から除外されたcaseの`NO_LEGAL_GROUNDING`はoutput call 0、`generation_status=INVALID`として残りcaseを続行する。schema/旧validator不合格は出力全体を破棄し、state/send副作用は常に0。保存原本、固定差分allowlist、再計算input/body/projection、configの差はprovider前、実行中に許可外source差が生じた場合はcleanup後にfail-closedとする。

outerにはGC2 experiment/task/runner mappingだけを追加する。P2/IC2の決定的MockTransportでbody/wire/private/public result、reserve境界、ownership/cleanupが変更前と値同一であることを回帰する。

## 7. focused acceptance

- 全32 projectionで`allowed_refs`件数0〜2、完全値交差、一意性、固定順を検査する。片側だけの正形式refを除外して継続するpositive、両側same identityのvisibility/完全値衝突、malformed、片側内duplicateをprovider前に拒否するnegativeを分ける。
- refs 0で全到達evidence arrayが`[]`、QUESTION sourceがnull、claim_updatesが`[]`だけを受理する。必須ref kindは`NO_LEGAL_GROUNDING`かつcall 0。boolean false/not/空enumを使わず公式converter 32/32を通す。
- claim tupleは単一non-self actorだけを列挙する。legal tupleの`[]`/1〜4件を受理し、refと別actorのcross-product、self、multi/no actor、集合外player/refをschemaで拒否する。verdict/confidence/根拠は固定しない。
- 全7 speech actと全trigger/5 action、合法NONE、null/empty、no-message、PRE_VOTE/CO/ABILITYを静的fixtureで通す。必須refがあるkindはref存在caseで検査する。
- 既存authority-closed positive matrix 13件を候補schema→旧parser経路で受理し、元結果と一致させる。EvidenceRef/visibility、claim actor、option、alive/dead、owner-only ability、prior/new cause、reaction/CO/pre-vote、updates、text/bytesの代表negativeは旧parserより広く受理しない。
- IC2 choice32件について保存原本manifest/9files、全hash/binding、CI 2file差分、GC2 runtime/outer/shared-test差分、新GC2 source allowlist、現在再計算body/projectionを照合し、choice endpoint 0 callをMockTransportで証明する。各差分entryの旧新SHA・理由・review binding欠落、未列挙差分、入力/product/IC2 helper差分、missing/extra/reordered case、raw/request/consumed/result/safe/projection/body/wire/kindの一点改変をclaim前に拒否し、任意fallbackがないことを検査する。
- resultのreused/new call・usage・latency分離、new最大32、480、retry/repair0、全32 row分母、reserve境界、length/invalid保持を検査する。秘密/raw/本文を公開rowへ出さない。
- P2/IC2既存focused、共有runtimeの即spawn exit、PID再利用、非所有process保護、private save failure、cleanup、source/config不変を回帰する。

独立Reviewerは、集合導出が旧validatorと同値か狭いこと、空集合が合法`[]`/nullを壊さないこと、claim tuple以外の判断をhostがしていないこと、choice replayが32件完全拘束され新call 0であること、共有runtime接点、accounting、privacyを確認する。

## 8. 一回測定と意味gate

独立設計APPROVED、最小実装/focused、独立tool review、freeze後に独立Testerが新outputを最大32call一回だけ生成する。32case全件を意味評価の分母とし、preflight case failure、call未実行、schema不合格、旧validator不合格、lengthを除外しない。baseline/IC2の旧意味評価を再実行しない。

既存相対gateはact/text mismatch `<23`、HARD fail `<=14`、FABRICATED_EVIDENCE `<=0`、SECRET `<=5`、STATE_CONTRADICTION `<=3`、ABILITY_CONTRADICTION `<=0`、各UNKNOWN非増加、SEMANTIC pass `>=7`、固定18質問回答`>=9`、合法NONE維持とする。GC2固有に`CLAIM_ACTOR`と`EVIDENCE_REFERENCE`の件数を保存するが、そこだけの減少で採用しない。

通常の不採用は6時間/6cycle全体の停止条件ではない。同条件retryなしで証拠を保存し、残り上限内の独立承認済みcycleへ進む。製品採用には別途D077のHARD 100%、重大秘密/状態矛盾0、長文exact copy 0、独立reviewが必要である。
