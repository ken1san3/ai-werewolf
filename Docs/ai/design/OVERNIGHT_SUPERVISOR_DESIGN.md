Status: APPROVED

# D059 — bounded overnight controller

Author: Detailed Design / GPT-6 Astra, 2026-09-08

## Purpose and scope

Complete the development infrastructure only. Game implementation and Astra game reviews are excluded from this work.
Normal runtime planning uses Sol; implementation uses Qwen; plan/test approval uses a different configured model (normally Claude).
Astra is not a default runtime provider. Gemini is not on the path.

Replace the unreleased workspace-writing Codex supervisor with a deterministic parent controller.
It consumes one trusted workpackage describing ordered, design-approved implementation units.
The units fix authority, invariants, acceptance and baseline tests; Sol supplies bounded clarification and a new test module.
After an independent review, the parent installs the approved test into its preallocated empty slot and runs one D058 child.
It proceeds to the next unit without user input. A request for a new design, failed review/retry, missing readiness,
quota, time or other unresolved conflict stops it. No future Phase or additional unit is invented at runtime.

This is more than D058's prewritten-contract queue: protected test preparation and contract refinement happen at runtime.
A trusted upper agent still prepares the ordered workpackage from an approved design before launch.
Creating the workpackage and the canonical design are not delegated to the runtime planner.
Completion means all units in this package passed their scoped reviews, not canonical game Phase completion.

## Execution boundary

- Parent: trusted Python in AIwolf, deterministic file operations only. No model has parent tool access.
- Planner/reviewer: packet-only native CLI, read-only, tools disabled exactly as D058 (`process.DISABLED`), isolated cwd,
  minimal environment, fixed argv, structured schema, no shell interpolation. No workspace-write, no `--add-dir`.
- Source edits: only existing v1 apply after D058's review and hash gates. AIagent source/config are never modified.
- Parent repository writes: only the currently allocated new `.py` test slot, after independent approval.
- Runtime files: one externally located parent run and its child roots; AIagent's existing `.task-locks` and runtime usage as v1 requires.
- Tests/production code are reviewed repository code executed under v1's existing trust boundary. This is not an OS security sandbox
  for arbitrary hostile Python; models cannot issue shell commands, but ordinary reviewed tests and implementation execute as before.
- No auto-commit, push, deploy, reset credit, purchase, default Astra provider, or new Claude send scope.
- Qwen must already be running. `llm.alive()` and `llm.tokenizer_available()` must both succeed before any paid call.
  Startup, ownership, killing or restarting the shared Qwen server are out of scope. Readiness failure is BLOCKED.

## Exact workpackage v1

Top-level exact keys:
`version issuer repo_root agent_root not_before expires_at limits providers quota_policy units`.

`version=1`. issuer is nonempty provenance text, not a cryptographic grant. repo_root and agent_root are canonical absolute paths.
repo must be Git; agent_root must be an installed D057 v1. Package must be outside agent_root. Its original raw bytes are bound.
`not_before` and `expires_at` are finite Unix seconds, increasing. The parent deadline is fixed on creation to
`min(expires_at, started_at + limits.seconds)`. Resume never extends it or changes the original package.

limits exact keys and inclusive ranges:

| field | range / meaning |
|---|---|
| seconds | integer 1..28800 (8 hours) |
| upper_calls | integer 1..100, planner/reviewer/child cloud maximum reservations combined |
| qwen_calls | integer 1..100, child local maximum reservations combined |
| plan_attempts | integer 1..3 per unit |
| packet_bytes | integer 1024..262144, complete packet, never truncated |
| output_bytes | integer 1024..1048576, per provider/subprocess |

providers exact keys `planner reviewer`. Each value exact `provider model executable cloud_read`.
provider is `gpt` or `claude`; model is an explicit corresponding model ID, native executable is absolute.
Planner/reviewer model IDs must differ under casefold. Default examples use `gpt-5.6-sol` / `claude-opus-4-8`.
cloud_read is an explicit unique relative path list, with no globs. Every packet file must be on the chosen provider's list.
Missing future files may be listed, but symlinks/reparse points/escape paths/private paths are rejected using v1 safe_path.
quota_policy uses D058's exact `mode reserve_percent snapshot`: strict_reserve or bounded_calls, no invented quota values.
Source bytes and schema are counted before dispatch; snapshot files are read-only advisory data, not instructions.

