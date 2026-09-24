# D081: Phase6探索経路の再評価と新しい有限比較の許可

日付: 2026-09-24
Status: ACCEPTED（ユーザーscopeの記録。詳細設計・製品採用の承認ではない）

## Authority

ユーザー「他エージェントの提案を確認し、これをすべて実行せよ」を、外部提案
`PHASE6_STAGNATION_REVIEW_20260924.md`のU1〜U5/G1〜G4全体への実施許可とする。
T505の静的診断だけの経路をT506へ置き換える。外部提案の原文は変更しない。

旧holdのうち、この新protocolに限り以下を解除する。

- 新しい共通3 seedでのbaseline/GB1の対照付き生成。旧原本・旧採否は上書きしない。
- 事前固定K回の機械検査付き再サンプル。無限retryや実行障害の再試行は許可しない。
- 既存Qwen/Gemmaの比較。新モデルDL、既存config上書き、本番モデル切替は行わない。
- Mainによるtest-only探索・測定・記録。候補ごとの重複review chainを省く。

受入・lifecycleの新境界は一回の詳細設計と独立reviewを通す。製品採用時の独立reviewを維持する。
主指標、非劣性margin、seed、call上限、停止条件は実測前に固定し、結果を見て変更しない。
speech_act/text整合は探索の主指標から外して診断指標とするが、構造validatorは緩めない。
生成後filterは機械的に検出できる失敗だけを扱う。自由文の意味安全を保証したと主張しない。
最終製品受入と探索選抜は分け、privacy/authority・ゲーム規則・最終安全条件を維持する。
通常game/Master Run/Phase7/Actions/main変更は今回実行しない。

## 提案の限定照合

1. 二項分布で両群をランダム標本とするq約0.43〜0.45は再現した。ただし実際は固定baseline、
   同じ異質な32ケース、相関する指標であり、その計算だけで採否確率1〜5%を実証できない。
   例: q=0.44993の6指標なら独立時2.77%、完全相関時55.01%。
   「相関しても桁は変わらない」「ほぼ確実に不採用」は結論として採用しない。
2. 二つの95%区間が重なることは、同等性・非劣性・差が標本誤差だけであることの証明ではない。
   新比較ではpairedケース/seed構造と不確実性を明示する。
3. GB1は既にkind＋authoritative_fact_ids選択→locked full outputの2段階。
   G3の段階生成とgroundingの組合せは実装済みであり、同じ方式を別の新案と偽らない。
   新paired測定でこの組合せを評価し、旧full-output責務が残る限界も記録する。
4. `phase6_grounding_basis_runner.py`は旧T480/source/CI/baselineへ固定されており、
   新3 seedとGemmaをそのまま実行できない。既存純関数と所有process/private evidence機構を
   再利用する新test-only runnerが必要。旧runnerの承認済みfreezeを変更しない。
5. speech_actは外部protocolへ送られなくても内部の状態・記憶・transactionへ影響する。
   「外に出ないから無意味」とはせず、診断化と構造検査維持を分ける。

## 研究との対応

[CICERO公式実装](https://github.com/facebookresearch/diplomacy_cicero)は戦略処理と対話処理を分ける。
[ReCon](https://arxiv.org/abs/2310.01320)は発話案と推敲を分け、
[形式制約の研究](https://arxiv.org/abs/2408.02442)は推論への悪影響を報告する。
いずれも本環境の原因を証明せず、有限実験の設計根拠として使う。

## 記録と次gate

T506 packetと`PHASE6_QUALITY_RECOVERY_PROGRAM_DESIGN.md`を現在のscopeとする。
旧D078〜D080は当時のgateによる不採用として保持し、新gateでの集計は別結果に保存する。
証拠の集計と意味採点を区別し、old annotationの修正で成功を作らない。
