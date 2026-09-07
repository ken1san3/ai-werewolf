# D058 — 有限キューによる無人開発

Date: 2026-09-07
Recorded by: Implementer / GPT-6 Astra

ユーザーの「できるだけ無人でゲーム開発を続けられるように続きを作成」に基づく。
Qwenを実装の主担当とし、承認済み有限レシピを順番に実行する。
契約、Red候補、適用後の確認を実際の独立上位モデルへ送り、結果と対象hashを保存する。
実装設計は `../design/INFRA_AUTONOMOUS_DESIGN.md`、独立設計承認は
`../design/reviews/INFRA_AUTONOMOUS_REVIEW.md`。詳細規範はそこへ集約する。

一度設定した範囲・期限・呼出数・送信ファイルを実行中に広げない。
利用枠の有効な実測がある場合は残量を考慮し、ない場合は明示した有限呼出数で運用するか停止する。
集計は通常のプログラムで行い、LLMを呼ばない。Geminiを通常工程に追加しない。
新しい設計の自動承認、将来Phaseの自動生成、常駐スケジューラはこの実装に含めない。
既存のゲームDesign Gateと正式完了承認を維持する。JOB_DONEは当該レシピの完了のみを示す。

操作・制限・復旧は `../spec/AUTONOMOUS_DEVELOPMENT.md`。
