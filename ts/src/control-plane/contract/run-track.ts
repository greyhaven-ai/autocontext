import type { EvalRun, RunTrack } from "./types.js";
import { evalRunIntegrityStatus } from "./eval-run-integrity.js";

export const RUN_TRACKS = ["verified", "experimental"] as const;

export interface EvalRunTrackAssessment {
  readonly track: RunTrack;
  readonly promotionEligible: boolean;
  readonly reasons: readonly string[];
  readonly warnings: readonly string[];
}

export function isRunTrack(value: unknown): value is RunTrack {
  return RUN_TRACKS.includes(value as RunTrack);
}

export function effectiveEvalRunTrack(run: Pick<EvalRun, "track" | "integrity">): RunTrack {
  if (run.track === "experimental") return "experimental";
  if (evalRunIntegrityStatus(run) !== "clean") return "experimental";
  return "verified";
}

export function assessEvalRunTrack(
  run: Pick<EvalRun, "track" | "integrity" | "adapterProvenance" | "reconciliation">,
  label: string,
): EvalRunTrackAssessment {
  const track = effectiveEvalRunTrack(run);
  const reasons: string[] = [];
  const warnings: string[] = [];

  const explicitTrackIssue = describeExperimentalEvalRunTrack(run, label);
  if (explicitTrackIssue !== null) {
    reasons.push(explicitTrackIssue);
  }

  const integrityStatus = evalRunIntegrityStatus(run);
  if (integrityStatus !== "clean") {
    reasons.push(`${label} EvalRun integrity status is ${String(integrityStatus)}`);
  }

  if (track === "verified") {
    if (run.adapterProvenance === undefined) {
      warnings.push(`${label} EvalRun is missing adapter provenance`);
    }
    if (run.reconciliation === undefined) {
      warnings.push(`${label} EvalRun is missing score reconciliation`);
    }
  }

  return {
    track,
    promotionEligible: reasons.length === 0,
    reasons,
    warnings,
  };
}

export function describeExperimentalEvalRunTrack(
  run: Pick<EvalRun, "track">,
  label: string,
): string | null {
  return run.track === "experimental" ? `${label} EvalRun track is experimental` : null;
}