units: ordered nonempty array, maximum 20. Each unit has exact `id template test_slot after_reads runner_launches`.
runner_launches is an integer 1..3, a maximum number of v1 runner launches including interrupted/resumed launches.
id matches `[A-Za-z0-9_-]{1,60}`, unique. template is a full valid v1 contract at package creation, with risk fixed `red`.
Template validation is v1's unmodified validator, so its initial read/edit files and baseline tests must already exist.
All templates refer to the same repo, have fixed independent design gates, and cannot change the parent/AIagent infrastructure.
For game targets, the existing `ai_status.py implement` gate must allow execution; D058 still rechecks it for every child.

test_slot is a unique, nonexistent relative `.py` file, strictly below one of template.test_paths that is an existing test directory.
It must not be an `__init__.py`, conftest.py, symlink, or overlap a source/edit/new path. Existing tests are never replaced.
All units' allow_new paths and test slots have one creator, are nonexistent initially, and do not overlap each other.
after_reads lists explicit paths produced by earlier units (source allow_new or test_slot only); no forward references.
After each earlier unit succeeds, these inputs become available; the parent appends them to the current read_list.
No model selects new write paths, risk, acceptance, invariants, baseline required_tests, checks, limits, or design gates.
Paths use v1 validation and Windows casefold duplicate/overlap checks. Generated test slots cannot be edited by any Qwen contract.

## Planner response and independent approval

Planner receives: package hash, unit id/index, immutable template, allocated test_slot, already-completed unit summaries,
explicit current source/test/design files from template.read_list/allow_edit/extra_read_context/design and after_reads,
their source hash, and any bounded previous review feedback. No entire repository scan enters a model context.

Planner exact response keys: `action reason goal context test_code required_tests`.
action is IMPLEMENT or NEEDS_DESIGN. reason is nonempty. NEEDS_DESIGN requires the remaining strings empty and required_tests empty.
IMPLEMENT requires nonempty goal/context/test_code and 1..100 unique exact test nodeids rooted at the allocated test_slot.
test_code must parse as Python; its UTF-8 bytes and all response fields fit output limits. It must collect before implementation:
use deferred imports/getattr where the requested interface is not implemented yet. Do not make collection depend on future imports.
The model proposes only clarification and tests, not authority fields. Tests must establish the unit's fixed acceptance.

Parent constructs the proposed contract deterministically:
copy the canonicalized template; append clarification to the original goal/context (preserve the original requirements);
append after_reads and test_slot to read_list, deduplicate;
append the generated exact nodeids to the original required_tests; all other fields remain unchanged.
The file is installed only after the peer approves this full proposed contract plus exact test bytes and base inputs.

In addition, all earlier installed test slots and earlier source allow_new paths are appended to read_list;
their tests remain in test_paths/required_tests. This is necessary because v1 snapshots tracked plus explicit inputs,
and these earlier outputs may still be untracked. It preserves earlier units' regressions without committing during a run.
This union is derived solely from completed receipts and predeclared package paths, and is covered by cloud_read authorization.

Reviewer exact response: `verdict reason`, verdict APPROVED / REVISE / NEEDS_DESIGN / BLOCKED.
The packet includes original package/unit authority, proposed contract, new test bytes, base input hash and completed-unit evidence.
The signed result binds actual provider/model, package hash, unit id, proposal/test/source hash, packet hash and raw response hash.
APPROVED permits only this proposal and this empty test slot. REVISE returns to planning within plan_attempts and remaining budget;
the same fixed authority is retained. Other verdicts stop immediately. Exhausted attempts become BLOCKED.
The parent never writes a review in a model's name without a real persisted call result.

## Shared locks and file identity

The parent holds the actual existing D058 `agent_root/.task-locks/autodev-repo-<sha256(canonical repo casefold)>.lock`
through creation/drive, followed by the parent run lock. This excludes other D058 parents/campaigns for the repo.
One child runs in the same Python process through Campaign.start/drive with `_held_repo` equal to that actually held repo.
No subprocess is asked to acquire this same lock. The child still takes its own campaign lock and v1 subprocesses take v1 repo locks.
Order: D058 repo lock -> parent run lock -> child campaign lock -> v1 run lock -> v1 repo lock.
Models cannot supply `_held_repo`. Source hashes also detect concurrent manual edits; no lock claims to stop an external editor.

