import type { TraceMessage } from "./public-schema.js";
import {
  applyRedactionPolicy,
  type RedactionPolicy,
  type SensitiveDataDetector,
} from "./redaction.js";
import type { RedactionSummary } from "./export-workflow-types.js";

export function emptyRedactionSummary(): RedactionSummary {
  return {
    totalDetections: 0,
    totalRedactions: 0,
    blocked: false,
    blockReasons: [],
    categoryCounts: {},
  };
}

export function redactTraceMessages(opts: {
  messages: TraceMessage[];
  detector: SensitiveDataDetector;
  policy: RedactionPolicy;
}): {
  redactedMessages: TraceMessage[];
  redactionSummary: RedactionSummary;
} {
  let blocked = false;
  const blockReasons: string[] = [];
  let totalDetections = 0;
  let totalRedactions = 0;
  const categoryCounts: Record<string, number> = {};
  const redactedMessages: TraceMessage[] = [];

  for (const message of opts.messages) {
    const result = applyRedactionPolicy(message.content, {
      detector: opts.detector,
      policy: opts.policy,
    });

    if (result.blocked) {
      blocked = true;
      blockReasons.push(...result.blockReasons);
    }
    totalDetections += result.detections.length;
    totalRedactions += result.redactions.length;
    for (const detection of result.detections) {
      categoryCounts[detection.category] = (categoryCounts[detection.category] ?? 0) + 1;
    }

    redactedMessages.push({
      ...message,
      content: result.redactedText,
    });
  }

  return {
    redactedMessages,
    redactionSummary: {
      totalDetections,
      totalRedactions,
      blocked,
      blockReasons,
      categoryCounts,
    },
  };
}
