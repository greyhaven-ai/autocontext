// Legacy model-record adapter — Layer 11 (Phase 1).
//
// Migrates pre-control-plane model records (stored under the prior training/
// registry shape) into first-class fine-tuned-model Artifacts.
//
// Data source
// -----------
// The training-layer ModelRegistry (src/training/promotion.ts) is purely
// in-memory with no persistence path. The v1 legacy adapter therefore reads
// from an explicit JSON file that callers supply via `--from <path>`, falling
// back to `<cwd>/.autocontext/legacy-model-records.json` when the flag is
// omitted. The file contains an array of ModelRecord-shaped documents, with
// the following optional enrichments:
//
//   - checkpointHash  (sha256:<64 hex>) — used verbatim when present;
//                     otherwise computeTreeHash(checkpointDir) is attempted.
//   - runId           — if present, provenance.authorType becomes
//                     "autocontext-run" and authorId mirrors this id.
//   - environmentTag  — defaults to "production" when omitted.
//
// Mapping rules
// -------------
//   ModelRecord.artifactId      -> Artifact.id if a valid ULID; else a fresh
//                                  ULID is minted and the legacy id is
//                                  preserved in provenance.authorId.
//   ModelRecord.scenario        -> Artifact.scenario (parseScenario; rejects
//                                  invalid slugs with a per-record error).
//   family + backend +
//   checkpointDir +
//   checkpointHash              -> pointer.json payload { kind: "model-checkpoint", ... }
//   ModelRecord.activationState -> Artifact.activationState (achieved by
//                                  replaying promotionHistory, then a final
//                                  state-pin if needed).
//   ModelRecord.promotionHistory -> one PromotionEvent per entry (validated
//                                  against the state-machine allow-list).
//   ModelRecord.registeredAt    -> Provenance.createdAt.
//
// Contract
// --------
//   - Never throws for per-record failures. Always returns a result bag with
//     `errors: Array<{ id, reason }>`. One bad record does not abort the batch.
//   - Idempotent: re-running on an already-migrated registry skips existing
//     ids (no writes, no state changes). Reported as `skipped`.
//
// Out of scope (Phase 2 / post-v1)
// --------------------------------
//   - @deprecated tags on training/promotion-*.ts — Phase 2 cleanup PRs.
//   - Removing the ModelRegistry class — Phase 3 removal.

import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, sep } from "node:path";
import {
  newArtifactId,
  parseArtifactId,
  parseEnvironmentTag,
  parseScenario,
  type ArtifactId,
  type ContentHash,
  type EnvironmentTag,
  type Scenario,
} from "../../contract/branded-ids.js";
import { createArtifact, createPromotionEvent } from "../../contract/factories.js";
import { computeTreeHash, type TreeFile } from "../../contract/invariants.js";
import type {
  ActivationState,
  Artifact,
  PromotionEvent,
  Provenance,
} from "../../contract/types.js";
import { validatePromotionEvent } from "../../contract/validators.js";
import { FineTunedModelPayloadSchema } from "./schema.js";

/**
 * Structural subset of the registry facade that the adapter needs. Typed
 * locally to preserve §3.2 import discipline (actuators/ does not import
 * registry/). The concrete Registry returned by `openRegistry()` satisfies
 * this shape nominally.
 */
export interface RegistryLike {
  saveArtifact(artifact: Artifact, payloadDir: string): void;
  loadArtifact(id: ArtifactId): Artifact;
  appendPromotionEvent(id: ArtifactId, event: PromotionEvent): Artifact;
}

export interface ImportLegacyOptions {
  /**
   * Explicit path to a legacy-model-records JSON file. Takes priority over
   * the default discovery path. Relative paths are NOT resolved — pass an
   * absolute path (the CLI resolves relative paths against `ctx.cwd` before
   * calling into this function).
   */
  readonly fromPath?: string;
}

export interface ImportLegacyError {
  readonly id: string;
  readonly reason: string;
}

export interface ImportLegacyResult {
  readonly imported: number;
  readonly skipped: number;
  readonly errors: readonly ImportLegacyError[];
}

