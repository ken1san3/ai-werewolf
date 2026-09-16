# Phase 6 sampler 構成・失敗時証拠回収の限定詳細設計

Status: DRAFT — 独立 Reviewer 承認待ち
Task: T352
Responsibility: Architect
Implementation authority: 本文だけでは付与しない

## 1. 目的と境界

T347/T348 が分離した次の二つの欠陥だけを修正対象とする。

1. llama.cpp b10697 (`093adb242`) の Qwen3.5 specialized chat 経路で、`reasoning_format=none` の JSON Schema grammar に chat template の `<think>` generation prefix が投入され、sampler 初期化が HTTP 400 になる構成不整合。
2. `scripts/run_phase5_local_smoke.py::_run_game` の completion wait が例外となったとき、owned process の停止後に残っている status/result/admission 原本を読まず、実 provider call が存在しても返却 row が `raw_metrics=[]`、集計が `provider_calls=0` 相当になる回収漏れ。

structured output、動的 JSON Schema、response の schema 検証、API key と private evidence の境界、ゲームロジック、AI 戦略、既存 timeout、retry/停止 policy、P3 保留は変更しない。provider の起動・停止・probe・game・model/GPU load、汎用 extension framework、無関係な refactor は本設計外とする。

## 2. 確認済みの実装事実

- `OpenAICompatibleBackend._request_payload` は現在 `response_format.type=json_schema` と要求ごとの `output_schema` を送り、provider 固有の reasoning/template 値を送らない。
- 同版 llama.cpp の `server-common.cpp` は request body の `reasoning_format` を request 単位で解釈し、`chat_template_kwargs.enable_thinking` を真偽値として解釈する。
- 同版 `common/chat.cpp` の Qwen3.5 対応経路は、`reasoning_format != none` の場合だけ `<think>...</think>` を response-format grammar の任意 reasoning 部として含め、その後の content に JSON Schema を課す。
- T348 が照合した保存 template は `enable_thinking=false` でも空の think block を generation prefix に残す。したがって `enable_thinking=false` 単独では不十分である。
- `reasoning_format=deepseek` は同版が受理する非 `none` 値であり、reasoning を `reasoning_content`、schema 対象を `message.content` として分離する。既存 backend は `message.content` の非空文字列だけを `StructuredGenerationResponse.text` に採用するため、公開 response contract を変えず JSON content を受け取れる。
- `_run_game` は正常経路で三つの completion wait（server result、9 client status、broker result）の後にのみ status/result/admission を読む。例外経路では `finally` の owned cleanup 後にも再読込しない。
- `_write_phase6_evidence` は既存 manifest の上書きを拒否し、shard identity、generation/terminal、server accepted text の対応を既存 `_phase6_semantic_population` で検証してから terminal manifest を原子的に公開する。

参照ファイルとセッション開始時 SHA-256 は `logs/t352-design/source-evidence.json` に記録する。private 原本や locator は参照していない。

## 3. Defect 1: llama.cpp 専用 structured-output 設定

### 3.1 型と既定互換

`ai_client/llm/types.py` に次の frozen value object を追加する。

```python
@dataclass(frozen=True)
class LlamaCppStructuredOutputConfig:
    reasoning_format: Literal["deepseek"] = "deepseek"
    enable_thinking: Literal[False] = False
```

`__post_init__` は値と型を厳密に検証する。`False == 0` のような coercion を許さず、現修正では `deepseek` と真偽値 `False` の組だけを受理する。任意 JSON mapping や任意 top-level provider field は導入しない。

`OpenAICompatibleBackendConfig` と `LocalLLMSettings` に次を追加する。

```python
llama_cpp_structured_output: LlamaCppStructuredOutputConfig | None = None
```

`None` が従来の provider-neutral 既定である。`None` の場合、request body のキー、dataclass の secret 取扱い、response contract、および従来値から計算される `config_fingerprint` を一切変えない。これにより、llama.cpp が対応するか不明な OpenAI-compatible provider へ unsupported option を送らない。

### 3.2 request wire contract

`OpenAICompatibleBackend._request_payload` は `llama_cpp_structured_output is not None` の場合に限り、既存 body へ次の二キーを追加する。

```json
{
  "reasoning_format": "deepseek",
  "chat_template_kwargs": {
    "enable_thinking": false
  }
}
```

