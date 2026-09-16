# R13レビューに基づくテスト修正・実行計画

作成: 2026-09-15 / T337 Integrator
対象: `EXTERNAL_REVIEW_LOG.md` R13、特にQ3/Q6
範囲: non-LLMテスト修正と後続計画。Stage B/Phase 6の完了判定ではない。

## 指摘の採否

| 指摘 | 今回の扱い | 受入・後続条件 |
|---|---|---|
| Q1: P2取得入口が実preflightで呼ばれない | 次回preflightへの必須作業として計画 | 下記の取得・private保存・public projection・独立照合をlaunch前に実施する。今回の実CIM取得は0件 |
| Q2: detailなしの場合の空本文/解析不能の区別 | 製品変更として後続設計 | 実際にdrainしたbody byte数をどの非公開記録へ保存するか、wire/公開metadataの不変条件と併せて独立設計・承認する。無条件で公開可能とは採用しない |
| Q3: 50ms ACKとdrain期限の競合 | 今回のtest-only修正対象 | ACK設定の製品分離を先に行わず、既存fixtureの通常通信予算を用い、対象drain期限50msと実drain処理の検証を維持・強化する |
| Q4: type/codeがmessageを押し出す | 後続設計候補 | 256 code point内の配分変更は承認済み抽出契約の変更。message/type/codeのliteral期待値を定義して独立承認後に修正 |
| Q5: byte上限が明示されていない | 追加製品変更は今回見送り | 現行のprintable Unicode最大256文字ならUTF-8は最大1024 bytes。後続境界検証で非ASCIIの実値を確認し、不要な重複検査を避ける |
| Q6: renameと純増の記録不足 | 実物照合して記録修正 | 旧264 node→現284 node、追加名21/旧名削除1/純増20。旧testのassertion保持をReviewerが確認 |

外部レビューにある「先行moduleがあれば必ず失敗」「Windows timerが原因」は外部の測定・解釈として扱う。今回取得していない生ログやOS内部原因を自分の実測とはしない。過去の全体回帰FAILを無条件で解除せず、修正後の新規一括測定と独立判定を行う。

## Q3の修正方針と受入matrix

対象は `tests/test_phase5_generation_admission.py::AdmissionBrokerTests::test_drain_grace_expiry_permanently_poisoned`。

| 境界 | 修正・観測 | 合格条件 |
|---|---|---|
| 通信準備 | クラス既存fixtureのACK/cancel予算0.5秒、shutdown2秒を利用 | 実broker/session経由のCLAIM、successor予約、ABANDONを実行。製品timeout/既定値は変更しない |
| 検証対象の期限 | provider_drain_grace_seconds=0.05とbackend_request_timeout_seconds=0.05を保持 | 設定値のliteral確認、実 `_drain_timeout` を無改変で通す |
| provider待機 | 既存fake backendをgate未解放で待機させる。backend_request_timeout_secondsはconstructor整合検査であり、実call期限はrequestの残時間 | drainがprovider未完了中にpoisonを発生させ、その後providerを終了することを確認 |
| 原因の特定 | 実drain taskからのpoison遷移を観測し、そのtask identityを確認 | 遷移直前がdraining、providerが未完了、gate未解放、reasonがPROVIDER_QUIESCENCE_UNKNOWN |
| 永久拒否 | 元のsuccessor/後続acquire/leaseなしのassertionを維持 | POISONEDが返り、private client bookkeepingが空、sessionは開いたまま |
| cleanup | gateをfinallyで解放、実drain task終了とprovider active=0を有限確認。broker closeは既存tearDownで実行 | provider/connection/所有taskが残らない。固定sleepによる結果の推測をしない |
| ACK期限違反 | 既存 `test_missing_control_ack_closes_session_and_cleans_claimed_slot` を同時検証 | 専用テストのACK50ms・通信喪失時のcleanupは維持 |

これは検証対象の期限を延長する変更ではない。通信前提は既存クラスfixtureへ揃え、従来曖昧だったdrain期限の所有者を追加assertionで特定する。製品にcontrol ACK用設定を新設する案は、fingerprint/IPC契約への影響もあるため今回は採用しない。独立Reviewerがこの分離を不適切と判断した場合は統合しない。

