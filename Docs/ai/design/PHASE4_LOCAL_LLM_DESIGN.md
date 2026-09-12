Status: APPROVED — T019 Architect R2 independently approved on 2026-09-11

# Phase 4 Local LLM — Detailed Design

## Purpose

Phase 3 で完成した一個の `BrainInput -> BrainDecision` 境界へ、外部のローカル LLM を
接続する。Phase 4 は backend abstraction、OpenAI-compatible local HTTP adapter、bounded
prompt projection、strict structured output、自然言語の chat / CO comment、vote / ability
selection、privacy-safe `ai.jsonl`、および一体の実 LLM client の証拠までを担当する。

World / Network の取り込み、server authority、received handle、deadline mapping、request
correlation、shared arbiter は変更しない。belief、suspicion、長期 strategy、九体共有 queue は
後続 phase の責務である。

## Authority and Current Implementation Facts

優先順位は canonical specification、現行 implementation / protocol facts、tests、本設計の順で
ある（D051）。本設計は次の既存契約を前提とする。

- `Brain` は `async decide(BrainInput) -> BrainDecision` 一個だけであり、World / Network object や
  raw protocol payload を受け取らない。
- `BrainInput` は frozen `WorldSnapshot`、coherent action context、bounded history、CO、private
  ability result view からなる。option は request-local `option_id` と受信済み typed handle の組で
  ある。
- `BrainController` だけが subtype、option、target/count/membership、current handle、deadline を
  再検証して typed Network send を行う。LLM の出力は server acceptance ではない。
- `BrainInvocationArbiter` は reservation と reaction を非 preemptive に直列化し、一 process の
  active Brain invocation を最大一個にする。
- `ReactionChatController` と `VoteAbilityController` は current World view と received handles
  だけから opportunity を作り、deadline/correlation/retry lifecycle を所有する。
- current `CoReportAction` は `kind`、target、claimed result の許可集合を列挙しない。したがって
  Phase 4 はそれらの symbolic value を捏造して dispatch しない。
- current dependency set に async HTTP client は無い。初期 adapter は `httpx>=0.27,<1` を runtime
  dependency として追加し、`AsyncClient` の cancellation と streamed response bound を使う。
- D034 と `LOCAL_LLM_SETUP.md` はこの host の optional environment evidence でしかない。model、
  path、port、sampling 値を game responsibility、role、task に結び付けない。

## Scope

- vendor-neutral structured-generation backend protocol と immutable request/response/error values
- loopback OpenAI-compatible `/v1/chat/completions` adapter
- authorized `BrainInput` からだけ作る deterministic and bounded prompt projection
- request ごとの received values で狭めた、一つの discriminated JSON decision schema
- strict JSON parse、schema validation、semantic authorization、および最大一回の repair
- `LLMBrain` による chat / CO-declare comment 生成と vote / ability selection
- bounded and private `logs/<game_id>/ai.jsonl` audit
- backend absence、HTTP failure、timeout、cancellation、invalid output の fail-closed behavior
- fake backend / fake HTTP tests、one-LLM-client offline completion、opt-in real local smoke

## Explicitly Out of Scope

- Phase 5 の九体共有 request queue、batching、GPU admission、throughput / backpressure policy
- Phase 6 の belief、suspicion、credibility、day summary、strategy、deception quality、prompt quality tuning
- role / team / effect / channel ID による Python 分岐、role ごとの model または Brain instance
- remote provider、provider failover、model router、automatic model selection、development-role assignment
- server、game core、content、wire protocol、current action handle の authority変更
- current `CoReportAction` に無い kind/result vocabulary の推測または protocol expansion
- accepted reservation の戦略的 replacement、controller retry/cap/deadline policy の変更
- UI、personality system、typing animation、Autodev、development helper runtime の復元

## Files and Ownership

### New production modules

| Path | Ownership |
|---|---|
| `ai_client/llm/types.py` | immutable backend, prompt, audit, status, config values |
| `ai_client/llm/backend.py` | vendor-neutral protocol and OpenAI-compatible async HTTP adapter |
| `ai_client/llm/prompt.py` | allowlisted bounded projection and dynamic decision schema |
| `ai_client/llm/decision.py` | strict JSON parse, schema validation, semantic conversion |
| `ai_client/llm/audit.py` | bounded single-writer `ai.jsonl` sink |
| `ai_client/llm/brain.py` | one `LLMBrain` implementation and per-call lifecycle |
| `ai_client/llm/config.py` | environment/CLI loading without a model or credential literal |
| `ai_client/llm/__init__.py` | Phase 4 public exports only |

### Changed integration surfaces

| Path | Change |
|---|---|
| `pyproject.toml` | add `httpx>=0.27,<1`; add opt-in `local_llm` pytest marker if the smoke has a pytest wrapper |
| `ai_client/__init__.py` | re-export only stable Phase 4 composition types |
| `tests/conftest.py` | register the new offline completion node as `completion` |

No Phase 4 implementation packet changes `server/`, `protocol/`, `content/`, `ai_client/network/`,
`ai_client/world/`, `ai_client/reaction_chat/`, or `ai_client/vote_ability/`. An implementation
discovery that requires such a change returns to the Integrator/Architect instead of silently expanding
scope.

### Tests and tools

| Path | Purpose |
|---|---|
| `tests/test_phase4_llm_contracts.py` | frozen config/audit-record/serialization contracts |
| `tests/test_phase4_llm_backend.py` | HTTP/config/resource/error bounds |
| `tests/test_phase4_ai_audit.py` | bounded writer lifecycle, durability, cancellation, failures |
| `tests/test_phase4_llm_brain.py` | projection/schema/repair/audit/Brain behavior |
| `tests/test_phase4_completion.py` | deterministic fake backend through one real production LLMBrain client |
| `tests/fixtures/phase4_client_process.py` | one LLM + eight LLM-free process composition |
| `tests/fixtures/phase4_fake_backend.py` | scripted deterministic protocol fake; test-only |
| `scripts/run_phase4_local_smoke.py` | finite opt-in real local profile smoke and cleanup |

## Public Interfaces

The following signatures fix semantics and required fields. Minor module-private helper names are not
contractual.

