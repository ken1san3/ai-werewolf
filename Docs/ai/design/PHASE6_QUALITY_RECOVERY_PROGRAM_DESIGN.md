# Phase 6 品質回復program 詳細設計

Task: T506  
Status: APPROVED

## 1. 目的と固定境界

本programは同じ32 synthetic caseを使い、`new baseline`、既存GB1、GB1への有限機械filter、最良候補のQwen/Gemma比較を、事前固定した3 seedで対照付き測定する。探索合格と製品受入を分け、適格な候補だけを製品採用reviewへ送る。旧D078〜D080のraw、annotation、集計、判定は改変・再採点せず、仮説の参考にだけ使う。新しい生成物は別experiment ID、保存先、hash chainを持つ。

製品schema、server authority、privacy、game rule、state更新、送信経路、通常game、Phase 7はこの設計の対象外である。test-only探索はMainが実装・実行・記録できるが、本設計は一度独立reviewを通す。各候補に同じreview chainを繰り返さず、製品採用時だけ対象bytesと受入証拠を独立reviewする。

## 2. 事前登録した比較と判定

### 2.1 主指標とcluster

主指標は本文だけを読む `semantic_annotation` と、比較用の数値 `semantic_outcome` を分離する。`semantic_annotation` はcanonical inputと会話文脈に対する本文内容の妥当性だけを `PASS/FAIL/UNKNOWN` で保存し、metadata、speech-act、structural statusを混ぜない。探索annotationはG4によりMainが行える。製品採用時には対象出力を独立担当が同じrubricで判定し、その証拠を最終gateへ束縛する。

`semantic_outcome` はstructural受理済みかつannotation PASSだけを1、それ以外を0とする。structural statusにかかわらず、生成物から本文を安全かつ一意に取得できる場合はcontent-only rubricでannotationする。構造不合格でも取得済み本文は内容に応じてPASS/FAIL/UNKNOWNを保持するが、outcomeは必ず0とする。本文未生成、取得不能、曖昧、または内容判定不能の場合だけannotation UNKNOWNとする。未生成、timeout、deadline後の `NOT_RUN` もoutcome 0とし、原因statusを別fieldに保存して32 case分母へ残す。arm内にUNKNOWNが一件でもあれば数値を表示した上で主比較を `INCONCLUSIVE` にする。したがって0への変換でUNKNOWNや欠測を隠さない。旧SEMANTICとrubricが異なる場合も旧結果を再採点せず、新しい同時対照だけで比較する。

seedは `4242027 / 4242028 / 4242029` に固定し、全arm・modelで同じcase/seed対応を使う。96出力を独立caseと扱わない。caseごとに3 seedのPASS率を作り、候補と同じcaseの対照との差を求め、32個のcase差をcluster単位とする。探索非劣性marginは `-2/32 = -0.0625` とする。bootstrap seed `20260924`、100,000回、case indexの昇順入力、実装versionをplanへ保存したcase-cluster bootstrapで平均差の片側95%下限を求める。

- 下限 `>= -0.0625`: 探索上の非劣性。
- 下限 `> 0`: 探索上の優越。
- UNKNOWN、32 cluster未完、対応seed欠落、下限が条件未達: `INCONCLUSIVE` または不適格。小標本から同等・一般優越を主張しない。

探索では主・副指標の非劣性と、安全指標の観測結果を分けて保存する。非劣性未達や安全違反があっても、それを採用可能と扱わず、事前登録済みの安全な次armとU5比較は有限予算内で実施する。製品受入では全96出力のstrict structural受理とHARD passに加え、`FABRICATED_EVIDENCE`、authority外参照、`SECRET_DISCLOSURE`、`STATE_CONTRADICTION`、`ABILITY_CONTRADICTION`、`EXACT_PEER_COPY` を各0、各UNKNOWNも0とし、既存D077の境界と対象実装の独立reviewを要求する。平均改善、質問回答、style、speech actで絶対条件を相殺しない。

