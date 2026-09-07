# INFRA_QWEN_RUNNER_DESIGN — Qwen-First Local Task Runner (v1)

Status: APPROVED
DESIGN: REQUIRED
- **Author role/model:** Detailed Design / Claude (Opus 4.8)
- **Approval:** MUST be signed by a *different* model in a separate review file (D053). This document does not self-approve.
- **Target path:** `C:/AIwolf/Docs/ai/design/INFRA_QWEN_RUNNER_DESIGN.md`
- **Grounds for DESIGN: REQUIRED:** D056 authorizes a Qwen-first infrastructure investment but explicitly states it does *not* approve any implementation method, and that new modules, execution state, and change-apply responsibilities must pass a real Design Gate signed by the acting role. D051/D053 require an independent model's approval before implementation of new modules that execute developer code and mutate the working tree. This is exactly such a module. It is infrastructure, not a numbered game Phase; the legacy game Phase gate (AGENTS.md §"Design invariants", `ai_status.py design`) is retained unchanged for game work.

---

## 1. Scope and non-goals

**In scope (v1):** a deterministic, local, single-machine runner that lets Qwen (via the existing `lib/llm.py`) perform contract-bounded implementation/fix tasks against selected repository files, prove them with real `pytest` collection + execution, gate the result mechanically, and emit a handoff artifact for a human/model to apply. The runner is a CLI over a persistent on-disk state machine.

**Non-goals:** no cloud API integration, no Gemini dependency, no new network client (reuse `lib/llm.py` → `llama-server` only), no queues, no long-running services, no web UI, no provider abstraction framework, no auto-commit, no push, no merge. A single local model server with **serialized** calls; `--n-cpu-moe 35` is the *reported* `dev` profile config (D056/config.toml, not a new benchmark).

**Honest security posture:** the runner executes developer-authored Python (candidate code + its tests) with the local user's privileges. The isolated candidate snapshot is a *working-tree isolation* mechanism, **not an OS security sandbox**. It does not contain arbitrary malicious Python. It reduces accidents (protects real tests/config/rules from candidate edits, keeps runs outside the source tree, avoids copying `.git`/secrets/outputs), but commands MUST NOT be described as hermetically sandboxed. Anyone running this accepts that a hostile task JSON or hostile candidate code can affect the machine. Task JSON is therefore **trusted, user-authored** input; commands are **trusted argv arrays run with `shell=False`, never model-generated shell strings**.

---

## 2. File layout

```
C:/AIagent/agent/
  lib/
    task_contract.py   # load+validate contract JSON; path allowlist; fail-closed
    task_state.py      # persistent state machine, atomic writes, locking
    task_source.py     # source selection, hashing, snapshot build, manifest
    task_gate.py       # mechanical gates, risk classification, evidence parse
    task_runner.py     # orchestration: plan/run/resume; serialized llm calls
    task_apply.py      # preflight, backup, apply, rollback, recovery
  tools/
    task.py            # CLI entry: plan|run|resume|status|apply|rollback
C:/AIwolf/
  scripts/ai_status.py           # add read-only "infra" surfacing of run status
  Docs/ai/design/INFRA_QWEN_RUNNER_DESIGN.md   # this file
  Docs/ai/decisions/             # follow-up decision when v1 lands (author-signed)
```

The runner lives entirely under `C:/AIagent`. AIwolf integration is limited to: (a) `scripts/ai_status.py` gaining a read-only `infra` view that prints the latest run's phase/risk/evidence pointer without launching anything, and (b) docs. The game Phase gate is untouched.

---

## 3. Task contract JSON (trusted, user-authored)

A single whole-file JSON document. All runner outputs are also whole-file JSON (never partial streaming). Required top-level fields:

```
{
  "contract_version": 1,
  "task_id": "kebab-case-unique",
  "repo_root": "C:/AIwolf",
  "goal": "human-readable natural-language objective",
  "risk": "green" | "yellow" | "red" | "hard_red",
  "read_list": ["relative/path.py", ...],        // files model may read
  "allow_edit": ["relative/path.py", ...],        // files model may modify
  "allow_new": ["relative/new_file.py", ...],     // new files model may create
  "protected": ["tests/**", "**/*.toml", "Docs/ai/**", "*.yaml"],
  "test_paths": ["tests/"],                        // pytest target(s), protected
  "test_command": ["python","-m","pytest","-q","--junitxml","{junit}","{tests}"],
  "extra_read_context": ["relative/ref.md"],      // large refs, read-only
  "limits": {
     "max_file_bytes": 262144,
     "max_total_edit_bytes": 524288,
     "max_model_output_bytes": 131072,
     "max_fixes": 2,
     "run_timeout_s": 1800
  },
  "smoke": false                                   // true => run smoke task only
}
```