```python
LLMRole = Literal["system", "user", "assistant"]

@dataclass(frozen=True)
class LLMMessage:
    role: LLMRole
    content: str

@dataclass(frozen=True)
class StructuredGenerationRequest:
    request_id: str
    messages: tuple[LLMMessage, ...]
    output_schema: Mapping[str, object]

@dataclass(frozen=True)
class LLMUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

@dataclass(frozen=True)
class StructuredGenerationResponse:
    request_id: str
    text: str
    provider_model: str | None
    finish_reason: str | None
    usage: LLMUsage

class StructuredLLMBackend(Protocol):
    @property
    def identity(self) -> BackendIdentity: ...
    async def generate(
        self, request: StructuredGenerationRequest
    ) -> StructuredGenerationResponse: ...
    async def aclose(self) -> None: ...
```

All mapping values are deep-copied into recursively immutable values at construction. Messages,
schema, and response text must be non-empty where applicable. `request_id` is a locally generated
UUID; the adapter copies it unchanged into `StructuredGenerationResponse.request_id` after validating
the provider response. The provider is not expected to echo it. It is not a server action event ID.

```python
@dataclass(frozen=True)
class BackendIdentity:
    backend_type: str                 # "openai_compatible_http"
    endpoint_origin: str              # scheme + loopback host + port; no userinfo/query
    endpoint_path: str
    model: str
    config_fingerprint: str           # SHA-256 of non-secret effective config

class LLMBackendErrorCode(str, Enum):
    CONNECT_FAILED = "CONNECT_FAILED"
    REQUEST_TIMEOUT = "REQUEST_TIMEOUT"
    HTTP_STATUS = "HTTP_STATUS"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    RESPONSE_ENCODING = "RESPONSE_ENCODING"
    RESPONSE_ENVELOPE_INVALID = "RESPONSE_ENVELOPE_INVALID"
    CLOSED = "CLOSED"

class LLMBackendError(RuntimeError):
    code: LLMBackendErrorCode
    http_status: int | None
    retryable: bool
```

The exception message is only the stable code. It never includes a URL with userinfo, headers,
credential, response body, generated text, or library exception string.

```python
@dataclass(frozen=True)
class GenerationSettings:
    max_output_tokens: int = 128
    temperature: float = 0.2

@dataclass(frozen=True)
class OpenAICompatibleBackendConfig:
    endpoint: str
    model: str
    api_key: str | None = field(default=None, repr=False, compare=False)
    generation: GenerationSettings = GenerationSettings()
    connect_timeout_seconds: float = 1.0
    read_timeout_seconds: float = 3.5
    write_timeout_seconds: float = 1.0
    pool_timeout_seconds: float = 1.0
    request_timeout_seconds: float = 4.0
    max_request_bytes: int = 65536
    max_response_bytes: int = 65536
    structured_mode: Literal["json_schema", "json_object"] = "json_schema"

class OpenAICompatibleBackend(StructuredLLMBackend):
    def __init__(
        self,
        config: OpenAICompatibleBackendConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None: ...
```

`GenerationSettings` is the **only source of truth** for generation length and temperature.
`StructuredGenerationRequest` deliberately has neither field. `LLMBrain` constructs the request from
the projection alone; `OpenAICompatibleBackend.generate()` always writes
`config.generation.max_output_tokens` and `config.generation.temperature` into the HTTP body. There is
no caller/adapter mismatch state and no private adapter default that can override this value object.

`LocalLLMSettings.from_env()` reads `AIWOLF_LLM_ENDPOINT`, required `AIWOLF_LLM_MODEL`, and optional
`AIWOLF_LLM_API_KEY`; absent model is a construction error. It uses the displayed frozen defaults for
all other fields. CLI overrides are parsed into typed values and applied once with `dataclasses.replace`
before composition; unknown keys and invalid values fail before resources start. `backend_config()`
returns a new frozen `OpenAICompatibleBackendConfig` by copying every same-named field and passes the
**same `GenerationSettings` instance** as `generation`. Composition passes `settings.brain` unchanged
to `LLMBrain` and `settings.audit` unchanged to `JsonlAiAuditSink`. Settings are never mutated after
validation. This is the exact T020 foundation -> backend/Brain/audit data flow.

Endpoint may default only to the model-neutral loopback transport location
`http://127.0.0.1:8080/v1/chat/completions`. API key may come from environment/CLI secret input but is
never written to a repository file, prompt, repr, fingerprint, outcome, or log.

The Phase 4 adapter accepts only `http` and a loopback host (`127.0.0.1`, `::1`, or `localhost`),
requires the explicit `/v1/chat/completions` path, rejects URL userinfo/query/fragment, redirects,
proxy inheritance, non-finite/non-positive timeouts, request/response bounds below 1024, output tokens
outside 1..512, temperature outside 0..2, and an empty model. `httpx.AsyncClient` is created with
`follow_redirects=False` and `trust_env=False`.

One call sends exactly one non-streaming request:

```json
{
  "model": "<configured model>",
  "messages": [{"role": "system|user|assistant", "content": "..."}],
  "temperature": "<config.generation.temperature>",
  "max_tokens": "<config.generation.max_output_tokens>",
  "stream": false,
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "aiwolf_brain_decision",
      "strict": true,
      "schema": {}
    }
  }
}
```

`json_object` is an explicit compatibility setting, not an automatic fallback; the same local strict
parser remains mandatory. Before send, the adapter deterministically UTF-8 serializes the **complete
HTTP JSON body**, including model, messages, dynamic schema, and generation settings, and rejects it
when it exceeds `max_request_bytes`. The adapter does zero retry. It uses both the configured `httpx.Timeout`
and an outer `asyncio.timeout(request_timeout_seconds)`. It checks `Content-Length` when present and
also reads chunks incrementally, aborting before accumulated bytes exceed `max_response_bytes`.
Only a 2xx JSON response with `choices[0].message.content` as one string is accepted. Provider extra
metadata may be ignored, but missing/ambiguous required values fail. `aclose()` is idempotent and
permanent.

### Prompt and decision interfaces

