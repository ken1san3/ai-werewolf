# Phase 6 Grounding Basis Isolation 最小実験設計

Task: T477
Status: DRAFT

## 1. 単一仮説

GB1は、自由文を作る前にモデル自身が利用するauthoritative factを有限IDで明示選択すると、構造化参照制約だけの場合より状態・能力の取り違えが減る、という一仮説だけを検証するtest-only実験である。

現行projectionには`grounding.current`と、budget内でnewestを必須保持する`grounding.ability_results.records`が既にあり、systemにもこれらが公開claim/guessより優先する契約がある。よって欠陥を「authoritative state欠落」とは判定しない。観測上の未分離点は、自由文生成前にどのauthoritative factを使うかを選ぶ閉じた段階がないことである。

注意書き追加、oneOf/field順変更、role配置、モデル、製品schema、ゲームrule、state更新、既存grounding ref grammarは変更しない。正当な推理・疑い・騙りを禁止せず、hostが本文の真偽や戦略を決めない。

## 2. 固定identityと範囲

- experiment: `grounding_basis_v1`
- 略称: `GB1`
- 実装task: `T479`
- 一回測定task: `T480`
- helper: `scripts/phase6_grounding_basis_probe.py`
- runner: `scripts/phase6_grounding_basis_runner.py`
- 保存先: `logs/t476-continuation/frozen-gb1`
- design/tool review: `T478_GROUNDING_BASIS_DESIGN_REVIEW.md` / `T478_GROUNDING_BASIS_TOOL_REVIEW.md`

prepare、plan、manifest、runner、outer、freezeは上記をexact照合し、一点違いをprovider起動前に拒否する。既存32case、canonical Qwen9、context 8192、choice 32/output 480、合計512、最大64 call、retry 0、repair 0、slot 1を維持する。

## 3. authoritative fact catalog

各caseの既存canonical projectionだけから、次のatomic leafへのJSON Pointerを決定的順序で列挙する。製品`PromptProjection`自体は変更しない。test-only helperがfrozen `projection.canonical_input`をplain deep copyし、そのcopyへcatalogをexact一回追加する。既存canonical JSON規則（UTF-8、`ensure_ascii=False`、key sort、compact separator、非有限値拒否）でuser contentを再生成し、baseline bodyの唯一のuser messageだけを置換する。call1/call2は同じaugmented user bytesを使う。新しい`PromptProjection`を構築せず、旧`prompt_sha256`をaugmented messageのhashとして流用しない。

1. `/grounding/current/day`、`phase`
2. `players`各要素の`alive`と、存在する公開`death/day`、`death/public_cause`
3. `alive_player_ids`と`vote_candidate_player_ids`の各要素
4. `/grounding/ability_results/records`各要素の`event_type`、`target_player_id`、`result_id`、`revealed_role_id`の存在する値

別authority source、`state.assessments`、memoryのpublic claim、自由文、role属性、private情報、欠損値はcatalogへ入れない。player IDやfact値をsystemへ移さない。各pointerへ`f000`から昇順のopaque IDを割り当て、user canonical data領域へ`grounding_basis_catalog:[{id,pointer}]`として追加する。catalogはpointer先の値を複製しない。元canonical inputはそれ以外byte/value不変で、catalogのID、pointer、解決値、source projection SHAをhostが送信直前に再照合する。

catalogが空でもcaseは有効である。上限を64 factsとし、超過、unknown path、非atomic値、重複pointer、順序差、許可外section、array index不一致、pointer解決不一致はprovider前`GROUNDING_CATALOG_INVALID`で当該caseを失敗にする。元projection/input/message hashとaugmented input/user/catalog hashを別fieldへfreezeし、元projectionの不変、catalog以外のJSON差0、送信直前のpointer解決値一致を検査する。

## 4. 二段階protocol

call1 schemaはclosed object exact二fieldとする。

```json
{
  "speech_act_kind": "既存7値enum",
  "authoritative_fact_ids": ["catalog内opaque ID、最大2、unique"]
}
```

空配列と1～2件を全7 kindで同条件に許可する。`NONE`のnonempty basisも拒否しない。これはNONEの意味を設計側で狭めず、推理、質問、未確認の主張、合法な騙りを閉じないためである。

