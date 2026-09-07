# D059 overnight supervisor — independent implementation review

Verdict: APPROVED

Reviewed design: `Docs/ai/design/OVERNIGHT_SUPERVISOR_DESIGN.md`

Design SHA256: `BE641372A705C7D1A2D8810FA6C480C0021122C6D9532782F1DB3C98BADCAB2B`

Implementation set SHA256: `118008D507AFB2A01A6755AA5265DD3D4338310B72AC259629D0DBE3F048903D`

The implementation-set hash is SHA256 over the UTF-8, LF-terminated rows below in path order.

```text
d55b011393a0fd46ef2bed62d32fe5579a4b8365ac1d8137feb6f71910f0d7da  Docs/ai/infra/overnight.json
16ac67269fd899da06169b8ce88d5dd533e38f26aef8dd5ef0a2a2afa84697bf  scripts/autodev_lib/process.py
743dfdd80ce7652a047629839d147d980704cf13cd4a6de84ef960477dc80a99  scripts/infra_status.py
db4dc8d28a80f0758b4ec650b4e3f8538732243a5d789de3e798837f92bb784b  scripts/overnight_demo.py
d3d6d4e8490a20615cfd59c2e48be011887940bb91ca4397188039196afb7392  scripts/overnight_lib/__init__.py
06986b1cc22c3a94205f335c020a1db413bc36b14d12472c3377a4ed49919049  scripts/overnight_lib/engine.py
bc3bdfdf1adeb55968bb8af9a61dd2ecb997e3dbbae981a2612bc0d5cd163337  scripts/overnight_lib/evidence.py
3826e77ea1d3ebb5e2740824f0f9e90ab9e0271514345783d4eea3714608a5ad  scripts/overnight_lib/policy.py
b10b9f0a347a3d9b4be5e622a2331c1c3828ea3573f6cd92eefe3a067e3c9944  scripts/overnight_lib/provider.py
e3b2325b42ab59be14a4219d1404cfa1a91e6d8e21d2803ca52cb5d50b3cf914  scripts/overnight_lib/report.py
0f4aa123aa055dc95a606c69d5ce870b68ebd39bb1b8c614815e1aa89566245b  scripts/run_overnight.py
fd10d3d3f27fbf00028018944e5a1715a15d2f134da9d0f2aa7fb5fed7c18bfa  tests/test_overnight_controller.py
```

No blocking implementation finding remains. The deterministic controller keeps write paths, tests, acceptance, invariants, design gates, providers, readable files, budgets and unit order in the immutable workpackage. Planner output can change only the byte-defined goal/context clarification and its allocated new test. A different configured model approves the exact proposal before installation; D058 then performs contract, Red and completion reviews around Qwen execution and journaled apply.

State and failure handling match the approved design. Parent calls persist INTENT before dispatch and bind DONE or known FAILED raw responses; missing raw remains UNKNOWN_DELIVERY and is never resent. Package-wide call maxima are reserved up front, child grants are charged once, and runner relaunches consume the original finite grant. The shared repo lock spans the controller and linked D058 child. Test installation, child creation, partial apply and completed-child advancement recover from their bound artifacts. Source relaxation occurs only for a verified v1 apply journal. Stop propagation reaches the child, while `--clear-stop` runs only after exclusive locks are held.

Provider processes remain packet-only, tool-disabled and read-only. Qwen health and tokenizer readiness are checked before parent dispatch and again before child dispatch. Cloud-read authorization includes future files. Windows runtime paths are conservatively rejected before run creation when native subprocess paths would be too long; the default run root is short. Status, report and the synthetic demo do not invoke a model.

Verification on Local Windows:

- independent focused run: `48 passed in 126.49s`, exit 0;
- Implementer remaining repository run: `369 passed, 4 warnings, 612 subtests passed in 232.02s`, exit 0; the D059 focused file was excluded because the independent run covered it;
- `python scripts/check_docs.py`: `文書の不整合なし`, exit 0;
- saved real-provider fixture `overnight-fac214eba669489d88a02e50cd948dfc`: COMPLETE for two units. The Reviewer independently checked four parent calls, two COMPLETE children, three Claude approvals per child, matching child-state hashes, two observed Qwen calls, `78,667` known tokens, zero unknown token fields and zero report-time model calls.

This approval covers the bounded D059 development infrastructure. It does not approve a game Phase, authorize automatic design changes, start or own Qwen, expand a workpackage, commit, push or deploy.

Signed: Reviewer / Sol, independent implementation-review lane, 2026-09-08.
