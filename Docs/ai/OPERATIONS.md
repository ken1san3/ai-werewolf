# Multi-Agent Operations

This is the small operating contract for the Main Integrator and short-lived workers. It
uses standard agent delegation and Git/test tools; it is not an autonomous-development
runtime.

## Main operation

Reconstruct through `ai_status.py integrate` and the authority map in `INDEX.md`. Choose the
smallest useful work decomposition and verification depth justified by risk and acceptance.
Mainを既定実行主体とする。役割は固定pipelineではなく、Mainにない独立性・専門判断・測定結果を
得る理由が明確な場合だけ委譲する。D075のdispatch/fresh/second/evidence reuse条件を適用する。
D051の必要な詳細設計と独立承認、acceptance固有の独立Tester・明示fresh session要求は維持する。

Before dispatch inspect current board state, dependencies, required design, expected/shared files,
actual Git diff and host task ownership. DONE/CANCELLED tasks are not candidates; IN_PROGRESS and
REVIEW work must be reconciled, not duplicated. A returned handoff is evidence to inspect, not
permission to run its suggested successor. Status tooling identifies pointers and discrepancies;
it does not authorize dispatch or implement dependency/acceptance judgment.

packetとboardの双方を作成・照合し終えるまでdispatchしない。packetの先行草案をREADY権限と
みなさず、将来gateもboardへBLOCKEDで登録する。終了後はhandoff回収とboard反映を確認する。
過去packetを一律liveに戻したり、CURRENT_STATEへ全task状態を複製したりしない。
`check_docs.py`のPASSは書式/参照整合性の証拠であり、独立承認の実在を保証しない。
MainはDONE前に必要な各Tester/Reviewerのsession責務、対象source hash、実測、判定、
handoff/hashを明示照合する。圧縮前の完全recordと証拠pointerを保存し、圧縮を承認の代用にしない。

Use standard host delegation/isolation. In a shared tree, permit parallel read-only work or
non-overlapping writers only; serialize board/state/review-queue writes under Main. Inspect the
host task tree before recovering assignments. If visibility is unavailable, report UNKNOWN and
hold the affected assignment; do not infer liveness or absence from old process counts, task state,
or file timestamps. Existing provider/runner process ownership remains unchanged.

Verify actual bytes, test scope/results and required independent verdicts before updating memory.
Write lifecycle only in TASKS, pointers/critical path/holds in CURRENT_STATE, measurements in the
named handoff. Keep CURRENT_STATE's checker-required Task state mirror synchronized. Packet status
is an assignment snapshot; do not rewrite historical packets/handoffs merely to synchronize it.

Main continues safe authorized work across task and wave completion, without a human confirmation
for each packet. Workers return at their assigned boundary. Honor explicit holds and user scope;
record recoverable evidence/pointers before context loss. Keep chat to meaningful transitions and
store logs with evidence. No custom scheduler, supervisor, model router or automatic merge layer.

## Agent Dispatch Gate（D075）

判定表は `AGENTS.md` の同名節、厳密な適用条件と受入例は
`decisions/D075_RISK_BASED_DISPATCH_AND_CONTEXT.md`。Mainが委譲理由をpacketのRequired independenceへ
一文で記録する。境界変更時だけArchitect、原因不明/race/横断またはMain限定診断で絞れない場合だけ
Investigator、分離価値がある実装単位だけImplementer。focused/regression/局所決定的確認はMainでよい。
acceptance/E2E/concurrency/long-running/completion/実provider/実gameの独立Testerは省略しない。
製品code/test contract実質変更・detailed design・acceptance判定・高risk運用変更には独立Reviewer。
status/整形/集計/hash/証拠コピーだけに新Reviewerを作らない。

担当完了後は新しい未知・独立性・設計判断・測定の4点だけを再判定し、全NOならMainが続行する。
Independentは対象設計/実装/自己採点から独立していること。既存Reviewerを複数taskで再利用できる。
freshは本人の対象設計/実装・以前の判断による直接の独立性毀損・canonical明示時だけ。
第二ReviewerはCritical/High、protocol/concurrency/security/authority境界、重要設計gate、第一担当判断不能、
実質的見解差、既知見落としの高い再発riskについて具体的論点がある場合だけ。通常経路に追加しない。

## Evidence reuse / context diet

artifact SHA-256と判定scope、canonical acceptance、関連依存、測定環境の適用条件が同じなら独立判定を
再利用する。同一証拠・同一diffの再レビューは禁止。指摘を直していない再確認を別taskへ振らない。
新依存/新criteria/新証拠/反証がある場合だけ、その差分と影響範囲を審査する。新runのidentity・private境界・
一回許可・acceptanceは旧code承認で代用しない。再利用はMainのpointer/hash照合で行い新承認chainを作らない。

