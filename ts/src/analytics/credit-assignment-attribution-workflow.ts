import { roundToDecimals } from "./number-utils.js";
import { buildZeroCredits } from "./credit-assignment-serialization-workflow.js";

export interface AttributionChangeLike {
  component: string;
  magnitude: number;
}

export interface AttributionVectorLike {
  scoreDelta: number;
  changes: AttributionChangeLike[];
  totalChangeMagnitude: number;
}

export function buildAttributedCredits(
  vector: AttributionVectorLike,
): Record<string, number> {
  if (vector.scoreDelta <= 0 || vector.changes.length === 0) {
    return buildZeroCredits(vector.changes);
  }

  const totalMagnitude = vector.totalChangeMagnitude;
  if (totalMagnitude === 0) {
    return buildZeroCredits(vector.changes);
  }

  const credits: Record<string, number> = {};
  for (const change of vector.changes) {
    credits[change.component] = roundToDecimals(
      vector.scoreDelta * (change.magnitude / totalMagnitude),
      6,
    );
  }
  return credits;
}