const DEFAULT_SOURCE_REL = ".autocontext/legacy-model-records.json";

const CONTENT_HASH_RE = /^sha256:[0-9a-f]{64}$/;
const LEGACY_ACTIVATION_STATES: ReadonlySet<string> = new Set([
  "candidate",
  "shadow",
  "canary",
  "active",
  "disabled",
  "deprecated",
]);

type LegacyEvent = {
  readonly from: unknown;
  readonly to: unknown;
  readonly reason: unknown;
  readonly timestamp: unknown;
  readonly evidence?: unknown;
};

type LegacyRecord = {
  readonly artifactId?: unknown;
  readonly scenario?: unknown;
  readonly family?: unknown;
  readonly backend?: unknown;
  readonly checkpointDir?: unknown;
  readonly checkpointHash?: unknown;
  readonly activationState?: unknown;
  readonly promotionHistory?: unknown;
  readonly registeredAt?: unknown;
  readonly runId?: unknown;
  readonly environmentTag?: unknown;
};

/**
 * Import legacy pre-control-plane model records as fine-tuned-model Artifacts.
 *
 * Returns counts for progress reporting plus a per-record errors array. Never
 * throws: source-file failures (missing / unreadable / malformed) also surface
 * via the errors array (except for a cleanly-absent file at the default
 * discovery path, which is a graceful no-op).
 */
export async function importLegacyModelRecords(
  cwd: string,
  registry: RegistryLike,
  opts: ImportLegacyOptions = {},
): Promise<ImportLegacyResult> {
  const errors: ImportLegacyError[] = [];

  const explicit = opts.fromPath !== undefined;
  const sourcePath = opts.fromPath ?? join(cwd, DEFAULT_SOURCE_REL);

  if (!existsSync(sourcePath)) {
    if (explicit) {
      // Explicit path that doesn't exist is a user-visible error.
      errors.push({
        id: sourcePath,
        reason: `source file not found: ${sourcePath}`,
      });
      return { imported: 0, skipped: 0, errors };
    }
    // Default discovery path absent — nothing to do.
    return { imported: 0, skipped: 0, errors };
  }

  let rawText: string;
  try {
    rawText = readFileSync(sourcePath, "utf-8");
  } catch (err) {
    errors.push({
      id: sourcePath,
      reason: `read failure: ${err instanceof Error ? err.message : String(err)}`,
    });
    return { imported: 0, skipped: 0, errors };
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(rawText);
  } catch (err) {
    errors.push({
      id: sourcePath,
      reason: `failed to parse JSON: ${err instanceof Error ? err.message : String(err)}`,
    });
    return { imported: 0, skipped: 0, errors };
  }

  if (!Array.isArray(parsed)) {
    errors.push({
      id: sourcePath,
      reason: "source file must contain a JSON array of ModelRecord documents",
    });
    return { imported: 0, skipped: 0, errors };
  }

  let imported = 0;
  let skipped = 0;

  // Stage each record's pointer.json under a single scratch directory that we
  // clean up on the way out. Each record gets its own subdirectory so
  // concurrent saveArtifact(...) calls never collide.
  const scratch = mkdtempSync(join(tmpdir(), "autocontext-legacy-"));
  try {
    for (const raw of parsed) {
      const outcome = importOneRecord(raw as LegacyRecord, registry, scratch);
      if (outcome.kind === "imported") {
        imported += 1;
      } else if (outcome.kind === "skipped") {
        skipped += 1;
      } else {
        errors.push(outcome.error);
      }
    }
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }

  return { imported, skipped, errors };
}

type RecordOutcome =
  | { readonly kind: "imported" }
  | { readonly kind: "skipped" }
  | { readonly kind: "error"; readonly error: ImportLegacyError };

