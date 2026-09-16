# Phase 6 外部出力契約・予算・反復抑止の限定設計

Status: DRAFT  
Task: T380  
対象順序: W2 → W1 → W3

独立 Reviewer の承認前は実装不可。

## 1. 目的と境界

保存済み r3 で観測された出力棄却、repair時の`PROMPT_TOO_LARGE`、直前同一 player の同文反復に対し、
既存の Phase 6 公開契約を緩めず、製品コードの最小修正を定義する。provider、実ゲーム、実行治具、
証拠 schema、監査 framework、期限、同時実行数、`DiscussionPromptConfig` の 8,192 proxy units
上限は変更しない。保存済み不正応答を新 schema で適合へ読み替えない。

実装候補は `ai_client/discussion/projection.py`、`ai_client/llm/decision.py`、
`ai_client/brain/controller.py` と既存の Phase 6 test module に限定する。新しい永続状態、公開 field、
validation code、audit/ledger/summary field は追加しない。

## 2. W2: 送信 schema と局所契約の一致

### 2.1 request-local schema

`discussion_output_schema()` は capture の trigger family と受信済み option を入力に、次を生成する。

1. capture triggerごとにschemaをspecializeする。PEER_CHATではreactionを非nullにし、trigger全体を
   captureの許可済みexact `EvidenceRef`の`const`にする。それ以外ではreactionを`const null`にする。
   CO_OPPORTUNITYではCO objectを非null、それ以外では`const null`、PRE_VOTEではreassessment objectを
   非null、それ以外では`const null`とする。これにより保存semantic failureの主要な欠落/余剰をprovider側で
   防ぎ、同時に利用不能object schemaの固定費を除く。
2. proposal `decision_kind` / `option_id` nullabilityは既存局所parser/state検査を維持し、今回の送信schemaへ
   重複実装しない。固定providerのconverterはschema nodeに`oneOf`があるとbranch処理後returnし、sibling
   `properties`を無視する。converter-compatibleな完全proposal branch複製は、保存96件中先頭棄却2件の
   nullabilityを前倒しする利益に対して固定費と複雑性が大きい。既存局所検査は送出前にfail closedするため、
   CHAT/trigger依存fieldの主要修正を優先する安全縮退を選ぶ。
3. CO内部identity、pre-vote内部identity、relation異endpoint等は既存局所意味検査を維持する。
4. schema は既に実 backend payload の `response_format.json_schema.schema` へ渡る
   `PromptProjection.decision_schema` 自体を変更する。prompt 文だけの変更にしない。

組の表現には固定converterが実際に処理する`const` / `$ref` / object propertiesだけを使う。未実証または
無視されるsibling `oneOf`、`allOf` / `if` / `then` / `dependentSchemas` は
導入しない。local validator、parser、
`DiscussionStateStore` の検査は削除しない。JSON Schemaで簡潔に表せない relation の異 endpoint、
answer/rebuttal actor、projection membership、CO/pre-vote の相互一致は局所意味検査の責務である。

固定費変更は次に限定する。

- provider制約へ寄与せずconverterも無視するrootの`$schema` annotationを削除する。
- `assessment`、`claim`、`relation_update`、`strategy`の反復shapeは、canonical schema proxyが実測で
  減少し固定converterのpositive/negative結果が不変の場合だけ`$defs`へ集約する。
- trigger-local specializationで利用不能な3 objectをexact `null`へ置換し、PEER reaction triggerは
  exact source `const`へ置換する。
- required field、closed object、enum、const、min/max、uniqueItems、player/evidence制約は削らない。

local Draft validatorで旧schemaが許した各triggerの正値を新schemaも許すこと、意図したtrigger依存反例だけを
追加拒否することを差分testする。固定upstream converterでgrammarを生成し、全required fieldを含むpositive
値を受理し、wrong/non-CHAT trigger、trigger依存fieldの欠落/余剰、欠落共通fieldのnegative値を拒否する。
nullabilityはschema対応済みと報告せず、局所拒否testを維持する。