## 実行順序

1. T338: 既存失敗・actual sourceの有限調査。原因の確認範囲と修正scopeを記録。
2. T337: 対象1testを修正。drain成功、drain expiry、ACK欠測のfocused検証を一括実行する。
3. T339: sourceを固定し、以下6moduleを同一pytestプロセスで一括一回実行。分割PASSや単独再試行を代用しない。
4. T340: testの前後差分、受入matrix、JUnit/原ログ/source hashを独立審査。
5. Main: 実物照合後に今回scopeの結果を統合。Stage A catalogの再同期、Stage B実行は別の計画改訂・開始条件を要する。

```text
python -m pytest -q tests/test_phase4_llm_backend.py tests/test_phase4_llm_contracts.py tests/test_phase5_generation_admission.py tests/test_phase5_brain_admission.py tests/test_phase5_local_smoke.py tests/test_phase6_private_review.py --junitxml=logs/t339-r13/junit.xml
```

独立実行の外側上限は300秒。Windows subprocessは非表示。環境/PID/開始終了/exit/timeout/stdout/stderr/JUnitとsource前後hashを `logs/t339-r13/` へ保存。source変化・cleanup不能・ERROR時は事実を記録し、blind retryしない。通常assertion FAILでもbatchを完遂し、失敗を隠さない。文書検査とdiff確認も行う。

実行環境訂正: 初回T339はworkspace-write sandboxで実行し、225 PASS/169 subtests/16 FAIL/43 ERROR。失敗は一時領域のPermissionErrorであり、通常hostの契約を満たさない。初回原本を保持して、automatic approval reviewで認められた `require_escalated` の通常hostで同一source/6module/300秒の一回を別測定する。保存先は `logs/t339-r13-host/`。ACL/TEMP/security変更は行わず、承認が拒否された場合はBLOCKEDとする。

## Q1: 次回Stage B preflightの追加作業

次回の担当packetへ以下を転記し、そのrevisionを独立Reviewerへ通す。既存凍結文書や消費済み実行packetは今回変更しない。

1. 実観測の許可とprovider PID・所有者を確認する。providerの起動停止は行わない。
2. 新しいowner-only private保存先を用意し、PID確定後に一度だけ `await _record_provider_command_observation(pid, private_path, existing_observation)` を呼ぶ。既存3 booleanは同じ観測から渡す。
3. raw command lineを公開せず、redacted argvはprivate側へ、state/hash/environmentだけを公開handoffへ記す。OBSERVED以外のhashはnullを維持し、推測で埋めない。
4. exclusive create、保存原本と返却projectionの一致を独立確認。観測不能時は理由を記録し、必要条件を満たすまでlaunchを保留する。P2のmock PASSは実保存の証拠へ流用しない。
5. freeze/source/argv・保全・具体的一回実game許可を既存条件に従って確認した後にだけ実行する。

## 測定記録

Q6照合: `logs/t337-r13/collected-node-diff.json`。旧6test fileのbyteを別領域へcopyしてcollectのみ実施（製品の旧treeを実行した証拠ではない）。旧264/現284、追加21/削除1を確認した。初回collectは移設による相対import先欠落でERROR。ログを保持し、依存scriptをcopyした二回目でcollect成功。各runでテスト本体は未実行。

対象rename: `test_http_error_envelope_preserves_every_attribute_and_privacy` → `test_http_error_detail_uses_private_broker_metric_not_error_wire`。T336での19新規関数に、1件のrename追加名とパラメータ展開分1件を含め、node追加21/削除1となる。今回のdrain修正では既存node名を維持する。

修正後の実行結果・承認・残課題は `Docs/ai/handoffs/tasks/T337_R13_TEST_REPAIR_PLAN.md` に実測後記録する。

Q6照合の訂正: T340がparameter文字列内のスラッシュによる2nodeのprefix欠落を検出。collect原ログは不変のまま、最初の `::` より前のpathだけを正規化して訂正した。旧JSONは `logs/t337-r13/collected-node-diff.before-path-correction.json` に保持。264/284・追加21/削除1の件数は訂正前後で不変。
