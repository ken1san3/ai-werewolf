# D068 — External Phase 5 review disposition and semantic acceptance authority

Date: 2026-09-12

Status: Accepted design boundary — T159 independently approved by T161; T162 applies the bounded
documentation and CI remediation.

## Context

After the Phase 5 checkpoint, an external review raised F1--F5 and M1--M9. T158 and T160 checked all
14 claims against code, tests, approved designs, and retained T154 evidence. T159 defined the
smallest remediation and T161 approved it with zero findings. The review is useful input but is not
canonical authority, and its inferred performance targets do not become game rules.

Phase 5 remains formally complete. It proved one shared exact 9B provider path, nine clients,
bounded admission, short output, server-authoritative game completion, private audit, measurement,
and cleanup. Belief, strategy, semantic quality, and responsive conversation were explicitly Phase
6 scope.

## Private transcript content inspection

T158 Investigator and T160 Reviewer performed manual/content-level inspection of the retained T154
private audit shards. This was not a user transcript review. The raw text remains private and is not
copied into this decision or other active public documentation.

Aggregate observations:

- 101 audit records retained prompt, response, and structured decision fields;
- 71 decisions contained text: 48 chat and 23 CO declarations;
- the 48 chat decisions matched the server's accepted-chat counts per player;
- only five distinct utterances occurred, all short repeated role claims;
- 18 literal self-role mismatches occurred, and actual werewolf seats repeatedly self-disclosed.

This establishes a Phase 6 quality input, not a failure of Phase 5 transport/runtime acceptance. The
existing private audit is the evidence source; a duplicate text logger would enlarge privacy risk
without adding observability.

## Terminal disposition of all findings

| ID | Disposition | Evidence, reason, and future route |
|---|---|---|
| F1 | **FIXED** | The no-text-retention premise is false: private audit already contains exact bounded prompt/response/decision fields. Active docs now distinguish Phase 5 mechanics from conversation quality and record only aggregate T158/T160 inspection. Semantic repair remains Phase 6 Design Gate scope; no second logger or raw-text publication. |
| F2 | **DEFERRED — Phase 6 entry** | T154 measured Q8, but the user-owned gameplay choice remains open in `OPEN_QUESTIONS.md`. The proposed 180-second/5–6-speech/queue-three values were not measured. The user must select current rules, a tempo, or a new Design Gate for a client-neutral general cap before Phase 6 implementation. |
| F3 | **DEFERRED — Phase 6** | Roughly 3k-token prompts and history contribution are measured; an 800–1,000-token optimum, cache reuse, and alternate-model benefit are not. Phase 6 must design authorized bounded summary/recent context and then measure the exact 9B once; no 35B/fallback. |
| F4 | **NOT APPLICABLE** | Frames are bounded length-prefixed strict JSON, not HMAC-signed; constant-time comparison protects the HELLO token. Authentication, owner isolation, fairness, stale cancellation, and concurrency one are approved Phase 5 requirements. No broker reduction is authorized. |
| F5 | **FIXED** | Active operations now name one semantic PASS authority and require route reassessment after three failed rounds on the same acceptance/evidence objective, even when mutation names differ. This records the T140 lesson without a quota engine or weakened acceptance. |
| M1 | **NOT APPLICABLE** | `ProtocolMessageValidator.decode_server()` already applies the shared Draft 2020-12 envelope and concrete-payload schema before reducer materialization. Reducer semantic/state recovery checks are a separate necessary boundary. |
| M2 | **NOT APPLICABLE** | `ShortChatConfig` literal values plus runtime equality checks are the approved inspectable fixed profile and are directly tested. Moving them to hidden constants fixes no behavior. |
| M3 | **DEFERRED — Phase 6 tooling hardening** | Ruff/mypy are absent, but baseline scope/cost is unmeasured. A bounded Phase 6 infrastructure task must measure first, choose reviewed rules/paths, and avoid blanket ignores before making CI blocking. |
| M4 | **FIXED** | Existing push/pull-request CI now runs the non-completion partition on Python 3.10–3.13 and the completion partition once on Python 3.13. The two marker selections remain disjoint and union the default collection; completion was not moved to nightly/manual-only. |
| M5 | **FIXED** | README now states Phase 5 complete/Phase 6 not started, points volatile test evidence to `CURRENT_STATE.md`, identifies `standard_9` accurately, and makes `pyproject.toml` authoritative for runtime dependencies. No generator was added. |
| M6 | **DEFERRED — Phase 6 housekeeping** | Retained Phase 4 evidence, user/inherited paths, and ACL-inaccessible temporary roots cannot be swept safely. A separate housekeeping task must prove ownership/type/reparse facts and preserve hash/path mapping before any exact cleanup. Root clutter does not invalidate T154 lifecycle cleanup. |
| M7 | **DEFERRED — Phase 6 / Phase 8 on touch** | Large functions exist but no behavior defect was shown. Split only AI code materially changed in Phase 6 or content-loader code materially changed in Phase 8; do not proactively refactor the closed runner or unrelated network code. |
| M8 | **DEFERRED — Phase 8** | `standard_9` is nine seats with six unique role types. LLM-free all-role tests exist, but deterministic alternate-role network completion does not. Phase 8 must force fox/nekomata/wise-werewolf/privacy interactions; current ROADMAP wording is corrected now. |
| M9 | **DEFERRED — Phase 8** | `rules.guard` and `rules.medium` are canonical configuration namespaces, not runtime role-ID branches. A capability/effect-key migration is a public schema decision for the Phase 8 MOD-loader Design Gate. |

## Semantic acceptance authority

Each acceptance or evidence objective names exactly one semantic PASS authority. A wrapper may own
outer preflight, process lifecycle, cleanup, and evidence sealing, but it must not parse the same
nested evidence into a second, competing PASS. It delegates the semantic result to the named
authority and may independently fail closed only on its own outer boundary.

After three failed correction rounds on the same acceptance/evidence objective, the Integrator stops
the repair loop and reassesses the route even if each round uses a new symptom or mutation label.
Unknown causes go to an Investigator; a boundary mismatch goes to an Architect. The original
acceptance remains unchanged. This is a judgment rule, not a task quota, scheduler, validator
framework, or automatic workflow engine.

## Consequences

- The Phase 5 checkpoint and T154/T155 engineering evidence remain valid; no rerun is required for
  this documentation/CI repair.
- The exact 9B remains canonical. 35B, alternate, fallback, model search, and comparison are outside
  this decision.
- Q8 remains open until the user selects one approved product path; no benchmark extrapolation closes
  it.
- Phase 6 prompt/memory/context work requires a new detailed design and independent approval, then
  one finite exact-9B game with a separate private transcript-quality review.
- M3/M6 are bounded Phase 6 infrastructure/housekeeping debt; M7 is on-touch; M8/M9 belong to Phase 8.
- No product source, game rule, prompt, model configuration, frequency default, or acceptance value is
  changed by this decision.