Parent snapshots the fixed source universe: all template inputs/edit paths/designs, initial test files under template.test_paths,
future allow_new/test_slot paths (null when absent), and after_reads. Traversal is bounded to these roots, with a file-count ceiling of 2000.
Git HEAD and original workpackage hash are fixed. Before each parent model call/review/test write, current bytes must match expected.
Parent-authorized new test bytes and successful child candidate hashes are the only updates accepted into expected state.
After a child, expected changes are derived from its receipt/v1 candidate evidence, never by trusting a fresh uncontrolled snapshot.
During a resumed partial child apply, defer edit verification to the existing v1 journal/source checks; never start another unit first.
All package/design/protected authority remains unchanged. Test slots are checked again immediately before atomic install.

## Durable state and reservations

All parent state is atomic JSON using v1 atomic_bytes (temporary file, fsync, replace).
Exact state keys:
`version run_id package_source source_sha256 package_sha256 started_at deadline last_observed_at phase resume_phase
reason index attempt expected head calls allocations upper_reserved qwen_reserved proposal child receipts events`.
Identifiers, enums, nonnegative counters, events, saved packet/raw hashes and referenced confined paths are validated on every load.
state version=1, run_id matches directory basename. Snapshot is stored in expected. Events live in the same atomic state record.
No model can edit state files. Runs and children must live outside the repository and AIagent code tree.

Parent model call records have deterministic unit/attempt/stage IDs and INTENT/DONE/FAILED state.
Before dispatch: validate size/scope/quota/source, atomically save packet, draw one upper call from that unit's allocation and persist INTENT in the same atomic state.
After dispatch: atomically save raw response, then bind its hash and result. INTENT without durable raw is UNKNOWN_DELIVERY;
no resend, fallback model, or new parent to silently evade the spent reservation. Saved raw may finish the state transition without another call.
An OS exception after INTENT is also unknown delivery. A known failed response is BLOCKED.

At start, before any provider invocation, calculate and persist the entire package allocation:
for each unit, upper_max = `2 * limits.plan_attempts + 3`,
qwen_max = `unit.runner_launches * 2 * (template.limits.max_fixes + 1)`.
Sum all units' maxima and reject before creating a run if they exceed limits.upper_calls/qwen_calls.
upper_reserved/qwen_reserved are these immutable sums, not measured use.
Each unit starts with upper_spent=qwen_spent=0. A parent INTENT draws one upper call atomically;
creating the one child grant draws 3 upper and qwen_max once, before any child creation.
Drawn allocations are never returned or reused, including unexecuted child calls.
A resumed child spends only its original grant. All runner restarts consume its max_local_calls;
when that finite ceiling is insufficient, stop BLOCKED rather than silently replacing it.

Each child has one red job, draft=false, max_attempts=1, max_cloud_calls=3,
max_local_calls=qwen_max, and receives the exact peer-approved proposed contract.
Qwen implements inside v1 only; no second draft can replace the approved goal/context.
Persist the child grant, dedicated root and source-manifest path before creation.
The parent cannot lower counters, replace a child on failure, or create a new unit after a terminal child failure.

Child manifests inherit the fixed parent deadline as expires_at; max_seconds is the fixed parent lifetime (rounded down, minimum 1), and expires_at enforces the remaining time.
Parent holds the shared lock while discovering/creating a child inside its dedicated root.
If restart finds exactly one existing child there, verify its original manifest/source and reuse it. Zero permits creation;
multiple children or partial invalid state are INVALID, never deleted or guessed. No unbounded filesystem discovery.

## State transitions and stop behavior

| parent phase | next / recovery |
|---|---|
| READY | readiness + source/gate check -> PLANNING; all units complete -> COMPLETE |
| PLANNING | saved/one new planner call -> REVIEWING or NEEDS_DESIGN |
| REVIEWING | APPROVED -> INSTALLING_TEST; REVISE -> next bounded PLANNING attempt; otherwise terminal |
| INSTALLING_TEST | verify approval/source/slot, atomically install exact new test, update expected -> RUNNING |
| RUNNING | reserve/find/start/resume same child; child COMPLETE -> validate receipt/expected, advance index atomically -> READY |
| PAUSED | explicit resume restores previous active phase, unchanged budgets/deadline |
| PAUSED_QUOTA | explicit resume after fresh quota input; same phase and budgets |
| COMPLETE / NEEDS_DESIGN / BLOCKED / UNKNOWN_DELIVERY / LIMIT_REACHED / INVALID | terminal, status/report only |

