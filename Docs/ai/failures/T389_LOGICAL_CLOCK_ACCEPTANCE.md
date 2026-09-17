# T389 未完走の限定診断

Mainは新規保存証拠 `logs/t389-logical-clock-acceptance/measurement.json` と
`phase-wall.json`、現在のrunnerの監査集計箇所だけを確認した。旧runは再調査していない。

## 分類

- F acceptance/test defect（総予算）: 初夜600 REAL秒、day約1800 REAL秒、
  vote/night約600 REAL秒で進行している。scale0.1の各phaseは設定180/60/60 LOGICAL秒と一致。
  初夜60＋3日×300＋4日目昼180＋投票60 = 1200 LOGICAL秒で総予算に到達する。
  今回のGAME_TIMEOUTは停止したphaseが10倍待たされた証拠ではなく、正常なphase進行中の
  総予算到達。day4投票完了と総予算が競合する構成である。完走に必要な総予算は未確定。
- F acceptance/test defect（監査）: `_audit_summary` はgenerationのrequest_idだけを列挙し、
  既存generic検証は一意性を要求する。一方Phase6は同request_idのattempt1/2を正規repairとして
  許容し、semantic検証は(player, request_id, attempt_ordinal)で識別する。
  T389のB11重複警告はこの不一致を含む。原本欠落や真の重複がないとは未判定。
- D LLM output quality: OUTPUT_INVALID50、REPAIR_FAILED34、responsive accepted0。
  具体的な品質失敗と内容の判定は新原本の独立レビューへ渡す。
- E performance: queue p50約26.92秒/p95約58.87秒/max約80.08秒。性能測定として保持する。
  この値だけを今回の総予算到達のroot causeやGPU不足と断定しない。
- A/B: server phaseと総経過の観測から時計適用漏れ/混同は確認されていない。
  全経路に不具合がないという新たな承認ではない。Cは現時点で未確定。

## 結果と次工程

game_end=false/day4 vote、mandatory FAIL。今回起動したserver/broker/client/providerは停止済み。
GPU使用率は今回欠測。T391で次回用監視を作り、focused18 PASS・3秒実機読取を確認した。
新原本と新GPUツールだけを独立Reviewerへ渡す。旧時計承認は再利用する。
重大問題がある間、追加gameを開始しない。総予算/acceptance境界を変更する場合は
必要な設計gateを先に解決し、無断で上限を延長しない。Phase6未達、Phase7未開始。