**Validation is fail-closed.** Unknown top-level keys → reject. Missing required key → reject. `risk` not in the enum → reject. `test_command` MUST be an argv array (no shell string); `{junit}`/`{tests}` are the only permitted placeholders. `allow_edit ∩ protected ≠ ∅` → reject (a file cannot be both editable and protected). `test_paths` must be inside `protected` coverage. `contract_version` mismatch → reject.

### 3.1 Path rules (applied to every path in the contract and every path the model touches)

Reject, before any model call:
- absolute paths, `..` traversal, drive-relative escapes;
- symlinks, junctions, reparse points (resolve and compare real path is inside `repo_root`);
- Windows ADS (`file.py:stream`), reserved device names (`CON`, `NUL`, `COM1`…`LPT9`, trailing dot/space);
- case-insensitive duplicates within a list (`Lib/x.py` vs `lib/x.py`);
- any path resolving outside `repo_root`;
- any path matching `protected` when it appears in `allow_edit`/`allow_new`.

Path checks run at contract load *and* re-run against the candidate manifest after the model returns (see §7).

---

## 4. Source selection and dirty handling

Candidate source = the union of: files reported by `git ls-files` under `repo_root`, restricted to `read_list ∪ allow_edit ∪ test_paths ∪ extra_read_context`, **plus** files named in `allow_new` (which must not yet exist). We compute SHA256 for every included tracked file at plan time — this is the **source input manifest**.

Dirty-tree policy: we do **not** refuse a dirty tree wholesale. We include the *current on-disk bytes* of relevant tracked files (dirty or not) in the base snapshot and hash exactly those bytes. We refuse only on **concurrent modification / hash conflict**: if a file's hash at `apply` time differs from the hash recorded when the candidate baseline was prepared, the operation aborts (§8). This lets normal in-progress work proceed while still protecting against races.

Snapshot construction (`task_source.build_snapshot`): copy only the selected files into a run directory **outside the source tree** (`C:/AIagent/agent/runs/<task_id>/<run_id>/candidate/`). Never copy `.git`, credential files, or benchmark/output directories. The snapshot is where the model's edits are applied and tests run — the real repo is never mutated during run/plan.

---

## 5. CLI (`tools/task.py`)

All subcommands take `--contract path.json` and operate on persistent state; they are idempotent and resumable.

- `plan --contract c.json` — validate contract, select source, build source input manifest, classify risk, write initial state; **no model call**. Prints what will be read/edited and the gate plan.
- `run --contract c.json` — execute the model task in the snapshot, run gates, up to `max_fixes` fix cycles, produce evidence + handoff artifact. Serialized single model call per step.
- `resume --contract c.json` — continue an interrupted run from the last atomically-persisted phase without re-running the model if a validated candidate already exists (§9).
- `status --contract c.json` (or `--task-id`) — print current phase, risk, gate result, evidence path; read-only.
- `apply --contract c.json` — apply a PASS-gated candidate to the real tree after preflight (§8). Human-invoked step.
- `rollback --contract c.json` — restore from persisted backup (§8).

Exit codes: `0` PASS/clean; `2` gate NON-PASS; `3` escalation required (Red/Hard Red); `4` validation/contract failure; `5` lock/state error. Non-zero for anything not a clean PASS.

---

## 6. Persistent state and locking

State lives at `runs/<task_id>/state.json`, written **atomically** (write temp + `os.replace`). Phase transitions are **monotonic** and each transition is persisted before the next action:

```
PLANNED → SNAPSHOT_READY → MODEL_EDITED → GATED → (FIX_1 → GATED)* → PASS | NONPASS | ESCALATE
```

The state records: contract SHA256, source input manifest, snapshot manifest, current phase, per-phase evidence pointers, fix count, and the run input revision (`git rev-parse HEAD` + a flag if dirty bytes were included).

**Locking:** `runs/<task_id>/lock` holds pid + monotonic start time + host. Acquire before any mutating subcommand. **Stale-lock recovery:** if the lock's pid is not alive (OS check) *and* the recorded start is older than `run_timeout_s`, the lock may be broken with an explicit logged `stale_lock_recovered` note; otherwise the command refuses (exit 5). We never silently steal a live lock.

---

## 7. Model interaction and candidate manifest verification

