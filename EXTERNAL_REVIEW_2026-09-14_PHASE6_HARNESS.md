# 外部独立レビュー — AIwolf harness と Phase 6 継続運用

作成: 2026-09-14
実施: 外部独立レビュー session（Claude Opus 5）
実行範囲: read-only 検査 + `scripts/check_docs.py` / `scripts/ai_status.py integrate` / `Get-Acl`（読取りのみ）
**実装・ファイル編集・commit・ACL 変更・pytest 実行は一切行っていない。**

## この文書の位置づけ

`Docs/ai/INDEX.md` の分類における **外部レビュー入力**である。canonical authority ではない。
`CURRENT_STATE.md` / `TASKS.md` / 承認済み design / packet / handoff のいずれも、この文書によって
上書きされない。D068 が外部レビューについて定めたとおり、「有用な入力であって canonical authority
ではない」。採否と disposition の決定は Main Integrator とユーザーに属する。

観測は 2026-09-14 11:30〜13:00 頃のスナップショットである。レビュー中に別 session が
`Docs/ai/CURRENT_STATE.md`（Active task T267 → T281）、`Docs/ai/TASKS.md`（T280/T281/T282 の
board record 追加）、`pyproject.toml`（`windows_private` marker 追加）を更新している。
以降の作業で既に解消した項目が含まれ得る。

---

# 第 I 部 — 構造レビュー

## 総合判定

**CHANGES REQUIRED**

Astra 向け harness 移行（D070）そのものは妥当で、scaffolding も十分に削られている
（RUNBOOK 4.6KB / PROMPTS 1.1KB / ARCHITECTURE 4KB、Autodev は D065 で除去済み）。
判定を CHANGES REQUIRED にしたのは設計思想の問題ではなく、**実際に失敗を起こす経路が
現在 5 件開いており、うち 4 件は数行〜1 コマンドで塞げる**ためである。特に H1・H2 は
Phase 6 の成果物そのものを失う／流出させる経路で、しかも Main が自力で塞げない
（commit 未承認）ため、Current Blockers に載っていないこと自体が欠陥である。

---

## HIGH

### H1. Phase 6 の全成果が未コミット・バックアップなしの単一障害点

1. **severity**: HIGH（現在の最大リスク）
2. **根拠**
   - `HEAD = 71607b1`（2026-09-12 19:06）。以降 **40 files modified / +7096 行**、**untracked 291 件**。
     untracked 側に `ai_client/discussion/`（P6-A/B の中核）、`scripts/phase6_private_review.py`、
     `tests/test_phase6_*.py` 12 本すべてが含まれる。
   - 受入証拠の正本は `logs/`（102MB、`.gitignore` 対象）にしか存在しない。
   - D071 に「ユーザーは明示 backup なしと回答」と記録されており、かつ
     **T252 で private 原本 61 件を実際に一度失っている**。
   - `CURRENT_STATE.md` の Current Blockers にこの項目がない。
3. **failure mode**: ディスク障害／Windows の cleanup 不具合（T251 で Acronis 因果が未確認のまま残存）／
   誤操作のいずれかで、Phase 6 A–G の実装と全受入証拠が同時に消える。hash 一覧は残るが
   `OPERATIONS.md` 自身が「hash 一覧だけでは原本の保全にならない」と明記しており、再構成不能。
4. **時期**: 今すぐ
5. **最小修正案**: 破壊操作ではないので、ユーザーが一度だけ次のどちらかを承認すれば足りる。
   - 作業ブランチ上で 1 commit（push 不要）: `git switch -c phase6-wip && git add -A && git commit`
   - commit を避けるなら `git bundle` + `logs/` の別ボリュームへのファイルコピー

   AGENTS.md の「commit は明示承認なしに行わない」により Main は自己解決できない。
   **これは BLOCKED 7 項目報告で今すぐ上げるべき Human Gate である。**

### H2. Phase 4 実モデルの private transcript が untracked かつ gitignore 外で公開 remote 直下にある

1. **severity**: HIGH
2. **根拠**
   - `git status`: `?? .phase4-real-smoke-final/` `?? .phase4-real-smoke-retry2/`
     `?? -retry3/` `?? -retry4/`（計 約 1.4MB）
   - 中身は `.phase4-real-smoke-retry4/run/ai.jsonl`、`player-N.stdout.log` 等
     = **実 LLM の prompt/response audit 本文**
   - `.gitignore` は `tmp*/` `logs/` `_to_delete/` は持つが `.phase4-real-*` を持たない
   - remote は `https://github.com/ken1san3/AIwolf.git`
   - さらに実測 ACL（第 II 部 原因②）により、これらは **ローカル `BUILTIN\Users` から読取り可能**
3. **failure mode**: H1 を解消するための `git add -A`（= 最も自然な一括 commit）で private transcript が
   commit され、push すれば公開される。D068 の「raw text を公開文書へ出さない」境界が、docs ではなく
   ファイルシステム側から破られる。H1 の修正と衝突するのが最悪の点。
4. **時期**: 今すぐ（H1 より先に）
5. **最小修正案**: `.gitignore` に 1 行 `.phase4-real-*/` を追加。削除は未承認なので ignore のみ。

### H3. `logs/phase6-private-evidence/` に実原本と合成 test 入力が同一 prefix で混在している

1. **severity**: HIGH
2. **根拠**
   - `tests/fixtures/phase6_evidence.py` の `_PREFIX = "p6f-private-evidence-"` を全用途で共用。
     現在 21 container が同名 prefix + ランダム suffix で並ぶ。
   - 内訳が名前から区別できない: T261 実ゲーム原本 67 件、T278 の合成 G 入力（`72qrfhl4` = 27MB）、
     T267 の合成 root（`ngyb05ax` = 27MB）、observer seal（`slg088fc` 等 25K〜144K）
   - `tests/test_phase6_private_review.py:57-69` が **`tmp_path` fixture を override** し、
     37 node 全部がこの base 直下に container を作る。cleanup はしない。
   - 実害が既に出ている: `T280_PHASE6_TEST_EVIDENCE_SUPPLEMENT.md` は所有関係を**時刻相関でしか
     特定できず**、「この相関は候補特定の根拠であり、生成因果や完全な 37 件対応を証明しない」として
     **T267 の G gate 可否を UNKNOWN に格下げ**している。
