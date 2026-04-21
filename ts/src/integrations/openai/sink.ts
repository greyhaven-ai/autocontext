/**
 * TraceSink interface + FileSink implementation.
 *
 * Spec §4.1 + §7.2. No beforeExit by default; ``registerBeforeExit: true`` opts in.
 * Mirror of Python ``autocontext.integrations.openai._sink``.
 */
import { appendFileSync, fsyncSync, mkdirSync, openSync, closeSync } from "node:fs";
import { dirname } from "node:path";

export interface TraceSink {
  add(trace: Record<string, unknown>): void;
  flush(): void;
  close(): void;
}

export interface FileSinkOptions {
  /** Max traces before auto-flush. Default: 64. */
  batchSize?: number;
  /** Seconds since last flush before auto-flush. Default: 5. */
  flushIntervalSeconds?: number;
  /** How to handle write errors. Default: "raise". */
  onError?: "raise" | "log-and-drop";
  /** Register a process.on("beforeExit") closer. Default: false. */
  registerBeforeExit?: boolean;
}

/** Batched JSONL trace sink.
 *
 * Buffers traces in memory; flushes on batchSize or flushIntervalSeconds elapsed.
 * Writes are append-only with fsync. Mirror of Python FileSink.
 */
export class FileSink implements TraceSink {
  private readonly _path: string;
  private readonly _batchSize: number;
  private readonly _flushIntervalMs: number;
  private readonly _onError: "raise" | "log-and-drop";
  private _buffer: Array<Record<string, unknown>> = [];
  private _lastFlushAt: number = Date.now();
  private _closed = false;

  constructor(path: string, options?: FileSinkOptions) {
    this._path = path;
    this._batchSize = options?.batchSize ?? 64;
    this._flushIntervalMs = (options?.flushIntervalSeconds ?? 5.0) * 1000;
    this._onError = options?.onError ?? "raise";
    if (options?.registerBeforeExit) {
      process.on("beforeExit", () => {
        try { this.close(); } catch { /* best-effort */ }
      });
    }
  }

  add(trace: Record<string, unknown>): void {
    if (this._closed) {
      throw new Error("FileSink is closed");
    }
    this._buffer.push(trace);
    if (this._buffer.length >= this._batchSize) {
      this._flushLocked();
      return;
    }
    if (Date.now() - this._lastFlushAt >= this._flushIntervalMs) {
      this._flushLocked();
    }
  }

  flush(): void {
    if (!this._closed) {
      this._flushLocked();
    }
  }

  close(): void {
    if (this._closed) return;
    this._flushLocked();
    this._closed = true;
  }

  private _flushLocked(): void {
    if (this._buffer.length === 0) {
      this._lastFlushAt = Date.now();
      return;
    }
    try {
      mkdirSync(dirname(this._path), { recursive: true });
      const lines = this._buffer
        .map((t) => JSON.stringify(t, _sortedReplacer))
        .join("\n") + "\n";
      appendFileSync(this._path, lines, "utf-8");
      // fsync the file to ensure durability
      const fd = openSync(this._path, "r");
      try {
        fsyncSync(fd);
      } finally {
        closeSync(fd);
      }
    } catch (err) {
      if (this._onError === "raise") throw err;
      // log-and-drop
      try {
        process.stderr.write(`[FileSink] flush failed: ${String(err)}\n`);
      } catch { /* ignore */ }
    } finally {
      this._buffer = [];
      this._lastFlushAt = Date.now();
    }
  }
}

/** JSON replacer that produces sorted-key output (matches Python sort_keys=True). */
function _sortedReplacer(_key: string, value: unknown): unknown {
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b)),
    );
  }
  return value;
}
