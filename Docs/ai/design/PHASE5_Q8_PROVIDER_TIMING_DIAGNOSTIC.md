# Phase 5 Q8 Provider Timing Diagnostic Addendum

Status: APPROVED

This addendum defines the smallest privacy-safe observation needed to resolve the T071 ambiguity.
It does not change Q8 acceptance, start a provider, alter the host profile, or reinterpret the T070
result. The approved `PHASE5_NINE_AI_AGENTS_DESIGN.md` remains authoritative except for the narrowly
conditional extra-key compatibility rule stated below. A separate Reviewer must approve this
addendum before implementation.

## Authority and established facts

- The approved Phase 5 design requires separate provider prompt and completion duration from a
  complete `ProviderTiming`; end-to-end latency is not a substitute.
- T070's ordinary nine-client smoke passed. Its sole Q8 attempt completed the nine Q8-A generations
  but failed closed with `PROVIDER_TIMING_UNAVAILABLE` before Q8-B--D.
- T071 proved that exact `ProviderTiming` values survive broker IPC, admission metrics, and Q8
  aggregation. The first unresolved boundary is the successful provider HTTP envelope to
  `OpenAICompatibleBackend` response conversion.
- The current adapter accepts only a `timings` object whose key set equals `prompt_n`, `prompt_ms`,
  `predicted_n`, and `predicted_ms`. Absence, non-object values, missing keys, invalid values, and
  otherwise-valid objects with extra keys all currently become `provider_timing=None`.
- The T070 provider is llama.cpp build 10697 (`093adb242`). The current external `game` profile has
  thinking disabled and does not contain `--perf`; the locally inspected help says `--perf` defaults
  to false. These facts make omission plausible but do not prove the actual response shape.

No user product choice is needed to measure or repair this evidence boundary. Q8's later discussion
duration/server-cap choice remains unchanged and out of scope.

## Scope and non-scope

The implementation authorized after approval may touch only the typed LLM response contract, the
OpenAI-compatible adapter, broker-owned admission metric projection, public LLM exports, the Phase 5
runner's diagnostic-only Q8-A path, and their focused tests. It may add one fixed-schema diagnostic
summary to a new ignored run directory.

It must not:

- retain a complete HTTP envelope, arbitrary key name or value, prompt, schema, generated text,
  header, URL query, exception text, API key, game entry token, admission token, or credential;
- expose one client's generated response or identity mapping to another client;
- infer provider timing from request wall time, broker latency, provider stderr, or token counts;
- change `PROVIDER_TIMING_UNAVAILABLE`, Q8-A--D acceptance, game rules, server, protocol, content,
  generation limits, or ordinary-smoke acceptance;
- start, stop, probe, upgrade, or configure the model during implementation or review;
- edit `C:\AIagent\agent\config.toml` as a repository implementation step; or
- retry a diagnostic run or combine it with Q8-B--D.

## Typed observation contract

Add these frozen, model-neutral types beside `ProviderTiming`:

```text
ProviderTimingShapeStatus =
    ABSENT
  | NOT_OBJECT
  | MISSING_REQUIRED_KEY
  | INVALID_REQUIRED_VALUE
  | VALID_EXACT
  | VALID_WITH_EXTRA

ProviderTimingFieldState =
    NOT_OBSERVED
  | MISSING
  | INVALID_TYPE
  | INVALID_VALUE
  | VALID

ProviderTimingDiagnostic:
    status: ProviderTimingShapeStatus
    prompt_n: ProviderTimingFieldState
    prompt_ms: ProviderTimingFieldState
    predicted_n: ProviderTimingFieldState
    predicted_ms: ProviderTimingFieldState
    extra_key_count: int
```

`extra_key_count` is a non-negative integer bounded by the existing complete-response byte limit. It
counts keys other than the four fixed required names; their names and values are never copied. The
four field names above are the complete diagnostic vocabulary. The type has no arbitrary mapping,
string detail, raw value, or extension field, and its representation is therefore safe for the
metadata-only evidence path.

### Field classification

The adapter must distinguish a missing top-level member from a present null/non-object value; it may
not use `envelope.get("timings")` as the observation input without a separate membership check.

- `ABSENT`: the successful decoded envelope has no top-level `timings` member. All four fields are
  `NOT_OBSERVED`; `extra_key_count=0`.
- `NOT_OBJECT`: `timings` is present but is not a JSON object. All four fields are `NOT_OBSERVED`;
  `extra_key_count=0`.
