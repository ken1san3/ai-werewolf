# Admission consumer deadline / provider drain 限定修正設計

Status: DRAFT

R1 state: T372初回CHANGES_REQUIRED反映済み、独立再承認前は実装不可。

## 目的と根拠

この設計はT373の限定scopeで、admissionのconsumer期限と、開始済みprovider callの有限停止確認を分離する。ゲーム時間、feature timeout、backend request timeout、provider drain graceの数値は変更しない。

確認した現物は次のとおりである。

- `ai_client/llm/admission_broker.py`（T371 hash `5bb2819506746779d81c0548a1ec71f3fe558e1fae51cb24e62a63a00bb21af2`）は、`GENERATE`受理時のremaining leaseを `_run_backend_call` へ渡し、その値で直接backend coroutineを `asyncio.timeout` している。
- T371合成5条件では、待機entryだけの期限切れは `EXPIRED`、poison=falseだった。3.0秒lease中に0.9秒で自然終了するabandonは `ABANDONED_DRAINED`、poison=falseだった。一方、0.6秒lease中に0.9秒で自然終了予定のactive callは、consumer current、先行ABANDON、ordinal 2 repairの三条件すべてで約0.6秒時点にbackend taskがcancelされ、`UNKNOWN`、poison=trueとなった。設定したbackend timeout 1.5秒とdrain grace 2秒には到達していない。
- R7公開相関では、UNKNOWN対象は同一lease内でqueue 35.996122秒後にclaimされ、providerは1.889119秒後にcurrent/UNKNOWNとなった。先行2件の `EXPIRED` は別invocation/clientである。R7原本には正確なcutoffとunclassified exception種別がなく、R7 UNKNOWNがdeadline取消だったとは確定できない。

Phase 5設計には、直接HTTP callを「backend timeoutとremaining leaseの小さい方」で包む記述と、consumer deadline/取消後も「直接HTTP taskを単に取消さずshieldして自然drainする」記述が併存する。canonical仕様によりdeadline後のゲーム送信は禁止されるが、開始済みprovider transportをその時刻に強制取消する必要はない。実装事実とT371再現に照らし、本設計は後者のdrain-or-poison、同時呼出し1、fail closedを維持し、前者のremaining leaseによるprovider取消だけを置き換える。承認後、この文書がこの限定点の詳細設計となる。

## 四つの期限と時計

全時刻比較は同一hostの `time.monotonic()`、秒単位、境界は `now >= cutoff` を期限切れとする。

1. **admission cutoff**: `AdmissionRequest.not_after_monotonic`。enqueue、offer、claim、各ordinalのprovider call開始を許す最終時刻であり、consumerが結果を受理できる最終時刻でもある。
2. **consumer acceptance cutoff**: admission cutoffと同じ絶対値・同じdispatch identityを使う。期限後のresponse/errorはconsumerへ配送せず、typed game sendにも使わず、brokerがconsume/discardする。
3. **provider request timeout**: provider call開始時を起点とする既存 `backend_request_timeout_seconds`。開始済みcallの実行上限であり、queue待ち時間やremaining admission budgetを差し引かない。
4. **provider drain grace**: consumerがcurrentでなくなりslotが `DRAINING` に入った時刻を起点とする既存 `provider_drain_grace_seconds`。自然terminalを待つ上限である。既存の `provider_drain_grace_seconds >= backend_request_timeout_seconds` を維持する。shutdownは既存の `shutdown_grace_seconds >= provider_drain_grace_seconds + cancellation_grace_seconds` を維持する。

`invocation_id`、client owner、call ordinal、provider task identityを一組として扱う。古いdeadline taskは、この全identityが現在slotと一致する場合だけ遷移できる。別lease、attached successor、再利用済みslotへ作用してはならない。

## 状態遷移契約

### provider call開始前

- enqueue、offer選択、claim、`GENERATE`処理の各境界で `now < admission cutoff` を要求する。
- cutoff以後はordinal 1もrepair ordinal 2も新規provider callを0件とし、既存 `EXPIRED` / `ADMISSION_EXPIRED` 経路で終了する。
- `ENQUEUED` / `OFFERED` は既存どおり `EXPIRED`。provider taskを持たない `CLAIMED` もdeadline watcherが同一identityを確認してterminalにし、後続 `GENERATE` を拒否する。

### provider call開始後

- brokerはprovider taskをprovider request timeoutで有限に包む。remaining admission budgetでは包まない。
- admission cutoff到達、client ABANDON、切断、phase取消、shutdownのうち最初にconsumer-currentを失わせる事象は、lock下で一度だけ `consumer_current=False`、`ACTIVE -> DRAINING` とし、同一provider taskに一つだけdrain watcherを関連付ける。
- cutoff到達だけを理由にprovider taskをcancelしない。clientのABANDONが欠落してもbroker deadline watcherが同じ遷移を実行するため、無応答・悪意あるconsumerに依存しない。
- provider terminal処理は、deadline watcherの実行有無にかかわらず、同一broker lock内で現在のslot identityと `now >= slot.request.not_after_monotonic` を再確認する。exact equalityも期限切れである。cutoff済みなのに `consumer_current=True` なら、そのlock取得者が一回だけ `consumer_current=False` とし、`ACTIVE -> DRAINING` 相当のconsumer abandonmentを確定する。terminal処理自身が既にproviderのsafe terminalを保持している場合は、新しいdrain watcherを起動せず、そのsafe terminalをconsume/discardしてslotを終了する。
- safe terminal（response、`PROVEN_TERMINAL`、`NOT_STARTED`）がdrain grace内に到着したらconsume/discardし、cutoff/abandon後なら `ABANDONED_DRAINED` としてslotを解放する。consumerへ `RESULT` / backend `ERROR` を配送しない。provider terminalがdeadline watcherより先にlockを取得しても、このlock内cutoff再確認により配送は0件となる。
- provider request timeout、drain grace超過、`UNKNOWN`、unclassified exception、broker cancellation後にquiescenceを証明できない場合は、従来どおり `PROVIDER_QUIESCENCE_UNKNOWN` でrun全体を永久poisonする。UNKNOWNを `EXPIRED`、`ABANDONED_DRAINED`、`PROVEN_TERMINAL`へ変換しない。
- safe terminalとcutoffが競合する場合、broker terminal処理が同じlock内で時刻を再確認する。lock取得時に `now >= cutoff` ならwatcherの遅延に関係なくdiscardし、`now < cutoff` のときだけcurrent consumerへの配送候補にできる。consumer側の既存最終deadline recheckも維持するが、brokerの期限後配送禁止をconsumer側だけに依存させない。いずれも同じresponseを二度解決しない。
- slot解放・attached successorのenqueue・次offerはprovider quiescenceのsafe証明後だけ許可する。`DRAINING` またはpoison中のslot再利用は0件とする。

