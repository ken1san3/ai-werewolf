# Phase 6 完走検証の有限総予算設計

## 1. 目的と維持条件

Phase 6 の次回完走検証に限り、ゲーム期限の合計を有限のまま 3000 LOGICAL 秒まで許可する。
モデル、provider concurrency、発言量、各 phase の期間、`standard_9` のルール、seed、時計換算、
B01–B11 の合格条件は変更しない。実装と実行は独立Reviewerの承認および別途要求されている
品質修正のgateを通過した後に限る。朝07:00 JSTは開始条件ではなくデータ収集の目安である。

## 2. 現在の 1200 LOGICAL 秒が完走境界にならない理由

現行 preset は Night0 60秒、Day 180秒、Vote 60秒、Night 60秒である。Runoff が発生すれば
さらに60秒を要する。T390が確認した新規証拠では、通常のphase進行だけで次の位置に達した。

```text
Night0 60
+ Day1–3 × (Day 180 + Vote 60 + Night 60) = 900
+ Day4 Day 180
= 1140 LOGICAL 秒
```

残り60秒は Day4 Vote と重なり、1200.0004321 LOGICAL 秒で `GAME_TIMEOUT` になった。
したがって1200は異常停止を検出した値ではなく、正常なphase期間の途中を切る値である。

## 3. LOGICAL 総予算

次回の明示的な Phase 6 completion budget は **3000 LOGICAL 秒**とする。

根拠は9人ゲームで「各昼夜サイクルに少なくとも1人の脱落がある」場合の保守的なphase上限である。
9人から終端まで最大8回の脱落機会を見込み、毎日Runoffが1回発生する最長サイクルを使う。

```text
Night0 60 + 8 × (Day 180 + Vote 60 + Runoff 60 + Night 60)
= 2940 LOGICAL 秒
```

3000はこの2940に60秒を加えた有限境界である。phase期間やルールを短縮せず、Day4 Voteより後の
終端機会を観測できる。一方、これは終端保証ではない。`standard_9` は棄権を無制限に許し、同票後を
`no_lynch` とし、突然死を無効にしている。護衛成功や襲撃結果によってNightにも脱落がない場合がある。
LLMの出力もseedだけでは固定されない。このため脱落なしのサイクルは反復でき、有限の規則上限日数はない。
3000到達時に `game_end=false` ならB01は引き続きFAILであり、追加時間や同条件再試行へ自動移行しない。

## 4. 設定境界

- `--max-seconds` は引き続きLOGICAL秒であり、Phase 6 の明示的completion構成だけ上限3000を受け付ける。
- 非Phase 6、既存smoke、既定値1200、および `GamePlan.hard_limit_seconds=1200` の意味は変えない。
- Phase 6 でも3000は自動既定値にせず、次回freezeで明示する。`3000 < value`、非有限、0以下は起動前に拒否する。
- serverとbrokerへ同じ3000 LOGICALを渡し、metadataにも単一値を保存する。別々の値や暗黙の延長を認めない。
- `AIWOLF_TIME_SCALE=1.0` の換算は恒等のままにする。明示3000なら3000 REAL秒相当、既定1200なら従来どおりである。
- 初期検証の `AIWOLF_TIME_SCALE=0.1` では3000 LOGICAL秒は30000 REAL秒相当である。

## 5. 独立REAL安全上限

LOGICAL総予算から安全監視値をruntimeで自動算出しない。次回freezeでは次の値を別の設定として明示し、
metadataへ個別保存する。

| 境界 | 値 | 用途 |
|---|---:|---|
| game completion | 3000 LOGICAL秒 | serverのゲーム期限 |
| process supervision | 36000 REAL秒 | server結果を待つ親・broker監視 |
| outer test watchdog | 36300 REAL秒 | readiness、結果回収、停止処理を含む外側上限 |
| cleanup | 120 REAL秒 | owned processの停止確認 |

36000 REAL秒はscale 0.1のゲーム換算30000 REAL秒に20%の監視余裕を加えた値である。
outerはさらに300 REAL秒を持ち、既存のready待ち、client status回収、broker結果回収を収容する。
cleanup 120秒はouter timeout後にも別枠で実行し、外側cancelで省略しない。socket timeout、provider drain、
cancellation、poll、sleep、GPU監視間隔、telemetryは引き続きREALであり、3000/36000へ連動させない。

監視値の大小関係を起動前に検査する。

```text
3000 / 0.1 = 30000 REAL
30000 < 36000 < 36300
cleanup = 120 REAL（outer後の別枠）
```

scale 1.0でも明示値を勝手に縮めず、LOGICALとREALの独立性を維持する。これらの大きな上限は
停止を遅らせる許可ではない。server result取得後は直ちに通常のstatus回収・broker停止・cleanupへ進む。

## 6. 結果契約

- `game_end=true` と既存B01条件を満たす場合だけ完走とする。
- 3000到達、REAL supervision、outer watchdog、cleanup failureを別のreasonと時計単位で保存する。
- `time_scale`、設定LOGICAL予算、`logical_duration_sec`、`real_duration_sec`、REAL監視3値を保存する。
- exact 3000のserver終端結果は、serverが期限内に確定して結果を書いた場合に回収する。親監視との競合で
  結果を捨てず、既存のwait-failure recoveryとcleanup後に保存済みserver truthを一度だけ回収する。
- 3000で未完走なら結果はFAILのまま固定する。同条件retry、予算の自動倍増、Phase7開始を行わない。
- scale 0.1の完走はscale 1.0の性能合格を意味しない。

## 7. 最小focused検証

実LLMやゲームを起動しない既存test seamだけを使い、次を先行する。

1. Phase 6 の明示3000を受理し、3000超、0以下、非有限をpreflightで拒否する。非Phase 6と既定1200は不変。
2. scale 0.1で3000 LOGICAL→30000 REAL、scale 1.0で3000→3000を既存`GameTime`で確認する。
3. 明示した36000 REAL supervisionがscaleで再換算されず、server/broker/親waitとmetadataへ正しい単位で配線される。
4. fake clock/fixtureで2999.999相当ではtimeoutにせず、3000到達で未完走を一度だけ`GAME_TIMEOUT`にする。
   exact境界で既に書かれたserver resultはrecovery後も失わず、二重cleanupしない。
5. 3000 LOGICAL timeout、36000 REAL supervision timeout、36300 REAL outer timeoutを異なるreasonとして保持する。
6. 現行phase期間60/180/60/60、preset rule、モデル、concurrency、発言上限が差分で変わっていないことを固定値testで確認する。

製品差分、focused結果、この設計を対象未設計の独立Reviewerが承認するまで次runを開始しない。
品質修正側の独立gateもすべて通過した後に限り、同一freezeの実gameを1回だけ実施する。

