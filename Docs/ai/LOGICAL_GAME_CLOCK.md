# Phase 6 Logical Game Clock

T387のユーザー指定契約。現在のPhase6 runnerでゲーム時刻とREAL監視を分離する。
モデル、concurrency、token/chat上限、ゲームルール、wire schema、責任・所有権は変更しない。

## 設定と単位

`AIWOLF_TIME_SCALE` は未設定なら1.0。正の有限数のみ受け付け、非数/0/負数/非有限値や
換算overflowは起動前に拒否する。実acceptanceの初期値は0.1。
logical elapsed = real elapsed × time_scale。logical budget / time_scale = real budget。

| 対象 | 実装上の単位と換算箇所 |
|---|---|
| server phase/vote/night期限 | runnerの注入clockだけLOGICAL。coreは既存の整数時刻を使用 |
| game completion | `--max-seconds`をLOGICAL予算としてserver待機へ換算 |
| client phase/reaction/vote/admission/queue期限 | server残りLOGICAL時間をNetworkClientでREAL残り時間に換算。以後のabsolute monotonic deadlineはREAL |
| decision/reaction/vote予算・guard・minimum start budget | runner compositionで一度REALへ換算 |
| provider total response予算 | backendとbrokerの各総予算境界で同じlogical値をREALへ換算。直列に二重換算しない |
| connect/read/write/pool socket timeout | REAL。不変 |
| abandoned provider drain、cancellation、shutdown、lock安全 | REAL。不変。長い総応答予算を使っても放棄後の安全待機は延びない |
| process supervision、ready、poll、sleep、cooldown | REAL。scaleで延長しない |
| wall timestamp、queue/generation/provider timing・tokens | 既存REAL計測・件数。不変 |

scale1は従来のstart-gate client clock/sleepを維持する。非1ではclientはREAL monotonicと
asyncio.sleepを直接使用し、別processのadmissionと同じ絶対clock領域を使う。
serverだけがLOGICAL clockを使用し、clientからserver時刻を変更できない。
この運転構成は `scripts/run_phase5_local_smoke.py --phase6`。別の独自server起動構成では
同じserver clock注入とclient予算換算が必要であり、環境変数だけで未対応serverを変換したとは扱わない。

## 安全監視をゲーム期限から分離

`--real-supervision-seconds` は独立したREAL上限。未指定は従来のmax-seconds数値を維持し、
scaleから自動算出しない。broker監視はこの値＋既存ready30秒、親のserver結果待ちはこの値。
cleanup・socket・test runnerの上限は別のREAL値のまま。
0.1運転で従来の1200 REAL監視を使うと、day180 LOGICAL=1800 REALより先に監視が切れる。
今回の候補実行条件はgame1200 LOGICAL、明示supervision14400 REAL、外側総監視14700 REAL、
cleanup120 REAL。これらのREAL値は実行前に独立審査し固定する。scale変更では増減しない。

## 証拠と判定

server resultおよび成功/失敗のrun rowに `time_scale`, `real_duration_sec`,
`logical_duration_sec` を保存。開始不成立/欠測はnullであり0/PASSへ置換しない。
winner、reached day/phaseも保存する。既存phase wallとqueue/generation latencyはREALのまま。
run metadataにscale、game logical予算、独立REAL監視値を保存する。

Functional Acceptanceは既存B01–B11とmandatory式を使い、必須合格かつgame_endでのみPhase6 DONE。
Performance MeasurementはREAL時間、token、queue/generation p50/p95/max等をそのまま報告。
socket read timeoutがREALのため総応答予算より先に切れる可能性も残す。
scale0.1での完走はscale1.0の実時間性能達成を意味しない。

## 検証範囲

LLMなしの `tests/test_logical_game_clock.py` と関連focused回帰を先行する。
製品差分＋本仕様＋新focused証拠を独立Reviewerが一度審査し、その承認を実行gateで再利用する。
実LLMは9人game1回。同条件再試行は禁止。保存証拠をA適用漏れ/B時計混同/C製品bug/
D出力品質/E性能/F試験欠陥へ分類し、必要時のみ限定修正する。Phase7は開始しない。