3. **failure mode**
   - (a) 証拠の provenance が原理的に証明できず、独立 gate の verdict が恒久的に UNKNOWN になる（既発生）
   - (b) 27MB/回で増え続けるため、いずれ容量起因の一括削除が行われ、**同じ場所にある実原本を巻き込む**。
     T252 の 61 件喪失と同一メカニズムの再発
   - (c) D071 の「G Implementer へ実原本を開かせない」が、パス分離ではなく運用注意でしか担保されていない
4. **時期**: 今すぐ（ACL/TEMP を触らない範囲で可能）
5. **最小修正案**: `create_private_evidence_container` に用途区分の引数を足し、**新規**分のみ
   `logs/phase6-private-evidence/game/` と `logs/phase6-private-evidence/synthetic/<task-id>-<timestamp>/`
   に分ける。既存 21 container は移動も削除もしない（履歴保全）。合成側だけなら容量回収の判断も安全にできる。

### H4. CI が Phase 6 を構造的に実行できず、platform 独立の唯一の gate が事実上停止している／未検証の POSIX 分岐を抱えている

1. **severity**: HIGH
2. **根拠**
   - `tests/fixtures/phase6_evidence.py:34-37` が `os.name != "nt"` または Python < 3.13 で無条件 `RuntimeError`
   - `tests/test_phase6_evidence_retention.py:50,59` が `subprocess.CREATE_NEW_PROCESS_GROUP` と `taskkill` を
     **無分岐**で使用（`run_long_regression.py:185` は `if os.name == "nt"` で分岐しており、こちらだけ抜けている）
   - `tests/test_phase6_private_review.py` の session fixture 経由で 約 37 node が同 helper に結合。**marker なし**
   - `.github/workflows/ci.yml`: ubuntu-latest × Python 3.10–3.13 で `-m "not completion"`、
     加えて ubuntu 3.13 で `-m completion`。`test_phase6_semantic_completion.py:134` は completion marker 付きだが
     同 helper を使うので **completion job も落ちる**
   - `logs/` は `.gitignore` 対象 → fresh checkout に親が無く、G の `private_case_root` は `mkdir` せずに
     helper を呼ぶため Windows runner を足しても失敗する
   - `logs/p6g-ci-audit-20260914/static-audit.md` が同じ結論に到達済み
   - **副作用として重要**: 誰も POSIX 分岐を実行していない。`scripts/phase6_private_review.py:624-625` の
     POSIX publish は `os.link` + `unlink` のみで、Windows 分岐にある `GetFinalPathNameByHandleW` による
     最終パス同一性検証も readback 検証もない。`_plain_absolute:141` の POSIX privacy 検査も
     `S_IMODE & 0o077` だけで、所有 uid も ancestor の mode も見ない。
3. **failure mode**
   - CI が恒常的に赤 → 「CI は落ちているもの」として無視される状態が固定化し、Windows 単一ホストの
     測定だけが唯一の gate になる（= Tester independence が platform 面で消える）
   - より危険なのは、**受入の PASS artifact を書き出す経路に、一度も実行されたことのない弱い実装が
     存在する**こと。将来 Linux で動かした瞬間、truncated／入れ替わった aggregate が検出されずに
     `human_quality_pass` として採用され得る
4. **時期**: CI 分割は今すぐ。POSIX 分岐の扱いは T279/T282 の結論待ちでよい（ただし Phase 6 closure より前）
5. **最小修正案**
   - static-audit.md の案 1–4（`windows_private` marker 登録 → 該当 module/node に付与 →
     Linux は `not completion and not windows_private` / Windows 3.13 job で `windows_private` →
     G の base を `mkdir(parents=True, exist_ok=True)`）
   - POSIX 分岐は、独立 Tester が実測できるまで**「unsupported platform として fail closed」に倒すのが
     最小かつ安全**。弱い実装を残すより明示的に拒否する方が受入 semantics を守れる
   - 注: レビュー中に `pyproject.toml` へ `windows_private` marker が追加されたことを観測した（案 1 の着手）

### H5. `AGENTS.md` が上限まで残り 3 文字

1. **severity**: HIGH（運用の詰み方が悪い）
2. **根拠**: `AGENTS.md` は 7997 文字、`check_docs.py:706` の `SESSION_CONTEXT_LIMITS` は 8000。
   同 checker のコメントは「**上限は引き上げず**、古い記録を review_archive/ へ退避する」と明記。
   他は余裕あり（OPERATIONS 8056/16000、CURRENT_STATE 6574/12000、TASKS **10227/12000 = 85%**）
3. **failure mode**: 次に安全規則を 1 つでも AGENTS.md に足すと docs CI が落ちる。上限引き上げが
   禁じられているため、**最も安い回避策が「既存の安全規則を削る」**になる。2026-09-13 に日本語規則と
   BLOCKED 7 項目報告を追記した直後にこの状態
4. **時期**: 今すぐ
5. **最小修正案**: 「人が読むリポジトリ文書の共通ルール」+「BLOCKED 7 項目報告」の 2 ブロックを
   `OPERATIONS.md` へ移し、`AGENTS.md` には 1 行の pointer を残す。authority 表・design invariants・
   review checklist は入口に残す

---

## MEDIUM

### M1. packet 作成と board 記録の順序が保証されておらず、TASKS.md が唯一の lifecycle 正本になっていない

