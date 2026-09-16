# Phase 6 Stage B R15 開始identity限定補遺

Task ID: T346  
Responsibility: Architect  
Status: IN_REVIEW — T345 exact revision独立承認待ち

## 1. 適用範囲と優先順位

本補遺は `Docs/ai/design/PHASE6_STAGE_B_DETAILED_DESIGN.md` §3の開始source identityだけを、T330以降に承認されたR12 P1/P2、R13 test-only、R14文書へ限定して更新する。旧設計本文、R1 plan/catalog/batches、旧freeze/raw/判定を書き換えない。canonical優先順位とD051、D073は旧設計どおり。T345が本補遺のexact SHA-256を独立承認するまで候補であり、起動authorityではない。承認後もS1/S2、具体的一回許可とMain明示launch割当を別に満たす。

歴史的基礎は `logs/t328-stage-b-preparation/frozen.json`、SHA-256 `7cf6816eb059851039ee2795876b0dd3c2577dc1cf1ad089ef79d6a8496ae74c` の191 sources / 6 documentsである。T303→T314/T315→T322/T323 R10→T327/T331 R11/T329の承認履歴を保持し、本補遺で旧PASSの範囲を広げない。T307/T316/T330のFAIL/BLOCKED/UNKNOWNと消費済み一回許可は不変。

## 2. 第一層: 許容する製品・test差分

次の11 pathだけを旧hashから新hashへ置換する。製品5件、test6件であり、T333 R6のP1/P2限定実装承認が根拠である。P1はprivate handle限定のHTTP error detail保全、P2は明示preflight用のbounded async観測である。P3、Q2 byte数記録、wire/V1/V2変更、ゲームlifecycleへのP2自動接続など未承認機能を許容しない。

| path | 旧T330 SHA-256 | 今回許容 SHA-256 |
|---|---|---|
| `ai_client/llm/admission_broker.py` | `ecf64256b38b529d2cbb10f5ace54e9d631a61cc42dbd01b3898b755716b62b1` | `5bb2819506746779d81c0548a1ec71f3fe558e1fae51cb24e62a63a00bb21af2` |
| `ai_client/llm/admission_metrics.py` | `6484b582ee27acc63648d0e63ed2e96842f46d48359078212526a50bd1add5a7` | `8f881713bd7760738a42042db30d3e32d31e7e70fe4208e586d64541f333642b` |
| `ai_client/llm/backend.py` | `84c4b1f37553f52c0193a39f2eb2b2601cc8588067f5290756f999ee10ee7840` | `fbeaa40639c6f439dac5c2da646f64a90ac5ee03db84c770a062c090e66d0816` |
| `ai_client/llm/types.py` | `02dbae2f80e95970b11e75dad0375247ed6e67c85d9884dd1fa3ec94ecea8278` | `057bf61cc7b3f3ed32c7bd47b05e23766472f322461417371059e6a4b645130d` |
| `scripts/run_phase5_local_smoke.py` | `6ff3943b5c79b9dd0453aea304b00517f516f84276dd460978be7b0ccf2bc30d` | `890a7828aeb15d6a7dba915ab36cd30a897b239bfb1dd6dac4312ac897d3b957` |
| `tests/test_phase4_llm_backend.py` | `a7f1e1614c7f1246e675604d663b4894dd7605db968543a9193beaf671d6d63f` | `b8ddf655dbc9a19e732a2c63f12c1f0d311ac2715b8b2b7e977e503df95e4d87` |
| `tests/test_phase4_llm_contracts.py` | `c6bb897c98c13b349f29f729dd1f9a66f1a9e9633ac6ba255290311c62b0bbeb` | `0aa6d436a53064538ea6a7b60dc6f2f79419c07e834bcddf6ca89c4572ed2001` |
| `tests/test_phase5_brain_admission.py` | `90f480dff65532edfe690a17c84dac82263e200cc5c06ebc1ef1c63d1b91c7eb` | `af5812378976cdd00b10175241b8180bc0ba4293185fb36910f6f766dd54f297` |
| `tests/test_phase5_generation_admission.py` | `06ae38f7f5bff61e5a093550c692ac0bfd155f02f2672c71ed540cd1e10ead27` | `3d14518ba823b57f4f90deed4c2e7f5af040e99b5345111e6c3c6bb721b75208` |
| `tests/test_phase5_local_smoke.py` | `3b009abaf0e4ad51120a08c8ce34bc225bfb13a7a54bd6495aa96c0024c24a70` | `bf2b807721a0809fb73c885a02494658ddcd0b6b85bd20c2a577e8facc962961` |
| `tests/test_phase6_private_review.py` | `950262f9a0c103ad0139a05840d0372132c0e0bb8da9e830daf7bd8119b9fc55` | `a3fd612d53267c847ff513a09666a5169c610f4539810477931d09796e3982b1` |

