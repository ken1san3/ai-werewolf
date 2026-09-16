# Phase 6 private 診断と新規証拠 provenance 境界の詳細設計

Status: DRAFT — T288 初回案。新規独立 Reviewer の承認前は実装不可  
Task: T288  
Responsibility: Architect

## 1. 結論と適用範囲

private review の公開 aggregate schema、判定式、唯一の human-quality PASS authority、stderr の
固定文言と exit code は変更しない。失敗の調査可能性は、公開されない owner-only の診断 artifact
へ閉じた reason code を一件だけ記録することで改善する。reason code は品質評価理由ではなく、入力、
linkage、保管境界、processor 自身のどの機械的段階で fail closed したかだけを表す。

今後作る証拠 container は `real-game`、`synthetic-probe`、`observer` を path で分離し、作成時に
呼出側が渡す `owner_task_id` と `run_id` を immutable provenance manifest に直接結び付ける。
時刻、directory の新しさ、process 一覧から所有を推定しない。既存の
`logs/phase6-private-evidence/p6f-private-evidence-*`、既存 manifest、private shard、hash、FAIL 記録は
移動、削除、改変しない。この設計は新規作成分だけに適用する。

ACL、security descriptor、TEMP、管理者設定は変更しない。`ctypes` による Win32 ACL/handle 検査は、
owner-only 判定と path component の置換防止を現在担っており、同等の安全性を示す代替がないため維持する。

## 2. 閉じた診断 artifact

processor CLI に optional の `--diagnostic DIAGNOSTIC_JSON` を一つ追加する。指定する production/review
運用では必須とし、既存 fixture 互換のため引数自体は optional とする。`DIAGNOSTIC_JSON` は checklist と
同じ `observer` container 内の未存在ファイルでなければならない。aggregate 出力先、run directory、
real/synthetic container、その子孫は拒否する。processor は既存 `_plain_absolute`、祖先 handle pin、
owner/DACL 検査、exclusive create、fsync、no-replace rename と同じ書込み境界を使う。

schema は `aiwolf.phase6-private-review-diagnostic.v1`、`additionalProperties:false` とし、fields は次だけとする。

| field | contract |
| --- | --- |
| `schema_version` | 上記固定値 |
| `processor_task_id` | 呼出 packet が指定した bounded ID |
| `observer_run_id` | observer provenance と完全一致する bounded ID |
| `outcome` | `PASS` または `FAIL` |
| `stage` | 下記 closed stage |
| `reason_code` | 下記 closed code。PASS 時は `NONE` |

`stage` は `PREREQUISITE`、`PATH_BOUNDARY`、`CHECKLIST_READ`、`RUN_DISCOVERY`、
`MANIFEST_READ`、`ARTIFACT_READ`、`POPULATION_LINKAGE`、`CHECKLIST_LINKAGE`、
`AGGREGATE_WRITE`、`COMPLETE` のみとする。`reason_code` は `NONE`、`UNSUPPORTED_PLATFORM`、
`PATH_NOT_ABSOLUTE`、`PATH_COMPONENT_UNSAFE`、`OWNER_OR_ACL_REJECTED`、`OBJECT_MISSING`、
`OBJECT_TYPE_INVALID`、`SIZE_LIMIT_EXCEEDED`、`ENCODING_INVALID`、`JSON_INVALID`、
`SCHEMA_INVALID`、`HASH_OR_LENGTH_MISMATCH`、`ARTIFACT_SET_INCOMPLETE`、
`ARTIFACT_SET_DUPLICATE`、`GAME_OR_SHARD_LINKAGE_INVALID`、`POPULATION_EMPTY`、
`POPULATION_LIMIT_EXCEEDED`、`CHECKLIST_ORDER_OR_MEMBERSHIP_INVALID`、
`NORMALIZATION_RESULT_MISMATCH`、`REVIEW_DIMENSION_FAILED`、`OUTPUT_ALREADY_EXISTS`、
`IO_FAILURE`、`INTERNAL_FAILURE` のみとする。

診断には path、basename、capture/player/request/game ID、本文、正規化文字列、例外文字列、OS error、
件数、hash、ACL/SID、時刻を入れない。1 invocation につき terminal record は一件だけである。processor は
内部 exception を `{stage, reason_code}` へ境界で写像し、未知 exception は
`INTERNAL_FAILURE` とする。`ReviewFailure` の自由文字列 category は廃止し closed enum と stage を運ぶ。
stderr は成功時空、失敗時 `phase6 private review failed\n` のままにする。