1. **severity**: MEDIUM
2. **根拠（本レビュー中の実地観測）**
   - 12:12 時点で `Docs/ai/tasks/` に T272・T273・T280・T281・T282 の packet が存在し、
     **board record は 0 件**。T280 は `handoffs/tasks/T280_*.md` の完了報告まで存在（12:09）。
     T281/T282 は packet 内 `Status: READY`
   - `ai_status.py integrate` の LIVE TASKS にこれらは一切出なかった
   - レビュー中に board が追い付き、T280/T281/T282 が追加された。
     **T272（fresh G Reviewer）と T273（H review）は 11:04/11:06 作成で 1 時間以上 orphan のまま**
     = G/H の独立 gate そのものが lifecycle 管理外にある
   - `check_docs.py:268` の `check_task_board()` は board→packet 方向のみ検査。
     packet→board の逆方向検査がないため orphan は検出されない
   - `CURRENT_STATE` の `Task state:` mirror は Active task 1 件しか照合せず、
     同時 live 4 件は未検査（`check_docs.py:355-365`）
3. **failure mode**: この窓の中で Main が context を失うと、(a) 完了済み worker 結果（T280）が回収されず、
   (b) READY packet が再作成されて二重 dispatch、(c) T272/T273 という**独立 gate が丸ごと存在しないまま
   G/H が閉じられる**。D070 が「conflicting live-next-action copies は operationally hazardous」として
   解こうとした問題と同型
4. **時期**: 今すぐ（安価）
5. **最小修正案**: ① 手順として board record を packet より先に書く。② `check_docs.py` に
   「`Docs/ai/tasks/T*.md` は全て TASKS.md に record を持つ（明示 DRAFT を除く）」の 1 check を追加。
   ③ `Task state:` mirror を live task 全件に拡張

### M2. `Review required` が機械検証されず、完了履歴の圧縮で gate の痕跡が消える

1. **severity**: MEDIUM
2. **根拠**
   - `check_docs.py:337` は DONE に対し handoff **ファイルの存在**しか見ない。
     「Independent Tester, then fresh Reviewer」が実際に APPROVED を返したかは検査されない
   - Completed history は `- T274 — CANCELLED/未承認：...` のような 1〜3 行 bullet へ圧縮される。
     `ai_status.task_records()` は `## Txxx` section しか parse しないため、
     **圧縮後は Role / State 語彙 / packet 実在 / Review required が一切検査対象外**になる
   - TASKS.md は 10227/12000（85%）で、圧縮圧力は継続的
   - 既に副作用が出ている: T271 の Notes に「board Notes 欠落による docs FAIL は Main 管理不備として保持」
3. **failure mode**: gate を通していない DONE と通した DONE が、圧縮後は文書上区別不能になる。
   Phase 6 closure の監査（H/J）で「A–G の全 gate が成立した」ことを board から再構成できない
4. **時期**: Phase 6 中に（closure 前）
5. **最小修正案**: DONE record に `Approved by: T2xx（Tester）/ T2yy（fresh Reviewer）` の機械可読 1 行を
   必須化し、**圧縮後の bullet にもこの 1 行だけは残す**。既存の圧縮済み履歴は改変しない

### M3. Main Integrator が worker 責務を吸収しており、独立性が「容量制約」で削られている

1. **severity**: MEDIUM（H/J の価値に直結）
2. **根拠**
   - `CURRENT_STATE`: 「T270 worker 中断後、**Main が G3file writer を引き継いだ**」
   - `T278` handoff 冒頭: 「責任: **Main の限定 Implementer 作業**」。対象は `tests/test_phase6_private_review.py`
   - `T281` packet: Responsibility Investigator だが `CURRENT_STATE` は「T281 で **Main が限定構築/保全補正**」
   - continuation handoff: 「新規 agent/完了済補助の再起動は **host thread limit で拒否**されたため、
     **解放済み T270 補助 session を T279 Architect として再利用**した」
   - 設計側の契約違反: `PHASE6_DISCUSSION_QUALITY_DESIGN.md §12` は P6-G の 3 ファイルを排他所有とし
     §11 は「**No other packet may edit those files**」、§12 冒頭は Integrator の write を
     「TASKS.md/CURRENT_STATE.md と Reviewer/Tester handoff」に限定している。T278 はこのいずれにも該当しない
   - 構造的原因: D070 が D067 の固定 routing を外し、「そのタスクが別 gate を要するかを Main が risk で
     判断する」に置換したが、**Main が自分の作業について自分で独立性要否を判定する**経路が残り、
     その判定根拠を記録する場所もない
3. **failure mode**: G に残る独立性は T267 と T272 の 2 つのみ。T267 は T280 により verdict UNKNOWN へ
   格下げ済み。T272 も容量制約で既存 session に再割当されれば、**G の 3 ファイルを書いていない session が
   一つも verdict を出さないまま G が閉じる**。fixed workflow を外したこと自体より、
   「容量不足時の fallback が『session 再利用』になっている」ことが効いている
4. **時期**: 今すぐ（T272 割当の前に）
5. **最小修正案**
   - T272 の packet に「G3 ファイルを一度も書いていない session であること」を明示条件として書く
     （T282 packet には同種の条件が既に書かれているので、同じ書式で足せる）
   - packet に「別責務を要さないと判断した理由」を 1 行必須化
   - **host thread limit 枯渇は BLOCKED 条件として扱う**（session 再利用で回避しない）。
     AGENTS.md の停止条件「required independence を honor する」の具体化に当たる

### M4. TEST_POLICY Phase 6 の「helper が expected visibility を導出してはならない」に T278 の修正が反している

