# D079: minimal-output一回品質測定の不採用

日付: 2026-09-23
状態: 決定済み

## 決定

T499のtest-only minimal-output candidateを製品採用しない。
T497独立意味評価と固定gateのMain hash照合に基づく。全32件を分母に保持し、
SEMANTIC改善でHARD回帰を相殺しない。追加生成・同条件retryは行わない。

## 根拠

baseline → candidate:

- HARD fail: 14 → 30
- SEMANTIC PASS: 7 → 19
- STYLE PASS: 15 → 12
- act/text mismatch: 23 → 27
- fabricated evidence: 0 → 2、ability contradiction: 0 → 2
- secret disclosure: 5 → 0、state contradiction: 3 → 1
- 固定質問回答: 9/18 → 6/18、legal NONE: 2/2維持、copy: 4 → 20
- quality UNKNOWN: 0。機械peer screenは0/4実施で、残り28件は未実施。

機械不受理28件のうちgrounding mirror不一致24件、非発話actionのnonnull本文4件。
HARD合成は2 PASS/30 FAIL。private update必要性が不明な32件をhostで補完せず、
機械結果のapplicability UNRESOLVED4/INVALID28と意味評価UNKNOWN0を区別した。

## 証拠

- `Docs/ai/handoffs/tasks/T497_MINIMAL_QUALITY.md`
- `Docs/ai/handoffs/tasks/T499_MINIMAL_SUITE_MEASUREMENT.md`
- `Docs/ai/handoffs/tasks/T499_MINIMAL_STRUCTURAL_DIAGNOSIS.md`
- `Docs/ai/handoffs/tasks/T499_MINIMAL_QUALITY_SUMMARY.json`
  SHA `269455ea100599483b3750546f95ff1dfc4a6aa3706e7e02458cbe93c299fd0d`

runは32call完走、retry0/repair0、source/config不変、所有process0。事前のnative互換停止は
generation0で原本保存後に独立承認済み修正を施した別freezeであり、品質生成のretryではない。

## best known stateと次の限定scope

製品コード・schema・validator・モデルは既存状態を維持する。不採用candidateはtest-onlyとして
隔離し、測定器・承認・新結果を証拠として保持する。実装の削除や原本rollbackは不要。
この実験だけで責務縮小全体を否定せず、H61単独の因果効果やモデル能力不足を断定しない。

次は生成に残ったgrounding mirrorの重複責務を対象に、参照存在・可視性・actor・authorityを
緩めず重複生成を省けるかを限定設計する。主観state補完やregex意味推定は使わない。
action/text競合、旧system指示の競合、copyを同時修正した候補は混ぜない。
詳細設計・独立承認前に次候補実装へ進まず、新provider測定は今回の一回許可に含めない。
Phase6は未達、通常game/Master Run/Phase7へ進まない。
