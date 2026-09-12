# Phase 3.5 Vote・Ability Controller — Detailed Design Request

Status: REQUESTED
Request status: CLOSED — implemented, independently tested, and APPROVED 2026-09-11
DESIGN: REQUIRED

## Gate evidence

An independent Reviewer applied D051 on 2026-09-10. Phase 3.5 is a new phase-critical
controller spanning World action handles, Brain/dispatch, Network outcomes, deadlines,
composition, async lifecycle, and nine-process completion. ROADMAP §3.5 deliberately leaves
selection, abstention, and deadline policy to detailed design. No implementation is allowed
until the resulting design receives independent approval.

## Required design scope

- Module ownership and boundaries among the controller, BrainController, World, Network,
  Reaction Chat, and composition root
- Public interfaces, immutable configuration/outcome records, and lifecycle
- Serialization for vote, runoff, night0, and night opportunities
- Stable opportunity identity across phase/day, connection generation, action generation,
  and deadline mapping replacement
- Deterministic selection from received VoteAction/AbilityAction handles only, including
  abstention, target counts, uses remaining, empty candidates, and duplicate prevention
- Deadline/start-budget, stale mapping, extension/shortening, disconnect/reconnect, and
  cancellation behavior
- Reservation replacement and accepted/rejected/unknown outcome semantics without treating
  transport receipt as server acceptance
- Failure visibility, bounded retention, and retry boundaries
- Coexistence with Reaction Chat without duplicate controller/Brain ownership
- LLM-free focused, regression, and nine-process completion evidence

## Out of scope

- Suspicion/inference strategy and natural-language quality
- Role-name branches or client-side duplication of server validation/no-selection rules
- Phase 3.5 production code
- Protocol, game-core, or content change unless separately authorized through design review

The Architect must derive answers from canonical sources and current implementation facts.
Any truly underdetermined product choice is returned to the Integrator using the decision
format in `OPERATIONS.md`; it is not silently invented.
