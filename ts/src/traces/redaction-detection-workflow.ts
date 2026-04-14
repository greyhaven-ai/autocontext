import type {
  Detection,
  PatternDef,
  ScanOptions,
} from "./redaction-types.js";

export function overlaps(left: Detection, right: Detection): boolean {
  return left.start < right.end && right.start < left.end;
}

export function scanTextForSensitiveData(
  text: string,
  patterns: PatternDef[],
  opts?: ScanOptions,
): Detection[] {
  const detections: Detection[] = [];

  for (const definition of patterns) {
    const flags = definition.pattern.flags.replace(/y/g, "");
    const regex = new RegExp(
      definition.pattern.source,
      flags.includes("g") ? flags : `${flags}g`,
    );
    let match: RegExpExecArray | null;
    while ((match = regex.exec(text)) !== null) {
      if (match[0].length === 0) {
        regex.lastIndex += 1;
        continue;
      }
      detections.push({
        category: definition.category,
        matched: match[0],
        label: definition.label,
        start: match.index,
        end: match.index + match[0].length,
        confidence: definition.confidence,
      });
    }
  }

  return opts?.dedup === false ? detections : dedupDetections(detections);
}

export function dedupDetections(detections: Detection[]): Detection[] {
  if (detections.length <= 1) {
    return detections;
  }

  const sorted = [...detections].sort((left, right) => {
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
