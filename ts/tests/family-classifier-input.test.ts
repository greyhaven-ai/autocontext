import { describe, expect, it } from "vitest";

import { buildFamilyClassificationBrief } from "../src/scenarios/family-classifier-input.js";

describe("family classification input", () => {
  it("normalizes solve/new-scenario briefs before family classification", () => {
    const brief = buildFamilyClassificationBrief(
      [
        "## Scenario Proposal",
        "",
        "**Priority:** Week 4",
        "**Generations to signal:** 20-40",
        "",
        "### Description",
        "",
        "Adapt under a known scoring exploit (e.g., keyword stuffing that rewards length).",
        "",
        "## Implementation Guidance",
        "",
        "Use SimulationInterface + WorldState even if the user did not ask for simulation.",
        "",
        "## Success Criteria",
        "",
        "Avoid gaming the metric.",
      ].join("\n"),
    );

    expect(brief).toContain("## Scenario Proposal");
    expect(brief).toContain("### Description");
    expect(brief).toContain("Avoid gaming the metric.");
    expect(brief).not.toContain("**Priority:**");
    expect(brief).not.toContain("**Generations to signal:**");
    expect(brief).not.toContain("Implementation Guidance");
    expect(brief).not.toContain("SimulationInterface");
    expect(brief).not.toContain("e.g.");
  });
});