INSTALLING_TEST recovery accepts an existing slot only if it equals the already approved test bytes; it never overwrites different bytes.
If crash happens after install but before expected is updated, use that approved hash as the sole recovery exception.
RUNNING with child COMPLETE can repeat receipt verification and atomic parent advancement without new calls or writes.
Child PAUSED/PAUSED_QUOTA map to matching parent pause; child UNKNOWN_DELIVERY maps to UNKNOWN_DELIVERY;
other unsuccessful terminal states map to BLOCKED (or LIMIT_REACHED when parent allocation/time is exhausted).

stop CLI writes the parent's `stop` file, reads the validated current child record and also writes its child's `stop` file
if the campaign exists. Child paths must be confined to the recorded dedicated root.
When a child path is not yet known, the parent rechecks its stop file before and after persisting the child root/path,
and after Campaign.start; if set, it writes the newly created child's stop before drive.
Thus a concurrent stop either sees the persisted child path or is observed by the parent after creation.
Existing child guard observes its own stop at existing boundaries. No second watcher thread is required.
It does not kill an unrelated Qwen server or claim a submitted request was cancelled. Ctrl+C uses existing process.execute tree cleanup;
the parent persists PAUSED when possible; uncertain delivery remains uncertain on resume.
resume does not remove stop automatically; an explicit `--clear-stop` flag is required to clear the parent and linked child stop files.
Per-call timeout <=300 seconds and remaining parent time. Existing subprocess size cap/tree termination are reused.
Wall deadline is persisted, with a session monotonic bound; backward wall-clock movement beyond 1 second from last observation stops INVALID.
Automatic polling/wakeup after process exit is not part of this controller. Once running it continues active units without chat input.

## CLI, output and verification

`Run-Overnight.cmd` invokes `scripts/run_overnight.py`.
CLI: `start --package FILE`, `resume/status/stop/report --run DIR`, `doctor --package FILE`,
plus no-argument local `overnight.local.json` with exactly one package or run key (same style as the prior launcher).
Saved/public paths use resolve(strict=True) so Windows AppData aliases do not escape into user instructions.
start prints the real run path before model calls, then final phase/reason. No automatic replacement of failed/complete runs.
doctor checks schema/design/dependencies/source sizes and Qwen readiness, but calls no model; it does not create a run.
status/report call no provider; report combines parent measured usage and linked D058 measured usage, with reservations separately identified.
Unknown token fields stay unknown; no API-dollar conversion of subscriptions. Exit 0=complete/read success, 3=paused/needs action,
4=invalid, 5=lock busy. Test fixtures may inject fake providers/readiness; production CLI has no such bypass flags.

The controller and the running unit's immutable manifest/state are not placed inside repo snapshot selection.
Provider logs never become model context automatically. Explicitly referenced source files are sent in full or the call is refused.

Required verification: fixed-authority/unknown fields/path/slot/source/approval tamper rejection; model self-review rejection;
call maxima and refusal before dispatch; child maxima and no-refund resume; second D058/parent lock exclusion;
plan revision cap; test install crash recovery; child creation/partial apply/complete-before-advance recovery;
unknown delivery/no resend; stops/clock/expiry/quota; Qwen unavailable without cloud call;
two generated-fixture units progressing without chat; actual packet-only Sol planning, different-model review and Qwen apply on synthetic data;
existing D058 and game regression tests. No game feature is implemented during this infrastructure verification.

## Exact durable records (normative)

All digest fields are lowercase SHA256 (64 hexadecimal characters); digest(JSON) uses D058 policy.digest
(sorted keys, ensure_ascii=False, allow_nan=False); byte digests bind exact bytes. JSON rejects duplicate keys and NaN.
Every named record below rejects missing/unknown keys. Lists and dictionaries also validate their members on load.
Paths stored inside records are relative to the parent run unless explicitly identified as absolute external source paths.
No symlink/reparse path is accepted. Referenced artifacts must have matching raw-byte digests before use.

- `expected`: map of normalized repo-relative paths to byte digest or null (absent future file).
  Keys equal the initial source-universe keys stored in `source.json`; this artifact has exact `head expected` keys.
  `head`: the initial Git HEAD string (40 or 64 hex); verify current HEAD before each transition.
  `source.json` is bound as `events[0].evidence`, a raw digest, and initial expected is its full map.
  Validate that source keys include every fixed input and future path and that any additional key is an initial
  regular test file under a declared test directory. Re-enumerate only these test roots and reject unexpected additions.
  Expected changes must be reconstructible from source.json plus the ordered completed receipts and, only during
  INSTALLING_TEST/RUNNING, the current signed approved test. Never accept a state-only hash change.