`tests/test_phase5_generation_admission.py` だけはT333 R6承認後にT337のR13修正を適用し、T340がexact test hashを独立承認した。ACK .05→.5秒、shutdown .1→2秒、観測wrapperと有限待機を含むtest-only差分であり、検証対象drain .05秒は維持する。T333承認のR12中間hashからこの1件だけが変わり、T337の175件freezeと現物は全一致した。

191 sourcesのkey集合をそのまま保持する。上記11件と第二層の `EXTERNAL_REVIEW_LOG.md` 1件以外の179件は旧T330 hashとexact一致しなければ停止する。ファイル追加・削除や未列挙差分を「文書だけ」「テストだけ」として暗黙許可しない。新補遺と新担当packet/handoffは第二層の追加control記録として別途列挙し、191件のsource母集団を縮小しない。

## 3. 証拠chainと回帰判定の境界

`logs/t332-review/source-before.json` の191件とその `source-before-files/` 原本を全hash照合した。control log以外190件は旧T330 freezeと一致する。この原本→現在11件の全文diffは `logs/t346-stage-b/approved-source-delta.patch` に保存した。

R12の承認freezeは `logs/t334-diagnostic-r4/source-freeze.json` SHA-256 `4f7d0b43e1b5e44a10605e7aaf09d4bea5ce88d16cda6e59d096e460014f0619`、承認patchは `logs/t332-review/scoped-final-r6.patch` SHA-256 `d90c939c0649828f67c7a48e79a4405d3bd31e34d303d8b90970d56aacf6580f`。T333はP1/P2限定APPROVEDだが、T334の固定6module全体はCLAIM境界1 FAILであり、当時の全体regressionはNOT APPROVEDのまま保持する。

R13のfreezeは `logs/t337-r13/source-freeze.json` SHA-256 `5c8c0365c88811fc5ad9446c1db4993c4d0878f1f83692ce18b51c7b9365ff53`。T339の新しいOwner通常host測定は284 passed + 175 subtests passed、failure/error/skipped 0。JUnit SHA-256 `b5908759c74802f56ec12d59492978c9968302c3b27c100a961ab9c26fad1a61` をT340が直接照合して固定6moduleをAPPROVEDとした。この新測定を今回identityのnon-LLM回帰根拠とする。初回sandbox FAIL/ERROR、filesystem cleanup未証明、旧CLAIM FAILは消さず、実CIM/provider/game成功やStage B全体PASSへ流用しない。

## 4. 第二層: control文書の個別照合

旧6 documentsは全件不変である。

