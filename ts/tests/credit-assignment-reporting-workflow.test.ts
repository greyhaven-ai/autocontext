import { describe, expect, it } from "vitest";

import {
  AttributionResult,
  ComponentChange,
  CreditAssignmentRecord,
  GenerationChangeVector,
} from "../src/analytics/credit-assignment.js";
import {
  formatAttributionForAgent,
  summarizeCreditPatterns,
} from "../src/analytics/credit-assignment-reporting.js";

describe("credit assignment reporting workflow", () => {
  it("formats attribution using role-aware ordering and guidance", () => {
    const result = new AttributionResult(3, 0.3, {
      hints: 0.05,
      playbook: 0.15,
      analysis: 0.1,
    });

    const formatted = formatAttributionForAgent(result, "coach");
    expect(formatted).toContain("Previous Coaching Attribution");
    expect(formatted.indexOf("playbook")).toBeLessThan(formatted.indexOf("analysis"));
  });

  it("summarizes credit patterns across records and sorts by total credit", () => {
    const records = [
      new CreditAssignmentRecord(
        "run-1",
        1,
        new GenerationChangeVector(1, 0.3, [
          new ComponentChange("playbook", 0.6, "changed"),
          new ComponentChange("hints", 0.4, "changed"),
        ]),
        new AttributionResult(1, 0.3, { playbook: 0.2, hints: 0.1 }),
      ),
      new CreditAssignmentRecord(
        "run-2",
        2,
        new GenerationChangeVector(2, 0.2, [
          new ComponentChange("playbook", 0.5, "changed"),
        ]),
        new AttributionResult(2, 0.2, { playbook: 0.2 }),
      ),
    ];

    const summary = summarizeCreditPatterns(records);
    expect(summary.runIds).toEqual(["run-1", "run-2"]);
    expect(summary.components[0]).toMatchObject({
      component: "playbook",
      totalCredit: 0.4,
      generationCount: 2,
    });
  });
});
