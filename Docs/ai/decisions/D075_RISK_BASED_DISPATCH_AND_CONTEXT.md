# D075 必要な独立性に基づく委譲とコンテキスト縮小

Date: 2026-09-16
Status: Accepted by explicit user instruction（改訂実装の最終差分は独立レビュー対象）

## Authority / supersedes

今回のユーザー指示を正本とする。D053の2体常設・model固定routingをrisk-based routingへsupersedeし、
D053本文・過去判定は変更しない。D066/D067の責務/model分離は維持する。
D067のordinary reviewをImplementer→Tester→fresh Reviewerへ自動的に回す固定経路も本decisionでsupersedeする。
D051の「毎実装前にReviewerが
設計要否判定」「次agentへの送信文で必ず終了」の固定経路だけをMain判断へ置き換える。
必要な詳細設計・canonical優先順位・独立設計承認は維持する。一般運用のfreshは下記で解釈し、
canonical acceptanceの明示的なfresh session要求は削除しない。合格式・秘密保全・ユーザー許可・
実runの一回制約は変更しない。

## Agent dispatch decision tree

Mainが既定実行主体。「Mainにない独立性・専門判断・測定結果の何を得るか」を一文で説明できなければ
委譲しない。固定人数・固定呼出し回数上限は置かない。
user scope/hold・現所有権→既存証拠の再利用→必要責務→既存担当の独立性と利用可能性の順に確認する。
担当完了はspawn triggerではない。「新しい未知/独立性/設計判断/測定」が全NOならMainが続行する。

| Responsibility | 必要となる条件 | 呼ばない例 |
|---|---|---|
| Architect | public interface、protocol/schema、state transition、lifecycle、concurrency ownership、acceptance criterion、product rule、component boundary変更 | 既存設計内bug、局所実装、ログ改善、test修正 |
| Investigator | root cause不明、flaky/race/concurrency、複数component横断、Mainの1回の限定診断で絞れない | 原因特定済み |
| Implementer | 分離する価値のある独立実装単位 | 小さく明白な既存設計内修正はMain |
| Tester | acceptance独立測定、E2E、concurrency、long-running、completion、実provider/実game、実装者測定だけでは不足 | focused、既存regression、syntax/check_docs、決定的局所確認はMain/Implementer |
| Reviewer | product code/test contract実質変更、detailed design、acceptance独立判定、高risk運用変更 | status更新、handoff整形、集計、hash照合、既承認同bytes/diff、指摘未反映の再確認、新判断なしの証拠コピー |

条件を満たす責務だけを選ぶ。原因既知ならMain修正→focused→必要なReviewerだけでよい。
未知の調査と設計がともに必要なら、それぞれ得る結果と境界を説明する。

## Independent / fresh reviewer

Independentは対象実装をしていない、対象設計を自己承認しない、acceptanceを捏造・自己採点する立場でない
担当。Main/実装者の自己承認は不可。前taskもレビューしたという理由だけで既存Reviewerを失格にしない。
対象へ関与していなければ同sessionで複数taskをレビューできる。
Fresh必須は本人の対象設計、本人の対象実装、以前の判断による直接の独立性毀損、canonical acceptanceの
明示的fresh session要求だけ。設計/実装本人は新sessionと称するだけで独立Reviewerにならない。
関与しない既存担当への交代で解消する独立性問題と、canonicalが新sessionそのものを求める場合を区別する。

## Second reviewer

2人目を通常経路に置かない。Critical/High、protocol/concurrency/security/authority boundary変更、重要な
detailed design gate、第一担当UNKNOWN/判断不能、実質的見解差、同種見落としの高い再発riskがあるとき
だけ追加担当が解決する具体的論点を記録して呼ぶ。通常bug fix・task名変更・担当完了だけでは呼ばない。
今回改訂はユーザー指定どおりMain実装＋最終独立Reviewer 1担当。恒久的な人数上限ではない。

## Evidence reuse

対象artifact SHA-256一致、承認scope/canonical acceptance/関連依存/測定環境の適用条件/独立性が同じなら
既存判定を再利用する。Mainがpointer/hashを照合し、同一証拠・同一diffの再レビューは禁止する。
存在するだけの証拠は承認ではない。変更依存/criteria、新evidence、実質的新問題、反証がある場合だけ
差分審査する。修正後も指摘と変更部分・影響範囲だけを渡す。CHANGES_REQUIREDの同bytesは未解決を保持する。
同code承認は新runのacceptance、現provider identity、private境界、one-use起動許可を代替しない。
新run bindingの必須確認はコード再レビューと別作業であり、新Reviewer生成理由にはならない。