1. **severity**: MEDIUM
2. **根拠**
   - `TEST_POLICY.md` Phase 6 節: 「共通 helper は正しい入力や test double の構築に限る。
     **期待する visibility、status/reason、hash、CAS、audit result、PASS を導出してはならない**」
   - `tests/test_phase6_private_review.py` の `_terminal()`:
     `"visibility": "PUBLIC" if generation["decision"]["kind"] in {"chat","co_declare"} else "AUTHORIZED_PRIVATE"`
     — 分類規則そのものを helper が導出している
   - 製品側の実態は**非対称**: `ai_client/discussion/model.py:184` で `CO_DECLARATION` は固定 PUBLIC だが、
     `ai_client/discussion/state.py:243-246` で **CHAT は `channel_is_public(...)` から導出**
     （D040「公開性は channel 自身の性質」）
   - 結果、G matrix には `AUTHORIZED_PRIVATE` な chat 行が 1 つも存在しない
3. **failure mode**: wolf channel の chat を PUBLIC と誤分類する回帰が入っても、
   (a) G の 37 node は fixture が同じ規則で入力を作るため検出せず、
   (b) P6-J の `privacy_safe` は人間が `capture_id` しか見ない closed checklist で判定するため
   channel を知り得ず、やはり検出しない。**privacy zero-tolerance が二重に素通りする**
4. **時期**: Phase 6 中に（G closure 前が望ましいが、H での指摘でも間に合う）
5. **最小修正案**: `_terminal()` に `visibility` を引数化し、期待値は
   `tests/fixtures/phase6_private_review_vectors.json` 側の literal oracle に置く。
   matrix に private-channel chat 行を 1 行追加。**G の排他所有ファイルなので Main ではなく
   G 担当が行うべき変更**（M3 参照）

### M5. Human Gate / 継続権限の解釈が拡大している

1. **severity**: MEDIUM
2. **根拠**
   - D071: 「**現在の依頼境界は P6-F 正式 DONE まで。G は開始しない**」
   - `CURRENT_STATE` Authorization: 「F 完了後の 2026-09-14 にユーザーが『このまま続行してください』と
     指示した。旧 F-only 停止境界は今回の続行を止めない。**G→独立試験/review→H→前提成立後 I→独立 J** の
     順序を維持して進む」
   - 同ファイル: 「I の tokenizer/provenance/数値選択 hold は**未解除**」。だが解除主体が明記されておらず、
     Next Integration Action は「I は T271/T276 の static 証拠を用いた限定 24witness 計測 packet の準備から」
     = Main が前提を自分で成立させる経路になっている
   - D069: 「Any later tempo, AI chat-invocation cap, CO relationship, or general server speech-cap change is a
     **separate product and design decision**」。token 予算の数値選択がこれに当たるか否かがどこにも書かれていない
3. **failure mode**: P6-I は実 provider/GPU/exact-9B の**再実行が禁じられた一回限り**のゲームで、
   証拠は既に一度失われた領域に出る。これが 9 文字の継続指示を根拠に、かつ予算数値を実行主体自身が
   選んだ状態で起動され得る。失敗した場合、design §11 が rerun を禁じているため Phase 6 closure に
   新しい Design Gate が必要になる
4. **時期**: 今すぐ（文書のみ、実装不要）
5. **最小修正案**: `CURRENT_STATE` の Authorization に 2 行足す
   - 「実 model 起動（P6-I）は本継続指示に含まれず、別途ユーザー承認を要する」
   - 「whole-response token の数値選択は Architect 提案 + ユーザー決定。Main は選択しない」

### M6. D068 の「3 ラウンドで route 再評価」が objective の細分化で機能していない

1. **severity**: MEDIUM
2. **根拠**: 「exact tokenizer で 24 witness を計測する」という単一 objective に対し、
   T248（model/metadata）→ T266（provenance followup）→ T268（static release binding）→
   T271（metadata entrypoint）→ T276（single-file metadata）→ T277（corpus, `KeyError: 'peer_answer'` で FAIL）→
   T281（corpus 補正）と **7 タスク・約 2 日**が費やされ、witness 計測は 1 件も出ていない。
   各タスクが別名の狭い objective を名乗るため、`OPERATIONS.md` の「同一 acceptance/evidence objective で
   3 ラウンド失敗」の counter が一度も立たない。D068 自身が
   「even when each round has a different mutation or symptom name」と書いているのに、
   **task 名の変更には効いていない**
3. **failure mode**: 前提タスクが停止規則なしに無限に伸び、P6-I がブロックされたまま token/時間だけが
   消費される。route 再評価（Investigator/Architect 投入）の判断点が来ない
4. **時期**: Phase 6 中に
5. **最小修正案**: packet に `Objective: <task ID ではなく objective 名>` と `Round: n` を持たせ、
   counter を task ではなく objective に付ける。既存の T269/T279 のような再評価は正しく機能しているので、
   仕組みを objective 側へ移すだけで足りる

### M7. 512 件上限で P6-J が構造的に閉じられなくなり得る

1. **severity**: MEDIUM
2. **根拠**
   - `scripts/phase6_private_review.py:21` `MAX_RECORDS = 512`、`:490` で `len(rebuilt) > MAX_RECORDS` を
     fail closed。checklist schema も `maxItems: 512`
   - design §11: 「The population is **never sampled**」「repair/re-run requires a new scoped task and review,
     **never automatic retry**」、runner は「must not ... retry the game」
   - 実測母数: T264 で 818 秒の fixture ゲームが accepted 67 件。本番は D069 の 180 秒 day・
     general cap なしで、日数次第で 512 を超え得る
3. **failure mode**: 唯一許可された exact-9B ゲームが 512 件超を生成 → processor が fail closed →
   `human_quality_pass` が原理的に出せない → design が再実行を禁じているため、Phase 6 closure に
   新しい Design Gate とユーザー判断が必要になる。**ゲームを走らせてから判明する**のが最悪の点
4. **時期**: P6-I 起動より前に（Phase 6 後では手遅れ）
5. **最小修正案**: P6-I packet の machine evidence に accepted text 件数を必須出力させ、
   512 を超えたら J に進まず停止する条件を明記。併せて超過時の route
   （decision で上限引き上げ / checklist 分割）を**ゲーム実行前に**決めておく

