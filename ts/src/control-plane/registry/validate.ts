import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { Artifact, PromotionEvent } from "../contract/types.js";
import type { ArtifactId } from "../contract/branded-ids.js";
import { validateArtifact, validatePromotionEvent } from "../contract/validators.js";
import {
  validateAppendOnly,
  validateLineageNoCycles,
} from "../contract/invariants.js";
import { isAllowedTransition } from "../promotion/transitions.js";
import {
  artifactDirectory,
  listArtifactIds,
} from "./artifact-store.js";
import { hashDirectory } from "./content-address.js";
import { readHistory } from "./history-store.js";

export type IssueKind =
  | "payload-hash-mismatch"
  | "schema-validation-error"
  | "lineage-cycle"
  | "append-only-violation"
  | "invalid-promotion-transition"
  | "history-parse-error"
  | "metadata-parse-error"
  | "signature-missing"
  | "signature-present"
  | "signature-invalid";

export interface ValidationIssue {
  readonly kind: IssueKind;
  readonly artifactId?: ArtifactId;
  readonly path?: string;
  readonly message: string;
}

export interface ValidationReport {
  readonly ok: boolean;
  readonly issues: readonly ValidationIssue[];
}

const HARD_FAILURE_KINDS: ReadonlySet<IssueKind> = new Set<IssueKind>([
  "payload-hash-mismatch",
  "schema-validation-error",
  "lineage-cycle",
  "append-only-violation",
  "invalid-promotion-transition",
  "history-parse-error",
  "metadata-parse-error",
  "signature-invalid",
]);

/**
 * Walk the registry and report:
 *   - payload hash mismatches
 *   - schema validation failures
 *   - DAG cycles in lineage (parentArtifactIds)
 *   - append-only violations (history vs metadata.promotionHistory)
 *   - invalid promotion transitions (per the allow-list)
 *   - signature status (present / missing — informational in v1)
 *
 * `ok` is true iff no hard-failure issues are present.
 */
export function validate(registryRoot: string): ValidationReport {
  const issues: ValidationIssue[] = [];
  const ids = listArtifactIds(registryRoot);

  // First pass: parse metadata for each id (for cycle detection).
  const metadata = new Map<ArtifactId, Artifact>();
  for (const id of ids) {
    const dir = artifactDirectory(registryRoot, id);
    const metaPath = join(dir, "metadata.json");
    if (!existsSync(metaPath)) {
      issues.push({
        kind: "metadata-parse-error",
        artifactId: id,
        path: metaPath,
        message: `metadata.json missing`,
      });
      continue;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(readFileSync(metaPath, "utf-8"));
    } catch (err) {
      issues.push({
        kind: "metadata-parse-error",
        artifactId: id,
        path: metaPath,
        message: `not valid JSON: ${(err as Error).message}`,
      });
      continue;
    }
    const v = validateArtifact(parsed);
    if (!v.valid) {
      issues.push({
        kind: "schema-validation-error",
        artifactId: id,
        path: metaPath,
        message: v.errors.join("; "),
      });
      continue;
    }
    metadata.set(id, parsed as Artifact);
  }

  // Build parent-lookup for cycle checks.
  const parentLookup = (x: ArtifactId): readonly ArtifactId[] | null => {
    const a = metadata.get(x);
    return a ? a.provenance.parentArtifactIds : null;
  };

  for (const [id, art] of metadata) {
    const dir = artifactDirectory(registryRoot, id);

    // Payload hash check.
    const payloadDir = join(dir, "payload");
    if (existsSync(payloadDir)) {
      const recomputed = hashDirectory(payloadDir);
      if (recomputed !== art.payloadHash) {
        issues.push({
          kind: "payload-hash-mismatch",
          artifactId: id,
          path: payloadDir,
          message: `expected ${art.payloadHash}, got ${recomputed}`,
        });
      }
    }

    // Lineage cycle check.
    const cycle = validateLineageNoCycles(id, art.provenance.parentArtifactIds, parentLookup);
    if (!cycle.valid) {
      issues.push({
        kind: "lineage-cycle",
        artifactId: id,
        message: cycle.errors.join("; "),
      });
    }

    // History file vs metadata.promotionHistory.
    const historyPath = join(dir, "promotion-history.jsonl");
    let history: PromotionEvent[] | null = null;
    try {
      history = readHistory(historyPath);
    } catch (err) {
      issues.push({
        kind: "history-parse-error",
        artifactId: id,
        path: historyPath,
        message: (err as Error).message,
      });
    }
    if (history !== null) {
      // Validate each event against the schema individually for clearer errors.
      for (let i = 0; i < history.length; i++) {
        const ev = history[i];
        const r = validatePromotionEvent(ev);
        if (!r.valid) {
          issues.push({
            kind: "schema-validation-error",
            artifactId: id,
            path: `${historyPath}#${i}`,
            message: r.errors.join("; "),
          });
        }
        // Transition allow-list (per-event).
        if (!isAllowedTransition(ev.from, ev.to)) {
          issues.push({
            kind: "invalid-promotion-transition",
            artifactId: id,
            path: `${historyPath}#${i}`,
            message: `${ev.from} → ${ev.to} is not in the allow-list`,
          });
        }
        // Signature presence (informational).
        if (ev.signature !== undefined) {
          issues.push({
            kind: "signature-present",
            artifactId: id,
            path: `${historyPath}#${i}`,
            message: `event has signature (verification deferred to a future layer)`,
          });
        } else {
          issues.push({
            kind: "signature-missing",
            artifactId: id,
            path: `${historyPath}#${i}`,
            message: `event has no signature`,
          });
        }
      }

      // Append-only check: history (on disk) MUST be a (super-)set extending
      // metadata.promotionHistory.
      const appendOk = validateAppendOnly(art.promotionHistory, history);
      if (!appendOk.valid) {
        // The file is shorter or differs from metadata — possible mutation.
        issues.push({
          kind: "append-only-violation",
          artifactId: id,
          path: historyPath,
          message: appendOk.errors.join("; "),
        });
      }
    }
  }

  const ok = issues.every((i) => !HARD_FAILURE_KINDS.has(i.kind));
  return { ok, issues };
}
