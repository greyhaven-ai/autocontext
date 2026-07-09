import { createHash } from "node:crypto";

export const EVALUATOR_EPOCH_REBASELINE = "evaluator_epoch_rebaseline";

export interface EvaluatorEpoch {
  epochId: string;
  rubricHash: string;
  judgeProvider: string;
  judgeModel: string;
}

function sha256(text: string): string {
  return createHash("sha256").update(text, "utf8").digest("hex");
}

/** Content hash of the rubric + judge. Byte-identical to the Python reference (see the shared fixture). */
export function computeEvaluatorEpoch(
  rubricText: string,
  judgeProvider: string,
  judgeModel: string,
): EvaluatorEpoch {
  const rubricHash = sha256(rubricText);
  // Keys inserted in sorted order so JSON.stringify matches Python json.dumps(sort_keys=True,
  // separators=(",", ":"), ensure_ascii=False) byte-for-byte.
  const canonical = JSON.stringify({
    judge_model: judgeModel,
    judge_provider: judgeProvider,
    rubric_hash: rubricHash,
  });
  return { epochId: sha256(canonical), rubricHash, judgeProvider, judgeModel };
}

/** Two epoch ids are comparable only when equal; null (legacy/unknown) equals only null. */
export function areComparable(a: string | null | undefined, b: string | null | undefined): boolean {
  return (a ?? null) === (b ?? null);
}

export interface EpochBaselineDecision {
  rebaseline: boolean;
  staleEpoch: string | null;
}

/**
 * Decide whether a round's epoch forces the improve loop to re-baseline.
 * Parity with Python resolve_epoch_rebaseline.
 *
 * The first round (hasBaseline false) establishes the baseline and never re-baselines. When a
 * baseline exists and the round's epoch is not comparable to it, the prior baseline is stale and is
 * excluded so the loop re-baselines under the round's epoch.
 */
export function resolveEpochRebaseline(
  baselineEpoch: string | null,
  roundEpoch: string | null,
  hasBaseline: boolean,
): EpochBaselineDecision {
  if (!hasBaseline || areComparable(baselineEpoch, roundEpoch)) {
    return { rebaseline: false, staleEpoch: null };
  }
  return { rebaseline: true, staleEpoch: baselineEpoch };
}
