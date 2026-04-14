import { describe, expect, it } from "vitest";

import { buildAttributedCredits } from "../src/analytics/credit-assignment-attribution-workflow.js";

describe("credit assignment attribution workflow", () => {
  it("returns zero credits for non-positive or zero-magnitude change vectors", () => {
    expect(
      buildAttributedCredits({
        scoreDelta: 0,
        totalChangeMagnitude: 1,
        changes: [{ component: "playbook", magnitude: 1 }],
      }),
    ).toEqual({ playbook: 0 });

    expect(
      buildAttributedCredits({
        scoreDelta: 0.4,
        totalChangeMagnitude: 0,
        changes: [{ component: "playbook", magnitude: 0 }],
      }),
    ).toEqual({ playbook: 0 });
  });

  it("distributes score delta proportionally with stable rounding", () => {
    expect(
      buildAttributedCredits({
        scoreDelta: 0.3,
        totalChangeMagnitude: 1,
        changes: [
          { component: "playbook", magnitude: 0.6 },
          { component: "tools", magnitude: 0.4 },
        ],
      }),
    ).toEqual({
      playbook: 0.18,
      tools: 0.12,
    });
  });
});