副指標は固定18質問の `content_answers_question`、全32件の `style_pass`、全32件の `hard_pass` とする。同じcase cluster法で、marginは順に `-1/18`、`-2/32`、`-1/32` の片側95%下限を要求する。最後の値はHARD失敗率なら候補−対照の片側95%上限 `<= +1/32` と同値である。該当しないcaseを質問分母へ足さず、三seedを独立件数にしない。いずれかのUNKNOWN、対応欠落、下限未達は副指標INCONCLUSIVE/不適格とする。speech-act一致とreason codeはmarginを持たない診断であり、主・副指標および最終絶対条件間の相殺はしない。

### 2.2 structuralとspeech act

strict JSON、candidate schema、locked field、catalog ID、既存legacy validator、option/actor/ref/visibility、duplicate/nonfinite/extra/missing fieldの検査は全attemptで維持する。validatorを緩和せず、機械受理・不受理のどちらも本文意味annotationと呼ばない。strict structural、HARD、metadata診断はsemanticとは別field・別集計とし、探索選抜と製品絶対条件でそれぞれ明示的に要求する。

`speech_act` と本文の意味的一致は主指標・最終ゼロ条件から外し診断として保存する。hostは本文からkind、参照、回答先、真偽を推定・修復しない。構造化されたlocked kindの一致は引き続きstrict structural条件である。

## 3. armと最小変更順序

1. **G1 new baseline / GB1**: baselineと既存GB1を同じ32 case×3 seedで新規生成する。旧rawは使わない。GB1は既存どおり、call 1で `speech_act_kind` と `authoritative_fact_ids` 0〜2件を選び、call 2でkindを固定し、選択basisを含むlegacy full outputを生成する。
2. **G2 GB1+filter**: G1と同じGB1 choiceをcase/seedごとに一度だけ使い、outputを第3attemptまで有限再生成する。第1attemptはG1生成物をexact hash bindingで再利用できる。再利用不能なら同一seedの再生成をせず当該rowを失敗にする。変更点は機械filterと有限停止だけである。
3. **G3 段階生成+grounding**: 新armや新promptを作らない。既存GB1がすでに「意図の最小構造であるkind＋authoritative basis選択→locked本文」の段階生成を構成するため、G1/G2のpaired 3-seed結果をこの仮説の検証とする。call 2がlegacy full output全体を一度に担う限界は記録し、結果不足時に隠さない。
4. **U5 model比較**: comparison integrityを満たすtest-only構成から一つを、(1) `semantic_outcome` 平均が最大、(2) HARD失敗数が最少、(3) 製品絶対条件の違反＋UNKNOWN合計が最少、(4) provider call数が最少、(5) arm ID辞書順、の順で決定的に選ぶ。製品不適格でも比較を実施し、その場合は「探索対照に選んだだけ」と明記して採用候補と呼ばない。選んだ構成をQwen3.5-9Bと既存Gemma3-12Bで測る。

   共通固定条件はcase、seed、projection/body contract、schema、sampling parameter、token/context budget、K、validator、annotation rubric、deadline/call accounting、直列slot 1である。一方、chat template、offload構成、model file、server/runtime file、launch引数はモデルを実行する既存profile固有の不可避差であり、同一とは主張しない。各profileの全値・file hash・argvをplanで事前freezeし、比較結果へ差を列挙する。先行するQwenの同一bytes証拠は再利用し、Gemmaだけを追加生成できる。共通条件の差、未登録差、profile drift、identity/hash不一致があれば比較を無効とする。

次段へ進めるのは前段の保存・hash検証後だけとする。不適格でも安全な次armへ進めるが、条件を事後変更したretryは新cycleとして数え、事前設計なしには行わない。

## 4. authoritative候補とLLM選択

U3が許可する上限は、本人に届いた能力結果、公開CO宣言、公開の生死、構造fieldで宛先と未回答状態が明示された本人宛て発話である。ただし今回の実測subsetは、既存 `phase6_grounding_basis_probe.fact_catalog` がcanonical inputから決定的に作る `{id,pointer}` exact catalogだけとする。具体的にはcurrentのday/phase、playersのaliveと存在する公開death、alive/vote candidate ID、本人viewにあるability result scalarであり、順序、`f000`からのID、最大64、pointer解決規則を変更しない。