function importOneRecord(
  rec: LegacyRecord,
  registry: RegistryLike,
  scratchRoot: string,
): RecordOutcome {
  // ---- Identifier normalization ----
  const rawIdStr = typeof rec.artifactId === "string" ? rec.artifactId : "";
  const reuseId = rawIdStr !== "" ? parseArtifactId(rawIdStr) : null;
  // Idempotence check: if a valid ULID-keyed record already exists in the
  // registry, skip without error.
  if (reuseId !== null) {
    try {
      registry.loadArtifact(reuseId);
      return { kind: "skipped" };
    } catch {
      // Not present — fall through to import.
    }
  }
  const artifactId: ArtifactId = reuseId ?? newArtifactId();

  // Identifier used for error reporting — prefer the caller-visible value.
  const reportingId = rawIdStr !== "" ? rawIdStr : (artifactId as string);

  // ---- Scenario ----
  if (typeof rec.scenario !== "string") {
    return {
      kind: "error",
      error: { id: reportingId, reason: "scenario is required (string)" },
    };
  }
  const scenario: Scenario | null = parseScenario(rec.scenario);
  if (scenario === null) {
    return {
      kind: "error",
      error: {
        id: reportingId,
        reason: `invalid scenario slug: ${rec.scenario}`,
      },
    };
  }

  // ---- Environment tag ----
  let environmentTag: EnvironmentTag;
  if (rec.environmentTag !== undefined) {
    if (typeof rec.environmentTag !== "string") {
      return {
        kind: "error",
        error: { id: reportingId, reason: "environmentTag must be a string" },
      };
    }
    const parsedTag = parseEnvironmentTag(rec.environmentTag);
    if (parsedTag === null) {
      return {
        kind: "error",
        error: {
          id: reportingId,
          reason: `invalid environmentTag: ${rec.environmentTag}`,
        },
      };
    }
    environmentTag = parsedTag;
  } else {
    environmentTag = "production" as EnvironmentTag;
  }

  // ---- Family / backend / checkpointDir ----
  if (typeof rec.family !== "string" || rec.family.length === 0) {
    return {
      kind: "error",
      error: { id: reportingId, reason: "family is required (non-empty string)" },
    };
  }
  if (typeof rec.backend !== "string" || rec.backend.length === 0) {
    return {
      kind: "error",
      error: { id: reportingId, reason: "backend is required (non-empty string)" },
    };
  }
  if (typeof rec.checkpointDir !== "string" || rec.checkpointDir.length === 0) {
    return {
      kind: "error",
      error: {
        id: reportingId,
        reason: "checkpointDir is required (non-empty string)",
      },
    };
  }

  // ---- Checkpoint hash resolution ----
  const hashResolution = resolveCheckpointHash(rec.checkpointDir, rec.checkpointHash);
  if (hashResolution.kind === "error") {
    return {
      kind: "error",
      error: { id: reportingId, reason: hashResolution.reason },
    };
  }
  const checkpointHash = hashResolution.value;

  // ---- Promotion history pre-validation (each event must pass the schema +
  //      the state-machine transition allow-list). We verify transitions
  //      here — not just at appendPromotionEvent time — so malformed legacy
  //      data is reported without leaving a half-imported artifact on disk. ----
  const historyRaw: LegacyEvent[] = Array.isArray(rec.promotionHistory)
    ? (rec.promotionHistory as LegacyEvent[])
    : [];
  const events: PromotionEvent[] = [];
  let replayState: ActivationState = "candidate";
  for (let i = 0; i < historyRaw.length; i++) {
    const h = historyRaw[i]!;
    const built = buildPromotionEvent(h);
    if (built.kind === "error") {
      return {
        kind: "error",
        error: {
          id: reportingId,
          reason: `promotionHistory[${i}]: ${built.reason}`,
        },
      };
    }
    const ev = built.value;
    // Local precondition check: from must match the current replayed state.
    if (ev.from !== replayState) {
      return {
        kind: "error",
        error: {
          id: reportingId,
          reason: `promotionHistory[${i}]: from=${ev.from} does not match replayed state=${replayState}`,
        },
      };
    }
    if (!isLegalTransition(ev.from, ev.to)) {
      return {
        kind: "error",
        error: {
          id: reportingId,
          reason: `promotionHistory[${i}]: transition ${ev.from} → ${ev.to} is not in the allow-list`,
        },
      };
    }
    events.push(ev);
    replayState = ev.to;
  }

  // ---- Activation-state consistency ----
  const declared: unknown = rec.activationState;
  if (typeof declared !== "string" || !LEGACY_ACTIVATION_STATES.has(declared)) {
    return {
      kind: "error",
      error: {
        id: reportingId,
        reason: `invalid activationState: ${String(declared)}`,
      },
    };
  }
  const declaredState = declared as ActivationState;
  if (declaredState !== replayState) {
    return {
      kind: "error",
      error: {
        id: reportingId,
        reason: `activationState=${declaredState} does not match promotionHistory replay end state=${replayState}`,
      },
    };
  }

  // ---- registeredAt ----
  if (typeof rec.registeredAt !== "string" || rec.registeredAt.length === 0) {
    return {
      kind: "error",
      error: { id: reportingId, reason: "registeredAt is required (ISO-8601 string)" },
    };
  }

  // ---- Provenance ----
  const runId = typeof rec.runId === "string" && rec.runId.length > 0 ? rec.runId : null;
  const authorType: Provenance["authorType"] = runId !== null ? "autocontext-run" : "external-agent";
  let authorId: string;
  if (runId !== null) {
    authorId = runId;
  } else if (reuseId === null && rawIdStr.length > 0) {
    // We minted a fresh id; preserve the legacy one for audit.
    authorId = `legacy-model-record:${rawIdStr}`;
  } else {
    authorId = "legacy-model-record";
  }
  const provenance: Provenance = {
    authorType,
    authorId,
    parentArtifactIds: [],
    createdAt: rec.registeredAt,
  };

  // ---- Materialize pointer.json payload under scratch/<artifactId>/. ----
  const payloadDir = join(scratchRoot, artifactId);
  mkdirSync(payloadDir, { recursive: true });
  const pointer = {
    kind: "model-checkpoint" as const,
    externalPath: rec.checkpointDir,
    checkpointHash,
    family: rec.family,
    backend: rec.backend,
  };
  // Defense in depth: the pointer schema is also enforced on apply(), but
  // validating here surfaces malformed records via the errors array instead
  // of a later promotion-time crash.
  const parsedPointer = FineTunedModelPayloadSchema.safeParse(pointer);
  if (!parsedPointer.success) {
    return {
      kind: "error",
      error: {
        id: reportingId,
        reason: `pointer.json would fail schema validation: ${parsedPointer.error.message}`,
      },
    };
  }
  const pointerText = JSON.stringify(pointer, null, 2);
  writeFileSync(join(payloadDir, "pointer.json"), pointerText, "utf-8");

  // ---- Hash the payload directory (same algorithm the registry uses). ----
  const payloadHash: ContentHash = computeTreeHash([
    { path: "pointer.json", content: Buffer.from(pointerText, "utf-8") },
  ]);

  // ---- Build + save the Artifact, then replay promotion history. ----
  const artifact: Artifact = createArtifact({
    id: artifactId,
    actuatorType: "fine-tuned-model",
    scenario,
    environmentTag,
    payloadHash,
    provenance,
  });

  try {
    registry.saveArtifact(artifact, payloadDir);
  } catch (err) {
    return {
      kind: "error",
      error: {
        id: reportingId,
        reason: `saveArtifact failed: ${err instanceof Error ? err.message : String(err)}`,
      },
    };
  }

  for (const ev of events) {
    try {
      registry.appendPromotionEvent(artifactId, ev);
    } catch (err) {
      // If we fail mid-replay the artifact is half-imported on disk. Report
      // the problem so operators can clean up; don't throw.
      return {
        kind: "error",
        error: {
          id: reportingId,
          reason: `appendPromotionEvent failed at event ${ev.from}→${ev.to}: ${err instanceof Error ? err.message : String(err)}`,
        },
      };
    }
  }

  return { kind: "imported" };
}