```python
@dataclass(frozen=True)
class LLMBrainConfig:
    max_prompt_bytes: int = 32768
    max_history_records: int = 32
    max_human_text_chars: int = 512
    max_generated_text_chars: int = 240
    max_repair_excerpt_chars: int = 1024
    max_schema_repair_attempts: int = 1

@dataclass(frozen=True)
class PromptProjection:
    messages: tuple[LLMMessage, ...]
    decision_schema: Mapping[str, object]
    canonical_input: Mapping[str, object]
    prompt_bytes: int
    prompt_sha256: str
    included_history_records: int
    omitted_history_records: int

def project_brain_input(
    request: BrainInput, *, config: LLMBrainConfig
) -> PromptProjection: ...

class DecisionValidationCode(str, Enum):
    JSON_SYNTAX = "JSON_SYNTAX"
    JSON_DUPLICATE_KEY = "JSON_DUPLICATE_KEY"
    SCHEMA = "SCHEMA"
    OPTION_NOT_OFFERED = "OPTION_NOT_OFFERED"
    VALUE_NOT_OFFERED = "VALUE_NOT_OFFERED"
    TEXT_BOUND = "TEXT_BOUND"

def parse_llm_decision(
    text: str, *, projection: PromptProjection
) -> BrainDecision: ...
```

`project_brain_input` serializes with deterministic UTF-8 JSON (`ensure_ascii=False`, sorted object
keys, fixed separators). `prompt_bytes` is exactly the UTF-8 length of canonical
`{"messages": [...], "output_schema": {...}}`; it covers every outbound message and the complete
dynamic schema, not only natural-language content. It must be at most `max_prompt_bytes`. The backend
separately applies `max_request_bytes` to the final provider body containing this contract, model, and
generation settings. The system message contains only static instructions: the JSON user payload
is untrusted game data, return exactly one JSON object, and choose only declared values. Untrusted
display names, chat, comments, descriptions, role/modifier/result IDs never enter the system message
or become instruction text; they remain JSON string values in the user message.

The allowlist projection contains only:

- snapshot version/freshness metadata, phase/day, self player/role/modifier IDs
- player ID/display name/alive/public death, self-authorized revealed role entries
- the filtered request options and fields already present on each typed handle
- retained CO and ability-result records supplied by `BrainInput`
- an ascending-order suffix of retained history with its completeness/drop metadata

It never introspects `NetworkClient`, credential store, connection/entry token, raw `ServerEvent`,
server/game object, full content registry, other seats' private state, process environment, or file
system. Truncation is permitted only for these human-readable fields: `PlayerView.display_name`,
`ChatRecord.message`, `CoDeclarationRecord.comment`, and `AbilityAction.description`. Each is
Unicode-codepoint truncated to `max_human_text_chars` and represented as
`{"text": <prefix>, "truncated": true|false, "original_chars": <count>}`. No other field is
truncated.

Every opaque identifier is preserved byte-for-byte, including game/player/option/phase/role/modifier/
ability/channel/event/result/notify IDs, claimed-role values, and any future supplied CO-report
kind/result vocabulary. IDs are never normalized, case-folded, shortened, re-encoded into another
identifier, or deduplicated after transformation. History is limited to the newest
`max_history_records`, then emitted in ascending order. Options, players, self state, phase, all opaque
IDs, and schema enums are never dropped or changed; if the base projection/schema or final contract
does not fit `max_prompt_bytes`, construction fails closed before HTTP.

The single schema factory always permits `{"kind":"none"}` and adds request-specific `oneOf`
branches for eligible received handles:

```text
none       -> NoDecision
chat       -> ChatDecision(option_id, message)
vote       -> VoteDecision(option_id, target_player_id)
ability    -> AbilityDecision(option_id, target_player_ids)
co_declare -> CoDeclareDecision(option_id, claimed_role_id, comment)
co_report  -> CoReportDecision(option_id, kind, target_player_id, claimed_result)
```

All objects require exactly their named properties and `additionalProperties: false`; `kind` is the
discriminator. A chat/comment is 1..`max_generated_text_chars` codepoints. Each selection branch is
narrowed per option: exact option ID, vote target enum (plus null only if that received handle permits
abstention), exact ability target count with unique items from that handle, and claimed role from that
handle. The parser rejects Markdown fences, leading/trailing prose, trailing JSON, NaN/Infinity, and
duplicate keys before Draft 2020-12 schema validation, then maps to the existing frozen decision
dataclass. `BrainController` still performs the final independent mechanical validation.

Current `CoReportAction` supplies no kind/result/target vocabulary. Therefore the Phase 4 schema
factory does not add a `co_report` branch for current input, and a manually supplied co-report object
is `OPTION_NOT_OFFERED`/`VALUE_NOT_OFFERED`. Parser mapping and unit vectors still cover the existing
`CoReportDecision` union member using an explicit test-only projection whose vocabulary is supplied;
production never invents that vocabulary. `CoDeclareAction` remains supported, including bounded
generated comment. This is a fail-closed use of the current API, not a new game rule.

### LLMBrain and audit interfaces