診断書込み失敗時は aggregate PASS を許可しない。aggregate を安全に作れる failure では既存どおり
`human_quality_pass=false` を出すが、aggregate の failure count と reason code の対応から別の PASS 判定を
作らない。診断が `REVIEW_DIMENSION_FAILED` でも、その内容はどの dimension/record が失敗したかを示さない。
この artifact の存在や reason code は修復 routing の補助であり、品質 PASS authority ではない。

## 3. 新規証拠 path と直接所有 manifest

新規 container の形を固定する。

```text
logs/phase6-private-evidence/
  real-game/<owner_task_id>/<run_id>/
  synthetic-probe/<owner_task_id>/<run_id>/
  observer/<owner_task_id>/<run_id>/
```

`evidence_class`、`owner_task_id`、`run_id` は呼出側が作成前に明示する。各 ID は 1–128 scalar、UTF-8
512 bytes 以下、ASCII の `[A-Za-z0-9][A-Za-z0-9._-]*` に限定し、`.`、`..`、末尾 dot/space、colon、
separator を許さない。leaf は random prefix で決めず、exact path を exclusive に一度だけ作る。同じ
owner/run の再利用は fail closed とし、自動 suffix、上書き、削除、retry をしない。

container 作成直後、他の raw を書く前に `provenance.json` を owner-only atomic write する。schema
`aiwolf.phase6-evidence-provenance.v1` の fields は `schema_version`、`evidence_class`、
`owner_task_id`、`run_id`、`producer` のみとし、`producer` は `phase6-real-runner`、
`phase6-retention-probe`、`phase6-private-reviewer` の closed value とする。全 field は path と一致させる。
時刻や PID は所有 authority にしない。

real run の `ai/manifest.json` は既存 field と判定を保持したまま、同じ container の
`provenance.json` を manifest entry 形式（relative path、bytes、SHA-256、terminal）で一つ参照する。
synthetic retention evidence も自身の `provenance.json` hash を最終 record に含める。observer provenance
は `source_evidence` object を追加できる専用 schema
`aiwolf.phase6-observer-provenance.v1` とし、`source_evidence` は `evidence_class=real-game`、
`owner_task_id`、`run_id`、`provenance_sha256`、`evidence_manifest_sha256` のみを持つ。
processor は checklist の manifest hash、observer の source hash、real provenance、real manifest の
相互一致を直接検証する。`synthetic-probe` を human acceptance 入力にすることは常に拒否する。

observer container は checklist、diagnostic、aggregate の private 作業場所である。ただし公開可能な
canonical aggregate は processor 完了後に既存の公開経路へ bytes/hash を変えず複製できる。公開文書には
aggregate と許可済み hash だけを記載し、provenance/diagnostic/checklist の本文や path は記載しない。

## 4. retention と独立検証

新規 evidence route の受入順序は次に固定する。

1. synthetic owner が一意な `synthetic-probe/<task>/<run>` を作成し、直接 provenance と raw を書く。
2. 作成 process 終了後、独立 Tester が container、provenance bytes/hash、owner/DACL、reparse 不在を確認する。
3. 別の後続 pytest を同じ basetemp で実行し、pytest-owned work が cleanup されたことを確認する。
4. その終了後、独立 Tester が同じ synthetic container と全 bytes/hash/private 境界の残存を再確認する。
5. fresh Reviewer が設計実装と上記 raw test evidence を承認した場合だけ、新しい `real-game` run を一回作る。
6. real producer 終了後と後続 pytest 後に、独立 Tester が provenance、manifest、全 raw hash、private 境界を
   それぞれ再確認する。その後だけ observer を作成し直接 linkage を記録する。

検証結果は時刻一致ではなく、exact class/task/run path、provenance bytes/hash、manifest の相互参照、
process 終了、後続 pytest の return code、前後の全 artifact bytes/hash で判定する。既存原本をこの試験の
cleanup target にしない。

## 5. Win32 ACL/FFI の評価

外部監査 `EXTERNAL_REVIEW_2026-09-14_PHASE6_HARNESS.md`（SHA-256
`930ba960006bd9cc61ad6d37faf8ba403204373e22f4334af814e45358813202`）第II部が示す三案は、非正本の
監査入力として次のように評価した。

