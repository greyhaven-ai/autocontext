import { describe, expect, it } from "vitest";

import {
  buildHintAbReport,
  buildHintMetadata,
  effectiveHintStyle,
} from "../src/knowledge/index.js";
import { buildSupportPrompt, buildCuratorPrompt } from "../src/loop/generation-prompts.js";

describe("soft structural hints", () => {
  it("keeps structural hint prompting opt-in", () => {
    const base = buildSupportPrompt({
      role: "coach",
      scenarioName: "grid_ctf",
      scenarioRules: "rules",
      strategyInterface: "{}",
      strategyJson: {},
      analysisSummary: "summary",
      playbook: "playbook",
    });
    const structural = buildSupportPrompt({
      role: "coach",
      scenarioName: "grid_ctf",
      scenarioRules: "rules",
      strategyInterface: "{}",
      strategyJson: {},
      analysisSummary: "summary",
      playbook: "playbook",
      hintStyle: "structural",
    });

    expect(base).not.toContain("avoid full target solutions");
    expect(structural).toContain("prefer constraints, invariants, verification checks");
    expect(
      buildCuratorPrompt({
        tournamentSummary: "s",
        currentPlaybook: "c",
        proposedPlaybook: "p",
        hintStyle: "structural",
      }),
    ).toContain("route-locking");
  });

  it("derives effective style from the soft-hints toggle", () => {
    expect(effectiveHintStyle(false, "default")).toBe("default");
    expect(effectiveHintStyle(true, "default")).toBe("structural");
    expect(effectiveHintStyle(false, "structural")).toBe("structural");
  });

  it("builds hint metadata and A/B report metrics", () => {
    expect(buildHintMetadata("Check the invariant", { hintStyle: "structural" })).toMatchObject({
      hintStyle: "structural",
      isStructural: true,
      routePrescriptive: false,
    });

    const report = buildHintAbReport([
      {
        hintStyle: "default",
        score: 0.2,
        responseLength: 100,
        novelty: 0.1,
        rolledBack: true,
        hintAdopted: false,
      },
      {
        hintStyle: "structural",
        score: 0.4,
        responseLength: 80,
        novelty: 0.3,
        rolledBack: false,
        hintAdopted: true,
      },
    ]);
    expect(report.styles.structural.meanScore).toBe(0.4);
    expect(report.styles.structural.meanResponseLength).toBe(80);
    expect(report.styles.structural.meanNovelty).toBe(0.3);
    expect(report.styles.structural.rollbackRate).toBe(0);
    expect(report.styles.structural.hintAdoptionRate).toBe(1);
  });
});
