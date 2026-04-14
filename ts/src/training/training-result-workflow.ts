import type { TrainingResult } from "./training-types.js";

export function buildFailedTrainingResult(
  backend: string,
  start: number,
  error: string,
  checkpointDir?: string,
): TrainingResult {
  return {
    status: "failed",
    backend,
    checkpointDir,
    durationMs: performance.now() - start,
    error,
  };
}