## Context read set / packet / output

AGENTS active rules→ai_status active snapshot→assigned packet→packet明示canonical→relevant diff→
必要時のみ追加資料。workerの`--task`はMainのactive taskを上書きせず表示対象だけを選ぶ。
既定で履歴/無関係task/handoff本文/design treeを読まない。read setのpathだけを出す。
`--details`は選択taskの証拠/RUNBOOKを必要時に展開、`--all-live`は割当前の明示的な全live一覧。
不明な`--task`から別taskへ黙ってfallbackしない。完了taskは履歴であり再dispatch不可。
CURRENT_STATEはcurrent blocker/task/HEAD/acceptance/latest valid evidence/next action/explicit holdだけ。
過去全文はhistoryへbyte保存。TASKSはlive boardと閉じた状態の短い履歴pointerとし、DONE/CANCELLEDを
active bootstrapへ列挙しない。archiveの旧next actionは実行権限ではない。
packetはGoal/Scope/Out of scope/Changed・target files/Canonical references/Acceptance/Required independence/
Evidence locationを基本とし必要な識別子/gateを添える。過去経緯はpath/hash参照だけ。
ReviewerはVerdict/Findings/Evidence/Required fix/Next gate。ほかのhandoffも今回差分・実測・次gateだけ。
必要なraw/環境/exit/hashは保存しpath参照にする。新router/daemonや監査frameworkは作らない。

## Stop policy

agent利用不可時は既存独立担当再利用→完了担当解放→Mainの非依存作業→対象gateだけ保留。
自己承認・gate省略は不可。解放APIがないなら記録し、中断/archiveを解放と推定しない。
明示user holdを優先。現在はゲーム/provider停止を維持し、運用改訂とそのfocused/doc検査だけを行う。

## Dispatch examples

以下は文書検査用の受入例。yesは必要、conditionalは上記条件で判断。固定の呼出し数ではない。

| Case | Main | Architect | Investigator | Tester | Reviewer | Fresh | Second | Reuse |
|---|---|---|---|---|---|---|---|---|
| known_local_bug | implement+focused | no | no | no | yes | no | no | no |
| status_only | update | no | no | no | no | no | no | no |
| deterministic_saved_log | analyze | no | no | no | no | no | no | yes |
| unknown_after_one_diagnosis | bound | no | yes | no | no | no | no | no |
| concurrency_contract | coordinate | yes | conditional | yes | yes | conditional | conditional | no |
| real_game_acceptance | coordinate | no | no | yes | yes | canonical | conditional | binding_only |
| reviewer_did_previous_task_only | integrate | no | no | no | existing | no | no | yes |
| reviewer_authored_target | coordinate | no | no | no | independent | yes | no | no |
| canonical_fresh_required | coordinate | no | no | no | independent | yes | no | no |
| high_risk_second_opinion | coordinate | conditional | no | conditional | yes | conditional | conditional | no |
| same_approved_bytes_context | verify_hash | no | no | no | no | no | no | yes |
| same_bytes_new_acceptance | coordinate | yes | no | yes | yes | conditional | conditional | no |
| unchanged_unresolved_findings | fix | no | no | no | no | no | no | unresolved |
| agent_limit | nondependent_work | no | no | gate_only | existing_or_hold | conditional | no | conditional |

## T378以降への適用

T378/T379–T384は完了済み。既存実装承認・原本レビューは同scope/hashで再利用し、DONEを再割当しない。
Mainがまず保存rawの不正応答、反復、token/time、phase進行を限定分析する。集計/hash/deterministic parsing/
focused regressionごとにInvestigator/Tester/Reviewerを増やさない。
原因が既知設計内ならMain限定修正＋focused、製品差分の独立Reviewerのみ。
限定診断後も根因不明・race/横断ならInvestigator、上記境界変更ならArchitectを追加する。
実game/E2E/completion acceptanceは独立Tester＋独立Reviewerを維持。canonical freshは個別確認する。
第二Reviewerはrisk条件だけ。停止解除と必要な新run許可がない現在、game/providerは再起動しない。