```python
@dataclass(frozen=True)
class LLMClientIdentity:
    game_id: str
    player_id: str

class LLMInvocationStatus(str, Enum):
    DECISION = "DECISION"
    EXPLICIT_NO_DECISION = "EXPLICIT_NO_DECISION"
    REPAIR_SUCCEEDED = "REPAIR_SUCCEEDED"
    PROMPT_REJECTED = "PROMPT_REJECTED"
    BACKEND_FAILED = "BACKEND_FAILED"
    OUTPUT_INVALID = "OUTPUT_INVALID"
    REPAIR_FAILED = "REPAIR_FAILED"
    AUDIT_FAILED = "AUDIT_FAILED"
    CANCELLED = "CANCELLED"

@dataclass(frozen=True)
class LLMBrainSnapshot:
    active: bool
    calls: int
    backend_calls: int
    decisions: int
    explicit_no_decisions: int
    repair_attempts: int
    failures: int
    cancellations: int
    audit_failures: int
    last_status: LLMInvocationStatus | None
    last_error_code: str | None

AuditDecisionKind = Literal[
    "none", "chat", "vote", "ability", "co_declare", "co_report"
]

class AiAuditStatus(str, Enum):
    PROMPT_REJECTED = "PROMPT_REJECTED"
    BACKEND_FAILED = "BACKEND_FAILED"
    OUTPUT_INVALID = "OUTPUT_INVALID"
    DECISION = "DECISION"
    EXPLICIT_NO_DECISION = "EXPLICIT_NO_DECISION"
    REPAIR_SUCCEEDED = "REPAIR_SUCCEEDED"
    REPAIR_FAILED = "REPAIR_FAILED"

@dataclass(frozen=True)
class AiAuditDecision:
    kind: AuditDecisionKind
    option_id: str | None = None
    text: str | None = None
    vote_target_player_id: str | None = None
    ability_id: str | None = None
    ability_target_player_ids: tuple[str, ...] = ()
    claimed_role_id: str | None = None
    report_kind: str | None = None
    report_target_player_id: str | None = None
    claimed_result: str | None = None

@dataclass(frozen=True)
class AiAuditRecord:
    schema_version: Literal["aiwolf.ai-log.v1"]
    recorded_at_utc: str
    game_id: str
    player_id: str
    request_id: str
    phase: str | None
    day: int | None
    world_version: int
    backend: BackendIdentity
    attempt_ordinal: Literal[1, 2]
    prompt_sha256: str
    prompt_bytes: int
    prompt_json: str
    response_sha256: str | None
    response_bytes: int | None
    response_text: str | None
    latency_microseconds: int
    provider_model: str | None
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    status: AiAuditStatus
    backend_error_code: LLMBackendErrorCode | None
    validation_code: DecisionValidationCode | None
    decision: AiAuditDecision | None

@dataclass(frozen=True)
class AiAuditWriterConfig:
    queue_capacity: int = 16
    max_record_bytes: int = 131072

    def __post_init__(self) -> None: ...

class AiAuditErrorCode(str, Enum):
    RECORD_INVALID = "RECORD_INVALID"
    NOT_STARTED = "NOT_STARTED"
    CLOSED = "CLOSED"
    OPEN_FAILED = "OPEN_FAILED"
    WRITE_FAILED = "WRITE_FAILED"
    FLUSH_FAILED = "FLUSH_FAILED"
    FSYNC_FAILED = "FSYNC_FAILED"
    CLOSE_FAILED = "CLOSE_FAILED"
    SINK_FAILED = "SINK_FAILED"

class AiAuditError(RuntimeError):
    code: AiAuditErrorCode
    terminal_code: AiAuditErrorCode | None

    def __init__(
        self,
        code: AiAuditErrorCode,
        *,
        terminal_code: AiAuditErrorCode | None = None,
    ) -> None: ...

@dataclass(frozen=True)
class AuditWriteAck:
    sequence: int
    durable: Literal[True] = True

    def __post_init__(self) -> None: ...

class AiAuditSink(Protocol):
    async def start(self) -> None: ...
    async def write(self, record: AiAuditRecord) -> AuditWriteAck: ...
    async def aclose(self) -> None: ...

class JsonlAiAuditSink(AiAuditSink):
    def __init__(
        self,
        path: Path,
        *,
        config: AiAuditWriterConfig = AiAuditWriterConfig(),
    ) -> None: ...

class LLMBrain:
    def __init__(
        self,
        *,
        backend: StructuredLLMBackend,
        audit: AiAuditSink,
        identity: LLMClientIdentity,
        config: LLMBrainConfig = LLMBrainConfig(),
        request_id_factory: Callable[[], str] = uuid4_string,
        clock: Clock = time.monotonic,
        utc_clock: Callable[[], datetime] = utc_now,
    ) -> None: ...
    async def decide(self, request: BrainInput) -> BrainDecision: ...
    def snapshot(self) -> LLMBrainSnapshot: ...
```

`AiAuditWriterConfig` is validated by exact type, not truthiness or coercion. `type(queue_capacity)`
must be `int` and its value must be in `1..64`; `type(max_record_bytes)` must be `int` and its value
must be in `1..1_048_576`. Thus `bool`, zero, negative, float, string, and values above either finite
maximum are rejected during frozen-value construction, before a queue/executor/file is created.
Rejection is `ValueError` with exactly `queue_capacity must be an int in [1, 64]` or
`max_record_bytes must be an int in [1, 1048576]`, respectively. There is no coercion. The maximum
configured queued serialized payload is therefore 64 MiB and `asyncio.Queue(maxsize=0)` is
unrepresentable through the public configuration.

`AiAuditErrorCode` and `AiAuditError` are public T020 contracts. The enum names and values above are
stable. `AiAuditError(code, *, terminal_code=None)` stores only those two fields, calls
`RuntimeError.__init__(code.value)`, and therefore has `str(error) == code.value` and
`error.args == (code.value,)`. It never embeds a path, record, credential, OS/library message, or
exception chain. `terminal_code` is required iff `code is SINK_FAILED`, in which case it is one of
`OPEN_FAILED`, `WRITE_FAILED`, `FLUSH_FAILED`, `FSYNC_FAILED`, or `CLOSE_FAILED`; it is null for every
other code. An invalid constructor combination raises `ValueError("invalid audit terminal_code")`.

`AuditWriteAck` accepts only `type(sequence) is int`, `sequence >= 1`, and `durable is True`; invalid
construction raises `ValueError("sequence must be a positive int")` or
`ValueError("durable must be True")`. Sequence numbers are runtime acknowledgements, not serialized
record fields: one successfully started sink lifecycle begins at 1, and the single writer increments
by exactly one only after that record's write, flush, and fsync all succeed. Successful acknowledgements
are consequently gap-free, strictly increasing, and unique within that sink lifecycle. A failed,
cancelled-before-enqueue, or not-yet-durable item receives no `AuditWriteAck` and consumes no sequence.

`AiAuditDecision` has an exact closed shape. `none` requires every optional field null/empty. Every
other kind requires `option_id`. `chat` requires only bounded `text`; `vote` permits only its optional
target; `ability` requires `ability_id` and its target tuple; `co_declare` requires claimed role and
bounded `text`; `co_report` requires report kind/target/result and no text. Fields not named for the
selected kind must be null/empty. Every identifier is copied byte-for-byte from the validated decision
and projection.

