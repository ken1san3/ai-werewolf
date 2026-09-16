# Phase 6 R12 P1/P2 診断保全 詳細設計

Status: DRAFT

T335 Architect作成。独立Reviewer承認前は実装不可。

## 1. 範囲

R12/F008に対するP1/P2限定修正である。P3は完全に除外する。早期停止、runner lifecycle、B01–B11判定、Master catalog/Test ID、clock、model、512/200/600、ACL/TEMP、旧raw、旧FAIL/UNKNOWNは変更しない。本設計ではproduct/test実装、実provider/game、CIM、pytest、processor実行を行わない。

## 2. P1: broker所有のprivate detail

### 2.1 最小経路と互換性

backendとadmission brokerは同一processにある。非2xx本文から抽出したdetailは `LLMBackendError` の内部属性としてbrokerの `_record_call` までだけ渡し、明示的に設定されたowner-only metrics sinkの `PROVIDER_CALL_TERMINAL` recordへ保存する。private sink未設定時は保存しない。clientへは送らない。

これにより `GENERATION_IPC_PROTOCOL`、`ERROR` frameのexact key集合/canonical bytes、clientが復元する既存4属性、Brain/client status/通常auditを変更しない。`AiDiscussionGenerationRecord` V1と現行V2（`prompt_rejection_code`を持つ）のdataclass、serializer、collector、private reviewerも変更せず、旧V1/既存V2 bytes/hashを完全維持する。新artifactや新frameworkは作らない。

### 2.2 抽出契約

`LLMBackendError` に `backend_error_detail: str | None` を追加する。`str(error)`/`repr(error)` はstable codeだけでdetailを含めない。detailはHTTP_STATUS時だけ、exact `str`、1–256 code point、全character printableとする。違反はTypeError/ValueError。

backend pure helperは既存上限内まで完全drain済み `encoded: bytes` にだけ適用する。

1. strict UTF-8 decode。
2. duplicate key/non-finite拒否JSON decode。失敗、再帰上限、top-level非object、`error`非objectは`None`。
3. `error.type/code/message` だけを見る。他keyはcopyしない。
4. type/messageは非空str、codeはboolを除くintまたは非空strだけ採用。
5. 固定順 `type`, `code`, `message` の `key=value` を空白結合。value内の非printableは半角spaceへ1対1置換。
6. 先頭256 code pointへ切り、空なら`None`。本文全体fallbackや例外本文への埋込みは禁止。

empty/invalid UTF-8/JSON/depth/shapeは`None`。既存response上限超過は従来どおりRESPONSE_TOO_LARGEで、partial bytesをHTTP detailにしない。HTTP_STATUS以外、synthetic broker error、queue errorはdetailなし。

provider messageはprompt/schema/path/credentialをechoし得る。detailはredactedでも安全でも公開可能でもない **bounded untrusted private input** である。3-key/256文字は量と形の制限であり秘密除去ではない。実応答が必ず診断可能になる保証はなく、古いrawへ補作しない。

### 2.3 private sinkと全surface境界

`AdmissionMetric` に `backend_error_detail: str | None = field(default=None, repr=False)` を追加し、`event=PROVIDER_CALL_TERMINAL`かつ`backend_code=HTTP_STATUS`だけ許す。`repr(metric)` にdetailを含めない。`_source_error_is_valid` も不変条件を再検証する。不正なら従来どおりpoisonしdetailを出さない。`serialize_admission_metric` は固定keyとしてowner-only `admission.jsonl` へserializeする。consumer current/abandonedを問わず、実provider terminalの既存一recordへ同値を渡す。

opt-in入口は `AdmissionMetrics(capacity, *, handle: BinaryIO | None = None)` とする。現行 `path=` は既存呼出し互換のためmetadata-only用途に残すが、`backend_error_detail` の永続化は `handle` 注入時だけ許し、`path` と `handle` の同時指定はValueErrorとする。in-memory `records` にもdetailを残すのはhandle注入時だけで、未設定時はrecord前にdetailを`None`へprojectionする。

