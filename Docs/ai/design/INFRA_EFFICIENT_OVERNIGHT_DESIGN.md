Status: APPROVED — Reviewer / Sol (`gpt-5.6-sol`), 2026-09-08; final evidence-binding bytes are bound in the review record

# D060: recoverable launch and two-review Qwen workpackages

Author: Infrastructure / Astra (`gpt-6-astra`)
User authority: 2026-09-08 request to implement AIwolf_自動開発システム_調査評価改善方針.md.

## Scope

Infrastructure only. Preserve D059 version-1 runs and their evidence. Add opt-in
version-2 workpackages; never reinterpret an old run as the cheaper workflow.
No game implementation, quota reset, or automatic Gemini dependency in this change.

## Launch recovery (both versions)

Under the shared repository lock, discover existing runs for the exact resolved
package source path and raw package hash before creating a run. Check immutable
package identity, full Controller evidence and resolved run paths. A single valid
matching run is reused including terminal runs; ambiguous, damaged matching runs
stop explicitly. A leftover local-pointer tmp is a hint, never authority: validate
its JSON, path, run and package relationship. Never trust a filename or choose the
latest candidate. Detect same-package run directories even without a tmp hint.
No new provider call until the selected run is durably recorded in local config.

Write a unique tmp, flush/fsync, then replace with bounded PermissionError retry
(short total backoff). Preserve failed tmp for recovery; raise
LOCAL_POINTER_UPDATE_FAILED including the exact existing-run resume command.
Never delete the destination or broaden ACLs to make replace work. Persistent
access errors stop before model calls and preserve the run. Direct `start` also
deduplicates within its declared runs root. Interrupted incomplete initialization
of a matching package is an explicit recovery error, not permission to recreate.

## Version 2 authority and budgets

The workpackage schema retains fixed unit contracts, paths, protected tests, design
hashes, lifetime and quota policy. `version=2` requires planner `provider=qwen`,
`model=local-qwen`, one plan attempt, and a configured upper reviewer. The native
planner executable is the local Python interpreter, not an arbitrary model tool.
Planning is a bounded subprocess against the existing shared Qwen server, schema
constrained, tokenizer checked before generation. No tool access or cloud fallback.
Keep complete declared inputs; oversized context stops rather than truncates.

Reserve exactly 2 upper calls per unit: plan/test approval and final approval.
Reserve one Qwen planning call plus the existing bounded v1 implementation/fix
allowance. Qwen internal fresh review calls also count. No refund after failure.
Plan revision or final rejection stops; no hidden third cloud review. Model calls
persist INTENT and raw response; missing raw means UNKNOWN_DELIVERY/no resend.

## Execution and evidence

Reuse D059 deterministic proposal formation and independent plan approval binding
(source/package/proposal/test/packet/raw hashes and actual reviewer identity).
Only that approval permits protected test installation. The implementation runner
receives the identical derived contract; no second contract review is manufactured.

Use the existing v1 runner directly, with durable child creation intent and reserved
launches, scope checks, baseline and generated tests, bounded fix loop and own lock.
Do not modify AIagent configuration or bypass v1 game Design Gate. Parent retains
shared repo lock, time/stop checks and subprocess timeout. Never restart an orphan
or interrupted v1 execution as a fresh candidate. Unknown/incomplete creation stops.

After v1 reaches AWAITING_REVIEW, final upper review sees the plan approval, fixed
contract, full candidate changes, source/candidate manifests and raw gate evidence.
The complete packet and exact candidate are hashed before dispatch and rechecked
after response. This review combines Red approval and scoped completion judgment;
it is before apply, never permission to apply an unreviewed candidate.
Only a real APPROVED response is translated into the v1 Red approval schema with
its configured reviewer model and exact run/contract/candidate hashes.

Apply uses v1 transactional journal and conflict refusal. Partial writes can only
relax expected hashes to the corresponding validated journal before/after values;
never accept arbitrary source changes. Recovery revalidates final-review evidence.
After apply verify all expected output bytes, then rerun the same candidate gates
against the actual repository under the shared lock. Only those successful gates
and unchanged bytes permit a receipt and the next unit. Post-apply failure stops
with evidence; no automatic reapproval or claims of game Phase completion.

Persist per-unit model usage, unknown usage, wall time, retries, outcome and report
tokens/completed unit without any model-based analysis. Keep failed runs in reports.
Compact prior-unit receipts in model packets to identities/hashes, not recursively
resend entire prior proposals; local verification retains complete receipts.

## Acceptance