- For an object, each absent required key is `MISSING`.
- `prompt_n` and `predicted_n` are `VALID` only for a JSON integer represented by exact Python `int`
  (not `bool`) with value at least zero. A different JSON type is `INVALID_TYPE`; a negative integer
  is `INVALID_VALUE`.
- `prompt_ms` and `predicted_ms` are `VALID` only for an exact Python `int` or `float` (not `bool`)
  that is finite, at least zero, and converts through the existing round-half-up microsecond rule.
  A different JSON type is `INVALID_TYPE`; negative, non-finite/invalid-number, or unconvertible
  numeric input is `INVALID_VALUE`.

Overall status uses this fixed precedence:

1. missing top-level member -> `ABSENT`;
2. present non-object -> `NOT_OBJECT`;
3. object with any missing required key -> `MISSING_REQUIRED_KEY`;
4. all required keys present but any required field not `VALID` -> `INVALID_REQUIRED_VALUE`;
5. all required fields valid and `extra_key_count == 0` -> `VALID_EXACT`;
6. all required fields valid and `extra_key_count > 0` -> `VALID_WITH_EXTRA`.

This precedence gives each successful response exactly one status while the fixed field states retain
enough cause information for a bounded repair. It deliberately reveals neither arbitrary JSON type
names nor any timing value.

## Acceptance behavior and extra-key compatibility

The first diagnostic implementation is observational. Existing strict behavior remains:

- `VALID_EXACT` produces the existing normalized `ProviderTiming`;
- every other status produces `provider_timing=None`; and
- optional timing shape never makes an otherwise-valid generation fail.

Therefore the first measured run distinguishes the two T071 hypotheses without silently changing
the result being measured.

After that run, `VALID_WITH_EXTRA` is sufficient evidence for a separate bounded compatibility
repair. That repair may change only the adapter membership check from exact key-set equality to
required-key subset membership. It must parse the same four required values with the same exact
validation and normalization, ignore every extra member without inspecting, copying, logging, or
serializing it, and still reject missing or invalid required values as timing-unavailable.

This addendum supersedes only the original phrase "complete exact keys" insofar as it required the
provider object to contain no extra member. "Complete" continues to mean that all four fixed
required values are present and valid; the normalized `ProviderTiming` remains exactly four fields.
The original approved design and implementation history are not rewritten.

## Ownership and propagation

1. `OpenAICompatibleBackend._parse_response` owns classification because it is the only layer that
   sees the decoded HTTP envelope. It constructs one `ProviderTimingDiagnostic` for every otherwise
   valid successful response and attaches it to a new final optional field on
   `StructuredGenerationResponse`.
2. The new response field defaults to `None`. Existing Phase 4 callers, fake backends, positional
   construction, audit records, and `StructuredLLMBackend` implementations remain source-compatible.
   `None` means "backend did not provide this diagnostic", not `ABSENT`.
3. In Phase 5, the one broker owns the direct HTTP backend and reads the diagnostic only while
   recording that call's existing `PROVIDER_CALL_TERMINAL` metric. The fixed metric projection adds
   the six values: overall status, four field states, and `extra_key_count`.
4. The diagnostic is deliberately omitted from `_structured_response_to_wire` and
   `_response_from_wire`. It is not needed by a gameplay client, so the authenticated owner response
   remains unchanged. Existing prompt and generated-text payloads travel only on that invocation's
   authenticated owner request/response path; neither they nor the diagnostic may cross sessions.
   The broker's invocation/opaque-client association and existing response routing remain
   authoritative.
5. `AdmissionMetric` and `serialize_admission_metric` use explicit optional enum/count fields only.
   No generic details mapping is introduced. Non-provider events and responses from older/fake
   backends serialize those fields as null.
6. The diagnostic-only Q8-A aggregator reads only the broker-owned terminal metrics. It requires one
   diagnostic for each of exactly nine successful calls, zero dropped metrics, and matching terminal
   lifecycle evidence. It writes fixed status/field-state counts and extra-key count min/max only;
   it never reconstructs an envelope.

`ProviderTimingDiagnostic` may be exported from `ai_client.llm` for typed direct-backend tests. It is
not added to AI audit records, prompts, client status files, game messages, or wire protocol.

## Diagnostic-only runner contract

Add an explicit mutually exclusive runner mode (recommended CLI spelling
`--q8-provider-timing-diagnostic`). It reuses the approved Q8-A plan exactly: one broker, one worker,
three deterministic synthetic prompt buckets, three sequential requests per bucket, one cold and
eight warm, the existing per-request bound, and a 120-second row bound. It never starts or owns the
external model and stops after Q8-A regardless of outcome.

