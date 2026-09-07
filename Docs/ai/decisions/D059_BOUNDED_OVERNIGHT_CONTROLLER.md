# D059 — deterministic overnight controller

Recorded: 2026-09-08 / GPT-6 Astra / infrastructure author

ユーザーは開発基盤のみの完成を依頼した。Astraは開発基盤担当とPhase 3全体終了時のレビュー用であり、
日常の監督/計画はSol等、実装はQwenを基本とする。今回ゲーム機能を実装しない。

workspace-writeの上位CLIに一晩の権限を渡す試作は独立レビューでBLOCKINGとなった。
代わりに、上位モデルはtools無効のJSON提案/レビューのみとし、変更を決定的controllerと既存D058/v1へ限定する。
固定workpackageからclarificationと新規保護テストを準備し、独立承認後にQwen childを連続実行する。
親が子の最大呼出枠を発行前に予約し、既存repo共通lockと同一runの復旧を使う。

正確な契約と承認状態は `../design/OVERNIGHT_SUPERVISOR_DESIGN.md` と
`../design/reviews/OVERNIGHT_SUPERVISOR_DESIGN_REVIEW.md` を参照する。
このDecision自体は設計/実装の承認ではない。