`AiAuditRecord` validation is exact: schema literal and non-empty identities; `day` is null or a
non-negative integer; versions/counts/latency are non-negative integers; attempt is 1 or 2; hashes are
lowercase 64-hex SHA-256; `recorded_at_utc` is normalized UTC RFC3339 with `Z`; `prompt_bytes` equals
the UTF-8 byte length of `prompt_json` and its hash matches; response hash/bytes/text are either all
present and mutually matching or all null; token counts are null or non-negative.
`backend_error_code` is non-null iff status is `BACKEND_FAILED`; `validation_code` is non-null iff
status is `OUTPUT_INVALID` or `REPAIR_FAILED`. `decision` is non-null iff status is `DECISION`,
`EXPLICIT_NO_DECISION`, or `REPAIR_SUCCEEDED`. Response hash/bytes/text are present exactly when the
provider produced an extractable content string: `OUTPUT_INVALID`, `DECISION`,
`EXPLICIT_NO_DECISION`, `REPAIR_SUCCEEDED`, or `REPAIR_FAILED`. `PROMPT_REJECTED` and
`BACKEND_FAILED` have no response triplet. `AUDIT_FAILED` and `CANCELLED` are LLMBrain
snapshot/controller states, not serializable audit statuses. `prompt_json` is the exact bounded
canonical messages+schema contract, and `response_text` is the exact extracted provider decision
string already bounded by `max_response_bytes`; neither is further truncated by the audit layer. If
the complete record cannot fit `max_record_bytes`, the record is rejected with
`AiAuditError(RECORD_INVALID)` and an action fails closed.

`serialize_ai_audit(record) -> bytes` converts enums to `.value`, tuples to arrays, and nested frozen
values to fixed named objects; it retains every key including null fields, then uses
`json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)` and
appends exactly one `b"\n"`. This byte sequence is the sole queue/write representation. Record size
and hashes are verified before enqueue, so T020 contract tests can compile and run without a sink.

With `LLMBrainConfig` and `AiAuditWriterConfig` now defined, the complete settings value is:

```python
@dataclass(frozen=True)
class LocalLLMSettings:
    endpoint: str = "http://127.0.0.1:8080/v1/chat/completions"
    model: str = ""                 # required after loading; no model default
    api_key: str | None = field(default=None, repr=False, compare=False)
    generation: GenerationSettings = GenerationSettings()
    connect_timeout_seconds: float = 1.0
    read_timeout_seconds: float = 3.5
    write_timeout_seconds: float = 1.0
    pool_timeout_seconds: float = 1.0
    request_timeout_seconds: float = 4.0
    max_request_bytes: int = 65536
    max_response_bytes: int = 65536
    structured_mode: Literal["json_schema", "json_object"] = "json_schema"
    brain: LLMBrainConfig = LLMBrainConfig()
    audit: AiAuditWriterConfig = AiAuditWriterConfig()

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> LocalLLMSettings: ...
    def backend_config(self) -> OpenAICompatibleBackendConfig: ...
```

All nested defaults are frozen values. `config.py` imports these types from `types.py`; no forward
runtime reference or private backend access is required.

`LLMBrain` rejects concurrent `decide` calls as an invariant error; it creates no background task and
does not own World, Network, controller, arbiter, or backend shutdown. It makes one initial backend
call. An explicit valid `none` returns `NoDecision`. A parse/schema/semantic model-output failure may
make at most one repair call when configured and while the outer Brain task remains alive. Repair uses
the same original projection/schema plus only the stable validation code, response SHA-256, and an
invalid-output excerpt bounded by `max_repair_excerpt_chars`, encoded as untrusted JSON data. Before
the second HTTP call, the complete repaired messages plus the unchanged dynamic schema are
canonicalized and rechecked against the same `max_prompt_bytes`; the backend also reapplies
`max_request_bytes` to the complete repaired provider body. If either bound fails, no repair HTTP call
is made. Repair never changes, truncates, or widens identifiers/options and never uses a free-form
fallback.

Connect/HTTP/timeout/oversize/backend envelope failure is never automatically retried. Final invalid
output, prompt overflow, backend failure, or audit failure raises a sanitized `LLMInvocationError`
after updating the snapshot; `BrainController` records `BRAIN_FAILED` and sends nothing. Cancellation
is never converted to a decision. No late HTTP completion task survives the cancelled context.

`JsonlAiAuditSink` owns one `asyncio.Queue` bounded by validated `queue_capacity`, one writer task, one dedicated
single-thread executor, and the file handle used only in that executor. Maximum queued serialized
memory is `queue_capacity * max_record_bytes`. `start()` asynchronously opens the append-only file in
the executor and starts the writer; it is one-shot. `write()` serializes/checks before enqueue, awaits
queue capacity without blocking the event loop, then awaits that item's private acknowledgement. The
writer processes FIFO one at a time and performs write, flush, and `os.fsync` in its executor before
assigning the next success sequence and setting `AuditWriteAck(sequence=..., durable=True)`. Therefore
a would-be action cannot return from `LLMBrain` until
its exact record is durably acknowledged.

If a caller is cancelled before enqueue completes, no item exists. If cancelled after enqueue while
waiting for acknowledgement, the acknowledgement future is shielded from caller cancellation; the
caller returns no decision, while the item remains bounded and owned by the writer task. It is not
detached, and `aclose()` must drain/resolve it. LLMBrain re-raises cancellation and never dispatches.
It does not enqueue a second cancellation record from the cancelled task.

A `write()` checks lifecycle state before record serialization: before successful `start()` it raises
`NOT_STARTED`; after normal close admission begins it raises `CLOSED`; in `FAILED` it raises
`SINK_FAILED` with the retained terminal code. While running, serialization/record invariant/hash or
`max_record_bytes` failure raises `RECORD_INVALID` before enqueue and does not fail the sink. A file
open failure makes `start()` raise `OPEN_FAILED`. A writer operation failure makes its current waiter
raise exactly `WRITE_FAILED`, `FLUSH_FAILED`, or `FSYNC_FAILED`; a handle-close failure raises
`CLOSE_FAILED` from `aclose()`. Each I/O failure moves the sink to `FAILED`, retains that original code,
completes already-queued waiters with `SINK_FAILED(terminal_code=<original>)`, makes every future
`write()` raise the same sanitized `SINK_FAILED` form, and makes `aclose()` re-raise the original
terminal error. No underlying exception is chained or exposed.