起動はAGENTS active rules→`ai_status.py <entry> --task <id>`→packet→明示canonical→relevant diff。
過去handoff全文やrepository全履歴を渡さず、追加読取りは具体的な未解決事項がある場合だけ。
CURRENT_STATEは現在値だけ、過去全文はhistory/archiveへbyte保存。TASKSはliveと閉じた履歴pointerだけ。
DONE/CANCELLEDの旧状態・証拠は保持し、archiveの指示から再割当しない。通常表示は選択taskのread setのみ。
割当前の広い状態確認には`--all-live`、必要な証拠/RUNBOOK詳細には`--details`を明示する。
packetは `tasks/TEMPLATE.md`、出力は短いhandoff templateに従い、path/hashで再利用する。

## agent/thread上限時の継続（2026-09-16ユーザー指示）

`agent thread limit reached` だけで全体作業を停止しない。まず現在のagent/threadを一覧し、
実行中・完了・不要・独立性を照合する。既存独立担当再利用→完了不要担当の解放→Mainの非依存作業→
対象gateだけ保留の順。固定agent人数・呼出し回数上限を設けず、必要な担当を拒否しない。
Mainや対象の実装・設計担当を独立Reviewerに変えない。
reviewを省略せず、Reviewer→修正が必要なら修正→再レビュー→admission/実ゲームを維持する。
証拠回収後の不要担当はhost標準の終了・解放機能を使う。機能未提供なら制約を記録し、
中断やarchiveを枠解放と同一視しない。確認要求・実ゲームの未実行は0/未実行のまま残す。

## Task record fields

Each `TASKS.md` record contains `Task ID`, `Title`, `Role`, `State`, `Priority`,
`Dependencies`, `Scope`, `Goal`, `Acceptance`, `Tests`, `Notes`, `Expected files`,
`Design Gate`, `Review required`, `Task packet`, and `Handoff path`. A design-producing task
uses `Design Gate: PRODUCES DESIGN`; implementation uses `APPROVED` or `NOT REQUIRED`.
Allowed states are `READY`, `IN_PROGRESS`, `REVIEW`, `BLOCKED`, `DECISION_REQUIRED`,
`DONE`, and `CANCELLED`.
Task packets add canonical sources, allowed/out-of-scope boundaries, shared-file conflict
risk, and handoff destination.

## Handoff and review

Workers use `handoffs/tasks/TEMPLATE.md`. Reviewers use
`handoffs/tasks/REVIEW_TEMPLATE.md`. A completion claim is evidence, not approval. The
Integrator checks the actual diff, measured acceptance evidence, and every required independent
test/review before `DONE`. No extra role is mandatory for work whose contract does not require it.

## Acceptance authority and route reassessment

Each acceptance or evidence objective has one named semantic PASS authority. An outer
wrapper may own preflight, process lifecycle, cleanup, and evidence sealing, but it must not
reinterpret the same nested evidence into a competing PASS decision. It either delegates
the semantic result to the named authority or fails closed on its own outer boundary.

After three failed correction rounds on the same acceptance/evidence objective, stop the
repair loop and reassess the route even when each round has a different mutation or symptom
name. Mainがまず限定診断し、未解決の原因/境界にD075のInvestigator/Architect条件を適用する。
This is not a task quota and never weakens the acceptance criterion. See
`decisions/D068_EXTERNAL_PHASE5_REVIEW_DISPOSITION_AND_ACCEPTANCE_AUTHORITY.md`.

## Phase 6 test architecture

- The Integrator records the named acceptance-to-test matrix and one semantic PASS authority in
  each P6 packet. 実装者測定は必要な独立Tester/Reviewerの代わりにならない。毎task新sessionにはしない。
- Every spawned process has one owner, bounded wait/terminate/kill cleanup, retained first-failure
  diagnostics, and an explicit zero-residue check. Cross-process ordering uses a reviewed,
  parent-authored deterministic gate, never process-local clock coincidence.
- Run tiers separately: focused/default -> marked completion -> one reviewed exact-9B run ->
  distinct private human review. Long regression and soak do not reinterpret or automatically retry
  another tier's failure.
- Record and route a Windows-only permission, scheduling, or provider failure as an independent task
  unless it directly invalidates the active packet. Do not add a skip, sleep, retry, timeout
  inflation, or production change merely to make its harness pass.

## Decision routing

D075列挙のinterface/schema/state/lifecycle/concurrency ownership/acceptance/product rule/component境界を
変更するときだけArchitectを選ぶ。既存契約内の実装・ログ・test修正には挟まない。
canonical/decision/codeと必要なArchitect分析でも重要なproduct選択が残る場合だけユーザーへ上げる。

Use this form:

```text
━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠ DECISION REQUIRED — Txxx
━━━━━━━━━━━━━━━━━━━━━━━━━━
内容:
なぜユーザー判断が必要か:
影響範囲:
案A（利点 / 欠点）:
案B（利点 / 欠点）:
Architect推奨と理由:
回答方法: A / B / その他
継続中の独立task:
```

