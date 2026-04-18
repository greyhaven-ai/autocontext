// Public surface of the autocontext control-plane registry layer.
// This is the persistence + I/O facade — everything that touches disk.
// Imports from contract/ and promotion/ only; never the reverse.

import { join } from "node:path";
import type {
  ArtifactId,
  EnvironmentTag,
  Scenario,
} from "../contract/branded-ids.js";
import type {
  ActuatorType,
  Artifact,
  EvalRun,
  PromotionEvent,
} from "../contract/types.js";
import { createPromotionEvent } from "../contract/factories.js";
import { appendPromotionEvent as applyAppend } from "../promotion/append.js";
import {
  saveArtifact as fsSaveArtifact,
  loadArtifact as fsLoadArtifact,
  updateArtifactMetadata,
  artifactDirectory,
  listArtifactIds,
} from "./artifact-store.js";
import {
  saveEvalRun as fsSaveEvalRun,
  loadEvalRun as fsLoadEvalRun,
  listEvalRunIds,
} from "./eval-run-store.js";
import {
  appendHistory,
  readHistory,
} from "./history-store.js";
import {
  writeStatePointer,
  readStatePointer,
  deleteStatePointer,
  listStatePointers,
} from "./state-pointer.js";
import { acquireLock } from "./lock.js";
import { hashDirectory } from "./content-address.js";
import {
  createFsIndexCache,
  type IndexCache,
  type ListCandidatesFilter,
} from "./index-cache.js";
import { repair as fsRepair } from "./repair.js";
import { validate as fsValidate, type ValidationReport } from "./validate.js";

// ---- Re-exports ----

export { acquireLock } from "./lock.js";
export type { LockHandle } from "./lock.js";

export { hashDirectory } from "./content-address.js";

export {
  saveArtifact,
  loadArtifact,
  updateArtifactMetadata,
  listArtifactIds,
  artifactDirectory,
} from "./artifact-store.js";

export {
  appendHistory,
  readHistory,
} from "./history-store.js";

export {
  saveEvalRun,
  loadEvalRun,
  listEvalRunIds,
} from "./eval-run-store.js";

export {
  writeStatePointer,
  readStatePointer,
  deleteStatePointer,
  listStatePointers,
  statePointerPath,
} from "./state-pointer.js";
export type { StatePointer, StatePointerEntry } from "./state-pointer.js";

export {
  createFsIndexCache,
} from "./index-cache.js";
export type { IndexCache, ListCandidatesFilter } from "./index-cache.js";

export { repair } from "./repair.js";
export { validate } from "./validate.js";
export type { ValidationReport, ValidationIssue, IssueKind } from "./validate.js";

// ---- Registry facade ----

export interface Registry {
  saveArtifact(artifact: Artifact, payloadDir: string): void;
  loadArtifact(id: ArtifactId): Artifact;
  listCandidates(filter: ListCandidatesFilter): Artifact[];
  getActive(scenario: Scenario, actuatorType: ActuatorType, environmentTag: EnvironmentTag): Artifact | null;

  /**
   * Apply a PromotionEvent to an artifact transactionally:
   *   1. Acquire .autocontext/lock
   *   2. Load current artifact from disk
   *   3. Append the event via the contract's appendPromotionEvent (state-machine + invariants)
   *   4. Append to promotion-history.jsonl (verifies on-disk prefix)
   *   5. Rewrite metadata.json (atomic via tmp+rename)
   *   6. If event.to === "active": flip the state pointer AND demote any prior active artifact
   *   7. Release lock
   *
   * Returns the new (post-event) Artifact.
   */
  appendPromotionEvent(id: ArtifactId, event: PromotionEvent): Artifact;

  attachEvalRun(run: EvalRun): void;
  loadEvalRun(artifactId: ArtifactId, runId: string): EvalRun;

  /** Force a re-scan of every artifact's history and rebuild state/active/. Idempotent. */
  repair(): void;

  /** Walk the registry and return a structured validation report. */
  validate(): ValidationReport;
}

/**
 * Open the registry rooted at `cwd` (the project / workspace root). All on-disk
 * I/O is contained within `<cwd>/.autocontext/`. The constructor itself does
 * not perform any I/O; it returns a facade whose methods will create directories
 * as needed.
 */