既存の `response_format.type=json_schema`、`strict=true`、動的 `schema` はそのまま必須とする。`json_object` への切替、grammar/schema の削除・緩和、prompt への JSON 指示の移動は禁止する。`reasoning_effort` は追加しない。

この組合せでは、template が残す空 `<think>...</think>` を grammar 側が reasoning 部として受理し、その後の content だけに既存 JSON Schema を適用する。backend は引き続き `choices[0].message.content` を返し、`reasoning_content` を prompt、audit response text、decision 入力へ混入させない。

### 3.3 fingerprint と process bootstrap

`OpenAICompatibleBackendConfig.config_fingerprint` は、設定が非 `None` のときだけ次の非秘密値を canonical input に追加する。

```json
"llama_cpp_structured_output": {
  "enable_thinking": false,
  "reasoning_format": "deepseek"
}
```

`None` の場合はこのキー自体を fingerprint input へ追加しない。これにより既存 default fingerprint の byte-for-byte 互換を維持し、修正 profile の有無または値が違えば fingerprint が必ず異なる。API key は従来どおり fingerprint、`repr`、比較の対象外である。

`LocalLLMSettings.backend_config()` はサブ設定をそのまま渡す。Phase 6 の `_prepare_run` は、既存の exact model identity 検査と同じ境界で `LlamaCppStructuredOutputConfig()` を明示設定する。非 Phase 6、`LocalLLMSettings.from_env()`、他の OpenAI-compatible 利用は `None` のままとし、新しい環境変数や CLI option は設けない。

`_broker_bootstrap` は profile が非 `None` の場合だけ、秘密を含まない次の exact object を child bootstrap に載せる。

```json
"llama_cpp_structured_output": {
  "reasoning_format": "deepseek",
  "enable_thinking": false
}
```

`_phase5_broker_settings` は、キー欠落を legacy/default の `None` として扱う。キーがある場合は object、exact key set、exact string、型が厳密な `false` を検証して value object を再構築する。部分 object、余分なキー、文字列 `"false"`、数値 `0`、unknown reasoning format は child/backend 作成前に `ValueError` とする。既存の parent/backend fingerprint 照合が profile の欠落・改変も拒否する。

### 3.4 lifecycle と失敗分類

profile は request payload 構成だけを変える。backend の zero-retry、HTTP status/retryable 分類、provider quiescence、admission broker、timeout、cleanup は変更しない。修正後 live HTTP 200 と schema-valid content は本設計・offline test の完了条件ではなく、別途許可された有限 live 検証の未実施条件として残す。

## 4. Defect 2: completion wait 例外後の回収

### 4.1 適用点

回収対象は Phase 6 `_run_game` の次の三つの `_wait_for_paths` 呼出しで例外が発生した場合だけとする。

1. `server.result.json` wait
2. 9 client `*.status.json` wait
3. `broker.result.json` wait

readiness wait、spawn、relay、fingerprint 検査、通常 evidence write、Phase 5/Q8 は対象外とする。各 completion wait の直前にローカルな stage identifier を設定し、正常 return 直後に解除する。`except` は例外発生時の stage を保存し、現行どおり primary error の型名を `errors` の先頭へ一度だけ追加する。例外 message や private path/content は公開 row へ追加しない。`CancelledError` の `CANCELLED` 追記も維持する。

### 4.2 cleanup barrier

例外後は現行順序を守る。

```text
primary wait error
  -> stop files を touch
  -> _cleanup_owned_shielded を await
  -> 全 owned entry が返り、alive がすべて false と確認
  -> 既存原本を recovery read
  -> 可能なら既存 writer で evidence 化
  -> row/summary を失敗として返す
```

回収を cleanup より前または並行には行わない。cleanup が例外となった、返却 entry 数が `owned` と一致しない、または一件でも `alive=true` の場合は原本がなお変化し得るため回収を開始せず、primary error を保ったまま `PHASE6_RECOVERY_CLEANUP_INCOMPLETE` を追記する。provider PID は owned collection に入れず、外部 provider を操作しない。

completion wait の primary error を捕捉した後は、`_touch_stop` と `_cleanup_owned_shielded` を別々の `BaseException` 境界で実行する。stop touch が失敗しても owned cleanup の試行は省略しない。ただし stop touch または cleanup のどちらか一方でも例外となった場合、cleanup result の検査が例外となった場合、result が sequence/mapping として不正な場合、件数が `len(owned)` と一致しない場合、または一件でも `alive is not False` の場合は cleanup barrier 不成立とする。二次例外の型名、message、path は保存せず、固定 code `PHASE6_RECOVERY_CLEANUP_INCOMPLETE` を一度だけ後置する。