// ---- Helpers ----

type Resolution =
  | { readonly kind: "ok"; readonly value: string }
  | { readonly kind: "error"; readonly reason: string };

function resolveCheckpointHash(
  checkpointDir: string,
  hashFromRecord: unknown,
): Resolution {
  if (typeof hashFromRecord === "string") {
    if (!CONTENT_HASH_RE.test(hashFromRecord)) {
      return {
        kind: "error",
        reason: `invalid checkpointHash: ${hashFromRecord} (must be sha256:<64 hex>)`,
      };
    }
    return { kind: "ok", value: hashFromRecord };
  }

  // No hash provided — try to hash the on-disk checkpoint directory.
  if (!existsSync(checkpointDir)) {
    return {
      kind: "error",
      reason: `checkpointHash missing and checkpointDir ${checkpointDir} does not exist`,
    };
  }

  try {
    const files: TreeFile[] = [];
    walkTree(checkpointDir, "", files);
    if (files.length === 0) {
      return {
        kind: "error",
        reason: `checkpointHash missing and checkpointDir ${checkpointDir} is empty`,
      };
    }
    return { kind: "ok", value: computeTreeHash(files) };
  } catch (err) {
    return {
      kind: "error",
      reason: `checkpointHash missing and unable to hash checkpointDir: ${err instanceof Error ? err.message : String(err)}`,
    };
  }
}

