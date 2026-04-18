import {
  appendFileSync,
  existsSync,
  readFileSync,
  mkdirSync,
} from "node:fs";
import { dirname } from "node:path";
import type { PromotionEvent } from "../contract/types.js";
import { validateAppendOnly } from "../contract/invariants.js";
import { validatePromotionEvent } from "../contract/validators.js";
import { canonicalJsonStringify } from "../contract/canonical-json.js";

/**
 * Read the on-disk JSONL history at `path`. Returns [] if the file is
 * absent. Throws if the file ends without a newline (partial-write
 * indicator) or if any line is not parseable / not a valid PromotionEvent.
 */
export function readHistory(path: string): PromotionEvent[] {
  if (!existsSync(path)) return [];
  const raw = readFileSync(path, "utf-8");
  if (raw.length === 0) return [];
  if (!raw.endsWith("\n")) {
    throw new Error(`readHistory: ${path} ends without a trailing newline (possible partial write)`);
  }
  const lines = raw.split("\n");
  // Trailing newline produces an empty final element — drop it.
  if (lines[lines.length - 1] === "") lines.pop();

  const out: PromotionEvent[] = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    let parsed: unknown;
    try {
      parsed = JSON.parse(line);
    } catch (err) {
      throw new Error(`readHistory: line ${i + 1} is not valid JSON: ${(err as Error).message}`);
    }
    const v = validatePromotionEvent(parsed);
    if (!v.valid) {
      throw new Error(`readHistory: line ${i + 1} is not a valid PromotionEvent: ${v.errors.join("; ")}`);
    }
    out.push(parsed as PromotionEvent);
  }
  return out;
}

/**
 * Append-only writer for `promotion-history.jsonl`.
 *   - `prev` MUST equal the current on-disk history. If it doesn't, the file
 *     was tampered with and we refuse to append.
 *   - `next` MUST be an extension of `prev` (uses validateAppendOnly).
 *
 * Each new event is canonical-JSON-encoded and written as one line.
 * The file is created if it doesn't exist; the parent directory is also created.
 */
export function appendHistory(
  path: string,
  prev: readonly PromotionEvent[],
  next: readonly PromotionEvent[],
): void {
  const v = validateAppendOnly(prev, next);
  if (!v.valid) {
    throw new Error(`appendHistory: ${v.errors.join("; ")}`);
  }
  if (next.length === prev.length) {
    // Nothing to do — caller passed identical histories.
    return;
  }

  // Verify on-disk prefix matches `prev` (tamper check).
  const onDisk = readHistory(path);
  const prefixCheck = validateAppendOnly(onDisk, [...prev]);
  // onDisk should be a prefix of (or equal to) prev. If onDisk is longer,
  // that's also a desync; if onDisk differs in any earlier event, that's a
  // tamper. validateAppendOnly handles both: it requires onDisk.length <= prev.length
  // and onDisk events to equal the corresponding prev events.
  if (!prefixCheck.valid) {
    throw new Error(
      `appendHistory: on-disk history at ${path} no longer matches expected prev (append-only violation): ${prefixCheck.errors.join("; ")}`,
    );
  }
  if (onDisk.length !== prev.length) {
    throw new Error(
      `appendHistory: on-disk history has ${onDisk.length} entries but caller passed prev with ${prev.length} (concurrent writer?)`,
    );
  }

  mkdirSync(dirname(path), { recursive: true });
  const tail = next.slice(prev.length);
  let buf = "";
  for (const ev of tail) {
    buf += canonicalJsonStringify(ev) + "\n";
  }
  appendFileSync(path, buf, "utf-8");
}