Phase 6 broker childの具体入口は `_broker_child` である。実在する `scripts.phase6_private_review._locked_path(args.metrics, create=True)` で `admission.jsonl` をexclusive createし、全ancestorをpinしたまま、fdを `os.fdopen(fd, "ab", closefd=False)` で包み `AdmissionMetrics(..., handle=handle)` へ渡す。`_locked_path` は `_windows_private_path(component, handle)` でopened objectのowner/DACLを検証する。chmod、path文字列、bootstrap booleanをprivate証明に使わない。終了順は `await metrics.aclose()`、wrapperの明示 `flush()`/`os.fsync()`/`close()`、その後 `_locked_path` context終了によるfd/ancestor handles closeとする。各段の失敗後もfinallyで後続closeを行い、最初のstable failure codeだけ返す。ai_clientからscriptsへの依存は追加しない。

現行writerはserialize/write例外時に当該itemの `task_done()` がなく、`flush()`/`aclose()` が待ち続け得る。最小修正としてprivate `_writer_error: AdmissionMetricsWriteError | None` を持ち、各 `get()` を `try/finally: task_done()` で必ず対応づける。serialize/write/fsync失敗時はpayload/detailを含まないstable errorを保存し、残queueを `get_nowait()`/`task_done()` で有限drainして終了する。`record_nowait` はclosing/closed、writer終了、errorのいずれでもfalseとする。`flush` はwriter終了/errorを先に検査してraiseする。正常 `aclose` は最初にclosingへ遷移して新規recordを拒否し、`await queue.join()`、writer error確認、空queueへのSTOP `put_nowait`、writer awaitの順とする。正常closeで受理済みmetricをevictせず、`metrics_dropped`も増やさない。writerが既に失敗終了していれば有限drain済みのerrorを即raiseし、STOPを追加しない。注入handleはwriterがcloseしない。新timeout/task/lifecycle frameworkは作らない。

`_locked_path` のexclusive create/owner-DACL/ancestor検証失敗、queue full、sink write/flush/shutdown失敗ならbroker childは既存failure/`metrics_dropped`を維持し、detailをstdio、例外、別file、再送へfallbackしない。既存fileへの`ab`追記はPhase 6 private detailでは禁止する。通常library利用や他modeで明示handleがない場合はdetailを保存しない。

`_metric_aggregates`、broker result、client status、server result、summary、manifestの公開projection、failure summary、handoffはdetailを選択しない。AdmissionMetricへの汎用 `asdict()` をpublic生成に使わず、既存allowlist projectionだけを使う。actual parent `_raw_records` はadmission dictをowner-only run rootの `raw.jsonl` に複製するため、detailは既存private `admission.jsonl` と `raw.jsonl` の2 artifactへ保持される。追加artifactは作らず、`_strip_raw` 後の公開戻り値には残さない。private processorは既存manifest/hashでprivate artifact完全性を検査するが、detailをaggregate/public outputへcopyしない。

公開可能なのは従来のbackend code/status/retry/quiescenceと件数だけ。detail/detail由来hashはIPC、exception/repr、stdio、Brain/generation V1/V2、public JSON/handoffへ禁止する。synthetic secret echoはprivate metricにexact保存され、全public sinkで0件を検証する。

## 3. P2: provider command line観測

T328の `command_ngl99=true`、`command_context8192=true`、`command_jinja=true` は既存観測として維持する。gapはそれ以外のargvとprovider process envの同一性を保存記録で検証できない点だけである。

製品コードは変更せず、`scripts/run_phase5_local_smoke.py` に小さいread-only Windows observation helperを置く。現sourceにcommand line/argv adapterは存在しないため、既存adapterを前提にしない。providerの起動/停止/再起動はしない。

`_observe_windows_process_command_line(pid: int) -> ProviderCommandObservation` はboolを拒否する正のPIDだけを受ける。`shutil.which("powershell.exe")` で得た実行fileを使い、`shell=False`、stdin/stderr DEVNULLの固定list argvで一回起動する。固定PowerShell scriptはUTF-8の単一JSON object `{status, command_line_base64}` だけをstdoutへ出し、exit 0=OBSERVED、10=PROCESS_NOT_FOUND、11=ACCESS_DENIED、12=EMPTY、13=CIM_ERRORとする。command lineはPowerShell内でUTF-8 strict bytesへしてbase64化する。stderrや例外本文を出力契約に使わない。