`aclose()` atomically stops admission, creates/reuses one owned close task, queues one
sentinel after accepted records, drains all acknowledgements, closes the handle in the executor, and
awaits dedicated-executor shutdown. It is idempotent and awaited. Cancellation of one close waiter
does not cancel the owned close task; the composition's `finally` awaits the same task to terminal
before process exit. After successful close, repeated `aclose()` returns `None` and `write()` raises
`CLOSED`; after failed close, every repeated `aclose()` raises the retained original terminal error.
No writer/executor/file work is abandoned or silently swallowed.

The sink is a one-process/one-writer Phase 4 resource. Its path is explicitly
`logs/<game_id>/ai.jsonl`; it never writes public/private logs. It cannot contain API key,
Authorization header, entry/connection token, environment dump, raw HTTP body/error, or information
outside the `BrainInput` projection. Phase 5 must design multi-writer serialization before multiple
LLM processes share this path.

Every completed initial or repair attempt has one audit record; an outer-cancelled attempt may have no
record and is identified by snapshot/controller cancellation evidence. A valid action/no-decision record is durably
acknowledged before return to `BrainController`; audit failure raises `AUDIT_FAILED` and fails closed.
Backend/model failure records are also awaited while the invocation budget exists. If outer
cancellation interrupts their enqueue/ack, no action exists and writer ownership/close rules still
guarantee finite cleanup; `LLMBrainSnapshot` remains the immediate cancellation evidence.

## Composition and Resource Lifecycle

Two mutually exclusive composition profiles preserve exactly one Brain instance.

```text
LLM-free regression:
  DeterministicVoteAbilityBrain(delegate=<existing deterministic reaction brain>)
      -> one BrainController -> one BrainInvocationArbiter -> both feature controllers

Phase 4 LLM:
  LLMBrain -> one BrainController -> one BrainInvocationArbiter -> both feature controllers
```

`LLMBrain` is injected directly in LLM mode. It is not wrapped by
`DeterministicVoteAbilityBrain`, because that decorator intentionally consumes vote/ability requests
and would prevent ROADMAP 4.4 from reaching the LLM. The decorator remains unchanged for Phase 3
LLM-free baselines. There is no runtime type switch or model router inside World, Controller, or the
arbiter.

The production/local-smoke composition creates one `LocalLLMSettings`, derives exactly one backend
config through `backend_config()`, passes `settings.brain` to LLMBrain and `settings.audit` to the sink,
and injects the same monotonic callable throughout. It uses:

- `BrainRunConfig(max_decision_seconds=5.0, cancellation_grace_seconds=0.25)`
- both feature controllers with `brain_timeout_seconds=4.0`,
  `deadline_guard_seconds=1.0`, `minimum_start_budget_seconds=0.10`
- all other existing cap, jitter, retry, and correlation defaults unchanged

Controller cutoff may shorten the effective call below four seconds. Neither LLMBrain nor backend
extends a game deadline. The composition constructs backend/sink, awaits `audit.start()`, then starts
Network, World, BrainController, arbiter, and both feature controllers in ownership order. If any later
start fails, the same cleanup path runs. Normal game end or failure stops feature controllers, then
arbiter/BrainController, awaits `audit.aclose()` and `backend.aclose()`, then stops World and Network.
Audit close is shielded by the composition's `finally` and awaited to terminal even if the supervisor
itself is cancelled. Every stop/close is idempotent and awaited. A controller, audit, or World failure
is propagated through the owning await boundary; nothing silently restarts.

## Per-Invocation Lifecycle and State Transitions

```text
CAPTURED
  -> PROJECTING
  -> REQUESTING(1)
  -> VALIDATING(1)
      -> DECISION | EXPLICIT_NO_DECISION
      -> REQUESTING_REPAIR(2) -> VALIDATING_REPAIR(2)
           -> REPAIR_SUCCEEDED | REPAIR_FAILED
  -> BACKEND_FAILED | PROMPT_REJECTED | AUDIT_FAILED | CANCELLED
```

Terminal action-bearing values cross to `BrainController` only after audit. Every other terminal is
no-send. `LLMBrain` stores counters/status only, not an unbounded request/history cache. Prompt and
response objects become unreachable after the call and audit write.

World/Network ingestion continues in sibling tasks while HTTP is pending. Phase/action/mapping change
causes existing BrainController/arbiter cancellation or send-time stale/deadline suppression. The LLM
does not cache a result for a later generation. Reservation acceptance still requires the exact
server `action.accepted`; chat/CO finalization still requires the existing semantic evidence.

## Failure and Offline Behavior

- Missing/invalid config: fail before starting any client/model request with a sanitized validation
  error; no implicit model/role/remote fallback.
- Server absent/refused/DNS impossible under loopback validation: one `CONNECT_FAILED`, audit/snapshot,
  `BRAIN_FAILED`, no send. World/Network continue.
- HTTP non-2xx, timeout, malformed envelope, invalid UTF-8/JSON, oversize: stable error classification,
  zero transport retry, no action.
- Model JSON invalid: at most one bounded repair; then `REPAIR_FAILED`, no action.
- Unauthorized option/value or fabricated target: semantic validation failure even if JSON schema mode
  was accepted by the backend; no nearest-value substitution.
- Outer controller timeout/phase change/stop: cancellation closes the in-flight response and re-raises;
  no detached request, late result, or dispatch.
- `SendReceipt`, accepted/rejected/unknown, NOT_DELIVERED, and mapping re-arm retain the Phase 3.5
  controller semantics. LLMBrain adds no retry.
- Audit write failure: never dispatch an action that lacks its required AI audit record. The process
  remains diagnosable through controller outcome, LLM snapshot, stderr/supervisor failure type.

The server continues to observe only received actions or silence. It never receives an LLM health
state and never changes game rules because a model is offline.

## Test Matrix

### Backend/config unit tests — no model/network/GPU