### release、disconnect、shutdown

- `ABANDON`、deadline watcher、disconnect、shutdownが同じactive callに重なっても、drain watcherとterminal記録は各1件とする。
- `RELEASE` はprovider taskがquiescentでないslotを解放できない。abandon後の二重releaseは既存local no-opを維持する。
- shutdownは新規admissionを止め、同じdrain契約を待つ。有限境界を越えたtaskは取消・joinを試みるが、安全を推測せずpoison/cleanup incompleteを保持する。

## 最小実装scope

必要な製品変更は原則 `ai_client/llm/admission_broker.py` のみとする。

- constructorで検証済み `backend_request_timeout_seconds` を保持する。
- `_run_backend_call` の外側timeout引数をremaining leaseからprovider request timeoutへ変更する。
- 既存 `_deadline_timeout` を、同一identityの `CLAIMED`（provider未開始）と `ACTIVE` / `DRAINING` に対して上記遷移を適用できるよう限定拡張する。
- `_backend_terminal` 相当のlock区間でslot identityとcutoffを再確認し、deadline watcherが未実行でも期限後のresponse/errorを一回だけdiscardする。
- deadline、ABANDON、disconnect、shutdownが共用する小さなlock内helperでconsumer abandonment/drain watcherの一回性を保証する。新wire frame、新public API、新error enumは追加しない。
- metricsは既存event/fieldを使い、期限起因のconsumer abandonmentを既存consumer stateとterminal順序から検証可能にする。診断に不可欠と独立Reviewerが判断しない限りschemaを増やさない。

`ai_client/brain/invocation.py`、backend transport、ゲームserver、protocol schema、runtime timeout設定は変更対象外とする。実装中に既存wireだけではcurrent consumerのbounded completionを満たせない事実が判明した場合はscopeを勝手に広げず、Architectへ戻す。

## 反証可能な必須テスト

`tests/test_phase5_generation_admission.py` に決定的fake backendとevent gateで追加し、実LLM/network timingへ依存させない。

1. remaining lease 0.05秒、自然terminal 0.10秒、backend timeout 0.20秒、drain 0.30秒: cutoff後はconsumer配送0、backend cancellation 0、safe terminal後 `ABANDONED_DRAINED`、poison=false、successorがその後だけofferされる。
2. 同条件でconsumerがABANDONを送らない: broker deadline watcherだけで `DRAINING` となり、結果配送0、有限にslot解放する。
3. cutoff直前の `CLAIMED` でprovider未開始: cutoff後のordinal 1 `GENERATE` はprovider call 0。ordinal 1成功後にcutoffを越えたrepair ordinal 2もprovider call追加0。
4. provider request timeoutがremaining leaseより長い条件: cancel観測時刻がconsumer cutoffではなくprovider timeout側であり、quiescenceがUNKNOWNならpoison=true。UNKNOWNをexpiry扱いにしない。
5. drain graceを越えるhang: provider task cancel/joinを実行し、poison=true、successor/new offer 0、cleanup結果を明示する。
6. cutoff、ABANDON、disconnect、safe terminalをevent barrierで競合させる: provider peak concurrency=1、terminal/drain watcher各1、late delivery 0、二重releaseなし、古いdeadline taskが次leaseを変更しない。
7. clockをexact equality `now == cutoff` に固定し、deadline watcherをbarrierで意図的に遅延させたままsafe terminalを先にbroker lockへ入れる: terminal処理自身がconsumerを一回だけnon-currentとしてsafe response/errorをconsume/discardし、`RESULT` / `ERROR` 0、typed send 0、poison=false、safe terminal後だけslot解放となる。watcher解放後に二重terminal/drain watcher/slot変更がないことも確認する。
8. shutdown中のactive call、consumer切断、無応答consumer: 既存有限shutdown内でsafe drainまたはpoison/cleanup incompleteとなり、成功への読替え0。
9. 既存queue expiry、claim/cancel、attached successor、ordinal 2、drain completion/expiry、unknown poison、broker shutdown、およびbrain deadline suppression回帰を実行する。

実装受入では、上記focused test、関連Phase 5 broker/brain回帰、`python scripts/check_docs.py`、scope全diffを独立Testerが実測し、T372が設計hash・実装diff・証拠を独立審査する。旧R7のFAIL/UNKNOWNは変更しない。

## 非scopeとproduct判断

期限値の引上げ、ゲーム時間やfeature timeoutの変更、UNKNOWNの安全側への再分類、期限後のrepair/new call、provider同時呼出し増加、retry追加、public API拡張は非scopeである。この契約は既存安全要件から一意に導けるため、現時点で新しいproduct判断は不要である。
