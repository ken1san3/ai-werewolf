# Phase 6 whole-response output-budget clarification

Task: T246. Responsibility: Architect.
Status: APPROVED

T247 independently approved this plan (handoff50545999caab1fc3d10bcfaf11c48fbc4886a0d6d185cfaa32656cc7f234bbad). Main adopted the reviewed plan; no implementation/value selection or measurement is authorized without its concrete packet.

## 1. Selected route and authority

Select an offline, exact-tokenizer evidence step before selecting a Phase 6 provider budget.
Retain the current operational 96 whole-response-token setting pending that evidence; this is
preservation of the existing configuration, not a finding that 96 is adequate. No replacement
number, including 512, is selected. Do not start P6-I to discover a usable number or run a separate
generation probe before P6-I. Tokenizer measurement and later numeric selection require separately
scoped tasks after independent approval of this clarification.

Authority is selected PHASE6_DISCUSSION_QUALITY_DESIGN.md sections 7/10/11/12, D069, TEST_POLICY,
and current implementation facts. T244's complete handoff SHA-256 is
69d6b55806b545992753a183c5bb3576260b8d2a5b6a5cbd9eb0ca35d0f47fd8.
That handoff's six valid examples measure 331–639 UTF-8 bytes, not exact canonical-9B tokens.
They neither prove 96 insufficient nor establish a sufficient larger value. GenerationSettings'
1–512 range is validation capacity only. This document does not amend the selected design or
supply a Design Gate approval.

Preserve the complete ShortChatConfig (80 characters, 96 UTF-8 bytes, 5–30-token text guidance,
Literal96 field), complete strict semantic response, length-finish rejection even for complete
JSON, at most one full-response repair in the same lease/invocation, at most two CHAT starts,
separate CO, and existing finite timeouts/byte limits. Preserve D069 tempo and exact
Qwen3.5-9B-Q4_K_M.gguf identity. Legacy Phase 5 and Q8 provider limits remain exactly 96.
No schema abbreviation, inferred missing fields, suffix continuation, extra call, timeout
increase, alternate model, fallback, new retry, game rerun, or soak is part of this route.

## 2. Effective boundaries checked

| Boundary | Current behavior and implication |
|---|---|
| Runner `_prepare_run` | Replaces generation with max_output_tokens=96 for all profiles. Phase 6 checks the exact model string separately. Environment parsing supplies endpoint/model/key, not a token override. |
| Runner `_broker_bootstrap` | Propagates temperature, timeouts, byte ceilings and structured mode, but omits the generation cap. Parent-only numeric edits cannot change the broker cap. |
| Runner `_phase5_broker_settings` | Constructs GenerationSettings(max_output_tokens=96); this supplies the actual broker backend. |
| Backend `_request_payload` | Emits generation.max_output_tokens as provider max_tokens for the entire returned content. There is no separate provider meter for decision text versus discussion JSON. |
| ShortChatConfig | Literal96 is constructor-enforced but is not consumed as the backend limit. Its other fields and presence separately control text guidance, text bounds and length rejection. Retaining this object alongside a separately reviewed whole-response cap is technically coherent. |
| Brain and parser | Initial and repaired results require the same full schema; length invalidates before parsing. Final invalidity yields no proposal commit/send. A missing usage count is UNKNOWN. |
| Shared admission | Requests carry messages/schema/request identity, not per-request generation settings. Both initial and repair use the fixed backend; no broker request protocol change is needed. |

Source anchors: scripts/run_phase5_local_smoke.py functions named above and `_broker_child`;
ai_client/llm/config.py LocalLLMSettings; types.py GenerationSettings/ShortChatConfig;
backend.py `_request_payload`; brain.py length-validation branch; decision.py parse_llm_output;
discussion/projection.py initial/repair projection. Exact inspected file hashes and relevant
provider function excerpts are in logs/t246-architect/. Unrelated F collector bytes are not design
authority; any future runner edit must reconcile that file's then-current writer first.

