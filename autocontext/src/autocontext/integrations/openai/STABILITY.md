# Stability — `autocontext.integrations.openai`

**Stability level: stable** (API frozen until the next major version).

## Public surface

Symbols re-exported from `__all__`:

| Symbol | Kind | Stability |
|--------|------|-----------|
| `instrument_client` | function | stable |
| `FileSink` | class | stable |
| `TraceSink` | Protocol | stable |
| `autocontext_session` | context manager | stable |

All names prefixed with `_` (e.g., `_proxy`, `_session`, `_sink`, `_stream`,
`_taxonomy`, `_trace_builder`, `_wrap`) are **private** and may change without
notice.

## SDK version range

```
openai >=1.0,<2.0
```

The integration is tested against the three most-recent patch releases within
the 1.x line. Compatibility with 2.x is not guaranteed and requires a new spec.

## Semantic caveats

1. **`isinstance` check**: `isinstance(wrapped, OpenAI)` returns `False`.
   `instrument_client` returns a proxy object, not a subclass of `OpenAI`. Code
   that type-narrows on `isinstance(client, OpenAI)` will not recognise the
   wrapped client. Use duck-typing or check `hasattr(client,
   "_autocontext_instrumented")` instead.

2. **`FileSink.close()` is explicit**: `FileSink` does **not** register an
   `atexit` hook by default. Callers must call `sink.close()` (or use it as a
   context manager) to flush pending traces. Pass `register_atexit=True` to
   `FileSink(path, register_atexit=True)` for script-style use where the process
   may exit without an explicit close.

3. **Contextvar propagation**: `autocontext_session` stores its value in a
   `contextvars.ContextVar`. This propagates naturally across `asyncio.to_thread`
   and `contextvars.copy_context()` boundaries but does **NOT** propagate across
   raw `threading.Thread` targets. If you spawn threads manually, copy the
   context explicitly:
   ```python
   import contextvars, threading
   ctx = contextvars.copy_context()
   t = threading.Thread(target=lambda: ctx.run(your_fn))
   ```

4. **`stream_options.include_usage` auto-injection**: When making streaming
   calls (`stream=True`) and the caller has not set
   `stream_options.include_usage`, the integration automatically sets it to
   `True` so that token-usage metadata is included in the final SSE chunk and
   captured in the emitted trace. Callers that explicitly set
   `stream_options.include_usage=False` override this behaviour (their setting
   is respected).

## Breaking-change policy

This module follows **SemVer**. Any change to the public API surface (symbol
removal, signature change, protocol extension that breaks existing
implementations) requires a **major version bump** of the `autocontext`
package. Additions to the public API (new optional parameters, new symbols)
are minor bumps. Bug fixes and internal refactors are patch bumps.