---

## LOW

| # | 指摘 | 根拠 | failure mode | 時期 | 最小修正 |
|---|---|---|---|---|---|
| L1 | completion marker が二重機構 | `tests/conftest.py` の `_COMPLETION_TESTS` ハードコード allowlist と、`test_phase6_semantic_completion.py:134` の inline `@pytest.mark.completion` が併存 | 新しい重い別プロセステストを追加した際 conftest 更新を忘れると、4 Python バージョンの既定 matrix に静かに混入する | Phase 6 後 | allowlist を廃し inline marker に統一 |
| L2 | POSIX privacy 検査が ancestor/owner を見ない | `phase6_private_review.py:141` は最終 path の `S_IMODE & 0o077` のみ。Windows 分岐は DACL を検査 | world-writable な ancestor 下では他ユーザーがファイルを差し替え可能。現状到達不能だが H4 と組で危険 | Phase 6 後（H4 の結論に合流） | 実測されるまで POSIX は unsupported として fail closed |
| L3 | root に空 `tmp*/` 173 個 + `.pytest-tmp` `.tmp` `.tmp-tests` | `TemporaryDirectory()` の rmdir 失敗痕。全て空 | 直接の害は小さいが、**Windows の cleanup が実際に失敗している証拠**であり T252 喪失と同系統。`.gitignore` の `tmp*/` も将来の実ディレクトリを隠し得る | Phase 6 後（D068 M6 で deferred 済み） | 件数を Current Blockers に記録し「zero-residue」と混同しない |
| L4 | `Docs/ai/TOKEN_SAVING_PROPOSAL_20260907.md`（13KB）が INDEX のどの分類にも無い | `INDEX.md` の Repository classification に不在 = INDEX 自身の定義で UNKNOWN | 将来 authority と誤読される | Phase 6 後 | ARCHIVE と明記、または `review_archive/` へ退避 |

---

## 観点別の総括

| 観点 | 評価 |
|---|---|
| **Main Integrator authority model** | 設計は妥当。ただし「独立性要否を Main が自己判定し、その判定を記録しない」点と、容量制約時の fallback が session 再利用になっている点が実質的な穴（M3） |
| **CURRENT_STATE / TASKS / handoff / evidence の責務分離** | 文書上の分離は明確で `INDEX.md` の authority 表は良質。実装（`check_docs`）が board→packet の片方向しか守っていないため分離が保証されていない（M1、M2） |
| **stale state / duplicate authority** | D070 が狙った「stale live-next-action の重複」は解消済み。代わりに **packet 先行作成による orphan 窓**という新種が発生（M1、本レビュー中に実地観測） |
| **簡略化しすぎた安全機構** | fixed workflow の撤廃自体は妥当。危険なのは (a) 独立性判定の自己決定（M3）、(b) 未実行の POSIX 分岐が Windows 分岐より弱い（H4）、(c) 512 上限の事前検証なし（M7） |
| **Sol 以前の過剰 scaffolding** | **良好**。Autodev は D065 で除去、RUNBOOK/PROMPTS/ARCHITECTURE は十分小さい。残骸は L4 と `_to_delete/` 程度 |
| **Tester / Reviewer independence** | 契約としては維持（D051 の self-approval 禁止・D070 も明示保持）。実運用では M3 により侵食中。CI という platform 独立の第三者検証は H4 で停止 |
| **BLOCKED / Human Gate / autonomous continuation** | 停止条件の列挙は良い。問題は (a) H1 という明白な Human Gate が Current Blockers に無い、(b)「このまま続行」の解釈が D071 の明示境界を越えて J まで拡大（M5） |
| **evidence retention / private evidence / pytest cleanup** | pytest 管理外へ直接書く方針（D071、`OPERATIONS.md`）は正しい。実装が **1 つの base に実原本と合成を混在**させたため、provenance が既に UNKNOWN 化（H3） |
| **Windows 専用処理と Linux CI の platform boundary** | 現状 **CI は Phase 6 を実行不能**。静的監査で特定済み。最小修正案は妥当（H4） |
| **P6-G 以降の token 計測・fixture・model/GPU/Q8/soak** | Q8/D069 の扱いと「35B/fallback/soak なし」の維持は堅い。懸念は (a) whole-response 96 token が Phase 6 の semantic JSON（T244 実測 331–639 bytes）に対して不足の可能性が高いのに未測定、(b) その測定 objective が 7 タスク停滞（M6）、(c) fixture が期待 visibility を導出（M4） |
| **停止・再開・recovery 設計** | 再開手順（`ai_status.py integrate` → INDEX → packet）は健全で、handoff の pointer も丁寧。弱点は M1 の orphan 窓と、H1 により「repository が消えたら recovery 対象が無い」こと |
| **product / acceptance / test semantics** | **意図的な弱化は見つからなかった。** D068 の disposition、D069 の baseline、design §11 の PASS authority 単一化、TEST_POLICY Phase 6 節はいずれも厳格。非意図的な弱化が M4（helper による期待値導出 + private chat 母数の消失）と H4（未検証の弱い POSIX publish 経路）の 2 件 |

---

# 第 II 部 — テスト試行錯誤の分析

## 1. 何が起きているか（事実）

**直近 15 時間で 30 タスク。** `logs/t251-investigate`（09-13 22:32）から `logs/t281-investigate`（09-14 12:18）まで、
T251〜T282（T272/T273 は packet のみ）の 30 タスク。うち 09-14 10:31〜12:18 の
**1 時間 47 分で 14 タスク**（T265, T269, T270, T271, T274, T275, T276, T278, T267, T280, T277, T279, T281, T282）。

**その間、製品コードは 1 行も変わっていない。** Phase 6 の製品は `ai_client/discussion/`（6,124 行）で、
P6-E / T241 で独立承認済み。T242 以降に触られたのは `scripts/` と `tests/` だけ。