function walkTree(absRoot: string, relPrefix: string, out: TreeFile[]): void {
  let entries: string[];
  try {
    entries = readdirSync(join(absRoot, relPrefix));
  } catch {
    return;
  }
  for (const entry of entries) {
    const relPath = relPrefix === "" ? entry : `${relPrefix}/${entry}`;
    const absPath = join(absRoot, relPath.split("/").join(sep));
    const st = statSync(absPath);
    if (st.isDirectory()) {
      walkTree(absRoot, relPath, out);
    } else if (st.isFile()) {
      out.push({ path: relPath, content: readFileSync(absPath) });
    }
  }
}

type BuildEvent =
  | { readonly kind: "ok"; readonly value: PromotionEvent }
  | { readonly kind: "error"; readonly reason: string };

function buildPromotionEvent(raw: LegacyEvent): BuildEvent {
  if (typeof raw.from !== "string" || !LEGACY_ACTIVATION_STATES.has(raw.from)) {
    return { kind: "error", reason: `invalid from state: ${String(raw.from)}` };
  }
  if (typeof raw.to !== "string" || !LEGACY_ACTIVATION_STATES.has(raw.to)) {
    return { kind: "error", reason: `invalid to state: ${String(raw.to)}` };
  }
  if (typeof raw.reason !== "string") {
    return { kind: "error", reason: "reason is required" };
  }
  if (typeof raw.timestamp !== "string") {
    return { kind: "error", reason: "timestamp is required" };
  }
  const event = createPromotionEvent({
    from: raw.from as ActivationState,
    to: raw.to as ActivationState,
    reason: raw.reason,
    timestamp: raw.timestamp,
  });
  const v = validatePromotionEvent(event);
  if (!v.valid) {
    return { kind: "error", reason: `schema: ${v.errors.join("; ")}` };
  }
  return { kind: "ok", value: event };
}

/**
 * State-machine allow-list check. Mirrors promotion/transitions.ts but is
 * duplicated here to preserve §3.2 import discipline (actuators/ does not
 * import promotion/). Keep in sync with promotion/transitions.ts.
 */
const ALLOWED: Readonly<Record<ActivationState, readonly ActivationState[]>> = {
  candidate:  ["shadow", "canary", "active", "disabled"],
  shadow:     ["canary", "active", "disabled", "candidate"],
  canary:     ["active", "disabled", "candidate", "shadow"],
  active:     ["deprecated", "disabled", "candidate", "canary", "shadow"],
  disabled:   ["candidate"],
  deprecated: ["candidate"],
};

function isLegalTransition(from: ActivationState, to: ActivationState): boolean {
  const next = ALLOWED[from];
  return next !== undefined && next.includes(to);
}
