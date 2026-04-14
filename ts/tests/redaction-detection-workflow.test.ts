import { describe, expect, it } from "vitest";

import { scanTextForSensitiveData } from "../src/traces/redaction-detection-workflow.js";
import { buildDetectorPatterns } from "../src/traces/redaction-patterns.js";
import type { Detection } from "../src/traces/redaction-types.js";

describe("redaction detection workflow", () => {
  it("normalizes non-global custom patterns and finds multiple matches", () => {
    const patterns = buildDetectorPatterns([
      { pattern: /PROJ-\d{4,}/, category: "internal_id", label: "Project ID" },
    ]);
    const detections = scanTextForSensitiveData("PROJ-12345 and PROJ-67890", patterns, { dedup: false });

    expect(detections.filter((detection) => detection.category === "internal_id")).toHaveLength(2);
  });

  it("deduplicates overlapping detections by confidence then width", () => {
    const detections = scanTextForSensitiveData(
      "token sk-ant-api03-abc123def456ghi789",
      [
        { pattern: /sk-ant-[a-zA-Z0-9_-]{10,}/g, category: "api_key", label: "API key", confidence: 0.95 },
        { pattern: /api03-abc123def456ghi789/g, category: "credential", label: "Overlap", confidence: 0.5 },
      ],
    );

    expect(detections).toHaveLength(1);
    expect(detections[0]).toMatchObject({ category: "api_key", label: "API key" });
  });

  it("keeps overlapping raw detections when dedup is disabled", () => {
    const raw = scanTextForSensitiveData(
      "API_KEY=sk-ant-api03-abc123def456ghi789",
      [
        { pattern: /API_KEY=[^\s]+/g, category: "credential", label: "Assignment", confidence: 0.8 },
        { pattern: /sk-ant-[a-zA-Z0-9_-]{10,}/g, category: "api_key", label: "API key", confidence: 0.95 },
      ],
      { dedup: false },
    );

    expect(raw).toHaveLength(2);
  });
});
