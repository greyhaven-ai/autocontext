import type {
  Detection,
  PolicyAction,
  Redaction,
  RedactionResult,
} from "./redaction-types.js";

export function applyDetectionsWithPolicy(
  text: string,
  detections: Detection[],
  resolveAction: (category: string) => PolicyAction,
): RedactionResult {
  const redactions: Redaction[] = [];
  const blockReasons: string[] = [];
  let requiresManualReview = false;
  let blocked = false;

  const toRedact: Detection[] = [];
  for (const detection of detections) {
    const action = resolveAction(detection.category);
    switch (action) {
      case "block":
        blocked = true;
        blockReasons.push(
          `Blocked: ${detection.label} (${detection.category}) at position ${detection.start}`,
        );
        break;
      case "redact":
        toRedact.push(detection);
        break;
      case "require-manual-approval":
        requiresManualReview = true;
        break;
      case "warn":
        break;
    }
  }

  let redactedText = text;
  const sortedRedactions = [...toRedact].sort((left, right) => right.start - left.start);
  for (const detection of sortedRedactions) {
    const replacement = `[REDACTED:${detection.category}]`;
    redactedText =
      redactedText.slice(0, detection.start)
      + replacement
      + redactedText.slice(detection.end);
    redactions.push({
      category: detection.category,
      original: detection.matched,
      replacement,
      start: detection.start,
      end: detection.end,
    });
  }

  return {
    redactedText,
    detections,
    redactions: redactions.reverse(),
    blocked,
    blockReasons,
    requiresManualReview,
  };
}
