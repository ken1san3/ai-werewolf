# Phase 6 captured-input / audit correspondence gap

Date: 2026-09-13
Status: CLOSED at code/offline boundary by T249 independent PASS and T250 fresh APPROVED.
Overall P6-F completion remains separately BLOCKED; never dispatch from this record.

T243 independent Tester reproduced F1 on actual public BrainInput/context/capture/projection,
LLMBrain and serialize_ai_audit generation material. The collector verifies generation/terminal
hashes and their mutual fields but omits links to the projected capture's day, phase, world
version, before-state hash and base revision. Consistently changed audit/server metadata therefore
still produces semantic_requirements_met=true; this is a false qualification input, not a
measured full-game PASS. CHAT bucket keys also use unchecked day/phase.

Five reproduced gaps: day1->2; phase day->night; world_version1->999; before-state hash changed
while capture.state_sha256 stays; capture.base_revision0->9 while actual proposal/schema/audit
revision stays0. Appropriate prompt/generation digests are recomputed to isolate correspondence.
Direct audit revision drift versus proposal correctly rejects and remains a required control.
All five bad cases retain accepted2/responsive1/pre_vote1/CHATstart1/captrue. The valid baseline
contains accepted CHAT and CO exactly once in order; no imported assertion/oracle helper.

Authority: Docs/ai/handoffs/tasks/T243_PHASE6_TRUSTED_ADAPTER_INDEPENDENT_TEST.md,
SHA256638bfca20ee2e1581a27658aef11060d252d5ec2b29483d6d051402a77e2ff0d.
Raw public-audit-probe/linkage-evidence and14 canonical input/output hashes are under logs/t243-test/.
First probe9cases6PASS3FAIL; supplement6cases1PASS5FAIL (three deliberate repeats). Offline
baseline/outer/opaque/profile checks pass; real filesystem/process completion remains separately
BLOCKED/NOT EXECUTED. First independent F gate failure, not three repair rounds.

Main verified exact handoff/frozen inputs and raw evidence, routed T245 to runner collector and
semantic-completion tests only. Approved P6-A--E/source schema/lifecycle and provider profile remain
unchanged. Closure required repaired old-byte regression proof, independent test and fresh review;
no current F closure or completion-green claim. The environment boundary cannot be bypassed.

Closure evidence: T245 bounded collector/correspondence repair, exact original five failures
rejected and valid full baseline unchanged under independent T249 replay. T250 freshly reviewed
the complete four-file F diff with zero actionable findings,30 selected tests and seven exact replays.
T249 handoff SHA25626c899906335e6d0b3ad1b72ffedfebdad117fbe3cd4b5637f32da0acf8c23d4;
T250 handoff SHA256abff11704f9bf0986b9d7bce3398277dd17e35b958289ee92872d9e308d8e791.
Main verified full reports, evidence seals and frozen inputs before recording this disposition.
Required completion and16 filesystem nodes are not represented as PASS by this closure.
