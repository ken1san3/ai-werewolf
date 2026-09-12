# Model Assignments

These assignments are operational defaults only. Responsibilities and authority are
defined in `AGENTS.md`; task meaning is defined by its task packet. Changing this file does
not change either one.

| Responsibility | Current preferred model | Fallback | Notes |
|---|---|---|---|
| Integrator | GPT-6 Astra | Another integration-capable model | Maintain repository-wide context and verify worker evidence. |
| Architect | GPT-5.6 Sol | Another design-capable model | Design only; approval requires an independent Reviewer session. |
| Implementer | GPT-5.6 Sol | A contract-capable worker model | Use the packet and approved contract; do not infer additional authority. |
| Reviewer | GPT-5.6 Sol | An independent Reviewer | Session independence is mandatory; a different model is preferred for high-risk review when available. |
| Tester | GPT-5.6 Luna | Another test-capable model | Execute mechanical verification and preserve raw evidence; do not make design decisions. |
| Investigator | GPT-6 Astra | Another investigation-capable model | Diagnose and bound repair scope; do not absorb implementation by default. |

Qwen, Gemini, or later models may be assigned as workers when their capability and risk fit
the packet. Such assignment never creates a new responsibility or changes review rules.

Last updated: 2026-09-10