| path | 維持するSHA-256 |
|---|---|
| `Docs/ai/PHASE6_MASTER_TEST_PLAN.md` | `89c185547e1e4ea9571f086008e9000f26cb661e81f0aec8d448ab32345df968` |
| `Docs/ai/PHASE6_MASTER_TEST_CATALOG.csv` | `eec62fcac5ec7d4b7825ab7d765b21ad12e69902e56cc0cfa37e5ea6477fc04e` |
| `Docs/ai/PHASE6_MASTER_TEST_BATCHES.csv` | `a4c946fa3150fdf54c245eae3d249ed9cbc28f838c1b304a8c7cca2f2f4894ba` |
| `Docs/ai/design/PHASE6_STAGE_B_DETAILED_DESIGN.md` | `703841b33f340e2c5b9f2aab7f909b3b3820f3628b30f743c95adc5058ee0654` |
| `Docs/ai/PHASE6_STAGE_B_TEST_CASES.md` | `6bc5b07cc40ba79d90bc329b2dbfe0ea17ead19765ca57829c0466710853f48e` |
| `Docs/ai/PHASE6_STAGE_B_TEST_CASES.csv` | `f9814f3aade89e33492e7f24f4d8e266703ba0123aa89f3b0d905793ff7ade08` |

191 sources内の `EXTERNAL_REVIEW_LOG.md` は旧 `9fef2befee857dfae19b8fa92483bbc39b8b0a88afea4c0d86a5caee6757eca5` → 現 `080d29b8e78ab27d438246a8ec9fbfde1a973893f03542fb29aa513c04cee77c` のR12/R13/R14監査履歴を個別reconcileする。製品差分承認の根拠にはしない。本補遺承認後にこのcontrol logが変わる場合も、自動許容せず追加差分・現hash・authorityをReviewerが照合する。

R14で更新されたT328とTEST_POLICYはT342が下表のexact bytesをAPPROVEDとした。T328 S1の実provider観測/PID結合・原本独立照合は別の実行gateであり、本補遺では達成扱いにせず、文書承認を実観測成功へ読み替えない。R13計画の下表hashはT340承認hashと全一致する。

| control / 承認証拠 | 現SHA-256 |
|---|---|
| `Docs/ai/handoffs/tasks/T333_CLAUDE_TEST_FRESH_REVIEW.md` | `c3adda59c21e98bf889c9c2259fb35dca52145468064425d74937c02d18175d1` |
| `Docs/ai/handoffs/tasks/T334_CLAUDE_REVIEW_BASELINE_TEST.md` | `e0780438cfe0a9fc308779d5d2828c804d3dbd0f3ff673f1ff1590d50707ff15` |
| `Docs/ai/handoffs/tasks/T336_DIAGNOSTIC_IMPLEMENTATION.md` | `1e352e6b071495baeeb59bc24fad2f6ab9447f67d7312f67058e10de7b31b492` |
| `Docs/ai/handoffs/tasks/T339_R13_TEST_REPAIR_TEST.md` | `3bb018ae60bff1d6bfde6506e8180aca90dad2f7cec775fc9a540291a43e9721` |
| `Docs/ai/handoffs/tasks/T340_R13_TEST_REPAIR_REVIEW.md` | `5a02f0133e1826a54a18d9575c87f8cb853efcfb066996cb0e185fb533413877` |
| `Docs/ai/handoffs/tasks/T342_R14_DOCUMENT_REVIEW.md` | `df285579a61e65cf5aaef01fe0d15bfc52934a64d82c77867560b05e426b3936` |
| `Docs/ai/design/PHASE6_R12_DIAGNOSTIC_DESIGN.md` | `9ccad44cfd4b4f8150d96215cc01b184431671dc5d81662a07b4058c707fd516` |
| `Docs/ai/design/PHASE6_R12_DIAGNOSTIC_P2_ASYNC_SUBPROCESS_ADDENDUM.md` | `1ec7e517e45fef5b2b5dbab570c1d310cd271729511d089b535d0638a7eca49c` |
| `Docs/ai/tasks/T328_STAGE_B_PREPARATION.md` | `3a0c311766b06624a62c8b17289fbcf337e02e886491e1528cf90bc61b9ba4d9` |
| `Docs/ai/TEST_POLICY.md` | `c7979164c04706773e132464b74e0054b0fb538112058ef0b27b44dd1346ce16` |
| `Docs/ai/PHASE6_R13_TEST_REPAIR_PLAN.md` | `eee29f1d63419d6fa8b71a2f6821e59a4ee3211b283d017a5f162210d739f513` |

