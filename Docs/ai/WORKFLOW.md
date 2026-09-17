# Coordination Workflow

`AGENTS.md` defines authority and invariants; `INDEX.md` defines live state versus evidence.
`OPERATIONS.md` is the operating contract. This page routes lifecycle questions without a
second role pipeline.

Main chooses decomposition, responsibility, parallelism, and verification by task risk and
acceptance. Architect, Implementer, Tester, Reviewer, and Investigator are scope and independence
boundaries, not mandatory sequential stages. D051 design approval and packet/TEST_POLICY-required
independent tests still apply. fresh sessionと第二ReviewerはD075の明示条件だけで選ぶ。
既存Reviewerと同scope/hashの独立証拠を再利用し、同一diffを再審査しない。No worker self-approval is permitted.

Workers finish their bounded assignment, record measured evidence, and return. Main verifies it,
updates coordination, and continues the authorized objective across packets/waves. An explicit
user hold overrides continuation. Recovery reconstructs from repository/host evidence, not an old
chat's next action. Unknown IN_PROGRESS ownership requires reconciliation before redispatch.

`TASKS.md` owns lifecycle: READY, IN_PROGRESS, REVIEW, BLOCKED, DECISION_REQUIRED, DONE, CANCELLED.
Only verified acceptance evidence supports DONE. REVIEW is used when review is required; small
work without that requirement need not invent another role stage. Keep unrelated work moving
while an affected task is blocked. Dispatch/conflict checks, evidence rules, D068 reassessment,
process ownership and long-test instructions are in `OPERATIONS.md`.