## 3. Smallest bounded measurement contract

This is **tokenizer-only, offline, no inference**. It makes zero provider requests, including zero
tokenize endpoint requests; starts no serving process, game or model; loads no model tensors;
uses no GPU/network/install. Its purpose is to obtain content-token counts for explicitly fixed
valid response witnesses, not throughput, generated prose, finish_reason, provider enforcement,
or Phase 6 acceptance. The following preconditions are currently unresolved, not implied available.

Before dispatch, Main must supply a concrete manifest from authorized local deployment records:

- exact canonical model identity, its explicit absolute GGUF path and a trusted existing artifact
  digest/provenance binding that path to the canonical serving profile;
- the installed offline tokenizer entrypoint and implementation version/build or source digest;
- explicit tokenizer metadata source and digest, bound to that GGUF artifact, including vocabulary,
  merges/pre-tokenization, normalization, added/special-token definitions and flags;
- evidence from the matching installed implementation/config explaining completion counting:
  add-special/BOS behavior, EOS/stop treatment, and whether non-content output such as reasoning
  tokens consumes max_tokens. Model filename or a generic Qwen tokenizer alone is not sufficient.

The measurement packet must name exact files and commands; its worker must verify from source
and installed interface documentation that the chosen entrypoint cannot perform inference before
executing it. This is a bounded read-only prerequisite check, not an additional approval gate. It may use a verified metadata-only reader
of the named GGUF; it must not load tensors or substitute an approximate/different tokenizer.
If identity, offline capability, or counting semantics cannot be established from those supplied
sources, return UNKNOWN with the first cause and stop this objective. Do not scan directories,
query provider endpoints, install packages, repeat known access denials, or silently substitute a
backend utility that loads weights. No concrete tokenizer command is fabricated here: none is
identified in the scoped canonical sources. Main can obtain a narrowly named source inspection
packet if needed; unavailable tooling is a technical prerequisite, not a product question.

Freeze one synthetic corpus before counting, using only public constructors/projector/parser;
no test-private helper import and no real game/private shard. Use T244's six saved response shapes
as six baseline inputs (verify their recorded bytes/digests and parser validity), then six additional
valid shapes: peer REBUTTAL, OPINION_CHANGE, RELATION_HYPOTHESIS, strategy plus assessment updates,
nine-seat pre-vote ranking, and a peer ANSWER with text exactly at the 96-UTF-8-byte bound while
remaining within 80 characters. Use opaque synthetic authorized IDs and explicitly record every
context, handle and revision. Use representative longer IDs in the six additions; record their
lengths without claiming a maximum legal or worst-token case. Each response must satisfy the
existing proposal 16-KiB bound and backend 65,536-byte response bound.

For each of these exactly twelve witnesses, freeze compact and ordinary indented JSON encodings:
24 strings total, at most 65,536 UTF-8 bytes each. Validate each against its actual initial and
repair projection with identical schema/profile before any counting. Different serialization is
measured explicitly because bytes/whitespace can change token counts; neither form constrains
what the real model will emit. Record byte SHA-256 and parser result for each. Stop on first
invalid witness rather than token-counting a malformed semantic response as successful evidence.

Count each of the 24 strings once with the verified content-completion tokenizer flags. Bound the
whole offline job to 120 seconds and one owned worker, with no retries or parallel model work.
Use the existing task host's ordinary owned-process cleanup facilities; no new scheduler/engine.
Identity/parsing/IO/counting failure or timeout preserves the first raw error and completed prefix,
marks the run incomplete and stops. Do not relocate a failing task to bypass the known filesystem
boundary. Reject truncated tokenizer output, noninteger/negative counts and undecodable output.

