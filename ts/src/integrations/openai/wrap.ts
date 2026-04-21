/**
 * instrumentClient factory — double-wrap detection + identity resolution.
 *
 * Spec §4.1. Mirror of Python ``_wrap.py``.
 */
import type { TraceSink } from "./sink.js";

// wrap.ts will be implemented in Task 3.7
export function instrumentClient<T>(
  _client: T,
  _opts: { sink: TraceSink; appId?: string; environmentTag?: string },
): T {
  throw new Error("stub");
}
