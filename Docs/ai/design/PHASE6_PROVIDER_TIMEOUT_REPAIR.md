# Phase 6 provider timeout連動設計

Status: APPROVED  
独立承認: T311 revision 1。実装設計の承認であり、本番値・追加計測・実gameの承認ではない。
対象: T310 / T309 RC-01

## 目的と確認済み事実

Phase 6の実provider経路だけで、HTTP readと一回のtotal requestの有限timeoutを明示設定できるようにする。実測値の選定、追加実game、providerの起動・停止は本設計に含めない。`512`はwhole-responseの生成上限であり、最低生成長や所要時間を保証しない。現状の`read_timeout_seconds=3.5`はHTTP read待ち、`request_timeout_seconds=4.0`は一回のtotal request境界であり、この二値だけから同modelが必ずtimeoutすると断定できない。T307で確認できるのは最初のprovider callが`REQUEST_TIMEOUT`、quiescenceが`UNKNOWN`になった事実までである。

既存コードではbackend request timeout、broker drain、broker shutdownにそれぞれ有限境界があり、`provider_drain_grace_seconds >= request_timeout_seconds`と`shutdown_grace_seconds >= provider_drain_grace_seconds + cancellation_grace_seconds`を検証している。client側のbrain実行はfeature timeout、phase deadline、`BrainRunConfig.max_decision_seconds`の最小値で止まり、終了時はclient network shutdownとbrain cancellationにも別の有限境界がある。この安全停止、lease identity、poisoning、game clock、Phase 5/Q8の既定値を維持する。

## 変更範囲

変更対象は`scripts/run_phase5_local_smoke.py`と対応する既存test fileに限定する。`LocalLLMSettings`、`OpenAICompatibleBackendConfig`、`GenerationBrokerConfig`の既存public interfaceとfingerprintを再利用し、新しいprofile型、第三のhash、process間schema、backend/broker/client/brainのstate machine、wire protocolは追加しない。

runnerのPhase 6実provider起動引数に次の二つだけを追加する。

- `--phase6-read-timeout-seconds`: HTTP read gapの上限。
- `--phase6-request-timeout-seconds`: 一回のprovider request全体の上限。

両値はbool、非数、無限、0以下を拒否する。all-or-noneでなければ拒否し、Phase 6実provider runでは両方を必須とする。Phase 5/Q8へ指定した場合も拒否し、既定値を変えない。さらに次を満たさなければ子process生成前に失敗させる。

```text
read_timeout_seconds <= request_timeout_seconds
request_timeout_seconds < 44.0
```

`44.0`は新しい製品値ではなく、runnerが既にPhase 6のreaction/vote両featureへ設定しているtimeoutである。Phase 6に限り`BrainRunConfig.max_decision_seconds`もこの既存feature上限へ一致させ、現行5秒が新しいrequest候補を先に打ち切る経路を除く。44秒以上のrequest候補、feature上限自体の変更、game clock変更は本修正で採用せずHuman Gateへ返す。originalとrepairは一つのdecision deadlineを共有するため、二回分の完了時間は保証しない。既存のserver由来phase deadline、guard、遅い開始時の取消をそのまま優先する。

## public interfaceと伝播

親runnerは二値を`LocalLLMSettings`へ写像し、既存`_broker_bootstrap`でbroker childへ渡す。親とbroker childは既存backend `config_fingerprint`で同一性を照合する。新しいhashや全client statusへのtimeout情報は追加しない。実効値は既存sanitized arguments、backend identity/config fingerprint、broker ready/resultの既存記録で照合する。

broker設定は独立入力にせず、既存defaultを下限として次の一意な式で導出する。

```text
effective_provider_drain = max(default_provider_drain, request_timeout_seconds)
effective_broker_shutdown = max(
    default_broker_shutdown,
    effective_provider_drain + default_cancellation_grace,
)
parent_broker_result_wait = effective_broker_shutdown + _READY_SECONDS
```

導出値で`GenerationBrokerConfig`を構築し、既存の`request <= drain`、`drain + cancellation <= shutdown`検証と既存broker `config_fingerprint`照合を通す。clientはbroker readyから同じbroker configを受け取る。`NetworkClientConfig.shutdown_timeout_seconds`はprovider timeoutと異なるlifecycleなので変更しない。outer runnerの`max_seconds`、外側監視上限、cleanup 120秒も変更しない。親PID、shell exit code、開始・終了監視の不足（RC-02）はT309の運用証拠追補で扱い、本変更に新しい監視frameworkを作らない。

## lifecycleとfailure

通常は、client decision開始、lease claim、最大2回のprovider call、lease releaseの順で進む。各callはreadとtotal requestの両方で有限、decisionは既存44秒feature/controller上限とserver由来phase deadlineの最小値で有限である。各attemptは共有decision deadlineの残時間に従う。client取消後もbrokerがprovider taskを所有して導出済みgrace内でdrainする。terminalが確認できなければ従来どおり`PROVIDER_QUIESCENCE_UNKNOWN`でpoisonし、次のleaseを進めない。shutdown順序も変更しない。timeoutを延長してもprivacy、authorization、accepted population、length拒否、repair最大1回を緩めない。

設定欠測・不一致・関係違反は子process生成前のconfiguration failureとする。runtime timeoutは`REQUEST_TIMEOUT`のまま、quiescenceを推測で`PROVEN_TERMINAL`へ変えない。brain/server deadlineで取消された場合も既存drain/poisonへ進む。brokerまたは親waitの有限境界を越えた場合は既存のpoisonまたはcleanup failureを保持し、成功へ読み替えない。providerが前回UNKNOWN状態のままかどうかは設定で解消できないため、次の計測・実gameはユーザーによる安全な停止・再起動と別PID/identity確認後に限る。

## 受入条件と必要テスト

実装受入には次のfocused testと既存回帰が必要である。test数値は境界関係を検証するfixture値であり、実game採用値ではない。

1. Phase 6実provider runがread/requestのall-or-noneと全指定を要求し、非有限・非正数、`read > request`、`request >= 44`、Phase 5/Q8への混入をprocess生成前に拒否する。
2. 二値がbackendへ別々に写像され、親とbroker childの既存backend fingerprintが一致し、改変時は既存検査でfail closedになる。
3. requestが既存drain以下の場合と超える場合について、上記式どおりのbroker drain/shutdown、既存broker fingerprint、親result waitを確認する。
4. Phase 6 clientだけで`BrainRunConfig.max_decision_seconds == 44.0`となり、reaction/voteの既存44秒、server由来deadline、guardが変わらないことを確認する。
5. Phase 5/Q8の3.5/4.0、BrainRunConfig 5秒、broker defaults、network shutdown、max output、lease最大2、privacy/poisoningの既存回帰を実行する。
6. 既存brain/broker testのうち、共有deadline後の取消、provider drain完了、drain超過poison、broker shutdown timeoutを再実行する。新しい全lifecycle fixtureは作らず、今回の写像・導出に欠ける具体的caseだけを既存test fileへ追加する。
7. sanitized arguments、ready/result、既存fingerprintで実効設定を照合でき、credentialやprivate本文を追加露出しないことを確認する。

実gameでの有効性と採用値は、独立Reviewerが本設計を承認し、実装と独立testを通した後にも未承認のまま残る。安全なprovider再起動後の有限な計測で値を選び、別のHuman Gateで提示・承認されるまで実gameに使用しない。