Exactly one serialized `lib/llm.py` call per model step. Inputs are already length-guarded by `lib/llm.py` (`input_token_limit`, `_reject_oversized_input`, tokenizer preflight with character fallback), so oversized context fails closed before spending compute. `max_tokens` and `schema` are set so the model returns a **whole-file JSON** object mapping each edited/new relative path to its full new contents:

```
{ "files": { "relative/path.py": "<full file text>", ... }, "notes": "..." }
```

After the model returns, before running anything:
1. Re-apply **all** §3.1 path rules to every key in `files`.
2. Reject any key not in `allow_edit ∪ allow_new`.
3. Reject if any `protected` / `test_paths` / config / rule (`*.yaml`, `*.toml`) path appears — the model **cannot** edit tests, config, or rules.
4. Enforce `max_file_bytes` and `max_total_edit_bytes`.
5. Write files into the snapshot, then recompute the **candidate manifest** and verify: protected/test/config/rule files are byte-identical to the base snapshot. Any drift → NON-PASS, no test run.

New tests written by the model are **not trusted by provenance**: the same model that wrote the code wrote them. They run, but their passing is corroborating evidence only — the pre-existing protected `test_paths` are the authority. If a task's goal is "add tests," those new tests still run inside protected-suite context and cannot replace or weaken existing tests (existing tests are protected from edit).

---

## 8. Gates, risk levels, and evidence

Gates are **mechanical**, not LLM self-report. The gate never accepts a model's claim of "tests pass."

- **Collection gate:** `python -m pytest --collect-only` on the snapshot must succeed (exit 0, no collection errors). Import/collection failure → NON-PASS.
- **Execution gate:** run `test_command` producing a **JUnit XML** (`{junit}`). PASS requires: process exit code 0 **and** JUnit shows `errors=0, failures=0` and `tests>0`. Pass/fail is taken from exit code + JUnit counts + raw last line, never from any summary (AGENTS.md §Prohibitions, D034).

**Evidence** (`runs/.../evidence/`) captured for every gate step: raw combined stdout **and** stderr (separate files), exit code, final N lines, the JUnit XML, the run input revision, and environment (Python version, platform, profile name from `conf.profile()`). Evidence is raw; no interpretation is stored as truth.

**Hard limits:** `max_file_bytes`, `max_total_edit_bytes`, `max_model_output_bytes`, `max_fixes` (≤2), `run_timeout_s`. Exceeding any limit is NON-PASS/ESCALATE, never PASS. **Unknown or malformed outcomes** (unparsable JUnit, killed process, empty model body, timeout) are treated as NON-PASS or ESCALATE — never PASS.

**Fix loop:** on gate NON-PASS, at most `max_fixes` (≤2) additional serialized model calls are made, each fed the raw failing evidence, each re-verified through §7 and re-gated. After the limit, stop.

### 8.1 Risk-level reviewer policy

- **Green:** no mandatory reviewer. PASS gate → handoff artifact.
- **Yellow:** a **fresh reviewer** model call (independent of the fixer context) that returns **structured, concrete findings only** (a JSON list of `{file, line?, severity, finding}`), no free-form approval, no code. Findings are attached to the handoff; they inform the human, they do not auto-apply.
- **Red:** never applied by the runner without an **independent final review**. The runner emits ESCALATE (exit 3) and the handoff artifact; a different model/human must review before any `apply`.
- **Hard Red:** escalates **before local implementation** — the runner refuses to run the model at all and emits an escalation handoff (exit 3). Local implementation is not attempted.

Fail-closed: if `risk` is missing/unknown, treat as at least Red. If the reviewer step for Yellow fails to return valid structured findings, downgrade to NON-PASS/escalate, not PASS.

---

## 9. Interruptions, resume, and never re-running the model needlessly

Any interruption (Ctrl-C, timeout, crash) leaves the last atomically-persisted phase; the run is treated as **non-PASS** until re-validated. `resume` inspects state: if a `MODEL_EDITED` candidate exists and its candidate manifest still verifies against the recorded hashes, `resume` re-runs **gates only** and does not call the model again. The model is only re-invoked if no validated candidate exists or a fix cycle is pending. This honors "never rerun the model unnecessarily if a validated candidate exists."

---

## 10. Apply, backup, rollback, recovery

`apply` is human-invoked and only proceeds for a PASS-gated candidate (Green PASS, or Red/Yellow that a human/independent review has separately approved — the runner records but does not fabricate that approval).

