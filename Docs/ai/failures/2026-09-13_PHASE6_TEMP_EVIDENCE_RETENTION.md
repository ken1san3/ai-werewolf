# T252原本の一時領域保全失敗

Status: OPEN / BLOCKED。追加test・開発・review dispatchを停止した。

## 観測と原因境界

T252初回完走の61private artifactsはpytest標準一時領域に残され、hash一覧だけをrepository logsへ
保存した。Mainはその後、通常文脈の独立読取りで61件全てのbytes/hash一致を確認した
（logs/t252-test/main-private-verification.json）。その確認は当時の事実として維持する。

T253/T254の短期filesystem検証後、T254の通常require_escalated経路の最終sealが
`pytest-70/test_p6f_nine_client_semantic_0/run/broker.active.json` のstatで
FileNotFoundError / WinError 3となった。原本61件の現在hashは再確認できなかった。
これはsandboxだけでpathを見失ったという報告ではない。

Mainも2026-09-13 14:37:31 UTC、通常文脈で親ディレクトリの名前・日時だけを一度確認した。
`C:\Users\<user>\AppData\Local\Temp\pytest-of-<user>` は存在するが `pytest-70` は存在せず、
直下にはpytest-73/74/75だけが残っていた。追加pytestや原本復元・移動・削除は行っていない。

インストール済み `_pytest/tmpdir.py` はtmp_path_retention_countの既定値3、policyの既定値allを定義し、
session終了時のcleanupを登録する。`_pytest/pathlib.py` のcleanup_candidatesは
`number <= maximum_existing_number - keep` を削除候補とし、lock/deletability確認後にcleanupする。
実行argvとrepository pyprojectにはretentionの上書きがなく、現在の73/74/75と70不在はこの機構に整合する。
削除の瞬間、実際のPID、Windows syscallは採取していないため、削除主体と正確な時刻は推論のまま残す。

## Mainの責任と影響

Mainは「原本を保持する」としたT253/T254 packetを作りながら、pytest標準一時領域外への
保全を完了せずに後続テストを許可した。後続pytestによる自動整理を考慮しなかった判断に問題がある。
明示削除コマンドがなかったことは、原本保全を満たした根拠にはならない。

T252 raw stdout/stderr・結果・PID/時刻・private artifact hash一覧、当時のMain照合記録は残っている。
ただしこれらはprivate shard/manifest/受理ledgerの内容そのものではなく、hashから原本を復元できない。
再生成したファイルを元原本と名付けたり、元FAILをPASSへ変更したりしない。
T253限定修正とT254の84件・関連3件/6subtests・独立7件PASSは、それぞれの測定範囲に限定して保持する。
F全体承認と修正後完走は未成立。詳細な復旧条件は次のMain報告を参照する。

Recovery: [Main停止・再開報告](../handoffs/MAIN_INTEGRATOR_EVIDENCE_RETENTION_BLOCK_2026-09-13.md)
Evidence: logs/t254-test/main-retention-observation.json、final-seal.json、T252/T254 handoff。
