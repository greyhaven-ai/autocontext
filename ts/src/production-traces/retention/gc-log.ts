// Append-only audit log of retention deletions (spec §6.1: gc-log.jsonl).
//
// Wire format: one JSON object per line (JSONL). Entries are emitted via
// canonical JSON so the file is byte-deterministic — important because gc-log
// is an auditable record and operators will sometimes compare hashes across
// machines. The schema is intentionally schema-free on disk (no AJV schema
// file) — the log must stay human-readable and append-only, and we never
// want to reject historical entries that pre-date a future schema revision.
//
// This module never reads, parses, or rewrites existing entries. The single
// write mode is `appendFileSync` so partial-crash recovery is trivially safe:
// a torn tail line is the worst case and never invalidates prior entries.

import { appendFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname } from "node:path";
import { canonicalJsonStringify } from "../../control-plane/contract/canonical-json.js";
import { gcLogPath } from "../ingest/paths.js";
import type { GcLogEntry } from "./enforce.js";

/**
 * Append one entry to `.autocontext/production-traces/gc-log.jsonl`. Creates
 * the directory and file lazily. Canonical-JSON serialization guarantees
 * byte-identical output for logically-equal entries.
 *
 * The `entry` parameter is typed as `GcLogEntry` (the canonical retention
 * audit shape) but accepts a wider JSON-serializable object to keep the door
 * open for operator-driven manual entries. Shape invariants are enforced at
 * the call site in `enforce.ts`.
 */
export function appendGcLogEntry(cwd: string, entry: GcLogEntry): void {
  const path = gcLogPath(cwd);
  if (!existsSync(dirname(path))) {
    mkdirSync(dirname(path), { recursive: true });
  }
  appendFileSync(path, canonicalJsonStringify(entry) + "\n", "utf-8");
}

/**
 * Read every entry from `gc-log.jsonl` in on-disk order. Returns `[]` when
 * the file is absent. Malformed lines are skipped (spec: "schema-free on
 * disk, operator-readable") — operators who hand-edit the log should not
 * risk bricking the retention subsystem with a stray byte.
 */
export function readGcLog(cwd: string): readonly GcLogEntry[] {
  const path = gcLogPath(cwd);
  if (!existsSync(path)) return [];
  const raw = readFileSync(path, "utf-8");
  const entries: GcLogEntry[] = [];
  for (const line of raw.split("\n")) {
    if (line.trim().length === 0) continue;
    try {
      entries.push(JSON.parse(line) as GcLogEntry);
    } catch {
      // Malformed line — skip. Do NOT throw: the log is operator-readable and
      // may contain hand-edits; we refuse to brick the enforcement subsystem
      // on a stray byte.
    }
  }
  return entries;
}