**Preflight:** recompute SHA256 of each target file in the real tree and compare against the **source input manifest** recorded when the baseline was prepared. Any mismatch → refuse (concurrent modification; exit 5). This is the only dirty-refusal condition.

**Backup + hashes:** before writing, copy each target's current bytes to `runs/.../backup/` and persist **before-hashes**; after writing, persist **after-hashes**. Writes are atomic per file (`os.replace`).

**Rollback:** restores from backup, but **refuses to overwrite later user edits** — if a file's current hash differs from the persisted after-hash, rollback stops on that file and reports it rather than clobbering newer work.

**Partial-apply recovery:** apply records per-file completion; if interrupted mid-apply, `apply --contract` (or `rollback`) resumes/reverses using the per-file before/after hashes to determine which files were written.

**Never** auto-commit, touch `main`, or merge. Apply modifies the working tree only; committing is left to the human.

---

## 11. Required negative tests (must exist before v1 is accepted)

1. Contract with `..` / absolute / symlink / junction / ADS / reserved name / case-dup path → rejected at load.
2. `allow_edit ∩ protected ≠ ∅` → rejected.
3. Model output naming a protected/test/config/rule path → NON-PASS, no test run.
4. Post-model candidate manifest shows a protected file changed → NON-PASS.
5. Oversized model output / oversized file → NON-PASS (limit).
6. `pytest --collect-only` error → NON-PASS (no execution).
7. JUnit shows failures but process exit 0 (or vice versa) → NON-PASS.
8. Unparsable JUnit / killed process / timeout → NON-PASS or ESCALATE, never PASS.
9. Hard Red contract → refuses before any model call, exit 3.
10. `apply` when a target's hash drifted from the source input manifest → refused.
11. `rollback` when a file's hash ≠ persisted after-hash → refuses that file.
12. Stale-lock: dead pid + old start → recovered with note; live pid → refused.

## 12. Real-model smoke task (independent of game behavior)

A built-in `smoke: true` contract that targets a throwaway module under the run directory (e.g. a trivial "return the sum of a list" function with a pre-written protected test asserting `sum([1,2,3])==6`). It exercises: one real `lib/llm.py` call to the `dev` profile, whole-file JSON parsing, snapshot, path checks, real `pytest` + JUnit, and the Green PASS handoff — with **no dependency on any game logic or game file**. This proves the pipeline end-to-end against the actual local model server.

## 13. Handoff artifact

Every run emits `runs/<task_id>/<run_id>/handoff.json`: task_id, contract SHA256, risk, final phase, gate result, evidence pointers, candidate manifest with hashes, reviewer findings (Yellow) or escalation reason (Red/Hard Red), run input revision, and the exact `apply` command to run. This is the sole cross-model/human transfer object; no state is passed implicitly.

## 14. Independent approval binding

This design is `Status: IN_REVIEW`. A **different model** (not Claude) records approval in a separate file `Docs/ai/design/reviews/INFRA_QWEN_RUNNER_REVIEW.md`, containing the reviewer's role/model signature and the **SHA256 of this exact `INFRA_QWEN_RUNNER_DESIGN.md` file** it approves (D053, R-97). Any later edit to this design invalidates that binding and requires re-review. An infrastructure request manifest may carry its own gate; approving it does not imply Phase 3.4 or any game Phase is approved.

---

*Signed: Detailed Design / Claude (Opus 4.8). This document authors the design only; it does not grant its own approval, does not represent another role's judgment, and records no measured values as fact beyond the config-declared `--n-cpu-moe 35` and the 2026-08-30 profile benchmark already in `config.toml`.*

## 15. Implementation contract clarification (takes precedence over ambiguous v1 prose)

Author: migration coordinator / GPT-6 Astra. This addendum is part of the design submitted to independent Sol review; no approval is claimed. Earlier Claude text remains attributed to Claude only.