The mode requires two lowercase SHA-256 inputs in addition to the existing non-secret model/config
identity:

- provider serving-executable fingerprint, computed read-only from the bytes of the exact executable
  path resolved through OS process metadata for the serving PID in the Tester's owned provider tree;
- host `game` profile fingerprint, computed from canonical JSON containing only its exact `model`
  and `args` strings.

The serving artifact must also have a sanitized version/build identity tied to that same executable:
implementation name, version, numeric build, and commit identifier only. The Tester obtains it from
that executable's bounded version output or from the owned serving process's startup identity, checks
it while the same serving PID's resolved path is hashed, and retains only those fixed fields.
Implementation and version are ASCII tokens matching `[A-Za-z0-9._+-]{1,64}`, build is a
non-negative decimal integer, and commit is lowercase hexadecimal matching `[0-9a-f]{7,64}`; failure
to parse that allowlist is not replaced by raw text. Raw command lines, executable paths, environment,
version output, and process metadata are not evidence fields.
A launcher or wrapper SHA-256 may be retained as an additional fixed fingerprint, but never
substitutes for the serving-executable fingerprint.

Before creating a diagnostic output directory or starting a broker/worker, the Tester must prove the
provider process is in the owned tree, resolve its exact serving executable, hash that file, obtain
the sanitized identity, and match the expected profile/backend identity. Failure to resolve, read,
hash, or bind the identity to the exact serving artifact fails preflight with the sole result
`PROVIDER_TIMING_DIAGNOSTIC_INCOMPLETE`; the diagnostic runner does not start. The same
serving-executable fingerprint and sanitized identity must match at the end of every diagnostic or
verification run before the provider is stopped.

The Tester records how the two required hashes were computed and records the fixed boolean
`provider_profile_perf_enabled`; it does not copy other profiles or secrets. The machine summary
stores only the serving-executable and profile hashes, the sanitized fixed provider identity, that
boolean, the existing backend config fingerprint, the diagnostic counts, lifecycle/cleanup result,
and hashes of retained evidence files. Supplying a malformed or missing fingerprint in diagnostic
mode also fails before output/process creation as `PROVIDER_TIMING_DIAGNOSTIC_INCOMPLETE`.

The command returns success only when the nine requests and broker lifecycle are clean **and** all
nine calls have accepted complete `ProviderTiming`. Evidence completeness is evaluated before any
timing-shape result. It requires the serving-artifact preflight above; exactly nine successful
requests; exactly one well-formed terminal diagnostic associated one-to-one with each request; zero
dropped metrics; consistent request, response, metric, and summary counts; proven child/broker
terminal state; a matching end fingerprint; and clean exact-owned-process/file cleanup.

If any completeness condition fails, the command returns only
`PROVIDER_TIMING_DIAGNOSTIC_INCOMPLETE`. It must not publish a shape routing conclusion, authorize
`--perf` or an adapter repair, or label the primary failure `PROVIDER_TIMING_UNAVAILABLE`, even when
the available records contain a uniform shape or complete timing. The canonical
`PROVIDER_TIMING_UNAVAILABLE` result is preserved only after the entire completeness gate passes and
one of the complete-evidence shape branches below lacks nine accepted complete timings. This keeps a
cleanup/lifecycle failure distinct from a timing-capability failure.

## Exact one-run routing tree

After implementation focused tests and independent review pass, an independent Tester may execute
the diagnostic mode exactly once. No retry is allowed in that task.

0. **Completeness gate:** evaluate every preflight, evidence, lifecycle, metric, count, fingerprint,
   and cleanup condition above first. Any failure has the sole routing result
   `PROVIDER_TIMING_DIAGNOSTIC_INCOMPLETE`; preserve the bounded evidence, route one
   diagnostic-implementation investigation, and do not run or select branches 1--5.
1. **All nine `VALID_EXACT`, all nine complete timings:** diagnostic PASS. No adapter or host change;
   route to one normal Q8-A--D measurement task.
2. **Every status is `VALID_EXACT` but any timing is unavailable:** impossible contract mismatch;
   classify a production propagation bug and route one minimal Implementer repair. Do not change the
   host profile or infer timing.
3. **Every status is in `{VALID_EXACT, VALID_WITH_EXTRA}` and at least one is
   `VALID_WITH_EXTRA`:** route the single adapter compatibility repair defined above. No host profile
   change is authorized by this result.