既存projectionに宛先・未回答を保証するmetadataがないため、本文から推測して本人宛て未回答発話を追加しない。公開COも既存catalogへ新しい候補型を足すとG1の単一変更を破るため今回は追加しない。許可上限の未使用部分は欠落ではなく将来の別設計対象である。LLMは既存closed schemaで0〜2個のIDとkindを選ぶ。hostは選択、本文、actor、target、claim、真偽を作らず、本文regex、意味推定、未回答推定、主観補完をしない。未知ID、重複、上限超過、pointer不一致はcall 2前に拒否し、catalogと解決値はprivate evidence、公開記録はcountとhashだけにする。

## 5. 有限filterとtransactionality

`K=3` はoutput stageの総attempt数で、同一case/seedのchoiceは一回、outputは最大3回、成功時に早期終了する。choice不合格または `NO_LEGAL_GROUNDING` はoutput再生成で直らないため、output call 0でrowを終了する。1 case/seedは最大4 provider call、1 candidate/model armは最大384 callである。retry、repair、fallbackを別枠で追加しない。

各attemptは同じimmutable projection、choice、catalog、model profileを使い、output sampling seedだけを `base_seed + attempt_index * 1009`（attempt index 0〜2）へ決定的に写す。同じbodyとseedの無意味な再送を拒否する。呼出し前にdeadline、call budget、source/config/body/wire/contextを検査し、durable `attempt_started` を保存してから一回だけ送る。raw、request、usage、status、機械reject理由、hashをprivate durable evidenceへattempt別に保存し、失敗を上書きしない。公開rowにもattempt数、初回結果、最終受理index、各reject codeを安全な形で残す。filter前の各raw判定、棄却、未生成、filter後の受理をすべて分母と証拠に残す。

受理filterはstrict structural validator全条件に、closedな機械検出だけを加える。許可外ID/ref/actor/option、owner/visibility違反、structured stateとのexact contradiction、secret fieldのpublic出力、既知のexact-copy境界を含められる。自由文の妥当性、質問回答、嘘・推理・皮肉・役職主張の真偽はfilterで分類しない。機械検出不能な意味失敗はannotationと最終gateで落とす。

attemptが受理されるまで候補値をstate/sendへ渡さない。受理時もtest-only探索では保存するだけで、製品side effectは常に0。timeout、保存失敗、validator例外、deadline、K枯渇はcase failureとし、最後の不合格値を採用しない。claim後は所有provider/monitorだけを共通`finally`でcleanupし、非所有processに触れない。

## 6. runner再利用境界とruntime

旧 `scripts/phase6_grounding_basis_runner.py` はT480のsource、CI、baseline binding、1 seedへ固定されているため変更・直接再利用しない。新しいthin finite runnerを作り、次だけを明示的に再利用する。

- `phase6_grounding_basis_probe.py` のcatalog、choice/output body、strict validationというpure関数。
- `phase6_model_comparison.py` のcanonical 32 cases/projection、`request`、実token計数、model profile/launch引数、private sink、`cleanup_owned`。profile値はplanへfreezeし、暗黙のcurrent値を使わない。
- `phase6_two_stage_probe_runtime.py` のdeadline、durable marker、call accounting、owned lifecycleの挙動。既存contractへ候補を偽装せず、新runner内の小さな有限loopから同じprimitiveを呼ぶ。

新runnerはplan/freeze、32×3の全row事前作成、arm/model/seed/case/attemptの一意key、paired seed binding、call上限、REAL deadline、raw/public分離、source再確認、cleanupを所有する。共有runtimeを大きく一般化せず、旧runner・旧artifact・旧判定を変更しない。

providerは常にslot 1で、一時に一つのowned serverだけを許す。QwenからGemmaへ移る前にlistener、PID identity、monitor、GPU ownerの終了とremaining 0を確認する。非所有listener、所有権不明、model/profile不一致では起動・killをせず停止する。request 60秒、load 180秒、model 1200秒の既存上限は **1 model・1 arm・1 seed・32case block** ごとに適用し、三seedを一つの1200秒blockへ詰めない。各blockを一runとして保存・cleanupし、program全体の6 REAL時間deadlineを優先する。