cleanup field は、現行 `_settle_owned` が返す各 entry の `label`、`pid`、`returncode`、`alive`、`action` が expected owned process と型・値まで照合できた場合だけ実測 row を返す。cleanup helper が例外を返した場合や result が malformed の場合は公開 fallback を `[]` とし、停止済み entry を補作しない。stop touch だけが失敗し cleanup result 自体が正しく検証できた場合は、その実測 cleanup row を保持してよいが、recovery read/write は行わない。

### 4.3 recovery read の契約

専用の小さな helper（例: `_recover_phase6_wait_failure_evidence`）は新 framework にせず、`_run_game` が既に保持する `ai_dir`、`metrics_path`、9 status paths、server/broker result paths、`player_to_client` と現在値を受け取る。各既存ファイルだけを `_read_json` / `_read_jsonl` で読む。ファイルを待たず、新規 raw/status/result を作らず、子 process を再起動しない。

- 読めた各 client status は `statuses` に採用する。manifest 候補には exact 9 player set が揃った場合だけ使う。
- 読めた `server.result.json` / `broker.result.json` は各 result に採用する。
- 読めた `admission.jsonl` は空であっても実原本として `metrics` に採用する。欠落・malformed の場合に空 list を「実 call 0」の証拠として補作しない。
- source ごとの欠落・malformed は、値や path を含めない固定 code（`PHASE6_RECOVERY_CLIENT_STATUS_INCOMPLETE`、`PHASE6_RECOVERY_SERVER_RESULT_UNAVAILABLE`、`PHASE6_RECOVERY_BROKER_RESULT_UNAVAILABLE`、`PHASE6_RECOVERY_METRICS_UNAVAILABLE`）を一種類一回だけ `errors` へ追記する。
- helper 自身の予期しない例外も primary error を置換せず、型名を値に含めない固定 code `PHASE6_RECOVERY_FAILED` とする。

回収した `metrics` は失敗 row の `raw_metrics` と `_metric_aggregates(metrics)` に必ず使用する。これにより、原本に 107 provider call があれば summary も 107 を数え、原本を読めなかった場合は `PHASE6_RECOVERY_METRICS_UNAVAILABLE` により unknown/incomplete と分かる。単なる空集計を成功した実 call 0 と解釈してはならない。

### 4.4 manifest の発行条件

回収時に新しい「部分 manifest」schema は作らない。次が全て成立する場合に限り、既存 `_write_phase6_evidence(ai_dir, statuses, metrics_path, player_to_client, server_result)` を一度だけ呼ぶ。

- `ai_dir` と `metrics_path` が既に確定している。
- exact 9 player status が全て既存ファイルから正常に読めた。
- metrics 原本が正常に読めた。
- server result が正常に読め、`accepted_text` を持つ。
- `broker.result.json` が JSON object として正常に読めている。`success` や `shutdown_clean` などの値の合否はここでは判定せず、既存 `_validate_game_evidence` に委ねる。
- `ai_dir/manifest.json` が存在しない。

既存 writer の shard/identity/semantic/accepted-text 検証を迂回しない。broker result が欠落または malformed の場合は `PHASE6_RECOVERY_BROKER_RESULT_UNAVAILABLE` を後置し、writer を呼ばない。writer が `Phase6PopulationExceeded`、`ValueError`、`FileExistsError`、I/O error のいずれかを返した場合は terminal manifest を補作せず、元の wait error を先頭に残し、`PHASE6_RECOVERY_MANIFEST_REJECTED` を一回追記する。既存 manifest がある場合は broker result の可否とは別に、削除・上書き・補作せず現行 validator の対象として保持する。

回収 writer が成功しても、primary wait error が存在するため `success=false`、`machine_semantic_pass=false` は不変である。manifest の存在は game/run acceptance を意味せず、通常の `_validate_game_evidence` を実行して得た不足も errors に追加する。`errors` がある run を accepted summary に昇格させない。

### 4.5 返却と例外優先順位

