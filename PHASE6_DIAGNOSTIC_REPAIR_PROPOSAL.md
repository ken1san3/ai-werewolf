# Stage B 診断可能性の修正提案と テストケース

作成: 2026-09-15 / 外部独立レビュー session
対象: `EXTERNAL_REVIEW_LOG.md` R12 の P1 / P2 / P3
性質: **提案。承認済み設計でも実装指示でもない。**

## 0. この文書の位置づけと承認経路

本 session は R01–R12 の外部レビューを担当しており、**本提案の承認者にはなれない**
（D051「responsibility + session independence による self-approval 禁止」）。
採否と Design Gate は Main が判断し、**本 session と独立した Reviewer** が審査すること。

**FREEZE との関係**: Stage A の R1 plan（890 ID）と Stage B の B01–B11 は凍結済みで、
D073 は「FREEZE 後に Test ID を担当者判断で増やさない」と定める。
本提案の test は **R10 修正（T319–T323）と同じ前例**に従い、

- 新規 Test ID を Stage A catalog / Stage B cases に足すのではなく
- **既存 test module 内の focused test として追加**し
- 独立 Tester が focused batch で実行、独立 Reviewer が審査し
- catalog の再同期は次の計画改訂でまとめて行う

扱いとする。Stage B の B01–B11 と判定式は**一切変更しない。**

**変更しないもの**: product/acceptance/test semantics、B01–B11 の PASS 条件、
512 / 200 文字 / 600 bytes、clock（180/60/60）、model、
既存 ACL/TEMP/security、旧 raw・旧 FAIL・旧 UNKNOWN。

---

## 1. P1 — provider エラー本文の有限・構造化保存【HIGH】

### 1.1 問題

`ai_client/llm/backend.py:350-356` は非 2xx のとき response body を読まずに捨てる。

```python
# Only a fully drained response can prove provider terminality.
if not 200 <= response.status_code <= 299:
    raise _backend_error(
        LLMBackendErrorCode.HTTP_STATUS,
        http_status=response.status_code,
        retryable=_is_retryable_status(response.status_code),
        provider_quiescence=ProviderQuiescence.PROVEN_TERMINAL,
    )
```

T330 は 107 件すべてが HTTP 400 だったが、**理由が 1 件も残らなかった。**
T331 独立 Reviewer も「保存原本に 400 の詳細理由はなく、根因は UNKNOWN」と記録している。

**body は既に `encoded` として完全に読み込み済みである**（直前の `aiter_bytes` ループ）。
追加 I/O もハング risk も無い。

### 1.2 設計

**変更 1 — `ai_client/llm/types.py`: `LLMBackendError` に detail を足す**

```python
def __init__(
    self,
    code: LLMBackendErrorCode,
    *,
    http_status: int | None = None,
    retryable: bool = False,
    provider_quiescence: ProviderQuiescence = ProviderQuiescence.UNKNOWN,
    backend_error_detail: str | None = None,
) -> None:
    ...
    if backend_error_detail is not None:
        if type(backend_error_detail) is not str:
            raise TypeError("backend_error_detail must be str or None")
        if not 0 < len(backend_error_detail) <= MAX_BACKEND_ERROR_DETAIL_CHARS:
            raise ValueError("backend_error_detail length out of range")
        if code is not LLMBackendErrorCode.HTTP_STATUS:
            raise ValueError("backend_error_detail is allowed only for HTTP_STATUS")
        if any(not ch.isprintable() for ch in backend_error_detail):
            raise ValueError("backend_error_detail must be printable")
    self.backend_error_detail = backend_error_detail
```

`MAX_BACKEND_ERROR_DETAIL_CHARS = 256` を module 定数として置く。
既存の `http_status` 検証と同じ様式にし、**HTTP_STATUS 以外では持てない**不変条件を型で固定する。

**変更 2 — `backend.py`: 構造化された有限抽出**

```python
MAX_BACKEND_ERROR_DETAIL_CHARS = 256
_ERROR_DETAIL_KEYS = ("type", "code", "message")

def _error_detail(encoded: bytes) -> str | None:
    """Bounded structured extract of a provider error body. Never the whole body."""
    try:
        value = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    error = value.get("error")
    if not isinstance(error, dict):
        return None
    parts = []
    for key in _ERROR_DETAIL_KEYS:
        item = error.get(key)
        if isinstance(item, str) and item:
            parts.append(f"{key}={item}")
        elif type(item) is int:
            parts.append(f"{key}={item}")
    if not parts:
        return None
    joined = " ".join(parts)
    collapsed = "".join(ch if ch.isprintable() else " " for ch in joined)
    return collapsed[:MAX_BACKEND_ERROR_DETAIL_CHARS] or None
```