## 7. 全体予算、停止、記録

program開始時刻とdeadlineをplanへ固定し、最大6 cycle、開始から6 REAL時間、全arm合計最大864 provider callとする。内訳上限はbaseline 96、GB1 192、G2の追加output 192、Gemma最終比較384である。G2は同program G1のchoiceと第1outputをexact hash bindingで必ず再利用し、過去T480原本や別runを再利用せず、同条件第1attemptを再送しない。Qwen最良構成もG2証拠を比較へ再利用する。各candidate/model arm最大384、各case/seed最大4を越えない。再利用はcall 0として数える。callはrequest送信直前のdurable markerで消費済みと数え、不明な送信結果を未消費へ戻さない。

deadlineまたは上限到達時は新callを開始せず、未開始rowを `NOT_RUN` として32 case分母に残し、その比較をINCONCLUSIVEにする。通常の不適格は保存後に次の事前登録armへ進める。6 cycle、6時間、864 callのいずれかで必ず停止する。

G4の各cycle記録は、plan/source/model/runtime hash、arm、単一変更、case/seed、全attempt status/hash/reject code、call数、REAL時刻、cleanup、structural集計、独立annotation hash、cluster比較、ゼロ条件、`QUALIFIED/NOT_QUALIFIED/INCONCLUSIVE` だけを必須とする。本文・raw・catalog値・秘密はprivate保存し、公開文書へ転記しない。過去raw/annotationを再採点しない。

## 8. focused acceptanceと採用lifecycle

- seed対応、32 cluster、欠落を含む分母、margin、固定bootstrapをfixtureで再計算し、96独立扱い、UNKNOWNのPASS化、unpaired比較を拒否する。semantic annotationとoutcomeの分離、structural invalidでも安全かつ一意に取得できた本文へのcontent annotation、structural/missing/timeout/NOT_RUNのoutcome 0＋原因status、本文取得不能・曖昧・判定不能時のUNKNOWN、UNKNOWN存在時INCONCLUSIVEも検査する。
- baseline 1 call、GB1 2 call、K=3で最大4 call、choice fail時output 0、early stop、G1 choice/first-output再利用、派生seed、同body/seed再送拒否、K枯渇、deadline境界、864全体capをMockTransportで検査する。
- 全attempt保存、初回failと最終acceptedの分離、一意key、送信不明時の二重call防止、private保存失敗、cleanup、非所有process保護、side effect 0を検査する。
- authoritative catalogが既存GB1の許可pointer、決定順、ID、最大64とexact一致すること、および本文推測、CO/未回答候補の追加、stale/secret/unknown/duplicateを拒否することを検査する。
- strict validatorとclosed filterが既存受理境界を広げず、自由文意味を判定しないこと、speech-act診断を主判定へ混ぜないことを検査する。
- Qwen/Gemmaで共通固定条件が一致し、template/offload/file/launch引数はfreeze済みprofile固有差だけであること、各差を結果へ記録すること、provider ownershipをslot 1で直列化することを検査する。

探索で適格候補がなければ製品を変更せず、証拠を残して終了する。適格候補がある場合だけ、対象製品差分、全96の絶対条件、runtime/transactionality/privacy、回帰を独立Reviewerが確認する。承認後に限定採用しfocused/regressionを実測する。短い実gameはその後の別gateであり、本programの探索合格だけでは開始しない。

## 9. 独立review前の限定修正理由

- placeholder seedでは再現不能なため、generation三値とbootstrap seedを具体値へ固定した。
- 探索で安全違反を観測することと製品の絶対ゼロ受入を分離し、許可済みU5が早期に消える循環条件を除いた。
- G1の単一変更と既存実装を守るため、U3の許可上限から今回使う既存GB1 exact subsetを分離した。
- Gemmaの実測時間を収めるため、既存1200秒を従来どおり32case×1seedの有限blockへ適用した。
- model profile固有のtemplate/offload差を実行上の不可避差としてfreezeし、未登録driftと区別した。
- content-only annotationと保守的な数値outcomeを分け、欠測を分母に残しながら意味FAILを捏造しない形にした。
