# Phase 6 G/F 検証 platform 境界の詳細設計

Status: APPROVED — T282 revision 4で独立承認、Mainがexact reviewed SHA4216c7c1a85678a0fb5a9698e06f71ed6b45bf4ad79f795c9277107c4112f5f3を照合  
Task: T279 revision 2  
Responsibility: Architect

## 1. 最小目的

G/F durable-private acceptance を helper と同じ Windows / CPython 3.13 以上の prerequisite へ route する。
unsupported platform の private-review CLI は raw input read、aggregate publish、`human_quality_pass=true` より前に
redacted exit 1 で停止する。一般 AIwolf server/client、既存 Ubuntu offline regression、F finite completion の
game rules、wait、1200秒 `RunConfig`、acceptance は変更しない。

POSIX private processor の fd/race/marker/test/checker は未承認の future scope であり、本 revision は実装・
検証・CI routing に含めない。

## 2. exact marker と node partition

`pyproject.toml` に `windows_private` marker を登録する。module-level marker は次の二つだけである。

| file | marker 範囲 |
| --- | --- |
| `tests/test_phase6_private_review.py` | module 全 node |
| `tests/test_phase6_evidence_retention.py` | module 全 node |
| `tests/test_phase6_semantic_completion.py` | `test_p6f_nine_client_semantic_completion` だけ。既存 `completion` と併記 |

これにより semantic-completion module の helper を使わない offline node は Ubuntu 3.10–3.13 coverage に残る。
F finite completion は削除せず、`completion and windows_private` として Windows 3.13 で一度だけ実行する。

| job | platform / Python | selection |
| --- | --- | --- |
| `test` | Ubuntu 3.10–3.13 | `not completion and not windows_private` |
| `completion` | Ubuntu 3.13 | `completion and not windows_private` |
| `windows-private` | Windows 3.13 | `windows_private` |

CI workflow に短い inline collection step を置く。`pytest --collect-only -q` の全 node ID と上記3 selection
の node ID を比較し、union=全 node、任意2集合の交差=空、F finite completion が
`completion and windows_private` にあることだけを fail closed で確認する。collection は fixture/setup を
実行しない。fresh parent/helper/DACL の Windows 実測とは別条件であり、代替しない。新しい checker file や
framework は作らない。

## 3. fresh parent と責務分離

helper の前に exact `ROOT / "logs" / "phase6-private-evidence"` を
`mkdir(parents=True, exist_ok=True, mode=0o700)` で作る箇所は次だけである。

1. G `tests/test_phase6_private_review.py:private_case_root`。
2. F `tests/test_phase6_evidence_retention.py:test_private_evidence_session_a`。
3. F `tests/test_phase6_evidence_retention.py:test_private_evidence_survives_real_pytest_cleanup`。
4. F `tests/test_phase6_evidence_retention.py:test_private_evidence_rejects_wrong_anchor_and_overlap` の exact base。
5. F `tests/test_phase6_semantic_completion.py:test_p6f_nine_client_semantic_completion`。

これは親 directory の存在を作るだけで、ACL/DACL/TEMP/security を検証・変更しない。
`create_private_evidence_container` の既存責務は exact base、existing component directory/reparse、basetemp
overlap、container parent/prefix/reparse である。owner/DACL の実判定は processor Windows `_plain_absolute` /
`_windows_private_path` / `_locked_path` の責務であり、fresh Windows checkout の実測で独立に確認する。

## 4. CLI prerequisite guard

後続 Implementer は `scripts/phase6_private_review.py:process` の先頭で、path 処理、`_plain_absolute`、
`_read_json`、directory iteration、output temporary 作成の前に `os.name == "nt"` と
`sys.version_info >= (3, 13)` を確認する。不成立時は `ReviewFailure("unsupported_platform")` を送出し、既存の
redacted stderr と exit 1 だけを返す。aggregate は作成せず、input/output bytes を read/write しない。

この guard は private processor acceptance のみを helper prerequisite に一致させ、未承認 POSIX branch が
CLI で PASS を出す経路を閉じる。protocol、game rules、server/client、非private Linux regression は対象外である。

## 5. 時間・raw・独立検証

Windows-private job は `timeout-minutes: 30` とする。G guard/focused probe は既存の bounded command 上限120秒を
維持する。F finite completion は既存1200秒 `RunConfig` を維持し、保存済み818秒実測を包含するため、120秒を
流用しない。test timeout、completion body、ゲーム規則は変更しない。

実装の marker/mkdir/guard だけは新鮮な Windows parent と guard の実測を必要とする。一方、F finite completion
本体の既存 acceptance/raw は保持し、今回の marker/mkdir 修正のために完走を重複実行・削減・代替しない。
後続 I の必須 finite completion gate はそのまま維持する。remote CI 実行、upload、private raw の GitHub artifact
転送は本 scope 外であり、設計へ追加しない。

独立 Tester は collection partition、fresh Windows parent/helper、processor DACL read-only 観測、unsupported
guard の raw-read-before/aggregateなし、各 command の有限時間を測定する。fresh Reviewer は marker範囲、Ubuntu
offline coverage、F completion維持、fresh parent全5箇所（G1+F retention3+F completion1）の正確さ、guard、private raw非転送、scope最小性を確認する。

## 6. owned files

後続 Implementer は `.github/workflows/ci.yml`、`pyproject.toml`、`scripts/phase6_private_review.py`、
`tests/test_phase6_private_review.py`、`tests/test_phase6_evidence_retention.py`、
`tests/test_phase6_semantic_completion.py` だけを所有する。product、schema、fixture payload、population/aggregate、
ACL/security/TEMP/admin、POSIX source/test は変更しない。本 Architect session は承認しない。