T343/T344/T345の担当packet、具体launch条件、最新管理記録はMainが担当scopeとauthorityを列挙し、Reviewerが起動前のexact revisionを確認する。管理更新で上記製品allowlistや6 documentsを変更しない。T346補遺の承認hashとT345 approval recordのhashは新Runのcontrol記録に別々に結び付ける。補遺自身の本文へ自身hashを埋め込む循環は作らない。

今回提示条件は `Docs/ai/tasks/T343_STAGE_B_LAUNCH_CONDITIONS.md`。追加controlの現hash候補は `logs/t346-stage-b/supplemental-control-candidate.json` に別枠で保存する。ここには本補遺、T328、TEST_POLICY、T343/T344/T345 packet、今回提示条件を含める。候補採取後にQ2状態や担当packetが更新された場合、旧hash候補は承認を代替せず、最終freezeへ現hashと許可された差分を記録してT345が再照合する。191 sources / 6 documentsの母集団へ追加・置換しない。

## 5. 新Run freezeとlaunch前後の検査

`logs/t346-stage-b/source-identity-candidate.json` はhash候補と差分allowlistだけを持つ設計証拠であり、実行freezeではない。Testerは新run label・新created時刻で新しいfreezeを作り、191 sources / 6 documentsのkey集合を保ち、上記exact値を起動直前現物へ再照合する。

旧freeze objectを丸ごとコピーしてmetadataを継承しない。旧run_id/stage_b_run_id、tester/reviewer/packet、user_authorized_one_run、launch_authorized、stage_b_launch_authorized、launch_count、status、created時刻、PID/creation time、endpoint・実binary/model hash・profile・fingerprint、provider observation/hash、review approval、cleanupやresultは今回の取得・判断から記録する。取得不能を旧値で埋めずUNKNOWNとしてlaunchを保留する。固定model/clock/予算等の契約値も、現在実条件との一致を新preflightで証明する。

起動前にT345承認済み補遺、旧設計、T328 R14、T344 packet、今回具体条件、S1のOBSERVEDかつ非null公開hashと同じprivate原本の独立照合、S2/Q2のユーザー選択、未消費の一回許可、保全・host所有・非表示list argvを同じ新Runへ結合する。Q2回答未受領ではlaunchしない。補遺の承認だけで観測や実gameを起動しない。

起動開始後はsourceとfreezeを変更せず、終了後同じ191件/6件とcontrol承認revisionを再hashする。不一致、原本欠損、未承認差分を観測したら危険な依存評価を停止し、実行済み/FAIL/BLOCKEDを分けて保存する。旧原本のmetadata更新、旧PASSの転用、追加game/項目別retryで補完しない。

## 6. 維持する実行条件と受入条件

Qwen3.5-9B-Q4_K_M、standard_9、seed8625、9client、共有concurrency1、day/vote/night 180/60/60秒、read/request20秒、whole-response512、本文200文字/600bytesを維持する。server待ち1200秒、outer1500秒、owned cleanup120秒を別の時計として記録する。入力2097152/出力131072/計2228224 tokenはsoft予算であり実装済みhard capではない。accepted512件全件審査、513以上B11 FAIL、実game一回・retry0、所有外provider非操作、owner-only直接保全を維持する。

B01–B11と会話合格式 `B02 == PASS AND (B03 == PASS OR B04 == PASS OR B05 == PASS)`、B06の既存位置付け、全件審査・privacy・retention・停止/cleanup条件を変えない。受入は、11置換とcontrol1件以外の179件不変、6 documents不変、R12→R13→現物chain成立、全gate独立確認、実行前後の新freeze一致である。設計検証はhash/diff/文書検査だけを行い、pytest、実CIM/provider/LLM/gameはT346では実行しない。