### 2.2 失敗境界

provider前の structured-output制約違反、JSON parse/schema違反、dataclass構造違反、state意味違反は
別段階として保持する。公開診断 detail、新 field、任意例外文の転記は今回不要であり、X3は据え置く。
T348はHTTP 400 sampler段階、r3はHTTP 200応答後の棄却であり、同一原因とは扱わない。

### 2.3 W2受入

- `Draft202012Validator` で、各 trigger family の正しい最小値を許可する。
- 固定converterの生成grammarを既存upstream fixtureで検査し、各branchで共通required fieldが消えない。
- CHAT以外/wrong reaction trigger、PEER_CHATのreaction欠落、非PEER_CHATのreaction、CO/PRE_VOTE
  trigger依存fieldの欠落/余剰を送信schemaが拒否する。
- `none`+非null option、action+null option、wrong offered option、CO/pre-vote内部identity等はschema通過後も
  既存局所検査が拒否する。
- schemaを通る安全な合成値は既存 parser/state 検査も通る。schemaで表現しない意味反例は
  schema通過後に既存局所検査が拒否する。
- backend requestの `response_format` に同じ制約が存在することをpayload単体testで確認する。
- 保存済み不正responseの適合率上昇は受入条件にしない。後続生成の制約改善であり、実providerの
  改善率や完走可否は実ゲーム前に断定しない。

## 3. W1: 8,192を維持したrepair固定費削減

proxyは system/user messages と output schema を含む完全な
`canonical_prompt_json(messages, output_schema)`に対して計算する。provider tokenとは別単位であり、
未送信39件のprovider tokenは不明のままとする。

T379が同一r3要求を対応付けた結果、初回94件は6,836–8,189 unitsで送出可能だった一方、repair
49件は7,312–8,360 unitsで、`PROMPT_REJECTED` 39件はすべてrepairだった。超過は3–168 units
（median 79）、空repair wrapperの増分は177–180 units（median 180）。39件はいずれも元の完全
promptを保持し、invalid-output excerptは空だった。したがって、初回mandatory skeletonが8,192を
超えたという仮説は棄却し、直接対象を「元projectionに追加される空excerpt時でも存在するrepair固定
wrapper」とする。schemaのmarginal costは4,329–4,411 unitsである。

W2のconverter-compatibleな完全branchを含めた総schemaから固定費を削減する。具体的には、共通shapeの
`$defs`集約、trigger-localなnull specialization、利用不能action branchの除外を行う。`$schema`注記の
ようにprovider制約へ寄与しないannotationは、local Draft 2020-12
検証とbackend grammar生成の双方で不要であることをtestで確認できた場合だけ送信schemaから除く。
意味field、required field、closed-object制約、player/evidence enum、max/minは削らない。

schema削減だけでは、空いた枠を初回projectionがoptional memory/stateで再充填するためrepair余裕を保証
しない。したがってproducerである`project_discussion_brain_input()`は、consumerである
`build_discussion_repair_projection()`の最小repair envelopeを先に予約する。

予約値を180などの観測定数へ決め打ちしない。新しいconfig fieldやcomposition配線も追加しない。Pythonで
materializeできる`str`と`bytes`の長さは`Py_ssize_t`で表され、`sys.maxsize`以下である。したがって
`invalid_output_original_scalars`と`invalid_output_original_utf8_bytes`はともに`sys.maxsize`を最大値として
serializeしたときの10進桁数が上界になる。closed `DecisionValidationCode`全値、空excerpt、
`invalid_output_excerpt_truncated`の両値、SHA-256の固定64hex、両length=`sys.maxsize`を総当たりし、実際の
repair JSONを既存のsort/separator/UTF-8規則でserializeする。元messagesへ追加した完全
`canonical_prompt_json`との差の最大proxy/byte数を共通helperで算出し、これを予約値とする。実際の値は
この上界を越えないため、backend固有上限や新APIを投影層へ渡さず有限保証できる。