Record per string: identity-manifest hash, bytes/hash, exact tokenizer options, token count,
parser result, elapsed time and completion status. Record whether counts exclude BOS/EOS or other
provider accounting. If those extra costs remain unknown, record UNKNOWN rather than zero. Raw
synthetic strings may remain in task logs; no credentials, real private text or weight data belongs
in the public handoff. Preserve executable/source/corpus hashes and exact commands. The fresh Reviewer of the subsequent numeric-selection design assesses the exact raw
measurement, corpus, code and identity evidence as part of that required review. No standalone
Tester or additional measurement-only approval gate is added.

## 4. What the measurement permits

For a measured witness with content count N, N greater than 96 shows that this particular complete
encoding cannot be emitted within 96 content tokens under the established counting semantics.
It does not prove that every equivalent valid response exceeds 96. Counts at or below 96 do not
prove actual provider completion or quality. A measured largest witness is not a bound on every
legal proposal; even the additional bounded witnesses are not an exhaustive tokenizer proof.

After the complete measurement, a separate Architect selection must explicitly name either 96
or a finite B within the existing accepted 1–512 range, and justify it against measured response
coverage plus established provider accounting/termination costs. The selection must state its
coverage and uncertainty, including variable formatting, longer IDs, allowed optional updates,
generation behavior and unchanged deadlines. No automatic formula rounds to 512, and no byte or
prompt proxy is used as a token estimate. If mandatory desired witness coverage does not fit a
justifiable B, or unexplained provider accounting prevents a defensible selection, keep numeric
selection and implementation held and return the precise technical cause to Main. This document
does not declare any legal proposal size universally supported by a finite B.

An independently reviewed B is an evidence-based configuration for the existing single P6-I
validation, not proof that generation will complete in four seconds or pass the game. Tokenizer
results cannot close latency, provider, semantic-completion or human-quality acceptance. There is
no preliminary provider generation in this route. If actual generation is later proposed outside
the selected one-game/order/no-rerun contract, Main must treat it as a separate unresolved scope
change rather than interpreting this clarification as authorization.

## 5. Conditional narrow implementation interface

This section becomes implementable only after numeric selection and its fresh approval. Until
then B is a design parameter, not a runtime value or permission to substitute a default.

Public runner interface remains `--phase6`; add no CLI/environment token tuning surface. Define
one runner-local fixed `PHASE6_MAX_OUTPUT_TOKENS = B` using the independently approved literal.
`_prepare_run` uses B only for Phase 6, and exactly 96 for every legacy mode. ShortChatConfig and
GenerationSettings types/defaults stay unchanged. No local config field is repurposed.

For Phase 6 only, `_broker_bootstrap` adds exactly `phase6: true` and
`phase6_max_output_tokens: B` to its existing private bootstrap mapping. It checks the parent
settings cap equals the selected literal before serialization. Legacy bootstrap mappings remain
byte-equivalent for identical existing inputs; neither extra key is emitted in legacy mode.

`_phase5_broker_settings` is the existing reconstruction seam: if `phase6` is absent, require the
Phase 6 cap key absent and retain 96. If present, require its exact value/type `True`, the exact
canonical model string, and an exact int cap equal to the selected B (bool, float and string are
invalid). Missing cap, false/null/malformed marker, mismatched B or stray legacy cap fails before
backend construction/request. The reconstructed GenerationSettings uses this validated B while
all temperature/timeouts/byte limits/structured mode remain on the current path. Initial and
repair therefore receive the same effective B. The fixture does not become a token emulator.

The current backend config fingerprint already covers generation. Preserve its schema, and prove
that parent metadata fingerprint equals the reconstructed broker backend fingerprint. No custom
fingerprint, provider identity relaxation, broker wire-message field or per-request override is
needed. A mismatch fails configuration before any provider request; normal parent and child
construction must share the reviewed settings. In `_broker_bootstrap`, reconstruct the mapping locally through `_phase5_broker_settings` and
compare its backend fingerprint with the parent before returning it. On the real-provider Phase 6
path, `_run_game` additionally compares `ready_broker["backend_identity"]["config_fingerprint"]`
with the parent fingerprint immediately after readiness and before spawning clients; missing or
unequal identity fails with the existing owned cleanup. The top-level ready `config_fingerprint`
is the admission config digest and must not be used for this comparison. The deterministic fixture
retains its distinct synthetic backend identity and makes no real-provider fingerprint claim;
fixture mode cannot be selected through the public production CLI.

