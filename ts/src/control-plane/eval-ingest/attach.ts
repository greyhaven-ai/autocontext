// Orchestrate ingestion of a single EvalRun into the registry:
//   1. Validate the EvalRun (schema + business rules) via validateEvalRunForIngestion.
//   2. Refuse duplicates (same runId already ingested for this artifact).
//   3. Persist the EvalRun file via registry.attachEvalRun.
//   4. Append a new EvalRunRef onto the artifact and rewrite its metadata.
//
// The registry.attachEvalRun primitive only persists the EvalRun file; it does
// NOT touch the Artifact's evalRuns[] list. This module owns that higher-level
// atomicity: we write the file first (which the registry does under lock), then
// rewrite metadata (also under lock) — on partial failure the caller observes
// either (a) no file + no ref, or (b) file-only which repair/validate can
// reconcile. Critically, if the EvalRun-file write fails, metadata is never
// touched, so callers never see an EvalRunRef pointing at a missing file.
//
// Import discipline (§3.2): eval-ingest/ imports contract/ + registry/ only.

import type { Artifact, EvalRun, EvalRunRef } from "../contract/types.js";
import { updateArtifactMetadata } from "../registry/artifact-store.js";
import type { Registry } from "../registry/index.js";
import { EvalRunAlreadyAttachedError } from "./errors.js";
import { validateEvalRunForIngestion } from "./validator.js";

export interface AttachEvalRunResult {
  readonly artifact: Artifact;
  readonly evalRun: EvalRun;
}

/**
 * Attach a single EvalRun to its artifact. See module-level docstring for the
 * ordering rationale.
 */
export async function attachEvalRun(
  registry: Registry,
  evalRun: EvalRun,
): Promise<AttachEvalRunResult> {
  // 1. Validate (combined schema + business rules).
  const v = validateEvalRunForIngestion(evalRun, { registry });
  if (!v.valid) {
    throw new Error(
      `attachEvalRun: EvalRun failed validation: ${v.errors.join("; ")}`,
    );
  }

  // 2. Load current artifact (throws if unknown — already covered by validator,
  //    but we need the Artifact instance anyway).
  const current = registry.loadArtifact(evalRun.artifactId);

  // 3. Duplicate check against the existing EvalRunRef list.
  if (current.evalRuns.some((ref) => ref.evalRunId === evalRun.runId)) {
    throw new EvalRunAlreadyAttachedError(evalRun.artifactId, evalRun.runId);
  }

  // 4. Persist the EvalRun file first (under the registry lock). If this
  //    throws, metadata is never touched — the artifact's evalRuns[] list
  //    remains in its pre-attach state.
  registry.attachEvalRun(evalRun);

  // 5. Append the EvalRunRef and rewrite metadata.
  const newRef: EvalRunRef = {
    evalRunId: evalRun.runId,
    suiteId: evalRun.suiteId,
    ingestedAt: evalRun.ingestedAt,
  };
  const updated: Artifact = {
    ...current,
    evalRuns: [...current.evalRuns, newRef],
  };

  updateArtifactMetadata(registry.cwd, updated);

  return { artifact: updated, evalRun };
}