最大2はchoice 32 tokensを維持する最小上限である。実装前offline gateでは、最大長opaque ID二件を含むcanonical choice **output bytesそのもの**を固定公式tokenizerで計数し、completion tokens `<=32`を要求する。owned server native gateでは別に、各実bodyのrendered promptについて`prompt_tokens_actual + 32 + 1 <= 8192`をgeneration消費前に検査する。offline choice output計数をnative prompt/context PASSとして扱わず、いずれか不合格ならproviderを消費せず設計へ戻す。

call1 instructionの変更は「後続応答で参照するauthoritative fact IDを0～2件選ぶ」というfield意味の説明だけに限定する。警告、罰則、事実の言換え、例、推奨kindを追加しない。

call2は選択kindを固定したC5済みGC2 schemaを値変更なしで使う。stage control配置は承認済みSC2と同じfirst system appendを維持するが、systemへ追加するcanonical planは`authoritative_fact_ids`を含むopaque choiceだけで、catalog、fact値、player ID、role、本文、rawを含めない。user canonical dataはcall1と同じcatalog付き二message目に置く。call2 instructionはopaque IDをuser catalogで解決して選択basisとして扱う、という新field意味だけを加え、truth warningや本文ruleを追加しない。

strict JSON、duplicate/nonfinite/overflow拒否、choice schema、ID存在、全kind共通の0～2件制約、locked kind、C5 candidate schema、kind一致、既存`parse_llm_output`の順で検査する。hostはfact、本文、decision、actor、targetを補完しない。choice不合格または`NO_LEGAL_GROUNDING`ならoutput call 0、fallback/reuse/repairなしとする。

## 5. 旧choiceと比較境界

call1 schemaとcanonical user bytesが新しいため、IC2/SC2 choiceを再利用しない。全32 choiceを新規生成し、valid choiceだけ最大32 outputを一回生成する。旧IC2/GC2/SC2 rawを再読、再生成、再採点しない。比較は保存済み集計とsafe metadataだけを使う。

SC2との差はcatalog追加とexplicit basis選択を一つのcandidate interventionとして扱う。choiceが変わるため、個別case差をbasis効果と断定しない。C5の既知4case grammar cohort、chosen kind差、空/nonempty basis、ability basis有無を診断として分けるが、主分母と採否は全32である。

## 6. runner・privacy・有限停止

全32 rowをprovider前に初期化する。各stageでreserve（choice 240秒、output 120秒）、body/wire/schema/projection/catalog/choice binding、native apply-template、tokenize、context式、private durable保存をgeneration消費前に検査する。失敗rowと未処理rowを32分母へ残しrunを停止する。request60秒、load180秒、model1200秒、cleanup25秒、outer1320秒を維持する。

公開rowはcase/status、selected kind、選択fact ID数、catalog/body/schema/wire hash、usage、validation boolだけを持つ。catalog pointer/value、canonical user、raw、rendered prompt、本文、秘密はprivate evidenceだけへ保存する。request.bin、apply-template wire、generation wireをexact一致させる。所有provider/monitorだけを共通`finally`でcleanupし、owned remaining 0を要求する。非所有processへ触れない。

source allowlistは新helper/runner/tests、共有two-stage runtime、count_prompt private sink、outer固定mapping、C5 helper/review bindingをexact path/SHAでfreezeする。製品source chainは次を別fieldで固定する。

- baseline commit exact `598f34da07296b788f75ac85e475eca952b35b37`。
- Mainが後続で指定する、CI承認済みcapture製品3ファイル `ai_client/brain/controller.py`、`ai_client/brain/invocation.py`、`ai_client/brain/__init__.py` のexact SHA。
- 対応する独立design、implementation、constructor reviewの各path/SHA。
- 新bytes completion evidenceと成功CI evidenceの各path/SHA、CI run identity、tested commit。
- T478 tool review後のGB1 exact source manifest SHA。

現在観測中のCIで新しいcapture instrumentation/once failureが判明しているため、その未承認bytesや暫定SHAを設計から許可しない。Mainが修正後の独立承認と成功CI hashchainを指定するまでprepareは固定`PRODUCT_SOURCE_NOT_APPROVED`でprovider起動前に拒否する。未知の製品path追加、hash変更、review/evidence binding欠落、tested commit不一致も同じく拒否する。保存SC2/baseline sourceとGB1 current sourceの差はpathごとのold SHA/new SHA/approval bindingとして保存し、source件数一致やworking-tree現値で代用しない。既存runnerをimportして実行経路を共有せず、承認済みpure helperだけを必要最小限importできる。

