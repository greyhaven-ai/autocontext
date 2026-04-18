// Errors thrown by the eval-ingest layer.
// Exported from eval-ingest/index.ts for callers.

import type { ArtifactId } from "../contract/branded-ids.js";

/**
 * Thrown by `attachEvalRun` when the same (artifactId, runId) pair has already
 * been ingested. EvalRuns are append-only per artifact; the caller must pick a
 * fresh runId or target a different artifact.
 */
export class EvalRunAlreadyAttachedError extends Error {
  public readonly name = "EvalRunAlreadyAttachedError" as const;
  public readonly artifactId: ArtifactId;
  public readonly runId: string;

  constructor(artifactId: ArtifactId, runId: string) {
    super(`EvalRun ${runId} is already attached to artifact ${artifactId}`);
    this.artifactId = artifactId;
    this.runId = runId;
    // Restore prototype chain for correct instanceof in ES5-transpiled callers.
    Object.setPrototypeOf(this, EvalRunAlreadyAttachedError.prototype);
  }
}
