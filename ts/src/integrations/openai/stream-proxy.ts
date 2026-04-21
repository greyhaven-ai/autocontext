/**
 * AsyncStreamProxy — wraps OpenAI streaming responses; finalize-on-end/abandon.
 *
 * Uses FinalizationRegistry for abandoned-stream detection. Mirror of Python
 * ``_stream.py`` StreamProxy/AsyncStreamProxy. Spec §6.3.
 *
 * NOTE: In JS/TS the OpenAI SDK always returns Promises, so we handle both
 * the case where innerStream is a Promise<AsyncIterable> and a direct AsyncIterable.
 */

type OnFinalize = (outcome: Record<string, unknown>) => void;

/**
 * Finalizer callback — called by FinalizationRegistry when proxy is GC'd.
 * Must NOT close over the proxy itself to prevent reference cycles.
 */
function _abandonedCallback(
  state: { finalized: boolean },
  onFinalize: OnFinalize,
): void {
  if (state.finalized) return;
  try {
    onFinalize({ label: "partial", reasoning: "abandonedStream" });
  } catch {
    // best-effort
  }
  state.finalized = true;
}

/** FinalizationRegistry instance used by all AsyncStreamProxy instances. */
const _registry = new FinalizationRegistry<{
  state: { finalized: boolean };
  onFinalize: OnFinalize;
}>(({ state, onFinalize }) => _abandonedCallback(state, onFinalize));

export class AsyncStreamProxy implements AsyncIterable<unknown> {
  readonly _accumulator: {
    content: string[];
    usage: Record<string, unknown> | null;
    tool_calls: Array<Record<string, unknown>> | null;
  };
  private readonly _onFinalize: OnFinalize;
  private readonly _state: { finalized: boolean };
  private _innerStream: AsyncIterable<unknown> | null = null;
  private _innerStreamPromise: Promise<AsyncIterable<unknown>> | null = null;

  constructor(opts: { innerStream: unknown; onFinalize: OnFinalize }) {
    this._accumulator = { content: [], usage: null, tool_calls: null };
    this._onFinalize = opts.onFinalize;
    this._state = { finalized: false };

    // Detect if innerStream is a Promise<AsyncIterable> or direct AsyncIterable
    if (opts.innerStream && typeof (opts.innerStream as { then?: unknown }).then === "function") {
      this._innerStreamPromise = opts.innerStream as Promise<AsyncIterable<unknown>>;
    } else {
      this._innerStream = opts.innerStream as AsyncIterable<unknown>;
    }

    // Register finalizer — pass state+callback, NOT the proxy (prevents cycle)
    const state = this._state;
    const onFinalize = opts.onFinalize;
    _registry.register(this, { state, onFinalize });
  }

  [Symbol.asyncIterator](): AsyncIterator<unknown> {
    return this._makeIterator();
  }

  private async *_makeIterator(): AsyncGenerator<unknown> {
    // Resolve the inner stream if needed
    let inner: AsyncIterable<unknown>;
    if (this._innerStream !== null) {
      inner = this._innerStream;
    } else if (this._innerStreamPromise !== null) {
      inner = await this._innerStreamPromise;
    } else {
      return;
    }

    try {
      for await (const chunk of inner) {
        this._accumulate(chunk as Record<string, unknown>);
        yield chunk;
      }
      if (!this._state.finalized) {
        this._onFinalize({ label: "success" });
        this._state.finalized = true;
        _registry.unregister(this);
      }
    } catch (exc) {
      if (!this._state.finalized) {
        const { mapExceptionToReason } = await import("./taxonomy.js");
        this._onFinalize({
          label: "failure",
          error: {
            type: mapExceptionToReason(exc),
            message: String(exc),
            stack: exc instanceof Error ? (exc.stack ?? null) : null,
          },
        });
        this._state.finalized = true;
        _registry.unregister(this);
      }
      throw exc;
    }
  }

  private _accumulate(chunk: Record<string, unknown>): void {
    if (chunk["usage"]) {
      this._accumulator.usage = chunk["usage"] as Record<string, unknown>;
    }
    const choices = chunk["choices"] as Array<Record<string, unknown>> | undefined;
    if (choices && choices.length > 0) {
      const delta = choices[0]!["delta"] as Record<string, unknown> | undefined;
      if (delta?.["content"]) {
        this._accumulator.content.push(String(delta["content"]));
      }
      if (delta?.["tool_calls"]) {
        if (this._accumulator.tool_calls === null) {
          this._accumulator.tool_calls = [];
        }
        for (const tc of delta["tool_calls"] as Array<Record<string, unknown>>) {
          this._accumulator.tool_calls.push(tc);
        }
      }
    }
  }

  accumulated(): typeof this._accumulator {
    return { ...this._accumulator };
  }
}