- Pointer replace fails after tmp write; restart recovers same run, zero duplicates.
- Bounded retry succeeds after transient failure; permanent failure has actionable error.
- Invalid/mismatched tmp, multiple matching runs, symlink and corrupt evidence stop.
- Fresh-process load/resume reconstructs approval, calls, budgets and receipts.
- Version 2 completes multiple fixture units with two upper calls/unit, no others.
- Qwen planning failure/oversize, quota, stop, source/design/candidate/approval tamper,
  unknown delivery and interrupted apply stop or recover without bypass/replay.
- Same protected and required tests, post-apply gates, path limits and v1 journal apply.
- Legacy D059/D058 tests continue passing; real game run stays unstarted by this fix.

Unit preparation chooses coherent approved scopes that fit local context/output;
never merge unrelated designs merely to lower review count. Gemini remains optional
and not an automatic escalation destination. Unknown subscription headroom remains
unknown; bounded calls are not a promise to retain a percentage of weekly quota.

## Exact extension contract

Version 1 is unchanged. Version 2 has the same top-level/unit/provider keys as v1.
Only `version=2` and planner provider `qwen`/model `local-qwen` distinguish it;
planner executable must resolve to sys.executable, cloud_read retains full input
allowlist semantics. plan_attempts=1. All contracts remain risk=red.
Allocation per unit: upper_max=2, qwen_max=1+runner_launches*2*(max_fixes+1).
The leading 1 is planning; v1's two calls per attempt cover edit and fresh review.
No increase to current maxima (100 per package, 3 launches, 2 fixes).

Parent state retains D059 STATE_KEYS and state version=1; workpackage version
selects its verifier. Parent phases and call keys remain unchanged: READY ->
PLANNING (uN-a1-planner, Qwen) -> REVIEWING (uN-a1-reviewer, upper) ->
INSTALLING_TEST -> RUNNING -> READY/COMPLETE. The existing failure/paused phases
apply. Each planner intent increments qwen_spent; reviewer increments upper_spent.
The direct child reservation increments upper_spent by1 and qwen_spent by
qwen_max-1. Evidence verifier reconstructs these exact sums, not the old +3.

Child retains fields root/source/manifest_sha256/path/upper_grant/qwen_grant.
root=`children/uN`, source=`children/uN/source.json`, source content is the exact
approved contract (repo_root canonicalized only). manifest_sha256 hashes that JSON.
path points to one v1 run strictly under root/runs. upper_grant=1; qwen_grant is
the above implementation allowance. Before plan, persist child and source, then
create intent.json containing {contract_sha256}. Search only root/runs on recovery;
one valid run with identical contract is adopted. Zero with intent, multiple,
PREPARING/ESCALATED, or contract mismatch stops rather than plans again.

Use task.py plan --contract <source> --runs-root <root/runs>; task.py resume --run
<run>; task.py apply --run <run> --approval <root/approval.json>. Every command uses
trusted argument arrays and bounded process execution under parent temporal guards.
Reserve launch count durably before each resume, in root/execution.json with
{launches}; never exceed unit.runner_launches. PLANNED may execute; interrupted
v1 phases may resume only according to v1's own recovery. Only AWAITING_REVIEW
with gates.passed, full candidate_manifest/hash and matching contract proceeds
to final review. APPLYING/APPLIED recover only with saved final approval.

Final artifacts at child root/final/: packet.json, intent.json, raw.json,
record.json. intent={packet_sha256,provider,model}; record adds raw_sha256.
Missing raw after intent is UNKNOWN_DELIVERY. Packet exact fields are version,
stage=`final`, package_sha256, unit_id, proposal (full signed plan proposal),
contract, source_manifest, candidate_manifest, candidate_sha256, files (complete
current unit input files), changes (full edited candidate text), gates,
gate_history, evidence (gate log/JUnit/probe bytes keyed by safe relative path).
These fixed inputs are reconstructed from v1 evidence and checked before/after
dispatch and on load. Retain final packet rather than reconstruct files from
partially applied live source. Hash file texts against source_manifest; change
texts against candidate_manifest, scopes against contract. Reviewer raw schema
is {verdict,reason}, exactly as D059. Its approved result is mapped to v1's exact
approval_version/run_id/contract_sha256/candidate_sha256/verdict/reviewer_role/
reviewer_model object. Also retain intent/raw/record; small v1 approval alone is
never authority for a direct child.