**本文全体をコピーしない。** `error.type` / `error.code` / `error.message` の 3 key だけを読む。
形が違えば `None` を返す。llama.cpp は
`{"error":{"code":400,"message":"...","type":"invalid_request_error"}}` を返すのでこれで足りる。

**変更 3 — 呼び出し箇所**

```python
if not 200 <= response.status_code <= 299:
    raise _backend_error(
        LLMBackendErrorCode.HTTP_STATUS,
        http_status=response.status_code,
        retryable=_is_retryable_status(response.status_code),
        provider_quiescence=ProviderQuiescence.PROVEN_TERMINAL,
        backend_error_detail=_error_detail(encoded),
    )
```

**変更 4 — audit 記録**

`AiDiscussionGenerationRecordV2` に `backend_error_detail: str | None` を追加する。
**V1 は変更しない**（凍結済み記録形式）。
`brain.py:435` 付近で `backend_error_detail=error.backend_error_detail` を渡す。
`run_phase5_local_smoke.py` の collector が V2 の field 集合検証で新 field を受理する。

### 1.3 privacy 境界

- detail は **private shard（owner-only）にのみ**入る。既存の raw と同じ保護下
- **stdout / stderr へ出さない。公開 handoff へ転記しない。** 公開してよいのは
  `backend_error_code` / `http_status` / 件数のみ
- 抽出対象を 3 key に限定し 256 文字で切るため、要求本文の大量反映は起きない
- 形が想定外なら `None`。「読めたものを何でも入れる」実装にしない

### 1.4 これで何が変わるか

T330 と同じ事象が起きたとき、private 記録に
`type=invalid_request_error code=400 message=...` が 107 件残る。
**1 件読めば原因が判る。** 現状は 107 件読んでも判らない。

---

## 2. P2 — provider 起動構成の preflight 記録【MEDIUM】

### 2.1 問題

T330 preflight は PID / creation / listener / health / model basename / context / reasoning /
build / GPU / GGUF hash / serving files 35 件の hash まで記録している。
**しかし provider の起動引数を記録していない。**

T307 のときは「jinja 有効」が本文にあったが T330 には無い。
その結果、**T316 と T330 の provider 構成が同じかどうかを保存記録から判定できない。**
serving files の hash が同じでも、起動フラグが違えば挙動は変わる。

### 2.2 設計

製品コードの変更なし。**preflight 手順に 1 項目**足す。

1. provider PID の command line を read-only 取得する
   （Windows: `Get-CimInstance Win32_Process -Filter "ProcessId=<pid>"` の `CommandLine`）。
   **読取りは操作ではない。** provider の起動・停止・再起動は従来どおり行わない
2. 既存 secret allowlist（`run_phase5_local_smoke.py:88-90` の
   `AIWOLF_LLM_API_KEY` 等）に該当する token を `***` へ置換した
   **redacted command line** を作る
3. redacted 文字列を **private preflight 記録へ保存**する
4. **公開 handoff には SHA-256 のみ**記す

### 2.3 これで何が変わるか

「今回の provider は前回と同じ構成か」が **hash 1 個の比較**になる。
本文を公開せずに構成同一性を主張できる。
T316 と T330 の差分特定も、両者の hash を並べるだけで済む。

---

## 3. P3 — 同一失敗の連続による早期安全停止【MEDIUM】

### 3.1 問題

T330 は 1 回目の 400 のあと **106 回同じ 400 を受け続け、1208.9 秒で `GAME_TIMEOUT`** に達した。
1 回目と 107 回目に情報差は無く、**承認済みの一回の実行がほぼ情報ゼロで消費された。**

既存の `ADMISSION_POISONED` はこれを覆わない。実測で確認した。

| run | `PROVIDER_CALL_TERMINAL` の `(poison_transition, retryable, quiescence)` |
|---|---|
| T316 | `(False, None, PROVEN_TERMINAL)` 34 件 + **`(True, False, UNKNOWN)` 1 件** → poison |
| T330 | `(False, False, PROVEN_TERMINAL)` **107 件** → poison せず |

poison は **provider quiescence が UNKNOWN のとき**に発動する機構であり、
クリーンな 400 は `PROVEN_TERMINAL` なので poison しないのが**正しい**。
したがって P3 は poison に相乗りさせず、**別の停止条件**として置く。

