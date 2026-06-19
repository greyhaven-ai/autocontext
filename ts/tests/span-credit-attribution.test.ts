import { describe, expect, it } from "vitest";

import {
  AttributionResult,
  ComponentChange,
  GenerationChangeVector,
  attributeCredit,
  buildSpanAttribution,
  extractKnowledgeSpans,
  formatAttributionForAgent,
  rankSpansByCredit,
} from "../src/analytics/credit-assignment.js";

describe("span credit attribution", () => {
  it("keeps span ids stable for same source and text", () => {
    const left = extractKnowledgeSpans("hints", "- Check invariant\n- Try exact route");
    const right = extractKnowledgeSpans("hints", "  Check invariant\n\nTry exact route");

    expect(left.map((span) => span.spanId)).toEqual(right.map((span) => span.spanId));
    expect(left[0].metadata.source).toBe("hints");
    expect(left[0].metadata.lineNumber).toBe(1);
  });

  it("records correlative span credit", () => {
    const vector = new GenerationChangeVector(3, 0.3, [
      new ComponentChange("hints", 1, "Hints changed"),
    ]);
    const report = buildSpanAttribution(vector, attributeCredit(vector), {
      hints: "- Check invariant\n- Verify repair",
    });

    expect(report.schemaVersion).toBe(1);
    expect(report.mode).toBe("span");
    expect(report.spans[0].credit).toBe(0.15);
    expect(report.spans[0].evidenceLevel).toBe("component_correlated");
  });

  it("ranks and demotes low-credit spans without deleting them", () => {
    const spans = extractKnowledgeSpans("playbook", "keep me\ndemote me");
    const ranked = rankSpansByCredit(spans, { [spans[0].spanId]: 0.2, [spans[1].spanId]: -0.1 });

    expect(ranked.map((row) => row.text)).toEqual(["keep me", "demote me"]);
    expect(ranked[1].demoted).toBe(true);
  });

  it("formats span context for prompts when present", () => {
    const result = new AttributionResult(4, 0.2, { hints: 0.2 }, {
      contextAttribution: "span",
      spanAttribution: {
        schemaVersion: 1,
        mode: "span",
        spans: [
          {
            spanId: "hints:abc",
            source: "hints",
            text: "Check invariant",
            credit: 0.2,
            evidenceLevel: "component_correlated",
            metadata: { lineNumber: 1 },
          },
        ],
      },
    });

    const formatted = formatAttributionForAgent(result, "coach");

    expect(formatted).toContain("Span attribution (component-correlated, noisy)");
    expect(formatted).toContain("Check invariant");
  });
});