初回projectionのoptional state/memory追加判定では、complete bytes/proxyの実上限からこの予約を引いた
値を使う。mandatory system/schema/context/options/lifecycle/minimum state identity/peer triggerが予約込みで
収まらない場合は従来どおりbackend前に`PROMPT_TOO_LARGE`とする。予約はoptional state/memoryを従来より
早く落とし得るが、8,192を維持して既存1回repairを実行可能にするための明示的な設計差である。省略数と
既存のexhaustion markerは正確に更新し、必要fieldや秘密保全を弱めない。

空excerptの39件を収めるためにrepair instructionを削除・弱化したり、元のimmutable projectionをrepair時に
別物へ再投影したりしない。non-empty excerptを含む一般repairの適合は別にone-under/equal/one-overで維持する。

実装後は既存の `_prompt_contract` でW2前後の完全schema bytes/proxy、算出予約値、初回完全prompt、
空excerpt repair完全promptを同一fixtureごとに記録する。旧sibling-oneOf試作は完全promptを全triggerで
297 units増加させ、converterが共通fieldを無視するため受入証拠に使わない。新schemaの増減自体にPASSを
置かず、producer→consumerを連続実行して判定する。saturated入力、各validation code、response長境界、
各trigger familyで初回が上限内かつ空excerpt repairも上限内であること、non-empty excerptが収まる長さまで
決定的に縮むことを要求する。保存r3は完全initial capture/stateを保持していないため新producer入力を
補作しない。保存された49件の完全projectionに新schema/repair builderを適用する非送信replayで、旧39超過が
解消することだけを確認する。producer予約そのものは、完全入力を持つ既存fixtureと合成saturated fixtureで
actual `project_discussion_brain_input()`→`build_discussion_repair_projection()`を連続実行して確認する。
既存境界fixtureでは初回とrepair双方のone-under/equal/one-overを維持する。

8,192、32,768 bytes、section上限、縮約順序、mandatory trigger、repairの同一予算は変更しない。
削減後も超過する入力は従来どおりbackend前に `PROMPT_TOO_LARGE` とする。数値引上げは別の設計変更で、
今回の先行実装には含めない。

この予約は既存承認設計 `Docs/ai/design/PHASE6_DISCUSSION_QUALITY_DESIGN.md` §6 のstep 4–5にあるoptional
state/memory追加時のeffective complete-byte/proxy判定と、step 7のrepair境界を変更する。T381承認後の
実装では、同ファイル§6に「initial optional追加は算定済み最小repair envelopeを差し引いた上限で判定し、
mandatory項目は削らない」というaddendumを反映する。その他のprojection順序と上限値は変更しない。

## 4. W3: 直前同一 player の最小送出抑止

比較関数を製品側に1個だけ置き、`' '.join(unicodedata.normalize("NFKC", text).strip().split())`
を用いる。これは既存 private review と同一で、Unicode whitespaceを1個のASCII spaceへ圧縮する。
空文字化は既存の非空検査とは別に不正として扱う。

contextful `ChatDecision.message` と `CoDeclareDecision.comment` について、有効な`BrainResult`とdurableな
successful `DiscussionGenerationAck`を照合した後、`state.stage()`より前に、現在の request が既に持つ
許可済み`HistoryView.records`のself `ChatRecord.message`と`CoView.declarations`のself
`CoDeclarationRecord.comment`だけを候補にする。両viewを`(record kind, order)` identityで統合し、同identity
の同値重複は1件化する。異なる本文・player・kindを持つidentity競合、order欠測/不正、最大orderが一意で
ない場合は直前値を確定不能として抑止しない。一意な最大orderの1件を直前accepted textとする。候補と
直前値の正規化結果が一致すれば、`stage()` / `commit()` / `mark_dispatch_started()` /
network sendの前に拒否する。比較のために別ログ、全playerの
private本文、将来event、非許可channel、process-local推測値を集めない。直前値を許可済みviewから一意に
確定できない場合は反復と推測せず、既存処理を続ける。

