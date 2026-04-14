import type {
  Detection,
  PolicyAction,
} from "./redaction-types.js";
import { overlaps } from "./redaction-detection-workflow.js";

export function actionPriority(action: PolicyAction): number {
  switch (action) {
    case "block":
      return 3;
    case "require-manual-approval":
      return 2;
    case "redact":
      return 1;
    case "warn":
    default:
      return 0;
  }
}

export function resolvePolicyOverlaps(
  detections: Detection[],
  resolveAction: (category: string) => PolicyAction,
): Detection[] {
  if (detections.length <= 1) {
    return detections;
  }

  const sorted = [...detections].sort((left, right) => {
    const priorityDelta = actionPriority(resolveAction(right.category)) - actionPriority(resolveAction(left.category));
    if (priorityDelta !== 0) {
      return priorityDelta;
    }
    const confidenceDelta = right.confidence - left.confidence;
    if (confidenceDelta !== 0) {
      return confidenceDelta;
    }
    const widthDelta = (left.end - left.start) - (right.end - right.start);
    if (widthDelta !== 0) {
      return widthDelta;
    }
    return left.start - right.start;
  });

  const result: Detection[] = [];
  for (const detection of sorted) {
    if (!result.some((existing) => overlaps(existing, detection))) {
      result.push(detection);
    }
  }

  return result.sort((left, right) => left.start - right.start || right.confidence - left.confidence);
}
