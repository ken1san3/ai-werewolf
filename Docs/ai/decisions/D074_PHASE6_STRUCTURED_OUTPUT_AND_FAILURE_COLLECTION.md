# D074 — Phase 6 structured output設定と失敗時の原本回収

Date: 2026-09-15
Status: Accepted design boundary — T352設計をT353が独立承認。実装の最終承認は別gate。

## 背景

T344は107件全てHTTP400、GAME_TIMEOUTで終了した。T348は同版provider sourceと保存証拠から、reasoning_format=noneが除外したthink grammarへtemplate prefixが投入される衝突を確認した。Mainの有限再現では_run_gameのcompletion wait例外が回収処理を飛ばし、実107件がsummary0となった。旧FAIL/UNKNOWNは保持する。

## 採用した境界

- provider-neutral defaultを変えず、Phase6接続profileだけreasoning_format=deepseekと厳密なenable_thinking=falseを設定する。動的json_schema/strict/content検証は維持する。
- profileは非秘密のconfig fingerprintとparent/child bootstrapに含め、対応不明の接続先へ無条件には送らない。
- Phase6の3 completion wait例外だけ、同一owned全件の停止確認後に既存原本を回収する。元の例外/cancellationを一次原因として保持する。
- 証拠不足を完了と扱わず、manifestは既存writer/validatorの検証を保つ。読取/cleanup/集計/hashの二次失敗は固定codeで記録する。
- private inventoryは内部結果に保持し、回収失敗rowの公開要約からartifact locatorを除く。通常成功/Phase5/Q8の既存projectionは維持する。

## 承認と限界

承認設計は `Docs/ai/design/PHASE6_SAMPLER_AND_COLLECTION_REPAIR.md`、SHA-256 fcc7ef1d0453889cacc6b6025c96251f94beb7f001af4f482326f6a2730fe414。独立審査は `Docs/ai/handoffs/tasks/T353_SAMPLER_REPAIR_REVIEW.md` とlogs/t353-reviewの各版。実装/実測はT351 handoffで統合する。

ゲームロジック、AI戦略、schema、timeout/停止policy、P3保留、providerのユーザー所有権は変更しない。実provider HTTP200/schema-validと実ゲーム成功は、今回のoffline修正・回帰から推定しない。
