# D043 Local LLM Input Length Guards

## Status

Accepted (2026-08-31)

## Context

The development profile has a 32,768-token context window, but `ask.py` accepted unbounded
joined input and `summarize.py` used a fixed 120,000-character limit. Large documents therefore
reached the server only to fail before inference.

## Decision

`C:\AIagent\agent\lib\conf.context_window` derives the active profile's context window from
its llama.cpp `-c` argument. `llm.input_character_limit` reserves the requested output tokens and
a 2,048-token safety margin. `llm` rejects oversized final payloads before HTTP and records an
`input_too_long` outcome. `ask.py` checks after input concatenation; `summarize.py` clips its
source text to the derived remaining capacity.

## Consequences

- Limits follow profile configuration rather than a fixed character threshold.
- The usage log distinguishes a local pre-send rejection from an HTTP failure.
- Long, append-only documents must be passed as a relevant extracted section rather than whole
  `-d` documents.
