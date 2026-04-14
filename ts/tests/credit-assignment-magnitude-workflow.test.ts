import { describe, expect, it } from "vitest";

import {
  buildComponentChangeMagnitudes,
  listChangeMagnitude,
  textChangeMagnitude,
} from "../src/analytics/credit-assignment-magnitude.js";

describe("credit assignment magnitude workflow", () => {
  it("computes text and list change magnitudes with stable rounding", () => {
    expect(textChangeMagnitude("abc", "abc")).toBe(0);
    expect(textChangeMagnitude("", "new")).toBe(1);
    expect(listChangeMagnitude(["grep"], ["grep", "rg"])).toBe(0.5);
  });

  it("builds component change magnitudes for changed strategy surfaces", () => {
    const changes = buildComponentChangeMagnitudes(
      {
        playbook: "old plan",
        tools: ["grep"],
        hints: "keep it simple",
        analysis: "weak hypothesis",
      },
      {
        playbook: "new plan with branches",
        tools: ["grep", "rg"],
        hints: "focus on invariants",
        analysis: "stronger hypothesis with evidence",
      },
    );

    expect(changes.map((change) => change.component)).toEqual([
      "playbook",
      "tools",
      "hints",
      "analysis",
    ]);
    expect(changes.find((change) => change.component === "tools")?.description).toContain("+1/-0 tools");
  });
});