- `LocalLLMSettings.from_env` required-model/default/override validation; exact immutable conversion
  passes the same `GenerationSettings` object to backend config, while generation request has no
  duplicate token/temperature fields
- config accepts only explicit loopback HTTP endpoint and required model; rejects userinfo,
  query/fragment, redirect target, remote host, invalid numeric bounds, and secret-bearing repr
- fingerprint changes for non-secret effective config and is unchanged by API key; model and endpoint
  are config values unrelated to game role/responsibility
- exact request shape and Authorization omission/presence using `httpx.MockTransport`
- fragmented response at byte bound succeeds; declared or streamed over-bound aborts
- non-2xx, refusal, request timeout, invalid UTF-8/JSON/envelope/content type classification
- cancellation closes the response; no retry and no pending task; `aclose()` idempotent/permanent
- `json_schema` and explicit `json_object` modes; no automatic compatibility fallback

### Projection/structured-decision unit tests

- exact deterministic prompt/hash/complete messages-plus-schema byte count for a fixed `BrainInput`;
  all input dataclasses remain unmodified
- only allowlisted World fields appear; no raw payload/client/token/env/server value can be reached
- untrusted prompt-like chat/display/description remains JSON data, never system instructions
- newest bounded history suffix, ascending emission, truncation only for the four named human-text
  fields, and retention metadata; every opaque identifier is byte-for-byte unchanged
- base messages+schema, repaired messages+schema, and final provider-body byte overflow each fail
  before the corresponding fake HTTP call; options/identifiers are never altered or dropped
- strict parser rejects fences/prose/trailing data/duplicate keys/NaN/unknown fields
- all existing BrainDecision mappings; per-option vote/ability/claim membership/count constraints
- current CoReport handle produces no branch and cannot dispatch fabricated vocabulary
- generated chat/comment exact 1/240 boundaries
- first invalid output then valid repair succeeds exactly once; two invalids stop; backend error has zero
  repair; repair never broadens schema/options

### LLMBrain/audit/controller integration tests

- deterministic scripted backend returns each valid decision through the unchanged BrainController
  and typed sender; Controller still blocks a stale/fabricated output independently
- explicit `none` differs from backend/output/prompt/audit failure in snapshot and audit
- one Brain/Controller/arbiter instance serves Reaction and Vote/Ability with active count <= 1 and
  reservation next-grant priority
- slow fake backend does not block World version progress; timeout/cancel leaves no backend task
- outage, 429/500, oversize, invalid output, audit failure all produce zero send and bounded records
- exact frozen audit record optionality/cross-field rules and deterministic UTF-8 serialization
- bounded queue admission, asynchronous backpressure, per-record write+flush+fsync acknowledgement,
  cancellation before/after enqueue, writer failure fan-out, repeated awaited close, executor cleanup
- `ai.jsonl` one-line schema, hash/latency/config/attempt/status evidence and private-view fields; scan
  proves sentinel API key/Authorization/entry token/connection token/other-seat secret absent
- accepted/rejected/unknown evidence remains exact server-correlation evidence, never LLM/self-report

### Required offline completion

`tests/test_phase4_completion.py` is a normal `completion` node and needs no model, Internet, GPU, or
`C:\AIagent`. It runs the production `LLMBrain` with a deterministic scripted
`StructuredLLMBackend` in exactly one of nine independent client processes; the other eight retain the
approved LLM-free composed Brain. All nine use production Network, World, BrainController, shared
arbiter, Reaction, and Vote/Ability controllers.

The node asserts game end/cleanup, nine distinct non-parent PIDs, exactly one LLM process, backend
call/audit evidence, at least one server-authoritatively accepted chat and one accepted vote generated
by that LLMBrain, valid received option/target membership, all accepted reservations before deadline,
zero fabricated handles, and unchanged correlation/no-retry contracts. A scripted ability vector also
passes through production BrainController even if the chosen completion seat has no current ability.
The fake backend returns immediately; existing short test-local phase durations and 120-second node
budget may be reused without weakening Phase 3.5 assertions.

### Opt-in real local smoke

`scripts/run_phase4_local_smoke.py` is not an automatic CI test and never starts/stops a model server.
The operator supplies endpoint/model (and optional key) through CLI/environment after starting the
local service separately. The script starts one game server process and nine client processes, exactly
one using production `OpenAICompatibleBackend + LLMBrain`; the other eight are deterministic. It uses
test-local `standard_9` duration overrides of day/vote/night 12 seconds and silence 0, a fixed scenario
seed, and a hard 300-second scenario timeout.

Success requires: health is established by the first actual structured request (no provider-specific
health endpoint), at least one valid LLM decision, one accepted LLM chat, one accepted LLM vote, game
end for all seats, a non-empty bounded `ai.jsonl`, and zero child/model-helper processes owned by the
script after cleanup. If an ability handle is offered to the LLM seat, its selection must also be valid
and accepted; absence of an offered ability is reported, not fabricated. Every failure prints only
bounded diagnostics: process exits, counts/status/error codes, last controller outcomes, and log path.
It never prints credentials or full prompts.

Exact invocation contract:

```powershell
$env:AIWOLF_LLM_ENDPOINT = 'http://127.0.0.1:8080/v1/chat/completions'
$env:AIWOLF_LLM_MODEL = '<model exposed by the already-running local server>'
python scripts/run_phase4_local_smoke.py --max-seconds 300
```

Missing settings, unavailable model, timeout, invalid structured output, failure to dispatch required
actions, or leaked/orphan child is a non-zero exit. No skip converts a requested smoke to success.

## ROADMAP and Acceptance Mapping

| ROADMAP item | Design/required evidence |
|---|---|
| 4.1 LLM backend interface | `StructuredLLMBackend`, bounded local adapter, lifecycle/error tests |
| 4.2 Structured Output | dynamic discriminated schema, strict parse/semantic validation, one repair |
| 4.3 発言生成 | bounded `ChatDecision` and `CoDeclareDecision.comment`; accepted-chat evidence |
| 4.4 投票・能力選択 | per-handle vote/ability branches through LLMBrain and Controller tests |
| one AI Client by LLM | offline one-LLM production composition plus opt-in real-local smoke |

Implementation acceptance requires all of the following.