拒否は既存の「generation成功後・stage前のrequest/result不一致」と同じ経路を使う。具体的には
`_abort_after_generation(request, generation, proposal, DiscussionAbortReason.STAGE_FAILED,
DecisionStatus.INVALID_DECISION)`を呼ぶ。これにより生成ackの`DECISION`または`REPAIR_SUCCEEDED`、
proposal hash/identityを保持した `ABORTED` / `STAGE_FAILED` terminalを1件durableに書き、state revisionは
進めない。`FINAL_OUTPUT_INVALID`はgeneration statusが`OUTPUT_INVALID`/`REPAIR_FAILED`のpre-result専用
なので使用しない。新しいterminal reasonやvalidation codeは追加しない。

抑止時に新しいモデル再試行、CHAT opportunity、状態commit、dispatch、送信を発生させない。既存の
1回repairは構造/意味出力検証までに限定されているため、反復専用repairへ拡張しない。診断は既存の
boundedなinvalid/failure分類を再利用し、本文や正規化値を公開記録へ出さない。

この最小修正が保証するのは、clientが観測できた直前同player accepted textとの完全一致の送出抑止だけで
ある。private reviewのB09はさらに全game同文最大2回を要求し、保存rawも完全母集団ではないため、
W3だけでB09全解消とはしない。また、同じ短い返答が文脈上必要な場合も抑止され、質問応答や進行を
悪化させ得る。別文への自動書換えは意味を捏造するため行わない。

### 4.1 W3受入

- ASCII、全角、前後空白、改行/tab、Unicode空白の同値例を決定的testで拒否する。
- 大文字小文字や句読点などNFKC/空白規則で同値でない文は拒否しない。
- 別player、直前より前だけの一致、history/CO identity競合、最大order tie、直前accepted recordを確定不能な
  場合は拒否しない。
- ChatとCO commentの直前順序をまたぐ同player一致も、両方が許可済みviewにある場合は拒否する。
- 抑止時はsender 0 call、state revision不変、dispatchなし、private本文を含む新audit fieldなし。

## 5. 実装・検証順序

1. W2 schema helperとschema/parser/backend-payload focused tests。保存済み96応答は現schemaで95 PASS、
   1 FAIL、X1-only差替え後は77 PASSで18件を追加拒否した。既存raw適合率の改善ではなく後続生成制約の
   検証として扱う。parser棄却はCHAT制約17、
   option nullability 2、strategy focus順序2、CO SILENCE/DEFERのnull option/role 3。semantic内部関数を
   再生できた72件はPASS38、VALUE_NOT_OFFERED31、OPTION_NOT_OFFERED3で保存statusと一致した。ただし
   完全capture原本ではなく投影済みevidenceからの再構成であり、その範囲を越えて一般化しない。
2. 同じ `projection.py` 上でW1の重複除去、49 repair再構成、before/after計測。別担当へ並行編集させない。
   算定予約を常に適用し、保存projection replayと合成saturated producer→consumerの双方を検証する。
3. W3正規化・直前値選択・pre-stage rejectionとcontroller focused tests。
4. Phase 6 semantic output、memory projection、discussion state/transaction、brain controllerの関連回帰、
   repository既定testと必要なcompletion test（対象は独立Tester packetで確定）、
   `python scripts/check_docs.py`、scoped diff review。completion前の無差別な全pytest起動は要求しない。

独立 Reviewer が本設計を承認した後にのみ実装し、実装後は独立 Tester とfresh Reviewerを通す。
実provider PASS、B09 PASS、ゲーム完走はoffline testから推定しない。