- `allocations`: map with exactly the package's unit IDs; each exact
  `upper_max qwen_max upper_spent qwen_spent`. Integers; maxima equal the formula above, spent is 0..max.
  Reconstruct spent from one per INTENT/DONE/FAILED call plus any recorded current/completed child grant.
  Both reserved totals equal sum(max), never mutable remaining quota. Completed receipts retain each prior grant.
- `calls`: map with keys `u{index}-a{attempt}-{planner|reviewer}` (zero-based unit index, one-based attempt).
  Each exact `status provider model packet_sha256 raw_sha256`.
  status INTENT/DONE/FAILED. Provider/model equal the corresponding package role. packet_sha256 is mandatory;
  raw_sha256 is null only for INTENT. Files are fixed `calls/<key>/packet.json` and `raw.json`.
  Recompute packet from immutable unit, fixed completed receipts, expected source and preceding proposal/feedback;
  compare both canonical content and raw hash. No arbitrary call paths are loaded from state.
  DONE requires raw exit_code=0, error=null and a schema-valid result; FAILED requires a known failure result.
  Raw exact keys: `exit_code error stdout stderr seconds result usage` (process.invoke result).
  exit_code integer or null, error string or null, stdout/stderr strings, seconds finite nonnegative;
  result object or null, usage object or null (provider-owned usage fields, treated only as measured data).
  INTENT plus durable valid raw binds its digest and transitions without dispatch; missing raw is UNKNOWN_DELIVERY.
- Planner packet exact `version stage package_sha256 unit_id index attempt unit completed feedback files source_sha256`.
  version=1, stage=planner; unit is the immutable full unit. completed is the ordered list of prior receipt objects.
  feedback is empty on attempt 1, otherwise the last actual REVISE reason. files maps fixed authorized paths to full UTF-8 text;
  source_sha256 binds the complete current expected map, including absent paths. Output never controls files or authority.
- Reviewer packet has the same keys as planner packet plus `proposal`; stage=reviewer.
  proposal exact `response contract test_sha256 proposal_sha256`. response has the six exact planner response keys above;
  contract is the deterministic derived contract; test_sha256 binds response.test_code UTF-8 bytes;
  proposal_sha256 = digest({response, contract, test_sha256}), excluding its own field.
  Both roles receive all required inputs; cloud_read must cover that exact union for each role or no dispatch occurs.
- State `proposal`: null or exact `attempt packet approval`, where packet is the four-field proposal above,
  attempt equals current attempt and approval is null or the signed approval object below.
  Recompute contract and both hashes from fixed template, response and completed receipts before accepting it.
- Signed approval exact `version role provider model unit_id attempt package_sha256 source_sha256 proposal_sha256
  test_sha256 packet_sha256 response_sha256 verdict reason`.
  version=1, role=Reviewer, provider/model equal configured reviewer and differ from planner;
  source/proposal/test hashes match this attempt; packet/raw digests equal its saved reviewer call;
  verdict/reason equal actual raw.result. Only APPROVED permits installation. On load validate all bindings,
  including for historical completed receipts; do not trust a fabricated approval field without its saved DONE call.
- State `child`: null or exact `root source manifest_sha256 path upper_grant qwen_grant`.
  root=`children/u{index}`, source=`children/u{index}/source.json`, path=null or one campaign directory directly below root/runs.
  source contains the deterministic inherited D058 manifest; manifest_sha256 binds canonical JSON.
  upper_grant=3 and qwen_grant=unit qwen_max. Persist the record/grant and source before Campaign.start.
  The grant is charged even if source write or child creation then fails. If crash preceded source write, recreate only
  the same deterministic manifest using the saved parent deadline and original child seconds stored in source intent:
  to avoid an additional intent field, write source atomically before child record/grant; an unreferenced source can
  only be reused when its exact deterministic content matches (seconds = max(1, floor(parent deadline-parent started_at))).
  Child expires_at is parent deadline, so that seconds value cannot extend the actual parent time ceiling.
  Source mismatch, incomplete child, or more than one child is INVALID. Verify D058 manifest source and state via Campaign.verify.