**証拠 harness が製品より大きくなっている。**

| | 行数 |
|---|---|
| `scripts/run_phase5_local_smoke.py` | **3,884**（Phase 6 で +485） |
| `tests/test_phase5_local_smoke.py` | 2,041 |
| `tests/test_phase6_semantic_completion.py` | 692 |
| `scripts/phase6_private_review.py` | 699 |
| `tests/test_phase6_private_review.py` | 600 |
| `tests/test_phase6_evidence_retention.py` + fixture | 223 |
| **証拠 harness 合計** | **8,139** |
| Phase 6 製品（`ai_client/discussion/`） | 6,124 |

`run_phase5_local_smoke.py` は**リポジトリ最大の Python ファイル**（製品モジュールのどれよりも大きい）。
名目は「local smoke script」である。

**失敗ラウンドの実際の原因内訳**（T265/T270/T274/T262/T278/T255/T277 の handoff から）:

| ラウンド | 直接原因 | 層 |
|---|---|---|
| T265 r1 | basetemp の親未作成 → `WinError 3`、20 node setup ERROR、raw 未保存 | 環境 setup |
| T265 r2 | test oracle の誤り（`non_repetitive` の 3 件目だけ FAIL と期待） | review ロジック |
| T265 r4 | 各対象自身に `SE_DACL_PROTECTED` を必須化 → 14 FAIL | **Win32 FFI** |
| T265 r5 | redacted early failure、**原因は今も UNKNOWN** | **Win32 FFI（推定）** |
| T265 静的指摘2 | FFI に `argtypes`/`restype` 未設定 → 64bit pointer 切り捨ての危険 | **Win32 FFI** |
| T270 | `FILE_RENAME_INFO` の FileName buffer に NUL 終端領域なし → 誤名 artifact、CLI は exit 0（false success） | **Win32 FFI** |
| T274 | fixed record kind と visibility の不一致（CO_DECLARATION） | fixture |
| T262 | nested PowerShell ACL preflight FAIL、raw 未読・stderr 未保存 → **原因未確定** | ACL 取得 wrapper |
| T278 preflight | `token.user_sid` という存在しない field 参照 | ACL 取得 wrapper |
| T255 r1〜r3 | PowerShell ACL 呼出し/出力検証 | ACL 取得 wrapper |
| T255 r4 | basetemp の strict resolve（実は未作成の可能性を排除できず） | 環境 setup |
| T277 | `KeyError: 'peer_answer'`、prefix 一括書込みのため途中成果ゼロ | script ロジック |

**review ロジック自体が原因の失敗は 12 件中 2 件（T265 r2, T274）だけ**である。残りは全部 OS 境界
（ACL / FFI / temp path）。

---

## 2. 根本原因

### 原因① 失敗が構造的に診断不能に作られている ← 最大の原因

```
$ grep -c 'ReviewFailure("corrupt")' scripts/phase6_private_review.py
43
```

**43 箇所の異なる検査が、すべて `"corrupt"` という 1 語に collapse する。** さらに `main()`（:690-694）:

```python
try:
    process(args.run_dir, args.checklist, args.output)
except Exception:
    print("phase6 private review failed", file=sys.stderr)
    return 1
```

全例外を握りつぶし、**定数 1 行**しか出さない。

これが試行錯誤の正体である。1 回失敗すると、操作者が得る情報は「失敗した」だけ。どの検査か、
path 検査か ACL 検査か schema 検査か hash 不一致かも分からない。だから毎回、

1. 停止・全所有解放（packet の規律どおり）
2. source を静的に読む Investigator タスク（T269, T275）
3. 43 箇所のどれが発火したかを推測した修正タスク（T270, T274, T278）
4. 再実行 → また 1 語だけ

というサイクルが必ず 1 周し、**1 回の失敗 = 最低 2〜3 タスク**になる。
T265 probe-5 の原因は今日まで UNKNOWN のままである。

重要な点: **redact が必要なのは transcript 本文と path であって、「どの不変条件が破れたか」という
reason code ではない。** `visibility_mismatch` / `dacl_untrusted_sid` / `rename_final_path_mismatch` /
`manifest_hash` といったコードに private データは一切含まれない。privacy 要件を守ったまま診断可能にできる。

### 原因② 脅威モデルが矛盾しており、最も高価な部分が性質を何も改善していない

`scripts/phase6_private_review.py` 699 行の内訳:

| 関数 | 行数 | 内容 |
|---|---|---|
| `_windows_private_path` | **117** | 手書き ctypes（`GetNamedSecurityInfoW`, `GetSecurityDescriptorControl`, `ConvertSidToStringSidW`, `GetAce`, `OpenProcessToken`, `GetTokenInformation`） |
| `_locked_path` | 67 | ロック/ハンドル固定 |
| `_write_aggregate` | 46 | `SetFileInformationByHandle` + `FILE_RENAME_INFO` + `GetFinalPathNameByHandleW` |
| `_plain_absolute` / `_read_bytes` / `_manifest_artifact` | 79 | path 硬化 |
| **OS 硬化 小計** | **309（44%）** | |
| review 本体（`_population` + `_aggregate` + schema + CLI） | 約 390 | |

**この 44% が失敗ラウンドのほぼ 100% を生んでいる。**

そして実際の ACL を測ると、この硬化が守ろうとしている性質は**すでに同じ木の中で破れている**:

```
C:\AIwolf                                          Protected: False
  BUILTIN\Users                      ReadAndExecute
  NT AUTHORITY\Authenticated Users   Modify            <- 継承

C:\AIwolf\logs\phase6-private-evidence              Protected: False
  BUILTIN\Users                      ReadAndExecute
  NT AUTHORITY\Authenticated Users   Modify

C:\AIwolf\.phase4-real-smoke-retry4\run\ai.jsonl    <- 実LLM transcript
  BUILTIN\Users                      ReadAndExecute    <- ローカル Users が読める
  NT AUTHORITY\Authenticated Users   Modify

C:\AIwolf\logs\phase6-private-evidence\p6f-private-evidence-*   Protected: True
  OWNER RIGHTS / SYSTEM / Administrators のみ          <- ここだけ守られている
```