### 3.2 設計 — harness のみ。製品コードを触らない

client status には既に必要な値がすべて出ている（T330 の実 status で確認）。

```
brain.calls: 18
brain.failures: 18
brain.decisions: 0
brain.last_error_code: 'HTTP_STATUS'
brain.last_status: 'BACKEND_FAILED'
```

親 runner は既にこの status を polling し、`_touch_stop` という停止 control を持つ。
**`scripts/run_phase5_local_smoke.py` だけの変更で実装できる。**

停止条件（すべて満たしたときのみ）:

1. 9 client **全員**の `brain.decisions == 0`（一度も成功していない）
2. 9 client **全員**の `brain.failures >= EARLY_STOP_FAILURES`（既定 **5**）
3. 9 client **全員**の `brain.last_error_code` が**同一の非 null 値**
4. 上記 1–3 が **2 回連続の polling** で成立

成立時の動作:

- summary に構造化した `early_stop` を記録する
  （`reason`、`error_code`、`failures_per_client`、`polls`）
- 既存 `_touch_stop` を呼ぶ。**新しい強制終了経路を作らない**
- 以降は通常の owned cleanup と原本保全を通る

### 3.3 判定を変えないこと

**早期停止した run の合否は、停止しなかった場合と同一でなければならない。**
B01 は `game_end=false` で FAIL、証拠欠落項目は BLOCKED のまま。
**早期停止は「失敗を隠す」機構ではなく「同じ失敗の反復を止める」機構である。**
`early_stop` の記録は診断情報であって、PASS/FAIL の入力ではない。

### 3.4 誤発火しないこと

条件 1（全員 `decisions == 0`）が守りになる。
**1 client でも一度でも成功していれば発火しない。**
条件 3 は原因が単一であることを要求し、
条件 4 は一時的な status 読み取りのブレを除く。

---

## 4. テストケース

既存 module へ focused test として追加する。**新規 Test ID を凍結 catalog へ足さない。**
すべて deterministic、実 provider / GPU / 実 game を使わない。

### 4.1 P1 — `tests/test_phase4_llm_backend.py`

| # | test 名 | 入力 | PASS 条件 | negative boundary |
|---|---|---|---|---|
| 1 | `test_http_error_detail_extracts_exact_literal_from_llamacpp_body` | 400 + `{"error":{"code":400,"message":"invalid grammar","type":"invalid_request_error"}}` | `backend_error_detail == "type=invalid_request_error code=400 message=invalid grammar"`（**literal 一致**） | key 順序は `type,code,message` 固定。実装から期待値を導出しない |
| 2 | `test_http_error_detail_is_none_for_unexpected_shapes` | parametrize: `b""` / `b"not json"` / `b"[]"` / `b'{"x":1}'` / `b'{"error":"str"}'` / `b'{"error":{}}'` | 全ケース `backend_error_detail is None` | 「読めたら何でも入れる」実装なら 3 件以上で落ちる |
| 3 | `test_http_error_detail_is_truncated_to_exact_bound` | `message` が 1,000 文字 | `len(detail) == 256`（**exact**） | 255 / 257 では落ちる。境界を literal で固定 |
| 4 | `test_http_error_detail_replaces_control_characters` | `message` に `\n` `\t` `\x00` | detail に非 printable が 0 個、かつ長さが保存される | 単なる `strip()` 実装では落ちる |
| 5 | `test_backend_error_detail_requires_http_status_code` | `LLMBackendError(CONNECT_FAILED, backend_error_detail="x")` | `ValueError` | 他 code で detail を持てる実装は落ちる |
| 6 | `test_backend_error_detail_rejects_non_str_and_oversize` | parametrize: `1` / `True` / `""` / 257 文字 | `TypeError` または `ValueError` | 空文字を許す実装は落ちる |
| 7 | `test_http_error_detail_reaches_generation_record_v2` | backend が detail 付きで失敗 → brain 経由 | V2 record の `backend_error_detail` が **同一 literal**、V1 record には field が無い | V1 を変更する実装は落ちる |
| 8 | `test_http_error_detail_never_appears_in_stdout_or_stderr` | detail に sentinel `"synthetic-provider-secret"` を含む 400 | `capsys` の out/err に sentinel が **0 回** | 診断を print する実装は落ちる |
| 9 | `test_collector_accepts_v2_record_with_error_detail` | detail 付き V2 record を collector へ | 例外なく受理し、population から除外し件数へ計上 | field 集合検証を更新し忘れると落ちる |

