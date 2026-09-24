# Phase 6 品質回復program — T506結果

Status: COMPLETE / NOT_ADOPTED

## 比較の範囲

外部提案のU1〜U5/G1〜G4をD081と独立APPROVED設計に従い実行した。
本書は新しい3 seedの結果だけを比較する。過去の採点・採否は変更しない。
canonical 32 case、seed `4242027/4242028/4242029`、context8192、slot1。
製品、ゲーム、既存validator、モデル設定原本、Actions、mainは変更していない。

- 設計: `design/PHASE6_QUALITY_RECOVERY_PROGRAM_DESIGN.md`
- 独立設計review: `handoffs/tasks/T506_QUALITY_RECOVERY_DESIGN_REVIEW.md`、APPROVED。
- 実測HEAD: `e6f8f60`。plan SHA `8b0dbe55ec2b9cbf5d604a795676052ebae436a07b0b75046c2c7d36c5865fdc`。
- 新原本: `logs/t506-quality-recovery/program-v1`。private本文は公開文書へ転載しない。
- 意味annotationはG4によるMainの探索評価。blind評価・独立製品採用判定ではない。
- Qwen既存GB1とfilterの第1出力は同一hashで再利用。filterの追加出力だけ新しく生成。
- SEMANTIC本文annotationと、構造受理も要求する主指標outcomeを分ける。
  構造不合格でも可読本文を採点し、受理不能・未生成を分母から落とさない。

## Qwen paired 3-seed結果

各列96出力。質問は固定18問×3 seedで54。旧32件baselineとはrubricが異なる。

| 指標 | 新baseline | GB1 | GB1＋有限filter |
|---|---:|---:|---:|
| 構造受理 | 93 | 92 | 92 |
| HARD失敗（構造失敗込み） | 27 | 10 | 5 |
| SEMANTIC本文PASS | 50 | 45 | 50 |
| 主指標outcome PASS | 48 | 45 | 48 |
| STYLE PASS | 59 | 55 | 58 |
| 固定質問への内容回答 | 29/54 | 16/54 | 16/54 |
| act/text不一致（診断） | 78 | 60 | 61 |
| fabricated evidence | 4 | 0 | 0 |
| authority違反 | 1 | 0 | 0 |
| secret disclosure | 14 | 1 | 1 |
| state contradiction | 4 | 2 | 1 |
| ability contradiction | 1 | 1 | 0 |
| exact peer copy | 3 | 5 | 1 |
| 内容意味UNKNOWN | 0 | 1 | 0 |
| G14 NONE整合診断PASS | 3/6 | 0/6 | 0/6 |
| 物理provider call | 96 | 192 | 追加22 |
| 再利用込み構成call | 96 | 192 | 214 |

filterはG1のchoice/output初回192 callを再送せず、14 caseで計22 callを追加した。
4件はK=3を使い切り未受理。最後の不合格本文は証拠として残しただけで採用していない。
受理された92件に限定した診断ではsecret disclosure1件が残り、state/copy違反は未受理側。
全件受理を要求する絶対条件も満たさず、分母を受理件だけに縮めて採用しない。
UNKNOWNのGB1 1件はCO判断と実actionが競合し、一意の発話意図を確定できなかった。
filter後には合法SILENCEとなり、本文annotationはPASS、act整合は別診断とした。

## 事前登録した比較

case内3 seedを平均し、32 case（質問18）のpaired clusterをbootstrap100,000回。
96出力を96独立caseと扱わない。数値は候補−新baselineの率差。

| filterの指標 | 観測差 | 片側95%下限 | 非劣性margin | 判定 |
|---|---:|---:|---:|---|
| 主指標SEMANTIC outcome | 0.0000 | -0.1146 | -0.0625 | INCONCLUSIVE |
| 質問回答 | -0.2407 | -0.3704 | -0.0556 | INCONCLUSIVE |
| STYLE | -0.0104 | -0.0938 | -0.0625 | INCONCLUSIVE |
| HARD PASS | +0.2292 | +0.1250 | -0.0313 | QUALIFIED、探索上優越 |

HARD改善は確認できたが、会話品質の非劣性は確認できない。特に質問回答の低下を
安全性の改善で相殺しない。探索対照選定は事前順位でfilterとなったが、製品適格ではない。
GB1単独も総合INCONCLUSIVE。詳細は`qwen-comparison.json`、選定入力hashは`selection.json`。

## Qwen実性能

REAL時間。filter追加分だけを別表示し、GB1初回の費用を隠さない。

| 条件 | block合計秒 | call latency p50/p95/max秒 | prompt/completion tokens |
|---|---:|---|---|
| baseline | 825.6 | 5.45 / 5.95 / 6.20 | 165,204 / 20,495 |
| GB1 | 1,349.1 | 1.92 / 6.65 / 8.73 | 419,336 / 22,359 |
| filter追加 | 235.1 | 5.38 / 6.12 / 6.57 | 48,008 / 4,510 |

choice短出力とfull outputが混ざるためGB1 p50だけで応答性改善を主張しない。
GPU監視2,205 sample、監視error0、観測peak VRAM6,255MiB・GPU利用率100%。
Qwen全310 callはGENERATED、length0、transport error0、終了時所有process残0。
既知の中断時受理flag問題はQwen実測では発現していない。

## 機械filterで残る限界

- 正しい理由の再言及も既存self repetition guardで拒否され得る。G01の1件は内容PASSだが
  3 attemptを使い切った。validatorを弱めず、不利益も主指標へ残した。