1. No Phase 4 production import from `server.aiwolf_core` or `server.network`; no server/protocol/
   content changes.
2. Model/endpoint/sampling/credential are configuration only. No game role/responsibility/model name is
   coupled in source.
3. Backend resource bounds, timeout, cancellation, zero retry, sanitized error taxonomy, and idempotent
   close match this design.
4. Prompt contains only bounded authorized `BrainInput` projection, never raw Network/server data or
   token; oversize fails closed.
5. Structured output can create a decision only from the request-local option/value set; strict parser,
   dynamic schema, semantic check, and existing BrainController validation all remain present.
6. Repair is zero or one attempt, never widens choices, and never becomes a free-form fallback action.
7. Chat/comment length is bounded; current co-report vocabulary is not invented.
8. LLM mode uses exactly one direct `LLMBrain`, one BrainController, and one shared arbiter. Existing
   deterministic decorator and all Phase 3 LLM-free paths remain green.
9. HTTP wait never blocks Network/World ingestion; stale/deadline/cancellation cannot dispatch a late
   output.
10. Outage/failure is observable and no-send; it does not alter server progress or silently invoke
    another model/rule-based move.
11. `ai.jsonl` contains the required bounded private audit and none of the prohibited secrets/other-view
    information; unlogged actions are not sent.
12. Required unit/integration/offline completion tests run without model/network/GPU/external directory.
13. The finite opt-in smoke proves one real LLM-controlled client, required accepted actions, game end,
    log, and complete cleanup.
14. Phase 3.1–3.5 focused/completion regressions, full regression, compile, docs, and diff checks pass.

## Implementation Packets and Conflict Boundaries

After independent approval, the Integrator dispatches these dependency-ordered packets. Only T021 and
T022 are parallel; both consume a complete, reviewed T020 contract and can compile/run focused tests
without the other's files.

1. **T020 — frozen contracts/config foundation**: owns `ai_client/llm/types.py`, `config.py`, and
   `tests/test_phase4_llm_contracts.py`. It defines Generation/Backend/Brain/Audit settings,
   `LocalLLMSettings`, backend request/response/error values, exact `AiAuditRecord`/
   `AiAuditDecision`, `AiAuditWriterConfig`, `AiAuditErrorCode`, `AiAuditError`, `AuditWriteAck`, and
   `serialize_ai_audit`. Its focused tests import only these two owned modules and exhaustively assert
   both finite writer-setting boundaries (including bool rejection), exact exception fields/messages
   and terminal-code invariants, acknowledgement constructor validation, record validation, and
   deterministic serialization. It does not require or test backend/audit/Brain modules.
2. **T021 — backend adapter**: depends on approved T020; owns `pyproject.toml`,
   `ai_client/llm/backend.py`, and `tests/test_phase4_llm_backend.py`. It consumes the frozen T020
   request/config values and does not modify them, package exports, audit, or Brain.
3. **T022 — audit writer**: depends on approved T020 and may run in parallel with T021; owns
   `ai_client/llm/audit.py` and `tests/test_phase4_ai_audit.py`. It imports T020's complete record,
   serializer, writer config, and ack/error types. Its tests own queue/backpressure behavior, lifecycle
   state-to-error mapping, FIFO success sequences from 1 with no failed-item consumption, durable
   write/flush/fsync acknowledgement, cancellation, I/O failure fan-out/future-write behavior,
   repeated close, and executor/file cleanup. They reuse rather than redefine T020 value validation;
   T022 does not import or wait for T021.
4. **T023 — prompt/schema/LLMBrain integration**: depends on approved T021/T022; owns `prompt.py`,
   `decision.py`, `brain.py`, `ai_client/llm/__init__.py`, `ai_client/__init__.py`, and
   `tests/test_phase4_llm_brain.py`. It consumes public T020–T022 APIs and does not modify them or any
   Phase 3 controller.
5. **T024 — completion and real smoke**: depends on approved T023; owns Phase 4 fixtures, completion
   test, smoke script, and only the `tests/conftest.py` marker entry. It does not change production
   logic to make the harness pass.

Any integration mismatch becomes a separately bounded repair packet with explicit file ownership; it
is not pre-authorized to broaden Phase 4. Each packet receives independent tests/review. Shared exports
and `tests/conftest.py` are deliberately assigned to one packet each. `TASKS.md`,
`CURRENT_STATE.md`, and request status remain Integrator-owned.

## Rejected Alternatives

- **Wrap LLMBrain in DeterministicVoteAbilityBrain**: rejected because vote/ability never reaches the
  LLM and ROADMAP 4.4 would be false.
- **One Brain/model per action type or game role**: rejected because it duplicates state/resource
  ownership and couples role to model.
- **Pass WorldState/raw payload/server GameState to the prompt layer**: rejected because it permits
  mixed-version reads and secret leakage.
- **Prompt filtering after full server state was supplied**: rejected; privacy is established by
  network visibility and an allowlisted BrainInput projection.
- **Free-form JSON instruction without schema/parser/controller validation**: rejected because
  fabricated handles/targets could cross the client boundary.
- **Nearest target, first option, deterministic decorator, or canned chat after LLM failure**: rejected
  because it silently substitutes an action the LLM did not authorize and conflicts with canonical
  silence behavior.
- **Automatic provider/structured-mode fallback or HTTP retry**: rejected because request duplication,
  latency, and behavior become unbounded or ambiguous.
- **Unbounded history/response/raw HTTP logging**: rejected for latency, memory, privacy, and prompt
  injection risk.
- **Expand protocol for role descriptions or CO-report vocabulary in Phase 4**: rejected from this
  packet because current roadmap completion can be proved without server-authority expansion; a later
  gate can add explicit client-visible descriptors if canonical requirements demand them.
- **Make real model/GPU a required CI dependency**: rejected by Design invariant 10 and the Phase 4
  request.

## Decision Status

`DECISION_REQUIRED: NO`.

The canonical sources and current public APIs determine a safe, reversible Phase 4 boundary. The
absence of current CO-report vocabulary is handled by no-send rather than a new product rule. Model
choice remains external configuration, so no game or development responsibility is assigned a model
by this design. Independent review is required before any implementation packet becomes READY.