**8 が privacy の要である。** detail を追加する変更は、公開面へ漏らさないことを
同時に証明しなければ入れてはならない。

### 4.2 P2 — `tests/test_phase5_local_smoke.py`

| # | test 名 | 入力 | PASS 条件 | negative boundary |
|---|---|---|---|---|
| 10 | `test_provider_command_line_is_redacted_before_hashing` | secret token を含む合成 command line | redacted 文字列に生 token が 0 回。hash は redacted 文字列に対して計算される | 生文字列を hash する実装は落ちる |
| 11 | `test_provider_command_line_hash_is_stable_and_order_sensitive` | 同一 line 2 回 / 引数順を入れ替えた line | 同一 → hash 一致。順序違い → hash 不一致 | 正規化しすぎて差分が消える実装は落ちる |
| 12 | `test_public_preflight_record_contains_hash_not_command_line` | 合成 preflight 記録を生成 | 公開側に hash があり、生 command line は **0 回** | 公開へ本文を出す実装は落ちる |

### 4.3 P3 — `tests/test_phase5_local_smoke.py`

すべて合成 status dict を入力とし、実 process を起動しない。

| # | test 名 | 入力 | PASS 条件 | negative boundary |
|---|---|---|---|---|
| 13 | `test_early_stop_triggers_on_nine_identical_failures_twice` | 9 client すべて `decisions=0` / `failures=5` / `last_error_code="HTTP_STATUS"`、2 回連続 | 停止要求が **1 回だけ**発行され、`early_stop` に `error_code` と `failures_per_client` が記録される | 1 回目の polling では発行しない |
| 14 | `test_early_stop_does_not_trigger_when_any_client_decided` | 8 client 失敗 + 1 client `decisions=1` | 停止要求 **0 回** | 「多数決」実装は落ちる |
| 15 | `test_early_stop_does_not_trigger_on_mixed_error_codes` | 9 client 失敗だが `last_error_code` が 2 種類 | 停止要求 **0 回** | 原因が複数ある run を早期停止しない |
| 16 | `test_early_stop_threshold_is_exact` | parametrize `failures = 4` / `5` | 4 → 停止しない。5 → 停止する | 閾値を literal で固定。`>` と `>=` の取り違えを検出 |
| 17 | `test_early_stop_does_not_change_verdict_or_skip_cleanup` | 早期停止を発火させた合成 run | `game_end=false`、B01 相当の判定は FAIL のまま、owned cleanup が実行され `early_stop` は判定入力に**使われない** | 早期停止を PASS/成功へ読み替える実装は落ちる |
| 18 | `test_early_stop_preserves_raw_and_does_not_retry` | 同上 | 原本保全が実行され、追加の game / retry が **0 回** | 停止後に再試行する実装は落ちる |

**17 が acceptance の要である。** 早期停止は判定を変えないことを test で固定する。

### 4.4 合計

**18 test。** 全件 deterministic、実 provider / GPU / 実 game 0、
`completion` marker なし。既存 3 module に追加するため新 batch を作らない。

---

## 5. 実施順序の提案

1. **P1 を実装し 1–9 を通す。** これ無しで再実行しても T330 と同じ結果になる
2. **P2 を実装し 10–12 を通す。** T316 と T330 の provider 構成差を確定させる
3. **provider 側ログで 400 の理由を確認する**（F008 の指示どおり。P1 と独立に実施可）
4. **P3 を実装し 13–18 を通す**
5. 独立 Tester が 3 module の focused batch を実行、独立 Reviewer が審査
6. **そのうえで再実行の承認可否をユーザーが判断する**

**1–5 はすべて実 LLM を使わない。** 実行承認の消費は 0 である。

## 6. この提案が扱っていないこと

**R10-F-A（schema 適合率 23.5%、`VALUE_NOT_OFFERED` 13 件）は本提案の対象外である。**
これは診断可能性ではなく製品そのものの問題で、
schema を緩める / prompt を変える / model を変える のいずれもユーザーの product 判断を要する。
P1–P3 はその判断に必要な**情報を得られる状態にする**ための修正であって、
F-A を解決するものではない。

また R11-N1（generation 妥当性率を観測する B 項目が無い）も別件である。
B10 の `artifacts_fields` に generation status 内訳を足す案を R11 に記した。
**Stage B cases の改訂にあたるため、本提案の focused test とは別経路**で扱うこと。