- `errors[0]` は常に元の completion wait 例外の現行 sanitized 型名。
- `CancelledError` の場合はその次に `CANCELLED`。
- stop touch、cleanup、cleanup result 検査、recovery read、writer、validator、aggregate、artifact 収集の全てを、primary 捕捉後の独立した二次 `BaseException` 境界に置く。これらの問題は固定 secondary code として後置する。
- 二次失敗は primary を raise/return から消さない。exception message、audit 本文、API key、private locator は row や公開 summary に含めない。
- recovery manifest が存在する場合の `_validate_game_evidence` が例外を返したときは `PHASE6_RECOVERY_VALIDATION_UNAVAILABLE` を後置する。validator の例外型・message・path は公開しない。validator が通常どおり `list[str]` を返した場合だけ、その既存 sanitized findings を後置する。
- `_metric_aggregates(metrics)` と `_speaking_aggregates(statuses, server_result)` は return dict の式内で直接呼ばず、失敗 row の組立前にそれぞれ例外境界内で計算する。metric aggregate が失敗した場合は固定 code `PHASE6_RECOVERY_METRIC_AGGREGATE_UNAVAILABLE` と公開 fallback `{}`、speaking aggregate が失敗した場合は `PHASE6_RECOVERY_SPEAKING_AGGREGATE_UNAVAILABLE` と公開 fallback `{}` を使う。空 metrics を `_metric_aggregates(())` へ差し替えて `provider_calls=0` を補作しない。
- `artifacts` は cleanup と recovery 後に `_artifact_hashes(root)` で計算する。`rglob`、列挙順序化、`is_file`、suffix/relative path、`stat`、file read/hash のいずれか一件でも例外となった場合は、不完全な hash 集合を返さず固定 code `PHASE6_RECOVERY_ARTIFACT_HASH_UNAVAILABLE` と公開 fallback `[]` を使う。例外型・message・失敗 path は公開しない。
- server/broker の公開 projection も型を確認してから組み立てる。欠落・malformed 値は一律 `None` へ落とし、実測していない count `0`、private raw 値、例外本文、path を fallback にコピーしない。`raw_metrics` は従来どおり内部集約用であり、公開 summary へは `_strip_raw` を経由させる。
- 上記安全化は completion wait primary を持つ Phase 6 failure branch だけへ適用し、通常成功、readiness failure、Phase 5/Q8 の既存返却契約を変更しない。
- error 32件上限は維持する。固定 code を source 種別ごとに重複排除するため primary が切り落とされない。

## 5. 必須 test 契約（全て no-network）

### 5.1 backend/config focused regression

1. default `OpenAICompatibleBackendConfig` の request bytes と既存 expected payload が修正前と完全一致し、`reasoning_format` / `chat_template_kwargs` がない。
2. llama.cpp profile 有効時も `response_format.type=json_schema`、`strict=true`、要求 schema が維持され、追加二キーが exact value/type で一回だけ入る。
3. mock HTTP 200 が `message.content` に schema-valid JSON、別 field に `reasoning_content` を返しても、`StructuredGenerationResponse.text` は content の JSON だけとなる。
4. value object と両 config は unknown reasoning、`True`、`0`、`"false"`、不正型を拒否する。
5. default profile の fingerprint は修正前 fixture/golden input と一致する。profile 有効/無効は異なり、同一 profile は安定し、API key 差は影響しない。
6. `LocalLLMSettings.backend_config()` が profile を lossless に渡し、`repr` に API key を出さない。

### 5.2 runner/bootstrap focused regression

7. `_prepare_run(--phase6, exact model, explicit existing timeouts)` は exact llama.cpp profile を持ち、非 Phase 6 は `None`。
8. parent -> `_broker_bootstrap` -> `_phase5_broker_settings` の round trip で profile と backend fingerprint が一致する。
9. bootstrap の profile 欠落（Phase 6 新規 run）、部分値、extra key、型違い、値改変は backend/network 起動前に拒否される。一方、非 Phase 6 legacy bootstrap のキー欠落は従来設定を再構築する。

### 5.3 wait failure recovery regression

