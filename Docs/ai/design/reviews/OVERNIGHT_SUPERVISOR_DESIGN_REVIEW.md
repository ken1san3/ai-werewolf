# D059 overnight supervisor — independent design review

Verdict: APPROVED

Reviewed design: `Docs/ai/design/OVERNIGHT_SUPERVISOR_DESIGN.md`

Design SHA256: `BE641372A705C7D1A2D8810FA6C480C0021122C6D9532782F1DB3C98BADCAB2B`

Author: Detailed Design / GPT-6 Astra

The design is implementation-ready within its stated infrastructure-only scope. Authority remains in a trusted, immutable, ordered workpackage. The runtime planner can add only byte-defined goal/context clarification and one preallocated test; a different configured model must approve the exact contract, test bytes, source identity, and packet before installation. Existing D058 performs Qwen implementation, Red approval, apply, and completion checks.

The design mechanically bounds the parent and every child: all provider maxima are validated and reserved before run creation, grants are durable and never refunded, time never extends on resume, and stop propagation reaches an active same-process child. The shared repo lock, parent/child lock order, source universe, expected-hash transitions, child journal recovery, exact durable schemas, and terminal evidence are specified. The final clarification fixes contract byte reconstruction, limits D058's only permitted contract rewrite to `run_timeout_s`, and requires the child reviewer model to be in `upper_review_models` before allocation.

The earlier direct workspace-writing supervisor remains superseded. This approval does not authorize automatic design changes, game implementation, unlisted writes, Qwen server ownership, commits, pushes, deployment, or unbounded retries. It approves implementing the deterministic D059 controller described by the bound design.

No implementation or provider call was performed in this design review. Review environment: Local Windows.

Signed: Reviewer / Sol, independent design-review lane, 2026-09-08.