4. **All nine `ABSENT`:** route one reviewed host-operation task for `--perf`. Do not change the
   adapter based on absence.
5. **Any `NOT_OBJECT`, `MISSING_REQUIRED_KEY`, or `INVALID_REQUIRED_VALUE`, or any mixture involving
   `ABSENT` and a present-object status:** classify provider response instability/incompatibility.
   Make no permissive parser change; route to provider capability/version review or upgrade.
Only after branch 0 passes may exactly one of branches 1--5 be selected, using their listed order.
`VALID_WITH_EXTRA` never counts as Q8 timing success until the separately reviewed compatibility
repair is installed and verified.

## Conditional repair and no-loop boundary

The selected branch gets one scoped change and one Q8-A verification attempt after its focused tests,
independent Tester verification, and independent Reviewer approval:

- Extra-key branch: only the required-subset adapter repair; host profile remains byte-identical.
- Absent branch: only the reviewed `game` profile `--perf` operation; repository remains identical.
- Exact-valid propagation branch: only the proven projection defect.

If that single verification does not yield nine complete timings, stop. A newly exposed
`VALID_WITH_EXTRA` after the one `--perf` verification is the sole intermediate continuation rather
than a rollback-triggering terminal failure. In that case the independently reviewed `--perf`
profile bytes remain exactly in place, without another host mutation, while the already-specified
required-subset adapter repair is implemented, tested, and independently reviewed and while its one
final Q8-A verification runs. This is the sole permitted two-boundary sequence.

Any other non-passing result from the `--perf` verification, including incomplete evidence, requires
the provider to be stopped and the exact pre-operation profile bytes to be restored before routing
to `UNRESOLVED_PROVIDER_TIMING` or diagnostic investigation as applicable. Failure or incomplete
evidence from the final combined `--perf` plus subset-adapter verification likewise requires the
provider to be stopped and those same original pre-operation bytes to be restored. A passing final
combined verification retains the reviewed `--perf` bytes for the separate standard Q8 run. No third
code or configuration repair, automatic rerun, acceptance relaxation, or wall-time inference is
allowed.

"Byte-identical" is relative to the actual pre-task host profile for each route. A direct extra-key
branch begins without a host mutation and must leave its own pre-task profile bytes unchanged. The
special two-boundary branch records its original pre-`--perf` bytes as the rollback target; the
adapter-repair task begins from the already-reviewed `--perf` bytes and makes no further host change,
but failure of the combined verification restores the original pre-`--perf` rollback target.

The eventual standard Q8-A--D closure run is a separate accepted measurement, not a diagnostic
retry, and starts only after the selected boundary has passed its verification.

## Host `--perf` operation boundary

`C:\AIagent\agent\config.toml` is host operational state, not repository design or game authority.
An `ABSENT` result authorizes consideration, not an unreviewed edit. A separate task must:

1. prove the exact serving build and `game` profile, preserve the file's before SHA-256 and exact
   non-secret `game` model/args, bind the exact serving-executable fingerprint and sanitized identity
   to the owned provider process, and confirm no owned runner or model tree is active before
   mutation;
2. add exactly one `--perf` flag to `profile.game.args`, changing no model, thinking setting, context,
   endpoint, other profile, or repository file;
3. record after SHA-256 and an exact before/after profile diff, independently review it, then start
   and stop the provider only under the established bounded ownership procedure for the single
   verification run; and
4. treat a complete `VALID_WITH_EXTRA` result as the sole intermediate continuation: stop the
   provider, retain the reviewed after bytes, and carry their fingerprint into the subset-adapter
   task and final combined verification without another host mutation;
5. after any other failed or incomplete `--perf` verification, preserve evidence, stop the provider,
   and restore the exact before profile bytes; and
6. after a failed or incomplete final combined verification, preserve evidence, stop the provider,
   and restore that same original before image. Only an accepted complete timing verification may
   retain the reviewed operational change for the standard Q8 run.

This is an engineering evidence operation and does not choose discussion duration or another game
rule. No user product decision is required.

## Privacy source-to-sink matrix

The diagnostic adds no new payload transport. Tests enforce the following source-to-sink matrix;
"allowed" means only the existing authenticated owner/control path, not a new evidence sink.