`communicate()` 後のsize判定は禁止する。専用reader threadがstdoutを4096-byte chunkで読み、最大131072 bytesだけ保持し、超過後はEOFまでdiscardしてOVERSIZEを立てる。mainは2秒でprocessとreaderを待ち、未終了ならterminate、さらに1秒、なお未終了ならkill、さらに1秒でprocessとreaderをjoinする。terminate/kill/pipe closeは各exact一回、全handle closeはfinally。cleanup後もreaderが終了しない場合はOS_ERRORとしてargv/hash nullにし、成功扱いしない。JSON exact keys/types、exit/status一致、base64 strict decode、decoded上限を検証し、不一致/oversizeはPARSE_FAILED。取得のメモリと時間は有限である。

`_windows_command_line_to_argv(raw: str)` はこのhelper内だけの小さい `ctypes` wrapperとして `shell32.CommandLineToArgvW` と `kernel32.LocalFree` を呼ぶ。NULを拒否し、rawは32767 UTF-16 code units以下、argc 1–256、各argv 8192 UTF-8 bytes以下、canonical argv総量65536 bytes以下に制限する。API failure、上限超過、LocalFreeを含むcleanup失敗はPARSE_FAILEDでargv/hashを残さない。独自quote parserや `shlex` はWindows規則と異なるため使わない。このFFIはargv分解だけで、ACL/private判定には使わない。

raw command lineはCIM取得後に上記Windows parserへ直結し、分解/redaction成功前はfile/log/exception/stdioへ書かない。CLI secret grammarは `_REMOVED_CHILD_SECRET_KEYS` を流用しない（これはrunner child環境削除用のまま）。case-insensitiveな別固定集合/segment `key/token/secret/password/passwd/authorization/credential` を用いる。

- `--flag value` は次argvを`<redacted>`。
- `--flag=value` は`--flag=<redacted>`。
- quoted valueはadapter分解後の一argvとして同様。
- `/flag:value` は`/flag:<redacted>`。

secret value欠落はREDACTION_INVALID。未知flag valueにBearer、URI userinfo、明白なsecret key/valueを検出した場合はREDACTION_BLOCKED。どちらも全argv/hashを捨て、raw入力をerrorへ含めない。

取得状態は `OBSERVED / ACCESS_DENIED / PROCESS_NOT_FOUND / EMPTY / PARSE_FAILED / REDACTION_INVALID / REDACTION_BLOCKED / OS_ERROR` のclosed enum。OBSERVED以外はargv/hash null。既存booleanからhashを合成しない。provider envは既存adapter/CIMでは取得できないため取得せず `environment_observation=NOT_OBSERVABLE`。runner env/child scrub/ユーザー申告を代理にしない。

private `provider-observation.json` に `command_observation`、`command_argv_redacted`、`command_canonical_sha256`、`environment_observation` を追加し既存3 booleanを残す。canonical bytesはredacted argv arrayのUTF-8 JSON（ensure_ascii=false, sort_keys=true, separators comma/colon, allow_nan=false）。公開handoffはstateとhash/nullだけで、raw/redacted command/argvを出さない。

hash一致が保証するのはcanonical redacted argv bytes一致だけ。隠したsecret値、provider env、展開前文字列、binary/file identity、意味同一性は保証しない。同じ分解後argvならquote表現差は同hash、argv順差は異hash。旧T316/T330 argvを復元せず、過去記録へhashを補作しない。

## 4. focused test契約

原提案1–12の目的を実broker/private/public/互換境界まで具体化する。番号は新Master Test IDではない。全件syntheticで実provider/GPU/game/CIMなし。