Mark only the affected task `DECISION_REQUIRED`. Continue unrelated READY tasks. Stop all
work only for repository-destruction risk, missing credentials/external authentication, or
an explicit user hold, irreconcilable authority, or when no safe authorized work remains.

## Failure and recovery

- Preserve the first failure, command, environment, logs, and reproduction.
- 明白な局所不具合はMainが直接修正できる。分離価値のある実装、境界変更、未解決原因にはD075を適用する。
- Isolate only the failed task. Do not resume an archived run, build recovery state, discard
  inherited changes, or auto-merge.
- On conflict, compare base/current diff and integrate deliberately. Never erase another
  worker's changes.

## acceptance evidenceの永続保全

2026-09-14のユーザー指示により、新規acceptance evidenceは実game原本とsynthetic test証拠を
pathと所有task/runのprovenanceで分離する。observerも対象runを直接参照し、時刻相関だけで
所有を推定しない。D072により新規受入rawは実game原本を `logs/phase6-private-evidence/game/<task>-<utc>/`、
syntheticを `logs/phase6-private-evidence/synthetic/<task>-<utc>/` に分けて出力する。
既存evidenceは移動・削除せず、旧prefixと原hashを履歴として保持する。

P6-Iの実provider/GPU/exact-model gameは一般的な続行指示では起動できない。G/Hと安全な
前提準備の完了後、モデル・実行条件・token budget・保全・512 record上限対処・rerun禁止条件を
具体的に提示して、実起動直前にユーザーの明示承認を得る。承認済みの同じ条件を再確認する
だけの質問は不要。新規session上限時は上記の再利用・解放・非依存作業手順を使う。対象の
設計/実装に関与していない既存独立Reviewerは再利用でき、Mainの審査代替はできない。

pytest管理のtmp_path/basetempやTemporaryDirectoryは作業領域であり、acceptance evidenceの
永続保管先ではない。raw private原本は、pytest retention/cleanup対象外の専用private領域へ
直接出力するか、後続pytestを始める前に全bytes/hash付きで保全を完了する。hash一覧だけでは
原本の保全にならない。公開logsへのraw複製、保護を弱めた保存、仮の原本再生成は禁止する。

保全方法を変更したときは、作成processの終了後と後続pytest/cleanup後の原本残存・全hash・
private境界を独立確認してから受入用の実gameを起動する。合成のcleanup検証は、今回の所有が
確かな隔離作業領域だけで実施する。既存ACL/security/TEMP設定を回避のために変更しない。
過去のFAILや原本欠落は、新しい測定のPASSと別の履歴として保持する。D071を参照。

## 実runの開始・終了記録

長時間batchと実provider呼出しでは、担当Testerが開始前にBatch ID、予定上限、状態、
成果物ラベルを記録し、起動直後に親PID・process creation UTC・開始時刻を永続化してから
待機へ入る。T303の既存`batch-status.json`/`.jsonl`のPLANNED/START/END形式を再使用できる。
終了後は同一PID identityのexit code、実経過、cleanup、残存確認を記録する。
私的command/stdout/stderrはprivate領域へ置き、公開statusへ本文やprivate pathを載せない。
Mainは会話の進捗報告だけに依存せず、そのrunの状態記録を確認する。瞬間的なprocess不在を
未起動や完了の証明にせず、無応答の推測だけでinterruptしない。未取得はUNKNOWNで残す。

agent間送信が使えない場合は、割当前に限定されたhandoff読取り場所を指定し、独立Reviewerが
同一runを直接照合する。宛先の分からない別会話へprivate情報を送らない。
既存providerのquiescence UNKNOWN後はhealth/listenerだけで停止完了と扱わず、ユーザー所有
providerの安全な再起動と新process identity確認後に次の承認済み呼出しへ進む。

## Bounded long regression

The long runner executes ordinary tests repeatedly and never edits source files:

```powershell
python scripts/run_long_regression.py --hours 7 --per-run-timeout 900
```

Logs and `SUMMARY.md` go to ignored `logs/long-regression/<timestamp>/`. Each pytest child
has a hard timeout; timeout/cancel terminates its process tree. Stop with `Ctrl+C`, or create
the displayed `STOP` file for a graceful stop between cycles. A nonzero test exit ends the
run by default and preserves the first failure. Morning review begins with `SUMMARY.md`,
then only the first failed run's logs. This is test repetition, not unattended code writing.

Safe smoke command:

```powershell
python scripts/run_long_regression.py --hours 0.01 --per-run-timeout 60 --max-runs 1 --target tests/test_ai_status.py::DesignGateTests::test_task_records_are_model_neutral_board_records
```

Before and after a long run, record `git status --short`; no product source diff should be
created. The Integrator does not begin implementation merely because a long test completed.
