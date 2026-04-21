import { mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { ulid } from "ulid";
import type { ProductionTrace } from "../contract/types.js";
import { canonicalJsonStringify } from "../../control-plane/contract/canonical-json.js";

/**
 * Customer-facing filesystem emit helper.
 *
 * DDD anchor: mirrors Python ``autocontext.production_traces.emit.write_jsonl``
 * — same directory layout (``.autocontext/production-traces/incoming/<date>/
 * <batch-ulid>.jsonl``), same partition logic (UTC date from first trace's
 * ``timing.startedAt``), same cwd resolution order (explicit → env var →
 * process.cwd()).
 *
 * DRY anchor: per-line serialization delegates to Foundation B's
 * ``canonical-json.ts`` so two SDK calls with logically-equal inputs produce
 * byte-identical files. No custom JSON stringification.
 *
 * Side-effect discipline: the only top-level import side effect in this file
 * is bringing in ``ulid``'s batch-id generator, which is pure on import. All
 * filesystem operations happen inside the exported function body.
 */

const ROOT_DIR = ".autocontext";
const PT_DIR = "production-traces";
const INCOMING = "incoming";
const REGISTRY_ENV_VAR = "AUTOCONTEXT_REGISTRY_PATH";

export interface WriteJsonlOpts {
  /**
   * Base working directory. Resolution order when not provided:
   *   1. ``AUTOCONTEXT_REGISTRY_PATH`` environment variable
   *   2. ``process.cwd()``
   */
  readonly cwd?: string;
  /** Explicit batch id. Defaults to a freshly-generated ULID. */
  readonly batchId?: string;
}

/**
 * Write one or more traces to the incoming partition. Returns the absolute
 * path on success, or ``null`` when given an empty array (matches Python's
 * no-op).
 */
export function writeJsonl(
  traces: ProductionTrace | readonly ProductionTrace[],
  opts: WriteJsonlOpts = {},
): string | null {
  const list = Array.isArray(traces)
    ? (traces as readonly ProductionTrace[])
    : [traces as ProductionTrace];
  if (list.length === 0) return null;

  const base = resolveCwd(opts.cwd);
  const datePartition = partitionDate(list);
  const batchId = opts.batchId ?? ulid();

  const outDir = join(base, ROOT_DIR, PT_DIR, INCOMING, datePartition);
  mkdirSync(outDir, { recursive: true });
  const outPath = join(outDir, `${batchId}.jsonl`);

  // Assemble the full file body as a single string and write atomically. One
  // `writeFileSync` keeps the partial-write window zero-length which is what
  // ingestion's lock-shared semantics assume (A1 Layer 3).
  const body = list.map((t) => canonicalJsonStringify(t)).join("\n") + "\n";
  writeFileSync(outPath, body, { encoding: "utf-8" });

  return outPath;
}

// ---- internals ----

function resolveCwd(explicit: string | undefined): string {
  if (explicit !== undefined) return resolve(explicit);
  const fromEnv = process.env[REGISTRY_ENV_VAR];
  if (fromEnv && fromEnv.length > 0) return resolve(fromEnv);
  return resolve(process.cwd());
}

function partitionDate(traces: readonly ProductionTrace[]): string {
  const first = traces[0];
  const started = first?.timing?.startedAt;
  if (typeof started === "string") {
    const parsed = parseIsoUtc(started);
    if (parsed !== null) return formatUtcDate(parsed);
  }
  return formatUtcDate(new Date());
}

function parseIsoUtc(value: string): Date | null {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  return d;
}

function formatUtcDate(d: Date): string {
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}
