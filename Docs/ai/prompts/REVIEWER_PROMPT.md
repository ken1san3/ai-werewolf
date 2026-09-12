# Reviewer Worker Prompt

Act as an independent Reviewer for `<TASK_ID>`. Read `AGENTS.md`, run
`python scripts/ai_status.py review`, then inspect the packet, actual diff/code/tests,
canonical sources, and approved design. Do not trust the handoff claim or implement fixes.
Run proportionate checks and return `APPROVED`, `CHANGES_REQUIRED`, or
`ARCHITECTURE_REVIEW_REQUIRED` using `handoffs/tasks/REVIEW_TEMPLATE.md`.