- exact全文コピーは減る一方、前置きを足した部分コピーは現境界で受理され得る。
  これはSTYLEとして記録し、機械filterが近似コピーや意味安全まで保証するとは言わない。
- 質問を回答せず相手へ返す出力、汎用的なday/phase説明は残った。
- GB1は依然として後段でlegacy full outputを生成する。段階化だけで責務負荷を除いた
  とは言えず、今回の比較だけではモデル能力と出力契約の因果を完全分離できない。
- GB1の選択kindはCLAIM62、QUESTION31、OPINION_CHANGE2、ANSWER1、NONE0。
  選択された137 factの内訳はday/phase77、能力42、公開死亡15、生存/候補3。
  汎用state情報への選択偏りと回答不足は同時に観測されたが、因果は未確定。
  全件非NONEでも回答率は改善しないという結果であり、非NONE率を成功指標にしない。

## Gemma対照

同じfilterを既存Gemma3-12Bで3 seed×32件実施。モデルfile/template/offloadは登録済みprofile差。
共通case/seed/schema/sampling/budget/validatorは不変。

- 96件すべてchoice callが`finish_reason=length`、completion32 token。output callは0。
- hash照合した2件では、整形されたchoice JSONがkind fieldの途中で終了していた。
- 入力は2,142〜2,635 tokensでcontext8192を圧迫していない。**今回の新choice32上限の
  model/tokenizer・整形との適合失敗**であり、過去160件のcontext不足説を復活させない。
- 本文未生成のためHARD/SEMANTIC/STYLE内容評価は各UNKNOWN96。構造受理0/96、
  主指標outcome0/96、質問UNKNOWN54/54、比較INCONCLUSIVE。
- violation count0は「違反なし」と解釈できない。モデル会話能力の順位付けもできない。
- 96物理call、935.3 REAL秒、latency p50/p95/max = 6.26/6.74/7.02秒。
  prompt230,997 / completion3,072 tokens。GPU857 sample、error0、peak5,391MiB・100%。
  これはchoice失敗までの時間であり、会話1発言のlatencyではない。

予定した12 block/384 rowをすべて保全。物理call406/上限864、durable marker406と一致。
終了は07:53:49 UTC、program開始06:16 UTCから約98分。全source249/config/model/runtime
identityを終了時に再照合し、所有process残0、listener空き、transport retry0。
同条件再実行・予算拡張は行っていない。

## 測定後の限定修正

測定source freeze後、候補採否前にofflineで2点を発見し、
`logs/t506-quality-recovery/offline-finding.json`へ記録した。
provider測定中はsourceを変えず、全block終了・凍結照合後に修正した。

1. filterの前attemptが構造PASS・text guard不合格で、次attemptが未生成の場合、
   前の構造PASS flagが残る。未受理を受理と数えないよう終了flagと集計境界を修正。
   今回の実測では発現0。最後の不合格rawとattempt証拠は削除しない。
2. 統計実装がG14診断6/6を追加の絶対条件にしていた。承認設計2.2はact/textを診断へ
   分離しているため、この余分な条件を除いた。NONEの構造合法性は維持する。
   選定順位へ影響せず、既存の意味安全違反を無視する変更でもない。

修正版で全4条件を保存annotationから再集計し、凍結済み集計と完全一致を確認した。
再採点・再生成はしていない。raw/result/annotation/選定hashは不変。
Python3.13 focused54 PASS、依存設定済みPython3.10でfocused＋grounding104 PASS。
既存grounding runner/quality/probeとtwo-stage runtimeのPython3.13関連回帰は244 PASS。
最初に誤って選んだsystem Python3.10はhttpx未導入でcollection error。既存venvへ戻して
検証した環境選択ミスであり、test削除・skip・依存仕様変更はない。

## 提案ごとの実施・採否

| 提案 | 実施内容 | 結論 |
|---|---|---|
| U1/G1 | 主指標・副指標marginを事前登録、paired3seed比較 | 新探索手順として実施。品質非劣性未確認 |
| U2/G2 | K=3、機械検査、初回hash再利用、最大callと停止を固定 | HARD改善、回答低下。製品採用は見送り |
| U3 | 既存GB1のauthoritative pointer catalogとLLM選択 | 捏造0を観測。CO/未回答推定の新候補型は加えない |
| U4 | speech_act意味一致を診断へ分離、構造validator維持 | 非NONE増加を成功と扱わず運用 |
| U5 | 選定filterで既存Gemma3seed比較 | choice32上限で本文未生成。品質比較INCONCLUSIVE |
| G3 | 既存GB1の段階生成＋groundingをG1/G2で検証 | 重複armを作らず実測。full-output責務は残る |
| G4 | 新設計だけ独立APPROVED、test-only探索をMain実施 | 候補ごとの独立chain反復なし。製品採用gateは維持 |

候補の製品採用はすべて見送る。best known stateは製品不変＋保存済み新比較証拠＋
測定後補修済みtest-only tools。安全性の改善と質問への応答改善は別課題として残った。

次の最小scopeは、Gemmaのchoice JSONと32 token割当の適合性を保存証拠・offline fixtureで
診断し、総512/context8192を増やさず次実験の比較条件を設計すること。
新しいprovider測定・製品採用・本番モデル切替は本結果から自動許可しない。
詳細証拠・hashは`handoffs/tasks/T506_SAFE_RESULTS.json`、採否はD082を参照。