つまり同一の攻撃者モデルに対して:

- **実 LLM transcript（Phase 4 の `run/ai.jsonl`）はローカル Users が読める**
- **`logs/t278-implement/final-seal.json` — Main が整合性の anchor に使っている seal — は
  Authenticated Users が書き換えられる**
- `logs/phase6-private-evidence` base 自体が protected=False
- 守られているのは helper が作る container の中身だけ
  （Python 3.13 の `mkdir(mode=0o700)` が Windows で protected DACL を作るため。
  だから helper が Windows + 3.13 必須になっている）

**container の中身だけを 117 行の FFI で守り、その整合性を証明する seal と、より古い実 transcript を
無防備に置いている。** これは脅威モデルとして成立していない。工数の 44% と失敗の 80% を生んでいる部分が、
全体の privacy 性質を 1 ミリも上げていない。

付随して、この FFI が Windows 専用であることが H4（Linux CI が Phase 6 を実行不能、
未検証の弱い POSIX 分岐が残存）の直接の原因でもある。

### 原因③ 検証対象が製品ではなく自分の証拠ツールになっている

Phase 6 の完了条件は ROADMAP で **「前の発言を受けた会話が成立する」**。

直近 30 タスクが検証したのは、P6-F の evidence retention 機構（pytest cleanup に耐える private 保存）と、
P6-G の `scripts/phase6_private_review.py`（人間レビュア用の集計ツール）。
**どちらも製品ではなく、製品を評価するための道具**である。会話品質そのものは一度も測定されていない。

T280 の結論が象徴的である — 37+3 PASS を出したうえで、証拠の所有関係を時刻相関でしか特定できず
「G gate の最終可否は UNKNOWN」。**道具の検証が、道具の検証を必要とする段階**に入っている。

### 原因④ first-failure-stop が診断不能と組み合わさって確定的ループになる

「最初の失敗で保全して停止・所有解放・新 packet」という規律自体は正しい（証拠保全のため必要）。
しかし原因①により、停止後に得られる情報がゼロなので、必ず「静的調査 → 推測修正 → 再実行 → 停止」が回る。
D068 の 3 ラウンド規則は objective を細分化することで実質回避されており（M6）、
tokenizer chain は T248→T266→T268→T271→T276→T277→T281 の **7 タスクで計測ゼロ**である。

### 原因⑤ 環境前提が毎回手作業で、同じ setup ミスが再発する

- T265 r1: basetemp の親を作らずに `--basetemp` 指定 → `WinError 3` で 20 node が製品コード到達前に停止、
  しかも raw 未保存
- T257: sandbox/default temp で実行 → 既知の `WinError 5`
- G の `private_case_root`: fresh checkout で `logs/` 不在（`.gitignore` 対象）→ 同じクラス
- T277: prefix を最後に一括書込み → 失敗時に途中成果が残らない

packet は自然言語で「通常非昇格・新専有 basetemp・120 秒・開始時から raw 保存」と毎回書いているが、
**機械的に強制するものが無い**。だから毎回誰かが忘れる。

---

## 3. どこを直すべきか

優先順。すべて G の排他所有ファイル内 or 環境設定で、受入 semantics を一切落とさない。

### P0-1. 失敗に reason code を付ける（最小・最大効果）

```python
class ReviewFailure(ValueError):
    def __init__(self, category: str, code: str = "") -> None: ...
```

43 箇所に短い機械可読コード（`path_reparse`, `dacl_untrusted_sid`, `rename_final_path`,
`manifest_hash`, `schema_closed` 等）を付け、`main()` は `category` と `code` を stderr に出す
（path と本文は出さない）。

- **効果**: 1 回の失敗の解析コストが「Investigator タスク 1 本」から「stderr 1 行」になる。
  上表の失敗のうち少なくとも T265 r4/r5、T262、T270 はこれで即断できたはず
- **コスト**: 半日未満。privacy 要件に触れない
- **これを先にやらないと、以降のどの修正も同じ試行錯誤を繰り返す**

### P0-2. 脅威モデルを明示的に決着させる（T279/T282 のスコープを広げる）

現状は「選択肢が検討されていないまま最も高価な案が実装されている」状態である。3 択を明示して決めるべき。

| 案 | 内容 | 評価 |
|---|---|---|
| A | `C:\AIwolf` の ACL を protected 化して木全体を owner-only にする | **不可** — ACL 変更は未承認（D071 / CURRENT_STATE） |
| B | **FFI 硬化を捨て、private 領域はユーザーが用意した前提とし、script は path 健全性 / schema / hash / linkage / correlation だけを検証する** | **推奨** |
| C | 現状維持（container だけ FFI 硬化） | 脅威モデル矛盾が残り、失敗ループも CI 不能も残る |

**案 B を推奨する理由**:

- 受入 semantics は 1 つも落ちない。`_population` の correlation 検査、manifest hash 照合、
  closed schema、`non_repetitive` 再計算、`human_quality_pass` の導出 —
  **設計 §11 が PASS 条件として列挙しているものは全部残る**。
  消えるのは「この path の DACL が owner-only か」という、seal 自身が守られていない以上
  どのみち成立していない検査だけ
- 失敗原因の約 80%（FFI 起因）が消える
- POSIX 分岐問題（H4）が同時に消える。Windows 専用要件が無くなれば Linux CI で Phase 6 が回り、
  platform 独立の第三者検証が復活する
- 「private 領域の用意」は運用側の 1 回の作業（`logs/phase6-private-evidence` に protected DACL を
  一度設定する）で済み、これは script が毎回 117 行の FFI で再検証するより確実