| Protected source | Existing narrowly allowed transport | Forbidden sinks |
|---|---|---|
| Prompt/schema and generated text | Prompt/schema only in its owning request frame; generated text only in the matching owning response frame | diagnostic object or repr, diagnostic response fields, admission diagnostic/JSONL fields, summary, retained diagnostic output, stdout/stderr, handoff, and every other session's frame |
| API key, game entry token, admission token, and authentication/control secrets | Existing private provider or authenticated bootstrap/control transport for the exact owner only | diagnostic object or repr, every diagnostic/evidence/log/summary/output/handoff sink, and every other session's frame |
| Arbitrary timing extra-key names/values and sibling provider-envelope fields | None past the adapter; it may inspect only membership/count and fixed required-field validity needed to classify | diagnostic object or repr, structured response wire, every IPC response/control frame, admission metric/JSONL, summary, retained output, stdout/stderr, and handoff |
| Provider HTTP headers/URL material and exception text | Only their existing backend-to-provider request or in-memory error-handling boundary | diagnostic object or repr, IPC response/control frames, admission metric/JSONL, summary, retained output, stdout/stderr, and handoff |

The fixed status, four fixed field states, and bounded `extra_key_count` are the only diagnostic
projection allowed past the adapter. Prompt/text sentinel assertions exclude the matching owner's
ordinary payload frame from their forbidden scan, then require absence from every listed diagnostic,
evidence, log, and cross-session sink. Secret sentinels likewise exclude only the exact existing
private control transport that owns them. Extra-key/value and sibling-envelope sentinels have no
transport exception and must be absent from every downstream sink, including the owner response
wire. Tests must decode and inspect frames by owner/session rather than treating all IPC bytes as one
forbidden sink.

## Required tests and evidence

Before the one real diagnostic:

- table-drive missing top-level `timings`, present null/string/list, each missing required key, each
  invalid required type, negative/non-finite/unconvertible values, exact valid, and valid plus one or
  multiple arbitrary extra keys;
- assert the exact status precedence, four field states, extra count, round-half-up conversion, and
  unchanged strict first-stage `ProviderTiming` result;
- mutation tests proving that an extra-only object, partial object, and valid-required-plus-extra
  object cannot be confused, and that the later subset repair accepts only the last case;
- backward-compatibility tests for existing Phase 4 response constructors/fake backends and the full
  existing Phase 4 LLM suite;
- broker metric tests proving one call/one diagnostic association, optional-null compatibility,
  exact serialization allowlist, no metric drops, and unchanged response IPC schema;
- two authenticated-client tests proving diagnostics stay broker-owned and no response/diagnostic
  crosses client sessions;
- privacy sentinels placed in an extra key name/value, sibling envelope fields, prompt, generated
  text, header, exception, API key, entry token, and admission token; scan each sink according to the
  source-to-sink matrix, proving owner-path payload remains functional while every forbidden
  diagnostic/evidence/log/cross-session sink is clean;
- runner tests for mutually exclusive mode, validation-before-output, exactly nine Q8-A calls,
  stop-before-Q8-B, every post-completeness decision-tree branch, incomplete-over-shape precedence,
  timing-complete plus cleanup-failure classification, malformed/missing diagnostic evidence, exact
  serving-executable fingerprint provenance/mismatch/unresolvable preflight, sanitized identity,
  cleanup, and existing Q8 fail-closed behavior; and
- focused Phase 5 short-chat/backend, generation-admission, and local-smoke tests, followed by the
  existing Phase 4 and P5-A/P5-B/P5-F regressions, compile, docs, and diff checks.

The one real diagnostic retains the sanitized summary, broker admission JSONL, child status/result
files, empty/bounded stdout/stderr, file hashes, command without secrets, exact process ownership and
zero-orphan cleanup inventory, the two environment fingerprints, and the Tester's routing conclusion.
Existing response/audit content is not copied into the diagnostic summary or handoff.

## Rollback and decision status

The diagnostic code is optional metadata. Repository rollback removes the optional response field,
fixed metric fields, diagnostic-only runner mode, and focused tests without changing established
provider timing, generation, broker, game, or Q8 acceptance behavior. A conditional adapter repair is
independently revertible to exact key-set equality. Host rollback restores the byte-identical saved
profile only under the stopped-provider boundary above.

`DESIGN_STATUS: APPROVED`

`Q8_ACCEPTANCE_WEAKENED: NO`

`USER_PRODUCT_DECISION_REQUIRED: NO`

`FIRST_REAL_DIAGNOSTIC_BUDGET: ONE_Q8_A_ATTEMPT`
