# D098: 新S4初回評価結果

Status: RECORDED
Date: 2026-10-01

D097の許可に基づくS4_COMMON_PROVENANCE_V1の初回blind評価を完了した。元の192行/96pairを固定し、独立custodian/tool APPROVED後、設計/実装/旧採点へ参加していないfresh Reviewerが192行を一度ずつ観測した。annotation SHAをfreezeした後だけMainがunblind・承認済みhelper適用・機械集計を行った。

| 新S4、各96行 | PASS | FAIL | UNKNOWN | MEASUREMENT_NOT_OBSERVED |
|---|---:|---:|---:|---:|
| baseline | 93 | 0 | 0 | 3 |
| candidate | 81 | 0 | 5 | 10 |

candidateのUNKNOWN5件は `AUTHORITATIVE_ASSOCIATION_UNAVAILABLE`。保存contextの能力結果はtarget/result/orderを持つがEvidenceRefを持たず、canonical identity/actor associationを推測補完しなかった。自然文の真偽を主観でPASS/FAILへ補正していない。不受理13行を分母から落としていないため比較全体はMEASUREMENT_INVALIDで、品質改善/採用を認定しない。

旧annotation SHA 61ab122155f398460a7a889788dc85371b7d99e990fa81ce072c770b46c0a192、旧D092と他metricは不変。新annotation SHA 7df7cdf5656443e5eca16faddcbfa95e2b60d0993fb6446d35791a284544ff89。新生成/provider/process操作0。全source hash一致。

次の安全なscopeはT554の将来向けtest-only一次source identity設計だけ。明示選択されたowner abilityの既存authorityを変更せず、source hash/pointer/ownerの機械的束縛を検討する。これは今回のannotation再評価や欠測補完の許可ではない。新provider/製品統合/旧判定改変を行わない。

正本結果: `Docs/ai/handoffs/tasks/T553_S4_INITIAL_COMPARISON.json`。独立評価: `Docs/ai/handoffs/tasks/T553_S4_BLIND_EVALUATION.md`。custodian確認: `Docs/ai/handoffs/tasks/T553_S4_CUSTODIAN_REVIEW.md`。
