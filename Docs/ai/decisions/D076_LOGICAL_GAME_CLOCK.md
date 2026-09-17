# D076 — Phase6のLogical Game Clock

日付: 2026-09-16
根拠: ユーザーの明示契約、T387実装、T388独立APPROVED。

AIWOLF_TIME_SCALEはdefault1.0、初期実測0.1。REAL安全監視/性能計測とLOGICALゲーム予算を
分離し、既存monotonicやtimeを全置換しない。詳細な対象は `Docs/ai/LOGICAL_GAME_CLOCK.md`。

必要な新差分の独立レビューを完了した承認を再利用する。ゲーム実行はT389で1回、
同条件retry禁止。Functional AcceptanceとREAL Performance Measurementを区別する。
必須条件合格かつgame_endでのみPhase6 DONE。0.1完走は1.0性能達成を意味しない。
Phase7は未許可。T385/T386は承認済みとして再検査しない。

承認証拠: logs/t388-logical-clock-review/review.json
SHA-256: fc918b95d1289d0e225754c1a89a105b529ce1d32d56f61a492f462ca594486d。
