# Provider startup/readiness flake — 2026-09-12

Classification: independent host/provider startup-readiness flake.

The same canonical provider configuration previously reached readiness for T070 and T097. Later,
T107 exited before readiness and T112 remained active for an ad hoc 120-second observation without
a listener. Both later attempts stopped before any AIwolf runner, Q8, inference, network, game, or
completion call. T112 cleanup and a later T113 inventory both proved zero provider, listener, and
runner residue; Git and profile state were preserved.

The 120-second cutoff is not a canonical external-provider readiness limit, and the approved
sanitized evidence cannot establish whether loading was progressing. Do not weaken Q8 acceptance or
repair AIwolf production code from this evidence. Future investigation, if separately prioritized,
should remain a bounded host/provider task and capture only sanitized cold-start phase timestamps,
resource-state categories, exit category, listener transition, and serving-artifact identity while
preserving exact owned-tree cleanup. It must not be folded into the current retry loop.