- `receipts`: ordered list (not map), exactly index completed entries, each exact
  `unit_id proposal child outputs child_state_sha256`.
  proposal retains the full signed approved proposal, child retains the original grant/path/manifest record;
  outputs maps exactly that unit's allow_edit+allow_new and test_slot to final byte hashes.
  child_state_sha256 binds raw COMPLETE child state.json. Revalidate child state, its saved completion result,
  and v1 candidate/source evidence before accepting outputs; test hash equals signed approval, source hashes equal
  the v1 candidate manifest. Each prior source.json/manifest/approval remains immutable evidence.
  Replay receipt outputs in order over initial expected, then apply only current approved test as appropriate.
- `events`: ordered array of exact `index phase at evidence`, event index zero-based contiguous,
  phase a known parent enum, at finite nondecreasing Unix seconds. First evidence is source.json raw digest;
  other evidence is null. Maximum 2000 entries; refuse before exceeding it. Events are diagnostics, not independent approval.
- All top-level counters/index/attempt are bounded by package; proposal/child may exist only for current unit;
  completed run has index=len(units), proposal=child=null. Active attempt is 1..plan_attempts (READY uses 1).
  package_source is canonical absolute original path; source_sha256 binds its original raw bytes;
  package_sha256 binds saved canonical `package.json`. Both must still match. The saved package is validated on every load
  without requiring previously absent outputs to remain absent; original authority is revalidated against source.json.
  started_at/deadline/last_observed_at are finite; deadline equals original creation formula, and resume_phase is null
  or an active phase. Other enum/counter inconsistencies are INVALID, never repaired by model inference.

D059 approval release metadata is `Docs/ai/infra/overnight.json`, exact
`design review design_sha256 review_sha256`. Paths are confined repo-relative Markdown files.
doctor/start/resume verify actual bytes, the design's Status: APPROVED, the review's Verdict: APPROVED,
Reviewer / Sol signature (independent of author Astra), and that review explicitly contains the design path and byte hash.
Metadata cannot turn an unapproved document into approval. D058 check_design remains required for every child.

Write deny rules (casefolded normalized paths, applying to allow_edit, allow_new and test_slot):
`AGENTS.md`, `Run-Autodev.cmd`, `Run-Overnight.cmd`, `autodev.local.json`, `overnight.local.json`,
all `scripts/`, `Docs/ai/`, `.git/`, `.codex/`, `.agents/`, `.infra-runs/`,
and all paths matched by v1's protected-path rules. Generated test_slot is the sole exception to the
v1 generic test protection, strictly within a declared existing test directory; other deny rules still apply.
No unit repo may be AIagent or a parent/child of agent_root. AIagent paths cannot be relative-path escapes.
Reserved test modules `tests/test_autodev*`, `tests/test_overnight*` and `tests/test_run_autodev*`
are forbidden write slots (prefix match); custom directories do not grant access to those paths.

D058's existing contract canonicalization may only shorten proposed limits.run_timeout_s to an integer
1..min(300, proposed value); its final contract must otherwise equal the peer-approved proposal exactly.
Bind that actual final contract and its v1 evidence through the child job receipt. Historical completed receipts
verify immutable candidate/contract/approval/raw completion artifacts, not equality of the current working tree
with an old candidate (later units may legitimately edit the same allow_edit file).
The current tree must equal the replay of all completed outputs plus the current approved test, except while
resuming a partial child apply, where only that child's allow_edit/allow_new are deferred to v1 verification.

The same-process child uses a limited LinkedCampaign subclass. Its remaining() is the minimum of
D058 remaining() and the parent's monotonic/wall remaining time. Its guard() first runs D058 guard,
then checks the parent's time/stop/package identity only (not source hashes during partial apply),
converting a parent stop/time/error to D058 Pause. This enforces the parent's time bound even while
inside child.drive; no child authority, counters, apply, provider selection or lock is overridden.

Clarification construction is byte-exact for each of goal and context:
`base + "\n\nRuntime clarification:\n" + response_value`.
Do not strip, rewrite newlines or normalize Unicode in either value; nonempty validation uses strip only as a predicate.
Use UTF-8 without BOM for generated tests; response.test_code is written exactly, including its final newline or absence.
Package validation must reject before allocation/start unless the configured reviewer's model ID is an exact member
of AIagent conf.machine().task_runner.upper_review_models. That configured reviewer is the sole D058 child provider;
planner selection is not reused as a fallback child reviewer.
