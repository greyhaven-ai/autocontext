# Stability — `autoctx/integrations/openai`

**Stability level: stable** (API frozen until the next major version).

## Public surface

Symbols re-exported from `index.ts`:

| Symbol | Kind | Stability |
|--------|------|-----------|
| `instrumentClient` | function | stable |
| `FileSink` | class | stable |
| `TraceSink` | interface | stable |
| `autocontextSession` | function | stable |
| `currentSession` | function | stable |
| `SessionContext` | type | stable |

All files not re-exported from `index.ts` (e.g., `sink.ts`, `session.ts`,
`taxonomy.ts`, `trace-builder.ts`, `proxy.ts`, `stream-proxy.ts`, `wrap.ts`)
are **private** and may change without notice. Import only from the subpath
export `autoctx/integrations/openai`.

## SDK version range

```
openai >=4,<5
```

The integration is tested against the three most-recent patch releases within
the 4.x line. Compatibility with 5.x is not guaranteed and requires a new spec.

## Semantic caveats

1. **`instanceof` check**: `wrapped instanceof OpenAI` returns `False`.
   `instrumentClient` returns a `Proxy` object, not an actual `OpenAI`
   instance. Code that type-narrows on `instanceof OpenAI` will not recognise
   the wrapped client. Use duck-typing or check
   `(client as any)._autocontextInstrumented` instead.

2. **`FileSink.close()` is explicit**: `FileSink` does **not** register a
   `process.on('beforeExit')` (or `process.on('exit')`) hook by default.
   Callers must call `await sink.close()` (or use it as an
   `AsyncDisposable`/`using` resource) to flush pending traces. Script-style
   callers should add their own `beforeExit` handler or wrap in a try/finally.

3. **`autocontextSession` propagation**: Session context is stored in
   `AsyncLocalStorage`. It propagates naturally across all `await` boundaries
   within the same async call chain. No manual context-copying is required for
   `Promise`-based code or `worker_threads` that use `AsyncResource.bind`.

4. **`stream_options.include_usage` auto-injection**: When making streaming
   calls and the caller has not set `stream_options.include_usage`, the
   integration automatically sets it to `true` so that token-usage metadata
   is included in the final SSE chunk and captured in the emitted trace.
   Callers that explicitly set `stream_options.include_usage: false` override
   this behaviour (their setting is respected).

5. **`FinalizationRegistry` and abandoned-stream detection**: The integration
   registers open streaming responses with a `FinalizationRegistry` to emit
   partial traces for streams that are abandoned without being fully consumed.
   In production, detection timing depends on Node's normal GC cadence (not
   deterministic). In tests, pass `--expose-gc` to the Node process and call
   `global.gc()` explicitly after nulling the stream reference to trigger
   deterministic detection.

## Cross-runtime parity

This module maintains byte-identical trace output with
`autocontext.integrations.openai` (Python). Deviations are bugs. See
`ts/tests/integrations/openai/parity/` for the parity test corpus.

## Breaking-change policy

This module follows **SemVer**. Any change to the public API surface (symbol
removal, signature change, interface extension that breaks existing
implementations) requires a **major version bump** of the `autoctx` npm
package. Additions to the public API (new optional parameters, new exports)
are minor bumps. Bug fixes and internal refactors are patch bumps.
