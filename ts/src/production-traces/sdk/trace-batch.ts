import type { ProductionTrace } from "../contract/types.js";
import { writeJsonl, type WriteJsonlOpts } from "./write-jsonl.js";

/**
 * In-memory accumulator for high-throughput emit paths.
 *
 * DDD anchor: mirrors Python ``autocontext.production_traces.emit.TraceBatch``
 * — same ``add`` / ``flush`` / ``__len__`` surface. The only TS-side addition
 * is an explicit :meth:`clear` helper for tests and error-recovery paths that
 * want to discard the accumulator without writing to disk.
 *
 * Concurrency: ``TraceBatch`` is not thread-safe. Node's single-threaded event
 * loop makes this a non-issue for typical callers, but SDK consumers running
 * inside a worker pool should instantiate one batch per worker.
 *
 * Usage::
 *
 *   const batch = new TraceBatch();
 *   for (const event of stream) batch.add(buildTrace({ ... }));
 *   batch.flush();  // writes accumulated traces as one file
 */
export class TraceBatch {
  private traces: ProductionTrace[] = [];

  /** Append a trace to the batch. */
  add(trace: ProductionTrace): void {
    this.traces.push(trace);
  }

  /** Flush the batch to disk and reset. Returns the written path, or
   *  ``null`` when the batch is empty (matches Python). */
  flush(opts?: WriteJsonlOpts): string | null {
    if (this.traces.length === 0) return null;
    const path = writeJsonl(this.traces, opts);
    this.traces = [];
    return path;
  }

  /** Reset the accumulator without writing. */
  clear(): void {
    this.traces = [];
  }

  /** Current number of accumulated traces. */
  get length(): number {
    return this.traces.length;
  }
}
