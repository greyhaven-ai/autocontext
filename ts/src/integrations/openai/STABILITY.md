# Stability — `autoctx/integrations/openai`

**Stability level: beta** (subject to minor breaking changes before 1.0).

## Public surface

| Export | Stability |
|--------|-----------|
| `instrumentClient` | beta |
| `FileSink` | beta |
| `TraceSink` (interface) | beta |
| `autocontextSession` | beta |

## Internal modules

All files prefixed with `_` (sink, session, taxonomy, trace-builder, proxy, stream-proxy, wrap) are **private** — not part of the public API contract and may change without notice.

## Cross-runtime parity

This module maintains byte-identical trace output with `autocontext.integrations.openai` (Python). Deviations are bugs. See `ts/tests/integrations/openai/parity/` for the parity test corpus.
