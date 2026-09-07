# D059初回ゲームpackageが公開API未確定で停止

Observed by: Infrastructure / Astra
Environment: Local Windows
Date: 2026-09-08

Run: `C:/Users/<user>/.aiwolf-runs/overnight-9738f6620f004b829756bd9410749df2`
Unit: `p34-reaction-base`
Result: NEEDS_DESIGN、0/1完了、Qwen呼出0、ゲームコード変更なし。

承認済みPhase 3.4設計はjitterの決定性とsnapshot/outcomeの必要な意味を指定していたが、
テストが参照するjitter関数名・引数、trigger/outcomeの具体的なPython名とfieldを固定していなかった。
plannerがこれを新しい公開API判断として検出し、テスト生成前に停止した。

doctorのready=trueは依存・入力形式・接続の確認であり、実装契約の意味的な完全性を保証しない。
「起動設定済み」と「実装が完了まで進むことを確認済み」を混同しない。

機械report: GPT-5.5 1回、input 24,402 / output 614、計25,016 tokens。
未報告token項目0、report追加LLM呼出0。未完了のため完了単位あたり効率は算出不可。
旧runのstate/証拠/消費量を編集・削除しない。補足の独立承認前に自動再試行しない。

対策: 詳細設計担当が必要最小限の公開API契約を補い、独立Reviewerの実判定を受ける。
次packageでは補足を上位packetとQwen入力の双方へ含め、未確定のAPI名を残さない。
新packageへの移行時も今回の失敗分を利用実績から除外せず、残りの呼出上限と期限を明示する。

修正: 公開API補足を実Reviewer Sol (`gpt-5.6-sol`) が独立承認。
`../design/reviews/PHASE3_4_REACTION_TYPES_API_ADDENDUM_REVIEW.md` に署名/hashあり。
`.infra-runs/overnight-ready-v2/replacement-record.json` に旧run・消費・修正版hashを関連付けた。
旧runは未変更。修正版doctorはLocal Windows exit0/ready=true、生成呼出0。
追加した文書パスでWindowsの250文字事前制限に達したためtask_idを`p34`へ短縮して再検証。
基盤runtime・ゲームコードは変更していない。修正版の完走は未検証。