- Each plan creates a unique immutable run directory under `--runs-root` (default agent/runs), outside repo_root. For AIagent self-tasks use another explicit root outside C:/AIagent. CLI `plan --contract FILE [--runs-root DIR]`; `run --contract FILE [--runs-root DIR]` creates and executes a run; `run|resume|status|apply|rollback --run DIR` operate on that run. `apply` optionally takes `--approval FILE`; `unlock --run DIR` is the explicit dead-owner recovery command. No task-id lookup ambiguity. No implicit latest-run mutation.
- The contract is copied into the run, SHA256-bound. Add REQUIRED fields `context` (nonempty description including API/data/control/errors/ownership), `invariants` (nonempty string list), `acceptance` (nonempty string list), `required_tests` (nonempty exact pytest nodeid list), `design_gate` object, and `checks` (list of trusted argv arrays). Remove `smoke`: smoke is an ordinary contract against a disposable git repo, not a privileged mode. `test_command` is a list of pytest selection/options ONLY after the fixed `[sys.executable, '-m', 'pytest']` prefix; it cannot supply JUnit/collection/deselection or no-test flags; runner appends its own report path. Collection and execution use identical selection. `test_paths` are nonempty relative file/directory paths, not globs/nodeids, whose selected files are protected automatically. `checks` run additionally (AIwolf includes check_docs). Contract declares `limits.max_tokens` (1..8192), other hard limits as above; max_fixes is 0..2. Values are positive ints, not bools; unknown fields fail. Fixed hard ceilings cap file/output limits, timeouts and total snapshot bytes (64 MiB).
- `design_gate` is either `{decision: NOT_REQUIRED, reviewer: nonempty, reason: nonempty}` or `{decision: REQUIRED, reviewer: nonempty, reason: nonempty, author: nonempty, design: relative_file, sha256: hex64, status: APPROVED}`. REQUIRED verifies design hash and author != reviewer; trusted contract authors own the truth of signatures. Gate fields never come from Qwen. Design file is protected and included in the source manifest. This is an evidence check, not cryptographic proof of identity. Existing AIwolf game gate must also be satisfied for game contracts; infrastructure approval cannot override Phase 3.4.
- Snapshot copies ALL tracked regular files after forbidden-source filtering, plus explicitly named read/context files (untracked permitted), preserving relevant dirty bytes. Do NOT feed all snapshot bytes to Qwen: prompt includes only read_list, allow_edit and extra_read_context plus contract. Git symlinks/submodules, reparse points, secrets (.env variants, private-key extensions, credential files), .git, model/binary artifacts and runtime output directories are rejected if explicitly requested; excluded otherwise. Missing tracked input rejects preparation. Record the selected file list and hashes, absent allow_new paths, git revision, dirty flag. At apply, verify the entire selected source manifest and unchanged tracked selection, not only write targets. Added tracked inputs invalidate the plan. allow_edit must exist; allow_new must be absent and disjoint.
- Protected applies to patterns plus test_paths, design file, AGENTS.md, canonical/config files and explicit gate inputs. allow_new tests are permitted ONLY outside existing protected paths and alongside unchanged protected tests; they cannot be required_tests authority in this v1. Existing protected tests remain immutable. Test-engineer replacement/mutation automation is deferred; no claim v1 certifies model-authored tests. JSON parsing rejects duplicate keys, unknown keys, nonstring/empty contents, empty file map, NULs and source/diff markers. Model finish_reason must be stop; use llm.raw(build_payload(...)), preserving full response and usage. Catch SystemExit as a model error. A malformed edit never changes candidate files.
- Versioned state includes run UUID, contract/source hashes, phase, monotonic event index, attempt (0..max_fixes), review outcome, validated candidate manifest, gates and journal. Phases may cycle by attempt; event index strictly increases. Persistent transition before external action. Atomic JSON via temporary sibling + replace. Per-run mutating lock and global model lock use OS-held advisory file locking; process death releases OS lock, metadata remains for diagnostics. Lock files are persistent, never unlink/recreate during acquire. `unlock` can only clear stale diagnostic metadata after obtaining OS lock, and logs recovery; cannot release another live process's lock. This replaces unreliable PID-age-based automatic breaking. Single machine/local filesystem only; hostile concurrent modification is out of scope.
- Model results saved BEFORE candidate writes. Apply model files all-or-nothing after complete validation, using a fresh candidate built from base plus final edited contents. Save expected entire candidate manifest BEFORE tests. After every gate, compare all candidate source bytes to this manifest. Ignore only known generated caches/reports; unexpected new source files, protected changes, removed files, symlinks or changed edits mean integrity failure and escalate without auto-fix. Reports are outside candidate and freshly removed/unique per attempt. Write a read-only pytest collection plugin supplied by runner outside candidate; records exact collected nodeids and imported module origins. Required nodeids must be collected AND executed without skipped/error/failure; gate rejects zero tests/all skipped, malformed JUnit or exit/report disagreement. Verify project-local top-level module origins are inside candidate (exclude standard/external dependencies); disable pytest plugin autoload and strip PYTHONPATH/PYTHONHOME, set private temp and pycache paths. This improves isolation, not a malicious-code defense. `checks` execute in same candidate environment.
- Yellow reviewer schema `{verdict: PASS|FINDINGS, findings: [{severity: high|medium|low, invariant: string, evidence: string, correction: string, files: [allowed paths]}]}`; PASS requires empty findings; FINDINGS requires nonempty. Each finding must cite a contract invariant; invalid output escalates. Concrete findings feed one fixer cycle, within shared max_fixes. After changes rerun gates and fresh review. Style-only suggestions are excluded by prompt and cannot invent new invariants. Model review never replaces mechanical PASS. Red successful gates => AWAITING_REVIEW; apply requires external approval JSON `{run_id, candidate_sha256, verdict: APPROVED, reviewer: nonempty}` and preserves it. Hard Red => ESCALATED without LLM. Green/Yellow successful => READY, not semantic proof/auto-commit.
- Model transport/length/context errors => ESCALATED; test failures may fix. Crash at MODEL_RUNNING without saved response retries that call on resume (cannot prove remote completion), recording interruption. Saved validated result/candidate resumes gates without generation. Completed READY/APPLIED/ROLLED_BACK runs are idempotent. Crashes during candidate writes recover from saved result and immutable base; contract/base tampering rejects resume. A failed final attempt => ESCALATED plus exact evidence. Snapshot changed after READY requires revalidation, never direct apply.
- Apply performs full preflight before any writes and verifies saved candidate/source/contract hashes. Backups of original bytes and planned after-hashes are durable BEFORE first write. Journal enumerates every before/after value (null means absent) before file changes. Phase APPLYING saved first. Crash recovery accepts each current target only if equal to planned before or after; third value => conflict and no overwrite. Validate ALL targets first, then roll forward. Source read-only inputs must still match. Per-file os.replace; absent-file creation is also journaled. Rollback preflights ALL targets before restoring/deleting, rejects any third-value edits, and uses original base backups; it handles partially applied sets. Parent dirs created by apply may remain empty. Never restore/delete a path not in allow_edit/allow_new. Rollback interruptions remain ROLLING_BACK and may be resumed. Reparse/containment checks immediately precede writes. No guarantee against a malicious external process racing the check; file watchers/editors cause detectable conflicts under normal operation.
- Handoff always records status honestly (READY means tests passed, not reviewer approval), hashes, actual evidence, model usage, next command. `ai_status.py infra` prints infrastructure metadata and paths; ordinary roles retain existing game gate. Infrastructure gate metadata is a separate checked-in JSON with design SHA and signed review file SHA. Bad/missing metadata => blocked. check_docs verifies the infrastructure binding when metadata is present, with no AIagent/LLM dependency in CI.