| 案 | 判定 |
| --- | --- |
| A: repository tree 全体を protected ACL に変更 | D071 と本 task scope に反するため不採用。別の security/product authorization なしに実施不可。 |
| B: FFI を撤去し private directory は運用前提にする | 不採用。aggregate semantics が同じでも、現在 processor が読む container の confidentiality と ancestor/object replacement への耐性が失われる。運用前提を各 invocation で検証しない以上、安全性不変は証明されていない。 |
| C: 現行 FFI 境界を維持 | 本設計の採用案。診断不能は closed reason artifact で直し、platform routing は承認済み T279/T282 境界に従う。 |

監査が観測した旧 Phase 4 transcript と repository 配下 seal の弱い ACL は重大だが、それらは別 artifact の
confidentiality/integrity 問題である。弱い seal を強い ownership authority として使わないこと、旧 transcript を
別 scope で保護することが必要であり、現在の private container まで読み書き可能に戻す根拠にはならない。
新 provenance は weak external seal に依存せず、container 内の manifest と相互 hash linkage を使う。ただし
container 外へ公開した hash だけを原本や ownership の代替にしない。

現行 `_plain_absolute` は path component の reparse/存在を検査し、`_locked_path` は各祖先を handle で pin
して最終 object を開き、`_windows_private_path` は owner が current token に属し DACL の Allow SID が
限定集合内であることを確認する。この三者は次の要求へ直接寄与する。

- owner-only/privacy: path mode の見た目ではなく Windows security descriptor を実測する。
- TOCTOU: name を先に検査しただけで read/write せず、開いた handle と pinned ancestors を使う。
- output integrity: exclusive open と handle rename により既存 artifact を置換しない。

標準ライブラリだけで同じ ACL semantics と handle-relative不変条件を満たす代替は現状の source から確認
できない。複雑さを理由に撤去すると上記要件が未証明になるため、本 task は FFI を維持する。将来撤去する
場合は security-focused Architect が、symlink/junction/reparse、ancestor swap、inherited/untrusted Allow、
wrong owner、existing output race を含む adversarial test と fresh independent Windows 測定を先に設計する。

## 6. 実装所有、競合、有限テスト

T285 の未検証差分は `.github/workflows/ci.yml`、`pyproject.toml`、
`scripts/phase6_private_review.py`、`tests/fixtures/phase6_evidence.py`、
`tests/test_phase6_private_review.py`、`tests/test_phase6_evidence_retention.py`、
`tests/test_phase6_semantic_completion.py` に重なる。Main が T285 の exact diff と verdict を照合し、凍結を解除
するまで本設計の Implementer を dispatch しない。T285 bytes を上書き、巻戻し、暗黙採用しない。

解除後は一人の Implementer が次だけを serial に所有する。

- `scripts/phase6_private_review.py`
- `tests/fixtures/phase6_evidence.py`
- `tests/test_phase6_private_review.py`
- `tests/test_phase6_evidence_retention.py`
- `tests/test_phase6_semantic_completion.py`
- provenance field を生成するため必要な範囲だけの `scripts/run_phase5_local_smoke.py`

CI/marker routing は T285 の設計責務を維持し、本 packet は `.github/workflows/ci.yml` と
`pyproject.toml` を編集しない。新 helper/framework/file は作らない。

focused tests は closed reason の全値/schema、全 stage の代表 failure、unknown exception、stderr 非漏洩、
診断 write failure、診断/aggregate existing-file preservation、wrong observer path、real/synthetic/observer の
exact partition、invalid ID、duplicate run、provenance tamper、class/task/run mismatch、synthetic human-input reject、
real manifest/observer/checklist の直接 hash linkage、process 終了後と後続 pytest 後の残存を有限 fixture で覆う。
既存 aggregate literal schema/PASS vectors、512 boundary、path escape、ACL/reparse/handle race focused vectorsを
全て維持する。実 game、model/provider/GPU/tokenizer/network はこの実装・試験 packet で起動しない。

実装後は独立 Tester が Windows CPython 3.13 以上で focused tests、関連 G/F regressions、
`python scripts/check_docs.py`、diff check を実行する。次に session の異なる fresh Reviewer が公開 schema/PASS
不変、reason 非漏洩、provenance の直接性、ACL/TOCTOU 維持、T285 競合解消、test evidence を審査する。
verdict が exact `APPROVED` になるまで real acceptance run は許可しない。

## 7. Design Gate

ユーザーに新しい product rule の選択を求める必要はない。公開理由禁止、唯一の aggregate PASS authority、
D071 の原本欠落履歴保持と新規証拠 route を維持したまま、private operational diagnosis と provenance を
追加できる。本案は一回の bounded proposal であり自己承認しない。新規独立 Reviewer の詳細設計 verdict が
exact `APPROVED` になるまで実装は blocked とする。