10. 三つの completion wait の各々へ `TimeoutError` を注入し、event 順が `wait error -> stop touch -> cleanup complete -> recovery reads -> evidence/return` である。
11. cleanup 後に揃う既存 9 status、server/broker result、admission metrics を回収する case で、既存 `_write_phase6_evidence` が一回だけ呼ばれ、manifest validation が通るが、row は元の `TimeoutError` により失敗のままである。
12. admission 原本に 107 call identity を持つ synthetic metrics を置く case で、`len(raw_metrics)` と `_metric_aggregates` の provider call 数が原本どおりとなり、0へ化けない。
13. status/server/broker/metrics を一種類ずつ欠落または malformed にした table test で、対応する固定 recovery code が残り、recovery manifest writer は呼ばれず、run は失敗する。特に broker result 欠落/malformed では `PHASE6_RECOVERY_BROKER_RESULT_UNAVAILABLE` を後置し、既存 manifest があっても削除・上書き・補作しない。metrics 不明を call 0 の成功証拠として扱わない。
14. `_write_phase6_evidence` を `ValueError` / `Phase6PopulationExceeded` / `FileExistsError` で失敗させても、`errors[0]` は元の wait error、manifest の補作・上書きはなく、secondary code が後置される。
15. `_touch_stop` 例外、`_cleanup_owned_shielded` 例外、cleanup result の非 sequence・非 mapping・key/type/value 不正・件数不一致・`alive=true` を個別に注入する。全 case で row が返り、primary が先頭、`PHASE6_RECOVERY_CLEANUP_INCOMPLETE` が一回、recovery read/write がゼロである。cleanup helper 例外または malformed result の公開 `cleanup` は `[]` で、clean entry を補作しない。stop touch 例外でも cleanup 自体は一回試行する。
16. `CancelledError` case は shielded cleanup 完了後も `CancelledError`, `CANCELLED` の順を保持し、stop/cleanup/recovery の二次失敗がそれらを置換しない。
17. `_validate_game_evidence` の例外を注入しても row が返り、primary と `PHASE6_RECOVERY_VALIDATION_UNAVAILABLE` を保持し、例外本文・型名・path を公開しない。
18. `_metric_aggregates` と `_speaking_aggregates` の各例外を個別および同時に注入し、row が返ること、primary 順序、対応する固定 code、各公開 fallback `{}`、`provider_calls=0` の非補作を検証する。
19. `_artifact_hashes` の `rglob`、`is_file/stat`、file read/hash の各 failure を注入し、row が返ること、primary 順序、`PHASE6_RECOVERY_ARTIFACT_HASH_UNAVAILABLE`、公開 `artifacts=[]`、部分 hash entry の非公開を検証する。
20. malformed server/broker projection と、cleanup/aggregate/artifact の例外へ private sentinel を混ぜても、公開 row/summary の errors、fallback、projection に sentinel、例外 message/type、API key、private locator/path が現れない。
21. 正常 completion、readiness/fingerprint failure、Phase 5/Q8 の既存 test では recovery helper と failure fallback が呼ばれず、成功 manifest の一回発行と既存集計が変わらない。
22. failure row を `_strip_raw` した公開 summary には recovered `raw_metrics` と opaque client 値がなく、固定 error code と safe aggregate/artifact fallback だけが残る。

実装者は少なくとも関連 focused suite（`test_phase4_llm_backend.py`、`test_phase4_llm_contracts.py`、`test_phase5_local_smoke.py`、`test_phase6_semantic_completion.py`）、関連 regression、`python scripts/check_docs.py` を実行し、raw command/exit/PASS 数を handoff に記録する。実 provider/model/network を使う test は本 gate に含めない。

## 6. 実装対象と禁止事項

想定される最小変更対象は次だけである。

- `ai_client/llm/types.py`
- `ai_client/llm/config.py`
- `ai_client/llm/backend.py`
- `scripts/run_phase5_local_smoke.py`
- 上記契約に対応する既存 test files

ゲーム core、protocol/schema、discussion strategy、content YAML、provider process、旧 raw/private evidence、board/state、既存 freeze は変更しない。同じ目的を持たない T349/T350 の実績を補作しない。

## 7. 承認後の受入れと残存条件

本設計は Architect 作成物であり自己承認しない。実装開始前に新規独立 Reviewer が、次を確認して明示承認する必要がある。

- `deepseek` + `enable_thinking=false` が保存 upstream source の Qwen3.5 grammar/content 分離と一致すること。
- default request bytes と default fingerprint の互換が維持されること。
- recovery が三 completion wait と cleanup 後に限定され、部分 evidence を accepted manifest にしないこと。
- primary error、privacy、structured output、停止 policy、P3 保留が維持されること。

実装後は独立 Tester と fresh Reviewer が no-network test と scoped diff を確認する。live HTTP 200/schema-valid、最小 AIwolf provider path、追加 game/admission は未実施として明記し、別の明示許可・保全・有限条件なしに実行しない。
