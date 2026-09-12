# Phase 4 real-smoke chat acceptance recorded from wrong SessionResult surface

Date: 2026-09-11
Status: CLOSED — T031 independently tested/APPROVED and T025 final confirmation passed
Classification: deterministic completion-runner evidence defect; production chat/LLM behavior passed

## Symptom and evidence

T025 attempt 4 produced 25 valid real-model decisions: 13 chat, 5 vote, 4 CO-declare, and 3 ability.
The LLM client recorded 17 semantically accepted reaction/CO sends, zero rejection or deadline
suppression, and accepted vote/ability reservations. All nine clients reached game end with distinct
PIDs, all ten smoke-owned children exited `0`, stderr was empty, and the audit was non-empty.

The runner nevertheless reported only `missing accepted LLM action: chat.send`. Its server spy records
only direct `SessionResult.reply` values of type `action.accepted`. Production chat acceptance is not a
direct reply: `SessionManager` returns it as `SessionResult.channel_messages[*].acceptance`, and the
WebSocket server passes that internal evidence into delivery. The same server result recorded the LLM
vote/ability direct replies but no chat from any player.

## Repair boundary

T031 must make the runner record `chat.send` from the existing channel-message acceptance evidence,
while preserving direct vote/ability reply recording and every T025 criterion. Add a direct unit that
would fail when the spy ignores `channel_messages`. Do not change production chat, client semantics,
timing, model/config, acceptance, or server/protocol/content.

The tracked attempt-4 model tree was stopped and verified absent. No prompt/response or credential is
included here.

## T031 implementation note

T031 added one runner-private helper that retains direct `action.accepted` reply evidence for
vote/ability and additionally records each `ChatSubmission` from its authoritative
`acceptance.player_id` and `acceptance.action`. A direct no-network regression proves one chat
record is emitted without duplication, ignores the message payload as an author authority, and
keeps the exact request ID and reply sequence for direct vote/ability acceptances.

The Implementer passed the focused completion suite, network/state regression, Phase 3.5 plus
LLMBrain regression, compile, docs, diff, and zero-process checks on Local Windows. No real smoke or
model was started; independent Tester and Reviewer verification remain required before T025 retry.