After APPLIED, run the trusted task_gate.gate in a bounded subprocess with a state
copy: candidate=absolute actual repo, candidate_manifest=hashes of the exact live source scope below,
remaining fields from v1 (baseline_nodeids/edits/attempt). Source/output verification
precedes and follows it. Contract and its timeout (clamped downward to remaining
parent time) supply test_paths/test_command/checks. Thus it uses the identical
trusted probe/environment/JUnit/baseline-node and required-node predicates defined
by task_gate.gate, no candidate-supplied command. Store post.json with gates and
hashes of all post evidence. No post result -> rerun mechanical gate only; never
resend final approval or reapply APPLIED. Failed post result -> BLOCKED. Receipts
use v2 exact keys {unit_id,proposal,child,outputs,child_state_sha256,post_sha256};
v1 receipt keys stay unchanged. child_state_sha256 is the APPLIED v1 state hash; child_outputs
verifies final evidence, successful post evidence and approved candidate hashes.

Compact prior receipt packet entries are exactly {unit_id,outputs,
child_state_sha256,post_sha256}; locally replay and validate full receipts before constructing
them. Final input size over packet_bytes, or Qwen tokenizer over its explicit
input limit, stops before corresponding call; no truncation. Report groups calls
and elapsed raw seconds by unit, includes local v1 usage and actual final intent,
launch retries, event elapsed time, completed/failed/active, and unknown token
fields. Repeated mechanical post gates have evidence; no extra cloud cost.

Launch exactness: resolved source path (Windows case-insensitive Path equality)
plus SHA256 of original raw package bytes and canonical saved package JSON digest.
Search only the supplied resolved runs_root, nonrecursive overnight-* directories
(max 10000, otherwise stop); validate with safe_path to reject reparse/ADS paths.
Read tmp files only in local config parent, max16KiB and max100 candidates; retain
only hints whose run parent equals runs_root. Malformed tmp stops explicitly;
valid unrelated old tmp does not authorize another package. Complete matching
terminal run is reused and displayed; duplicate valid matches stop. Incomplete
run directory stops conservatively. Retry replace 4 attempts with sleeps
0.1,0.2,0.3 seconds. fsync the tmp file on Windows; successful os.replace consumes
that tmp, failed tmp remains. `recover` repairs pointer only and never creates a
run or dispatches a model. Immutable old package/deadline are never rewritten.


## Post-apply source scope correction

The live scope is the sorted exact set returned by task_source.selection(contract)
(all tracked eligible files and explicit read/edit/design paths) plus every
allow_new path. Missing allow_new, nonregular/reparse paths, selection changes,
file/aggregate size violations stop. Existing untracked unrelated files and Git
control data are not execution authority. All game/core and test files in the
v1 selection remain protected. source_guard/test_files additionally validate the
parent declared universe and prohibit unexpected tests.

Persist this ordered scope and its hash in post-request.json, together with the
fixed contract (only timeout clamped downward) and the APPLIED state copy. A trusted
subprocess uses an explicit argv/-c wrapper, hashes its wrapper code into the saved
process evidence, sets task_gate.manifest to hash this scope, then calls task_gate.gate
against the actual repository cwd. Before each hash it recomputes selection and
requires identical scope. Thus collection/test/check mutation, new tracked files,
missing outputs and scope changes all fail. All existing gate probe/JUnit/baseline/
required-node predicates remain unchanged. Pre/post full scoped hashes and every
approved candidate hash must agree. Store post evidence in children/uN/post-gate,
separate from immutable v1 pre-apply evidence; include request/wrapper/result hashes.
No installed AIagent module is edited. Constructor/receipt verification only reads
post evidence; missing evidence never triggers tests or model calls during load.

### Final post evidence binding

Before receipt creation, verify post.json and record its raw-byte SHA256 as
post_sha256. Load checks that hash before replaying post/final/APPLIED evidence.
Mandatory raw evidence includes collection result/stdout/stderr/probe; execution
result/stdout/stderr/probe/JUnit; each configured check result/stdout/stderr; and
all other existing files recursively within those safe child evidence directories.
Every mandatory path must be present in the post evidence hash map.
The trusted wrapper writes {gates,producer} to post-run-N/gate-result.json before
post-result.json; producer names that exact post-run-N directory. Its request
binds immutable post-request bytes and wrapper hash. Both producer request and
gate-result are mandatory hashed evidence. Host process result may be absent
after a crash, but wrapper-produced gate-result must exist and equal post-result.
A saved result finalizes without another gate/model/apply. A saved post is verified
and receipt-bound without rewriting. Failed post never creates a success receipt.
