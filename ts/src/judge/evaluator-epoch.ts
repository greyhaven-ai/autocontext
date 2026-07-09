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