export function openRegistry(cwd: string): Registry {
  const cache: IndexCache = createFsIndexCache(cwd);

  return {
    saveArtifact(artifact, payloadDir): void {
      const lock = acquireLock(cwd);
      try {
        fsSaveArtifact(cwd, artifact, payloadDir);
      } finally {
        lock.release();
      }
    },

    loadArtifact(id): Artifact {
      return fsLoadArtifact(cwd, id);
    },

    listCandidates(filter): Artifact[] {
      return cache.listCandidates(filter);
    },

    getActive(scenario, actuatorType, environmentTag): Artifact | null {
      return cache.getByState(scenario, actuatorType, environmentTag);
    },

    appendPromotionEvent(id, event): Artifact {
      const lock = acquireLock(cwd);
      try {
        const before = fsLoadArtifact(cwd, id);
        // Apply the event via the pure state-machine; throws on illegal transitions.
        const after = applyAppend(before, event);
        // Persist to history.jsonl with on-disk prefix verification.
        const historyPath = join(artifactDirectory(cwd, id), "promotion-history.jsonl");
        appendHistory(historyPath, before.promotionHistory, after.promotionHistory);
        // Rewrite metadata.json atomically.
        updateArtifactMetadata(cwd, after);

        // If we just promoted to active, flip the state pointer AND demote
        // any prior active artifact for the same (scenario, actuatorType, env).
        if (event.to === "active") {
          demotePreviousActiveAndPoint(cwd, after, event.timestamp);
        }
        return after;
      } finally {
        lock.release();
      }
    },

    attachEvalRun(run): void {
      const lock = acquireLock(cwd);
      try {
        const dir = artifactDirectory(cwd, run.artifactId);
        fsSaveEvalRun(dir, run);
      } finally {
        lock.release();
      }
    },

    loadEvalRun(artifactId, runId): EvalRun {
      const dir = artifactDirectory(cwd, artifactId);
      return fsLoadEvalRun(dir, runId);
    },

    repair(): void {
      const lock = acquireLock(cwd);
      try {
        fsRepair(cwd);
      } finally {
        lock.release();
      }
    },

    validate(): ValidationReport {
      return fsValidate(cwd);
    },
  };
}

/**
 * Internal: when an artifact is promoted to active, demote any prior active
 * artifact for the same (scenario, actuatorType, environmentTag) tuple to
 * "deprecated", and update the state pointer.
 *
 * The active → deprecated transition is in the allow-list (see promotion/transitions.ts).
 */
function demotePreviousActiveAndPoint(
  cwd: string,
  newlyActive: Artifact,
  timestamp: string,
): void {
  const prior = readStatePointer(
    cwd,
    newlyActive.scenario,
    newlyActive.actuatorType,
    newlyActive.environmentTag,
  );
  if (prior !== null && prior.artifactId !== newlyActive.id) {
    let priorArtifact: Artifact | null = null;
    try {
      priorArtifact = fsLoadArtifact(cwd, prior.artifactId);
    } catch {
      // Pointer dangles — write the new pointer and continue.
    }
    if (priorArtifact !== null && priorArtifact.activationState === "active") {
      const demoteEvent = createPromotionEvent({
        from: "active",
        to: "deprecated",
        reason: `superseded by ${newlyActive.id}`,
        timestamp,
      });
      const demoted = applyAppend(priorArtifact, demoteEvent);
      const historyPath = join(
        artifactDirectory(cwd, priorArtifact.id),
        "promotion-history.jsonl",
      );
      appendHistory(historyPath, priorArtifact.promotionHistory, demoted.promotionHistory);
      updateArtifactMetadata(cwd, demoted);
    }
  }
  writeStatePointer(
    cwd,
    newlyActive.scenario,
    newlyActive.actuatorType,
    newlyActive.environmentTag,
    { artifactId: newlyActive.id, asOf: timestamp },
  );
}

// Suppress unused-import warnings for the symbols re-exported by name.
void readHistory;
void deleteStatePointer;
void listStatePointers;
void listEvalRunIds;
void listArtifactIds;
