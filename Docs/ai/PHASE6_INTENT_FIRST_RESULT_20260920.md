# Phase 6 Intent-first cycle 1

## 上限と仮説

4 REAL時間（開始14:16:52Z、上限18:16:52Z）、最大4cycle。今回の仮説は「intent→本文→更新のtest-only生成形式が、HARDを増やさず本文/act整合を改善する」。C1/C2/K1/旧baselineは再生成・再採点しない。

## 実装・検証

製品schemaは不変。実験用closed schema、strict JSON、無損失adapter、双方向roundtrip、runnerのI1接点を追加。option/ref/authority等は従来validatorへ全体を渡して検査し、欠落補完・推測・trim・sort・deduplicate・意味補正は行わない。

PIDだけの追跡を保持process handle＋creation/exit time＋actual parentの照合へ変更。独立審査で指摘された終端spawn raceも限定修正し、曖昧な所有では停止操作を許さない。raw/stdout/stderrはOwner privateに保存。

| gate | 実測 |
|---|---|
| Python3.13統合 | 300 PASS（網羅補完前）、最終helper69/関連込み170 PASS |
| Python3.10統合 | 321 PASS（outer終端修正前） |
| Python3.10 outer最終差分 | 20 PASS / 2.28秒 |
| outer最終focused | 20 PASS、即spawn-exit・PID再利用・非所有保護を含む |
| Reviewer最終focused | 111 PASS |
| 非LLM schema変換 | 固定runtime版公式reference converterで32/32 PASS |
| tool review | T446 APPROVED、固定6file＋r1 planに限定 |
| docs/diff | PASS |

測定環境差による失敗と修正前の期待値不一致は [T444 handoff](handoffs/tasks/T444_INTENT_FIRST_CYCLE.md) に保持。T445実装、T446審査、T447独立測定の責務を分離した。新設計/旧証拠の再レビューは行っていない。

## 人工suite

T447で新候補32/32生成完了。Qwen3.5-9B、32case、seed4242026、context8192、max_tokens512、retry0/repair0。baselineは既存hashを再利用。candidate schema32/32、legacy31/32（TEXT_BOUND 1）、root order32/32、NONE32/32。独立意味審査32/32完了。

実行253.879 REAL秒、latency p50/p95/max 6.187/6.809/6.829秒、prompt/completion 55,068/7,511 tokens。GPU232samples、peak6,321MiB/100%、errors0。source67/67/config不変、outer timeoutなし・所有process残存0。詳細: [T447](handoffs/tasks/T447_INTENT_FIRST_MEASUREMENT.md)。

| 指標 | 保存baseline | Intent-first | 差 |
|---|---:|---:|---:|
| HARD pass / fail | 18 / 14 | 18 / 14 | fail ±0 |
| SEMANTIC pass | 7 | 8 | +1 |
| STYLE pass | 15 | 17 | +2 |
| act/text mismatch | 23 | 24 | +1（必須条件不達） |
| fabricated evidence | 0 | 0 | ±0 |
| secret disclosure | 5 | 1 | -4 |
| state contradiction | 3 | 2 | -1 |
| ability contradiction | 0 | 0 | ±0 |
| UNKNOWN | 0 | 0 | ±0 |
| 固定質問への本文回答 | 9/18 | 7/18 | -2 |
| NONE | 32 | 32 | ±0 |

root/intent/discussionの生成順は32/32で設計どおり。合法NONE（G14等）は保持。
意味評価: [T446 quality](handoffs/tasks/T446_INTENT_FIRST_QUALITY.md)。結果の再生成・再採点なし。

## 選択とbest known state

**Intent-first候補は不採用。** 主要指標の不一致24件は必須 `<23` を満たさない。
他指標の改善で相殺しない。HARD回帰による停止ではなく、次の生成契約変更にarchitectural判断が必要なためcycle1で停止する。
4時間は上限であり、残り時間を埋める追加runはしない。通常game/長時間Master Run/Phase7は未開始。

- 製品のbest known state: 開始時baselineを維持。製品code/schema/model/config変更0。品質候補の製品採用なし。
- 検証基盤: 独立tool APPROVEDのstrict adapter/runner/所有監視を保持。品質不採用はtool承認を取り消すものではない。
- rollback: 不要（test-only候補だけを追加）。C1/C2/K1保存結果は不変。
- 最も強い作業仮説: 生成順を意図先行にしても、既存speech_act oneOfとgrounded fieldを同時に満たす負担が残る。ただし今回の一条件で因果は確定していない。context不足やモデル弱さへ原因を置き換えない。
- 次の最小作業: 単一shape＋strict validatorの受理集合・null・groundingを設計比較する。まず非LLM設計だけで、実装/生成は未承認。
- 人間判断は一つ: **次scopeを「単一shape＋strict validatorの独立詳細設計」に進めるか。** Intent-first限定実装修正で解ける具体的欠陥は今回確認できていない。2-callへは進まない。

品質判定まで約42分、終了整理を含め約45分。新規生成32call、cycle1のみ。旧baseline/C1/C2/K1再実行0、retry/repair0。
1 seed/32件の限定比較であり、一般的なIntent-first全方式を否定するものではない。D077製品gateは未達。
