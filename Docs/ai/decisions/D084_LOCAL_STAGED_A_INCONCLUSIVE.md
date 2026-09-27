# D084 — 段A（L1＋L3）一回測定はINCONCLUSIVE

Status: ACCEPTED
Date: 2026-09-27

## 決定

T510のtest-only段Aは製品採用しない。事前固定deadlineで停止し、96行中72行が未実施のため、
品質比較はINCONCLUSIVEとする。完走、非劣性、品質劣化、製品安全の証明にはしない。
同条件retry・未実施補完・時間延長を行わず、原本・seal・全分母を保持する。

## 根拠

- `Docs/ai/handoffs/tasks/T510_SAFE_RESULTS.json`：保存済みT506 baselineとの算術比較。
- `Docs/ai/handoffs/tasks/T510_LOCAL_STAGED_SEMANTIC_REVIEW.md`：新本文だけの独立評価。
- `Docs/ai/handoffs/tasks/T510_LOCAL_STAGED_EXPERIMENT.md`：設計/tool承認、focused、有限実測。

55 provider calls、構造受理19、P枯渇4、途中ERROR 1、NOT_RUN 72。
観測本文23件ではHARD 20 PASS/3 FAIL、SEMANTIC 10 PASS/13 FAIL、STYLE 15 PASS/8 FAIL。
各軸73 UNKNOWN。状態矛盾3、act/text不一致21、accepted T planのNONEは23/23。
秘密開示・捏造根拠・能力矛盾・完全copyの観測0は、未観測行の安全を意味しない。
合法NONEは6件とも未実施。全96行/固定質問54行のbootstrap判定はすべてINCONCLUSIVE。

同じterminal 23行のwall 1103.860秒に対しprovider latency 346.824秒、差757.036秒（68.58%）。
これは生成call外の時間であり、特定関数やCPU/GPU性能への因果帰属ではない。
終了後offline profileでは全32入力の毎call再構築・所有port照合・HTTP client構築のコストを観測。
測定時全差分の内訳を特定したとは扱わない。context上限は最大3068/8192であり、容量不足の証拠はない。

## 維持する状態と次の境界

best known stateは製品不変、独立承認済みtest-only tool、seal済み部分実測と独立評価。
L1＋L3は結合介入であり、個々の責務縮小の効果を断定しない。D082/D083は維持する。
次はT511のoffline限定scopeでrunner時間の根拠を整理する。新しい計測、予算・lifecycle変更、
L2/API/製品統合はこの決定から許可されない。authority/visibility/所有権確認やdurable記録を削らない。
