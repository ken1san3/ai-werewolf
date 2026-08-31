# D043 Local LLM Input Length Guards

## Status

Accepted (2026-08-31)

## Context

The development profile has a 32,768-token context window, but `ask.py` accepted unbounded
joined input and `summarize.py` used a fixed 120,000-character limit. Large documents therefore
reached the server only to fail before inference.

## Decision

`C:\AIagent\agent\lib\conf.context_window` derives the active profile's context window from
its llama.cpp `-c` argument. `llm.input_token_limit` reserves the requested output tokens and a
2,048-token safety margin. `llm` counts the final payload through llama.cpp's `/tokenize` before
the completion request and rejects an oversized payload with `input_too_long`. If `/tokenize` is
unavailable, it falls back to the same numeric value as a conservative character limit.
`summarize.py` does not automatically truncate by default; an explicit `--max-chars` truncation
reports discarded characters to stderr and `usage.jsonl`. Every completion record declares whether
it used `tokenize` or `character_fallback`, and `doctor.py` treats an unavailable `/tokenize` on a
running server as unhealthy.

## Consequences

- Limits follow profile configuration and actual tokenization rather than a fixed character ratio.
- The usage log distinguishes a local pre-send rejection from an HTTP failure.
- A fallback that still completes is visible in the usage log rather than silently reducing capacity.
- An explicit truncation is observable rather than a successful-looking partial summary.
- Long, append-only documents must be passed as a relevant extracted section rather than whole
  `-d` documents.
