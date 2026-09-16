# D073 — Master Test Planに基づくテスト運用へ切替

Status: ACCEPTED（2026-09-14、ユーザーの即時変更指示）

ユーザーが明示的に実行を求めた添付本文を
`Docs/ai/PHASE6_TEST_OPERATION_RESET_2026-09-14.md` に保存した。
原文SHA256: e89a3c3ed6d1638530e4a5b232e58d82d1b8cd5cfba0b9ba5a1aab3d25d906ca。
原文を今回の最優先運用規則として採用する。旧packetやD072の逐次試験手順より優先する。

2026-09-14追記: 初回候補報告後、ユーザーがEXTERNAL_REVIEW_LOG.mdの指摘修正を依頼した。
T296で既存5修正と予定A16–A20を完成し、Mainが計画R1と最終collection/catalogを補正する。
以下の「今回はコード修正なし」は初回候補提示時の境界であり、この限定修正依頼が解除した。
FREEZE前の実試験禁止、実game直前承認、旧cycle取消は引き続き維持する。
採否はhandoffs/EXTERNAL_REVIEW_R03_DISPOSITION.mdを参照。

2026-09-14 G1追記: ユーザーの「新規指摘 G1 を確認」に続く「これを修正」により、
T297の新profile代表content計測を一回だけ許可する。既存counterのCPU vocabulary限定計測と
本件に必要な一回の独立read-only確認であり、製品数値変更、pytest再開、追加review chainや
実game起動は許可しない。補完結果を同じMaster Planへ反映して停止し、未FREEZEの条件を維持する。

## 今回の作業境界

実行中・待機中の旧テストサイクルと派生監査を打ち切る。コード・既存pytest・原本は削除しない。
現在の実装を保存し、CANCELLED/DEFERRED/KEEPを整理、Phase6 Master Test Planと全Test IDを作成する。
TEST PLAN FREEZE候補を提示して一度停止する。今回はコード修正、pytest、実game、計画レビューのdispatchを行わない。
旧途中結果はPhase完了判定に使用せず、FAIL/UNKNOWN/原本欠落を成功に読み替えない。

## 今後の固定順序

実装完了 → Master Test Plan作成・レビュー → TEST PLAN FREEZE → 全計画テスト → 全結果集計 →
FAIL一括triage → Repair Cycle 1 → 必要範囲再試験・回帰 → 必要ならCycle 2 → Final Run。
Phase単位でrepairは最大2cycleを原則とし、task名の変更で回数をリセットしない。
実行中の通常FAILでは独立した安全な計画項目を続け、コードをその場で変更しない。
クラッシュ/データ破壊/private漏洩/重大security/進行不能/明示完了条件不成立/重大regressionがあれば、
危険な依存項目を止めてBLOCKEDとし、残る安全な項目を集計まで進める。

FREEZE後にTest ID、調査、追加review、provenance層を担当者判断で増やさない。
計画外はDF形式で記録し、非blockerは次Phase/technical debtへ送る。証拠管理の完全性だけをblockerにしない。
Reviewerは凍結Test IDへのPASS/FAIL/BLOCKEDを判定し、新しいcritical pathを生成しない。
独立性を要する担当をMainや別責務で代替することはせず、必要最小限の予定担当で行う。

Stage Aはdeterministic/syntheticをまとめて実行。Stage Bは同じ実LLMゲームから複数項目を評価する。
今回候補は実LLM1回。失敗ごとにゲームを追加しない。修正確認の追加が必要な場合も既存FAILのIDに限定し、
実起動直前のユーザー承認を維持する。現時点の実起動許可はない。

private原本はpytest外のgame/syntheticに物理分離する。新provenance schema、producer ownership証明、
provenance専用review/監査、原本を証明するための追加試験は作らない。
ゲームprocessの通常cleanupとprivate保護は維持する。既存ACL/security/TEMP、実原本は変更しない。

## 製品境界と未完了

512は採用上限。実測510は代表contentの計測であり、512で全応答が常に収まる保証とはしない。
Phase5/Q8、server truth、privacy/authorization/schema/linkage、length拒否、repair1、CHAT2/CO別は維持する。
新指示§12の11評価対象を計画へ全て含める。D072の5完了条件とのblocker対応は候補計画に明示し、
質問応答/反論/判断更新を隠れた追加必須条件へ変えず、計画レビューで先に確定する。

T293は途中実装。初回FAILと留保completionの誤起動・強制停止を保存し、完成/PASSとは扱わない。
新規指示後は既知12processの停止報告を受領した。初回runの所有監視UNKNOWNは未解消の履歴。
今回は追加所有証明や旧raw監査を派生させない。
