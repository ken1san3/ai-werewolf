# Phase 6 枝内kind-first対照

Status: COMPLETE（限定対照完了、候補不採用、製品未変更）

## 結論

**kind-firstは不採用。** NONE固定は32→2件へ解け、act/text不一致も23→18へ減ったが、HARD fail14→23、根拠捏造0→8へ悪化した。SEMANTICも7→5 PASSへ低下。非NONE増加や内容回答増で相殺しない。

これでoneOfの微調整を打ち切る。枝順、NONE penalty/reason、property順の微修正、prompt注意書き追加、C1+C2の組合せを続けない。通常game/Master Runへ進める状態ではない。Phase6未達、Phase7未開始。

## 範囲

最新main `5a5635f` から `experiment/speech-act-kind-first-20260919` を作成。製品schema/prompt/validator/model/Logical Clockは未変更。C1/C2の棄却と旧baseline判定は再利用した。

静的分析は [生成順・枝負荷表](PHASE6_STRUCTURED_OUTPUT_STATIC_20260919.md)。枝の最初のkeyが異なるため、現schemaはkind値より前に候補枝を絞り込む。kind-firstは全枝の最初をkindへ揃えるが、grounding fieldの義務を減らさない。

[T437設計](design/PHASE6_KIND_FIRST_PROBE_DESIGN.md) とtoolをT438が独立承認。qw9の元32case、seed4242026、context8192、max_tokens512、retry/repair0、新candidateだけ32call。旧baseline再生成0。送信schemaのJSON値は同一、wire順だけ変更する。

## 比較条件

| 指標 | 元qw9 baseline | kind-first |
|---|---:|---:|
| HARD PASS / FAIL | 18 / 14 | 9 / 23 |
| SEMANTIC PASS / FAIL | 7 / 25 | 5 / 27 |
| STYLE PASS / FAIL | 15 / 17 | 15 / 17 |
| act/text不一致 | 23 | 18 |
| FABRICATED_EVIDENCE | 0 | 8 |
| SECRET_DISCLOSURE | 5 | 5 |
| STATE_CONTRADICTION | 3 | 3 |
| ABILITY_CONTRADICTION | 0 | 0 |
| 質問内容への回答 / 固定18件 | 9 | 11 |

不一致の減少とHARD fail・捏造・秘密・state・ability矛盾の各々非増加が必要。SEMANTIC改善や非NONE増だけでは採用しない。候補の相対改善とD077の製品合格は別であり、現baselineの違反を許容して製品へ移さない。

新candidateはCLAIM26、QUESTION4、NONE2。ANSWER/REBUTTAL/OPINION_CHANGE/RELATION_HYPOTHESISは0。全32出力でkindが先頭。UNKNOWN0、質問対象は旧baselineと同じ18件。G14の合法NONE対照の片方もCLAIMとなり本文と不一致だった。

根拠捏造8件は、存在する参照でも、その発話内容が生成した主張を支えていないケースだった。参照IDの存在検査だけでは意味上のgroundingを保証できない。

この固定条件では枝内順が出力選択へ影響することを支持する。一方、NONE回避が適切な接地を意味しないことも明確になった。単一巨大structured outputの負荷を検討する根拠になるが、同時生成が唯一原因だと証明したわけではない。元のprompt・モデル能力との相互作用は残る。

## 実性能と境界

32call完了、retry/repair0、finish_reason=stop32、structural28 PASS/4 FAIL。REAL266.943秒、外側276.868秒。入力55,068 tokenでbaseline同一、出力8,873 token（baseline7,110）。request p50/p95/maxは6.717/7.177/7.426 REAL秒（nearest-rank）。GPU244 samples/errors0、全GPU VRAM最大6,418 MiB。機能品質と実性能を分離し、非NONE増加を性能向上とも呼ばない。

canonical input同値32/32、candidate wire差32/32、private request.binとraw/resultのhash対応32/32。source/config/plan不変、tool所有残存0、最終listener0。

外側監視のPID番号追跡には、開始前から存在した非所有process2件をaliveと誤検知する欠陥が観測された。作成時刻との照合で今回所有ではないことを確認し、停止操作は行っていない。raw記録は維持。次回実provider前にPIDと作成時刻・親子関係を使う所有判定へ限定修正/非LLMテストが必要。今回のrunは再実行しない。

