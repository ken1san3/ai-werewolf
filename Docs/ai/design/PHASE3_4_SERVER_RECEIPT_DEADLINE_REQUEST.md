# Phase 3.4 Server Receipt-Time Deadline Design Request

Status: REQUESTED
Request status: CLOSED
DESIGN: REQUIRED

## Purpose

Define the smallest server-authoritative receipt-time contract for chat and coming-out
requests. The design must ensure that authorization and accepted-message evidence use one
timestamp sampled once, while preserving the approved Phase 3.4 behavior and keeping rule
decisions out of the network layer.

## Triggering evidence

- The post-review full regression preserved the exact Day 1 Reaction Chat result but found
  later accepted records whose test-side timestamp equaled the phase deadline.
- T005 investigation found that ability requests already receive an authoritative `now`,
  while chat and coming-out core calls do not; the completion spy currently samples time a
  second time after authorization.

## Required decisions

- Which server-owned component samples the single receipt timestamp.
- How that timestamp reaches chat and both coming-out authorization paths.
- The outcome when `now >= phase_ends_at`, including tick/dispatch ordering.
- How accepted-delivery evidence records the same timestamp used for legality.
- Compatibility and deterministic before/equality/after boundary tests.

## Constraints

- Do not change phase durations, game rules, acceptance thresholds, or protocol payloads
  unless a protocol change is proved strictly necessary.
- The network layer remains a typed dispatcher and does not decide game legality.
- Do not filter assertions, add retries/skips, or begin Phase 3.5 work.

Task packet: `Docs/ai/tasks/T006_PHASE3_4_SERVER_RECEIPT_DEADLINE_DESIGN.md`
Handoff: `Docs/ai/handoffs/tasks/T006_PHASE3_4_SERVER_RECEIPT_DEADLINE_DESIGN.md`
