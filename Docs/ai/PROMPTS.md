# Responsibility-based prompts

Prompts select a responsibility, never an executor model. Read `AGENTS.md`, run the named
status entry, then read only the assigned packet and its referenced sources.

| Responsibility | Canonical entry | Prompt template |
|---|---|---|
| Integrator | `python scripts/ai_status.py integrate` | `MAIN_INTEGRATOR_PROMPT.md` |
| Architect | `python scripts/ai_status.py architect` | `prompts/ARCHITECT_PROMPT.md` |
| Implementer | `python scripts/ai_status.py implement` | `prompts/IMPLEMENTER_PROMPT.md` |
| Reviewer | `python scripts/ai_status.py review` | `prompts/REVIEWER_PROMPT.md` |
| Tester | `python scripts/ai_status.py test` | `prompts/TESTER_PROMPT.md` |
| Investigator | `python scripts/ai_status.py investigate` | `prompts/INVESTIGATOR_PROMPT.md` |

`design` is a compatibility alias for the Architect entry; `fix` is the bounded
Implementer review-fix entry. Task-specific facts always come from `TASKS.md` and the task
packet, not from copied chat history. Historical actor/model labels and archived autonomous
runners are evidence only and are not launch interfaces.
