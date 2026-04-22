/**
 * AnthropicStreamProxy — block-aware accumulator for Anthropic SSE streams.
 *
 * Tracks content blocks by index (matching Anthropic's SSE structure).
 * Uses FinalizationRegistry for abandoned-stream detection.
 * Mirror of Python _stream.py for Anthropic.
 */
import type { AccumulatedBlock } from "./trace-builder.js";

type OnFinalize = (
  blocks: Map<number, AccumulatedBlock>,
  usage: Record<string, unknown> | null,
  stopReason: string | null,
  outcome: Record<string, unknown>,
) => void;

/**
 * Finalizer callback for FinalizationRegistry — fires when the proxy is GC'd.
 * Must NOT close over the proxy itself to prevent reference cycles.
 */
function _abandonedCallback(
  state: { finalized: boolean },
  onFinalize: OnFinalize,
  blocks: Map<number, AccumulatedBlock>,
  usage: { value: Record<string, unknown> | null },
  stopReason: { value: string | null },
): void {
  if (state.finalized) return;
  try {
    onFinalize(blocks, usage.value, stopReason.value, {
      label: "partial",
      reasoning: "abandonedStream",
    });
  } catch {
    // best-effort
  }
  state.finalized = true;
}

const _registry = new FinalizationRegistry<{
  state: { finalized: boolean };
  onFinalize: OnFinalize;
  blocks: Map<number, AccumulatedBlock>;
  usage: { value: Record<string, unknown> | null };
  stopReason: { value: string | null };
}>(({ state, onFinalize, blocks, usage, stopReason }) =>
  _abandonedCallback(state, onFinalize, blocks, usage, stopReason),
);

export class AnthropicStreamProxy implements AsyncIterable<unknown> {
  readonly _contentBlocks: Map<number, AccumulatedBlock>;
  private readonly _usage: { value: Record<string, unknown> | null };
  private readonly _stopReason: { value: string | null };
  private readonly _onFinalize: OnFinalize;
  private readonly _state: { finalized: boolean };
  private readonly _innerStream: AsyncIterable<unknown>;

  constructor(opts: { innerStream: unknown; onFinalize: OnFinalize }) {
    this._contentBlocks = new Map();
    this._usage = { value: null };
    this._stopReason = { value: null };
    this._onFinalize = opts.onFinalize;
    this._state = { finalized: false };
    this._innerStream = opts.innerStream as AsyncIterable<unknown>;

    // Register finalizer — pass state+callback, NOT the proxy (prevents cycle)
    const state = this._state;
    const onFinalize = opts.onFinalize;
    const blocks = this._contentBlocks;
    const usage = this._usage;
    const stopReason = this._stopReason;
    _registry.register(this, { state, onFinalize, blocks, usage, stopReason });
  }

  [Symbol.asyncIterator](): AsyncIterator<unknown> {
    return this._makeIterator();
  }

  private async *_makeIterator(): AsyncGenerator<unknown> {
    try {
      for await (const event of this._innerStream) {
        this._handleEvent(event as Record<string, unknown>);
        yield event;
        // Finalize immediately on message_stop (before iterator is fully consumed)
        if ((event as Record<string, unknown>)["type"] === "message_stop") {
          if (!this._state.finalized) {
            this._onFinalize(
              this._contentBlocks,
              this._usage.value,
              this._stopReason.value,
              { label: "success" },
            );
            this._state.finalized = true;
            _registry.unregister(this);
          }
        }
      }
      // Also finalize here in case message_stop was not in the stream
      if (!this._state.finalized) {
        this._onFinalize(
          this._contentBlocks,
          this._usage.value,
          this._stopReason.value,
          { label: "success" },
        );
        this._state.finalized = true;
        _registry.unregister(this);
      }
    } catch (exc) {
      if (!this._state.finalized) {
        const { mapExceptionToReason } = await import("./taxonomy.js");
        this._onFinalize(
          this._contentBlocks,
          this._usage.value,
          this._stopReason.value,
          {
            label: "failure",
            error: {
              type: mapExceptionToReason(exc),
              message: String(exc),
              stack: exc instanceof Error ? (exc.stack ?? null) : null,
            },
          },
        );
        this._state.finalized = true;
        _registry.unregister(this);
      }
      throw exc;
    }
  }

  private _handleEvent(ev: Record<string, unknown>): void {
    const type = ev["type"] as string;

    if (type === "message_start") {
      const msg = ev["message"] as Record<string, unknown> | undefined;
      if (msg?.["usage"]) {
        this._usage.value = msg["usage"] as Record<string, unknown>;
      }
    } else if (type === "content_block_start") {
      const idx = Number(ev["index"]);
      const cb = ev["content_block"] as Record<string, unknown>;
      this._contentBlocks.set(idx, {
        type: String(cb["type"] ?? "unknown"),
        buffer: "",
        id: cb["id"] as string | undefined,
        name: cb["name"] as string | undefined,
      });
    } else if (type === "content_block_delta") {
      const idx = Number(ev["index"]);
      const delta = ev["delta"] as Record<string, unknown>;
      const dtype = delta["type"] as string;
      const entry = this._contentBlocks.get(idx) ?? { type: "unknown", buffer: "" };
      if (dtype === "text_delta") {
        entry.buffer += String(delta["text"] ?? "");
      } else if (dtype === "input_json_delta") {
        entry.buffer += String(delta["partial_json"] ?? "");
      }
      this._contentBlocks.set(idx, entry);
    } else if (type === "content_block_stop") {
      const idx = Number(ev["index"]);
      const entry = this._contentBlocks.get(idx);
      if (entry?.type === "tool_use") {
        try {
          entry.finalizedInput = entry.buffer
            ? (JSON.parse(entry.buffer) as Record<string, unknown>)
            : {};
        } catch {
          entry.finalizedInput = { _rawJsonError: entry.buffer };
        }
      }
    } else if (type === "message_delta") {
      const delta = ev["delta"] as Record<string, unknown>;
      if (delta["stop_reason"]) {
        this._stopReason.value = String(delta["stop_reason"]);
      }
      if (ev["usage"]) {
        this._usage.value = {
          ...(this._usage.value ?? {}),
          ...(ev["usage"] as Record<string, unknown>),
        };
      }
    }
  }
}