### Independent review corrections (coordinator, pending Sol confirmation)

- In addition to the per-run lock, apply/rollback/recovery MUST hold a repo-scoped OS advisory mutation lock keyed by SHA256 of normalized/casefolded resolved repo_root, in a shared lock directory under the installed agent. Lock acquisition order is run then repo. Hold repo lock from before preflight until all journaling/writes/state persistence finish. This serializes distinct runs against the same repository. Preparation records file hashes before and after snapshot copying and rejects concurrent changes; final apply still checks every input. v1 assumes one installed agent per local user and local filesystem; external editors remain hash-conflict checks, not lock participants.
- Before the first model call, collect the pristine base candidate with the exact execution selection/environment/plugin, persist baseline nodeids and raw evidence. Baseline collection failure is ESCALATED (never ask Qwen to invent a smaller suite). Each candidate collection must equal the baseline set, or be a superset only for explicitly declared new test files. Lost baseline nodeids always fail. Required nodeids must be present in baseline and executed without skips/errors/failures. Execution report must cover every collected nodeid (setup errors/skip included as non-PASS); no silently missing execution is accepted. These rules do not claim arbitrary malicious candidate code cannot forge reports.
- Red external approval has EXACT fields `{approval_version: 1, run_id, contract_sha256, candidate_sha256, verdict: APPROVED, reviewer_role: Reviewer, reviewer_model}`. Version is integer 1, not bool. All three identity/hash fields must match persisted run. reviewer_model must be in the trusted runner configuration `[task_runner].upper_review_models` (default explicit GPT/Claude identifiers, no Qwen/Gemini); no arbitrary nonempty reviewer text. Reject missing/unknown fields, unknown model, wrong role/verdict, stale hash. Save exact approved JSON bytes and SHA256 before APPLYING. Signatures remain user-trusted records, not cryptographic attestation; the runner never generates approval for a caller.