| # | 実装先 / test名 | 入力 | exact PASS条件 |
|---|---|---|---|
| 1 | `test_phase4_llm_backend.py::test_http_error_detail_extracts_exact_literal_from_llamacpp_body` | MockTransport 400 + llama.cpp shape | detail literal `type=invalid_request_error code=400 message=invalid grammar`、既存status/retry/quiescence |
| 2 | 同 `::test_http_error_detail_is_none_for_unexpected_shapes` | empty、invalid UTF8/JSON、duplicate、NaN、array、missing/error非object/empty、deep | 全param None |
| 3 | 同 `::test_http_error_detail_is_truncated_to_exact_bound` | 1000字message | len exact 256 |
| 4 | 同 `::test_http_error_detail_replaces_control_characters` | newline/tab/NUL | 非printable 0、該当位置space |
| 5 | `test_phase4_llm_contracts.py::test_backend_error_detail_requires_http_status_code` | non-HTTP+detail、HTTP status欠落 | ValueError、str/reprにdetailなし |
| 6 | 同 `::test_backend_error_detail_rejects_non_str_empty_and_oversize` | int/bool/empty/257 | exact TypeError/ValueError、256受理 |
| 7 | `test_phase5_generation_admission.py::test_http_error_detail_uses_private_broker_metric_not_error_wire` および `test_phase5_brain_admission.py::test_http_error_detail_does_not_enter_brain_generation_v2` | detail付backendを各既存fixtureで1 call | private metric literal、client error既存4属性、Brain V2 fieldなし。ERROR exact keysと実装前literal wire bytes一致 |
| 8 | `test_phase5_local_smoke.py::test_http_error_detail_never_reaches_public_and_writer_failure_is_finite` | detail sentinel、handle正常/未設定/pathのみ/ACL拒否/ancestor置換/queue full/serialize-write-fsync failure | 正常時はadmission.jsonlとactual `_raw_records` raw.jsonlの2 private複製だけ。`repr(metric)`、全public allowlist projectionにliteral/key 0、publicでasdict不使用。各failureで全task_done、flush/aclose有限、stable error、wrapper close後locked context終了、既存追記/新artifact0 |
| 9 | `test_phase6_private_review.py::test_http_detail_preserves_old_v1_v2_bytes_and_private_processing_boundary` | literal旧V1/現V2 bytes/hash + detail付admission | V1/V2 bytes/hash不変、既存collector/processor受理、population/public outputにdetailなし、未知generation version拒否 |
| 10 | `test_phase5_local_smoke.py::test_provider_command_line_is_windows_parsed_and_redacted_before_hashing` | CIM subprocess/CommandLineToArgvWをstub、space/equals/slash/quoted secret | 固定shell=False argv、timeout/byte上限、private redacted argv literal、生secret 0、canonical redacted bytesのliteral hash |
| 11 | 同 `::test_provider_command_line_hash_is_stable_order_sensitive_and_observation_bounded` | 同argv、quote差、secret値差、公開arg順/値差、exit 10–13、timeout、stdout/base64/decoded oversize、NUL/argc/argv/total bound/API failure、旧boolean-only | canonical同一/secret差は同hash、公開順/値差は異hash、全失敗/旧recordはargv/hash null、env NOT_OBSERVABLE、raw/stderr非出力、terminate/kill/reader join/close有限かつexact回数 |
| 12 | 同 `::test_public_preflight_record_contains_only_command_state_and_hash` | OBSERVED/全欠測private record | publicはstate+hashのみ。raw/redacted command/argv/sentinel 0、既存3 boolean保持 |

case 7のwire fixtureは現行 `_encode_frame` のHTTP ERROR bytesを実装前literal固定し、実装後値から期待値を作らない。case 9も旧V1/現V2元bytesとSHA-256をliteral固定する。case 8は各serializer/projection直後のexact key集合も検査する。

## 5. 受入

承認後の実装順はtype/parser、backend、broker private metric、P2 pure helper、12 tests。IPC、Brain、generation V1/V2、新artifact/public schemaが変われば設計逸脱として停止する。

独立Testerは12件と関連6 module（`test_phase4_llm_backend.py`、`test_phase4_llm_contracts.py`、`test_phase5_generation_admission.py`、`test_phase5_brain_admission.py`、`test_phase5_local_smoke.py`、`test_phase6_private_review.py`）、`python scripts/check_docs.py` を実行し、Windows環境、raw command、件数、duration、pass/failを記録する。fresh Reviewerがprivacy、wire、V1/V2 bytes、hash限界を審査する。実provider/gameは受入に含めず、次Stage Bは既存のユーザー直前承認を維持する。