T282 の packet は既に「全面的な新 POSIX 実装/test/checker が最小性を超えないか」
「明示 platform prerequisite の選択肢を含め canonical 受入を落とさない最小境界が十分検討されているか」を
判定条件に入れており、**この 3 択を判断させる場所として適切**である。
ただし T279 の design が「POSIX 実装を足す」方向（案 C の拡張）なら、その前に案 B を検討対象に入れる必要がある。

### P0-3. 環境 preflight を 1 箇所に集約

`tests/fixtures/phase6_evidence.py` に、basetemp 親の `mkdir(parents=True, exist_ok=True)`、
実行文脈（非昇格 / restricting SID 空 / TEMP 不変）の確認、raw 保存先の事前作成をまとめた 1 関数を置き、
全 packet がそれを呼ぶ。

- 原因⑤ の 4 件は全部これで消える
- `logs/` 親不在（H4 の一部）も同時に解決

### P0-4. 「1 回の失敗 = 1 タスク」を緩める

診断可能になった後は、**同一 packet 内で「失敗 → reason code を読む → 同じ検査に対する 1 回の再実行」まで
を許可**する。証拠保全（最初の失敗の raw を保存して不変にする）は維持したまま、
所有解放と新 packet 起票を省ける。現在の規律は「失敗を隠さない」ためのものであり、
「1 回しか実行してはいけない」は実ゲーム（P6-I）にだけ必要な制約であって、合成 fixture には過剰である。

### P1. 会話品質の測定へ戻る

P6-G が閉じたら、証拠ツールの検証はそこで止めるべき。T280 のような「道具の検証の検証」をもう 1 段積むと、
Phase 6 の完了条件から更に離れる。

---

## 4. 今の作業は適切か

| 現在の作業 | 判定 | 理由 |
|---|---|---|
| **T282** platform 詳細設計の fresh review（Reviewer, IN_PROGRESS） | **適切** | routing が正しく、packet の判定条件も良い。ただし P0-2 の 3 択を判断対象に加えるべき |
| **T279** platform 境界の技術整理（Architect, REVIEW） | **条件付きで適切** | 正しい問題に正しい責務を当てている。design が案 C の拡張（POSIX 実装の追加）に寄っているなら、案 B が最小境界として検討されているかを T282 が必ず問うべき |
| **T267 / T280**（Tester, REVIEW） | **適切だった** | T280 が「証拠が足りないので G gate 可否は UNKNOWN」と正直に返したのは、独立性が正しく機能した数少ない例 |
| **T272**（G fresh Reviewer、packet のみ・board 未登録） | **今 dispatch すべきでない** | T280 が既に UNKNOWN を返しているので、今投げれば CHANGES_REQUIRED が返るだけで 1 ラウンド無駄になる。P0-1（reason code）と P0-2（脅威モデル決着）の後に投げるべき |
| **T281** corpus 構築の限定補正（Investigator、実体は Main 自身） | **不適切** | ① 同一 objective で 7 本目、計測ゼロ（D068 の 3 ラウンド規則を objective 細分化で回避）。② Main が自分で実行しており独立性が無い。③ そもそも token 予算の測定は、G が閉じていない現時点の critical path ではない |
| **board に無い作業** | **最大の問題** | H1（未コミット・バックアップなし、7,096 行 + untracked 291）、H2（`.phase4-real-*` が gitignore 外の実 transcript）、H4（CI 分割）が**どのタスクにもなっていない**。原因②の実測が示すとおり、H2 は privacy の実害として今まさに成立している |

### 総括

**やっていること自体は誠実で、規律も高い**（失敗を成功に読み替えない、原本を改変しない、
所有を解放する、UNKNOWN を UNKNOWN と書く）。手順違反（T265 probe-5 の D068 越境、T257 の temp 誤り）も
自分で記録している。この品質は珍しく、捨てるべきではない。

**適切でないのは「何を検証しているか」である。** 30 タスク・15 時間を、製品ではなく証拠ツールの
OS 境界に使っており、その OS 境界の硬化は、同じ木にある seal と旧 transcript が無防備である以上、
意図した性質を達成していない。

最短の立て直しは **P0-1（reason code）→ P0-2（案 B で脅威モデル決着）** の 2 つ。
この 2 つで、失敗ループの主因・Linux CI 不能・POSIX 未検証分岐・Windows 専用要件が同時に解け、
G を閉じて会話品質の測定に戻れる。あわせて H2（`.gitignore` 1 行）と H1（backup 承認）は、
原因②の実測が出た今、**タスク化して board に載せるべき**である。

---

## 推奨する着手順

1. **H2**（`.gitignore` 1 行）→ **H1**（ユーザーに commit/backup を BLOCKED 報告で要請）。
   この順序でないと private transcript が commit される
2. **H5**（AGENTS.md の 2 ブロック移設） — 以後の規則追加を可能にするため
3. **P0-1**（reason code）→ **P0-2**（脅威モデル 3 択の決着、案 B 推奨）
4. **H3**（新規 container のパス分離）→ **H4**（CI marker 分割、`windows_private` は着手済み）
5. **M5 / M7**（文書 2 行 + I packet の条件追記） — P6-I 起動前に必須
6. **M1 / M2**（`check_docs` に 2 check 追加）→ **M3 / M4 / M6**

---

## 検証した内容と検証していない内容

**検証した**: リポジトリ全ファイルの静的読取り、`git status` / `git diff --stat`、
`python scripts/check_docs.py`（exit 0）、`python scripts/ai_status.py integrate`、
`Get-Acl` による読取り専用の ACL 実測、行数・サイズ計測。

**検証していない**: pytest の実行、`scripts/phase6_private_review.py` の実行、実 provider / model / GPU、
private raw 原本の内容、Linux 環境での実測、GitHub Actions 上の実行結果。
CI の失敗は静的読解による予測であり、実行観測ではない。