Future exclusive write ownership: scripts/run_phase5_local_smoke.py only in `_prepare_run`,
`_broker_bootstrap`, `_phase5_broker_settings`, the local fixed constant and `_run_game` solely for the pre-client
readiness fingerprint comparison; tests/test_phase5_local_smoke.py for focused
configuration tests; named future task handoff/logs. No ai_client, schema/parser/audit, runtime,
World, server, content, protocol or semantic collector change. Because the runner/test files are
also F-owned, Main must serialize this future writer after the current F owner releases them.
If preserving this boundary proves impossible, return the exact finding rather than extending it.

## 6. Required evidence before configuration closure

The future implementation packet maps the following acceptance rows to named tests with literal
oracles independent from production constants. Tests are offline and invoke no provider/model:

| Acceptance | Required evidence |
|---|---|
| Effective selected cap | Phase 6 parent -> private bootstrap -> broker -> serialized actual backend payload contains literal B; initial and repair payloads both do. Assert exact model, generation and unchanged non-generation settings. |
| Legacy compatibility | Phase 5 smoke, Q8 and diagnostic modes produce literal 96 and unchanged bootstrap/payload/fingerprint known answers; unsolicited environment cap cannot override them. |
| Closed bootstrap | Missing B; B-1/B+1 where distinct; zero/513; true, float, string/null cap; false/string/null phase marker; wrong model; stray cap without marker all fail before backend construction. Parent cap mismatch also fails. |
| Config identity | Independently constructed expected fingerprint at literal B equals both actual parent and reconstructed broker fingerprint; changing generation/timeout/structured-mode material produces a mismatch and fails before request. |
| Short output and semantics | Existing literal short-profile, text-byte/character boundaries, unchanged schema on repair, full-JSON length rejection, all five action/proposal identities and trigger-specific rules remain exact. |
| Invocation preservation | Existing same-lease one-repair, two-CHAT and separate CO regressions pass; no new retry/backend fallback or call source. |

Run focused tests, relevant existing Phase 5 runner/short-chat/admission and Phase 6 semantic
regressions, docs, compile and scoped diff checks. Existing completion evidence remains a distinct
F gate; configuration unit tests or a fixture never substitute for it. A distinct Tester records
actual raw results and failures, followed by a fresh Reviewer of final scoped bytes. Do not
attempt known blocked filesystem completion or host-wide CIM operations as this task's check.

After all A–G dependencies and P6-H independent implementation approval, P6-I retains exactly one
finite exact-9B standard_9 game with the existing timeout and ordering. Its existing provider
finish_reason/usage, latency, semantic evidence, game end, audit and cleanup remain measured
facts; missing token usage is UNKNOWN, not zero. First failure preserves evidence and stops;
repair/rerun needs a new scoped task and review. P6-J remains the distinct private human review
of every accepted text and its sole aggregate PASS authority. Tokenizer/configuration review
cannot release I early, close F, or replace either machine or human acceptance.

## 7. Review disposition and unresolved prerequisites

Fresh Reviewer must independently assess source facts, corpus/counting limits, missing tokenizer
identity, conditional propagation/compatibility and unchanged acceptance order. Approval of this
plan alone authorizes no numeric substitution, provider request or implementation. Main alone
adopts a reviewed clarification and dispatches a concrete bounded next packet.

Open technical prerequisites: explicit canonical tokenizer provenance/installed offline entrypoint;
provider completion-accounting semantics; completed exact-token corpus evidence; reviewed numeric
selection and only then scoped implementation/test/review. No material product question is
established. Existing F completion blocker and P6-I gate remain. Architect releases source reads
and stops at the T246 boundary without approving this document.