## 7. focused acceptance

- 全32caseでcatalogを再構築し、許可pathだけ、最大64、opaque ID連番、pointer解決、元projection不変を確認する。
- 全7 kindそれぞれでID 0/1/2件、最大長fixtureを同条件で受理し、unknown/duplicate/3件/missing/extra/duplicate JSON/nonfinite/overflowを拒否する。
- 全32×7のcall2 C5 schemaを構築し、既知4case交絡、3件`NO_LEGAL_GROUNDING`、refs0/null、claim tuple、空配列items保持、旧authority positive 13と代表negativeを回帰する。
- call1/call2ともrole列`[system,user]`、元system prefix、必要最小instruction、catalog付きcanonical user bytesを固定し、systemにopaque choice以外の動的値がないことを検査する。
- 最大canonical choice outputのoffline公式tokenizer `<=32`、各generation直前の別native context gate、wire/private hash一致を確認する。offline tokenizerをnative prompt/context PASSとして扱わない。
- MockTransportで5 action、全32、最大64、choice failure output0、`NO_LEGAL_GROUNDING` output0、reserve、timeout、native/private保存失敗、全32分母、cleanup、公開privacyを検査する。
- experiment/task/runner/source/config/projection/catalog/choice/schema/body/wireの一点改変とduplicate row/caseをprovider前に拒否する。
- 意味annotationは32 unique caseをexact coverageし、raw/result/annotation hashとduplicate拒否を検査する。三値合成は次のtruth tableをrunner/集計双方で再計算し、不一致を拒否する。
  - structural FAILならoverall FAIL。ただしsemantic UNKNOWNのfieldと件数はUNKNOWNのまま保持する。
  - structural PASSかつsemantic PASSならoverall PASS。
  - structural PASSかつsemantic FAILならoverall FAIL。
  - structural PASSかつsemantic UNKNOWNならoverall UNKNOWN。
  annotationのsemantic判定をrunnerは上書きせず、保存annotationと機械resultから集計する。UNKNOWNを0、FAIL、PASSへ暗黙変換しない。

## 8. 一回測定と採否

独立design APPROVED、実装、独立tool APPROVED後だけT480 Testerが最大64 callを一回実行する。保存後は独立意味reviewを行う。gateはbaseline固定値に対する全32で、HARD fail `<=14`、mismatch `<23`、FABRICATED_EVIDENCE `<=0`、SECRET `<=5`、STATE_CONTRADICTION `<=3`、ABILITY_CONTRADICTION `<=0`、各UNKNOWN非増加、SEMANTIC pass `>=7`、固定18質問回答`>=9`、合法NONE維持、長文exact copy 0とする。機械rejectは必ずoverall HARD failである。

不採用、実行不能、gate不合格でも旧候補retryや条件微調整を行わない。製品採用には別途D077のHARD 100%、重大秘密/状態矛盾0、独立reviewが必要である。

## 8.1 offline choice token preflight実測

Mainが固定公式vocab tokenizerで最大ID `f062`/`f063`を含むcanonical choice outputを全7 kindについて計数した。`NONE/CLAIM/QUESTION=22`、`ANSWER=23`、`REBUTTAL/OPINION_CHANGE=25`、`RELATION_HYPOTHESIS=27` tokensで、全件`<=32`だった。generation callは0である。

- evidence: `logs/t476-continuation/gb1-max-choice-token-preflight.json`
- SHA-256: `2354f1f111d6424be54bc9d37091a86dd8c8fff1ea69db99f5fde708f2f4fcd1`
- method: `OFFLINE_VOCAB_TOKENIZER_NOT_SERVER_NATIVE`

この合格はchoice completion上限だけの証拠であり、owned serverのrendered prompt/context gateを代替しない。

## 9. 棄却した代替

- authoritative state自体の追加: 現projectionに既に存在し、欠落仮説と矛盾する。
- warning/例/罰則の追加: basis選択効果とinstruction効果が混ざる。
- 本文regexやhost意味分類: 正当な推理・騙りを誤って禁止し、製品ruleをtest adapterへ持ち込む。
- 既存GC2参照schemaの再調整: 構造受理は改善済みで自由文誤用を分離しない。
- fact値をsystemへ移す案: authority/privacy境界を変え、単一変数を逸脱する。
