import { describe, expect, it } from "vitest";

import {
  recordAttributedCredits,
  recordContributionDelta,
  summarizeContributionCredits,
} from "../src/analytics/credit-assignment-contribution-workflow.js";

describe("credit assignment contribution workflow", () => {
  it("records individual contribution deltas and summarizes them by component", () => {
    const contributions = new Map<string, number[]>();

    recordContributionDelta(contributions, "playbook", 0.1);
    recordContributionDelta(contributions, "playbook", 0.05);
    recordContributionDelta(contributions, "tools", 0.02);

    const credits = summarizeContributionCredits(contributions);
    expect(credits.playbook).toBeCloseTo(0.15, 6);
    expect(credits.tools).toBeCloseTo(0.02, 6);
  });

  it("records attributed credits in bulk", () => {
    const contributions = new Map<string, number[]>();

    recordAttributedCredits(contributions, {
      playbook: 0.2,
      tools: 0.1,
    });

    expect(contributions.get("playbook")).toEqual([0.2]);
    expect(summarizeContributionCredits(contributions)).toEqual({
      playbook: 0.2,
      tools: 0.1,
    });
  });
});