## 設計比較と費用

[A〜E比較設計](design/PHASE6_STRUCTURED_OUTPUT_OPTIONS.md) はT438が概念比較として承認。B/C/Dの詳細実装や追加生成の承認ではない。

- A: 現行同時生成。今回kind-firstが無効ならoneOf枝順/NONE penalty/追加reason/property微調整/注意書き追加を打ち切る。
- B: intent→本文→更新の1call。本文より前に接地した発話意図を置く。明示fieldの再配置だけで旧validatorへ渡し、推測補完しない。
- C: 単一shape＋strict validator。kind別必須/null/非該当/参照を厳密検査して旧unionへ変換。生成を簡単にするために受理境界を緩めない。
- D: 2call plan→utteranceは最後。9clientが各1判断を行う際、provider要求は9→最大18。32caseは32→最大64call。第2入力はsnapshot/検証済みplan再送となり、tokenも別計数が必要。
- E: regexはscreen補助まで。本文から意味metadataや根拠を機械的に生成しない。

Dの費用の例: 同じ生成service時間を仮定すれば、旧qw9のrequest合計171.394秒/32件を使った単純計算は、9件約48.2秒→18件約96.4秒。これは同費用callという仮定の算術で、plan/utteranceの実測予測や共有queueのp95ではない。入力長・生成長・admission/deadline・stale/cancelの追加コストは別途測定が必要。現在の32call枠でDを試さない。

token会計は [T440独立ticket](tasks/T440_TOKEN_ACCOUNTING_DESIGN.md) に分離。native tokenizer、schema in/out-of-band、completion reserve、truncation契約を扱い、今回の品質原因や同時変更にしない。

## focusedと承認

Windows Owner/Python3.13: 新probe＋既存C1/C2/comparison/conversation 139 PASS（18.27秒）。Python3.10: probe/C1/C2/comparison 113 PASS（18.47秒）。製品grounding/memory回帰84 PASS（37.10秒）。check_docs/diff PASS。

T438独立tool確認は11 PASS。[独立審査](handoffs/tasks/T438_KIND_FIRST_REVIEW.md) の設計/tool APPROVEDを再利用。通常game/長時間Master Run/Phase7は0。

## 凍結証拠

- plan: `48286abc9dfcbf5e8b24c7dd85f5ed7d5fc296b889d13362ca5409ac3a1345b3`
- runner: `017a08b6b028ebb9af8942d122994203f3b33b3b0e15fe911a749dd4fea691c3`
- helper: `7bdff4bb28046070cefabbd76ce0cc4193c3f62949f0ca190df4f879cfd4c7d1`
- test: `570ce616f80ed2cec09b8c9d85f00f61fc14a67672903ae83cb81066f14a7676`

- result: `37d34e30b0274d866bcce7003e48562585bf48bd4f9e935463f27a377a652b68`
- annotation: `65bf0c3660b2ba76b4393409841800683b6b2c6c3649094c3c513fb4f99d4f98`
- [safe32件](handoffs/tasks/T439_SAFE_RESULTS.json): `aee5b06fe9250607d1150e5ff7737a76bfe56a8679ec92a75603a5c4e743a4f9`
- [独立測定](handoffs/tasks/T439_KIND_FIRST_MEASUREMENT.md)、[Main変更/検証記録](handoffs/tasks/T436_KIND_FIRST.md)。

private原文はOwner専用root、公開成果物はcase ID/hash/数値/固定分類のみ。

## 次の最小作業と残存risk

次候補は**B intent-firstの詳細契約作成**を推奨する。全actionのfield移送表、許可済み参照の候補集合、intent→本文→更新の一貫性、strict adapterの拒否条件を先に定義する。既存のgrounding義務を残せるBを先に比較し、null field集合と出力費用を増やすCは次点。Dはcall数/queue/lifecycle費用があるため最後とする。

これは製品採用や新32生成の開始ではない。B/Cの詳細設計→独立承認→offline/focused→一因子の人工対照→独立意味評価の順を維持する。次の実provider前には上記所有監視も修正する。

残存risk: 32case/1seedの有限観測、自由文の根拠捏造・秘密/状態矛盾、本文とactの不一致。モデル採用は未決定、Qwen baselineを保持。候補を製品へ入れず、通常9人game/長時間Master Run/Phase7へは進まない。
