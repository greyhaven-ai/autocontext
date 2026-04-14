import { describe, expect, it } from "vitest";

import { applyDetectionsWithPolicy } from "../src/traces/redaction-application-workflow.js";
import { actionPriority, resolvePolicyOverlaps } from "../src/traces/redaction-policy-workflow.js";
import type { Detection, PolicyAction } from "../src/traces/redaction-types.js";

const API_KEY_DETECTION: Detection = {
  category: "api_key",
  matched: "sk-ant-api03-abc123def456ghi789",
  label: "API key",
  start: 8,
  end: 39,
  confidence: 0.95,
};

const CREDENTIAL_DETECTION: Detection = {
  category: "credential",
  matched: "API_KEY=sk-ant-api03-abc123def456ghi789",
  label: "Credential assignment",
  start: 0,
  end: 39,
  confidence: 0.8,
};

const resolveAction = (category: string): PolicyAction => {
  if (category === "api_key") return "block";
  if (category === "credential") return "warn";
  if (category === "internal_url") return "require-manual-approval";
  return "redact";
};

describe("redaction policy workflow", () => {
  it("orders actions by severity", () => {
    expect(actionPriority("block")).toBeGreaterThan(actionPriority("require-manual-approval"));
    expect(actionPriority("require-manual-approval")).toBeGreaterThan(actionPriority("redact"));
    expect(actionPriority("redact")).toBeGreaterThan(actionPriority("warn"));
  });

  it("preserves the strongest overlap for policy decisions", () => {
    const resolved = resolvePolicyOverlaps([
      CREDENTIAL_DETECTION,
      API_KEY_DETECTION,
    ], resolveAction);

    expect(resolved).toEqual([API_KEY_DETECTION]);
  });

  it("builds blocked, manual-review, and redacted results from detections", () => {
    const result = applyDetectionsWithPolicy(
      "token sk-ant-api03-abc123def456ghi789 and https://internal.example/api",
      [
        API_KEY_DETECTION,
        {
          category: "internal_url",
          matched: "https://internal.example/api",
          label: "Internal URL",
          start: 44,
          end: 72,
          confidence: 0.85,
        },
      ],
      resolveAction,
    );

    expect(result.blocked).toBe(true);
    expect(result.blockReasons[0]).toContain("API key");
    expect(result.requiresManualReview).toBe(true);
    expect(result.redactions).toEqual([]);
  });
});
