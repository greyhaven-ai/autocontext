/**
 * Sensitive-data detection and redaction pipeline (AC-464).
 *
 * Detector pipeline for secrets, PII, and sensitive data in traces.
 * Policy engine determines action: block, warn, redact, or require-manual-review.
 *
 * This is the gate for public trace sharing — without it, trace
 * submission is unsafe.
 */

import { applyDetectionsWithPolicy } from "./redaction-application-workflow.js";
import { scanTextForSensitiveData } from "./redaction-detection-workflow.js";
import {
  buildDetectorPatterns,
  DEFAULT_REDACTION_POLICY,
} from "./redaction-patterns.js";
import { resolvePolicyOverlaps } from "./redaction-policy-workflow.js";
import type {
  CustomPattern,
  Detection,
  DetectionCategory,
  PatternDef,
  PolicyAction,
  Redaction,
  RedactionResult,
  ScanOptions,
} from "./redaction-types.js";

export type {
  CustomPattern,
  Detection,
  DetectionCategory,
  PolicyAction,
  Redaction,
  RedactionResult,
};

export class SensitiveDataDetector {
  private patterns: PatternDef[];

  constructor(opts?: { customPatterns?: CustomPattern[] }) {
    this.patterns = buildDetectorPatterns(opts?.customPatterns);
  }

  scan(text: string, opts?: ScanOptions): Detection[] {
    return scanTextForSensitiveData(text, this.patterns, opts);
  }
}

export class RedactionPolicy {
  private actions: Record<string, PolicyAction>;

  constructor(opts?: { overrides?: Record<string, PolicyAction> }) {
    this.actions = { ...DEFAULT_REDACTION_POLICY, ...(opts?.overrides ?? {}) };
  }

  actionFor(category: DetectionCategory): PolicyAction {
    return this.actions[category] ?? "warn";
  }
}

export function applyRedactionPolicy(
  text: string,
  opts?: { detector?: SensitiveDataDetector; policy?: RedactionPolicy },
): RedactionResult {
  const detector = opts?.detector ?? new SensitiveDataDetector();
  const policy = opts?.policy ?? new RedactionPolicy();
  const detections = resolvePolicyOverlaps(
    detector.scan(text, { dedup: false }),
    (category) => policy.actionFor(category),
  );
  return applyDetectionsWithPolicy(
    text,
    detections,
    (category) => policy.actionFor(category),
  );
}
