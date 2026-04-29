import { describe, expect, it } from "vitest";

import {
  compactPromptComponents,
  compactPromptComponentsWithEntries,
  extractPromotableLines,
} from "../src/knowledge/semantic-compaction.js";
import { buildPromptBundle } from "../src/prompts/templates.js";

describe("semantic prompt compaction", () => {
  it("keeps recent experiment history and drops repetitive filler", () => {
    const original =
      "## RLM Experiment Log\n\n"
      + "### Generation 1\n"
      + "noise line\n".repeat(120)
      + "\n### Generation 7\n"
      + "- Root cause: overfitting to stale hints\n"
      + "- Keep broader opening exploration\n";

    const compacted = compactPromptComponents({ experiment_log: original });

    expect(compacted.experiment_log).toContain("Generation 7");
    expect(compacted.experiment_log).toContain("overfitting to stale hints");
    expect(compacted.experiment_log.length).toBeLessThan(original.length);
  });

  it("extracts high-signal session report lines for standalone TypeScript runs", () => {
    const original =
      "# Session Report: run_old\n"
      + "Long narrative that meanders without much signal.\n"
      + "filler paragraph\n".repeat(80)
      + "\n## Findings\n"
      + "- Preserve the rollback guard after failed harness mutations.\n"
      + "- Prefer notebook freshness filtering before prompt injection.\n";

    const compacted = compactPromptComponents({ session_reports: original });

    expect(compacted.session_reports).toContain("rollback guard");
    expect(compacted.session_reports).toContain("freshness filtering");
    expect(compacted.session_reports.length).toBeLessThan(original.length);
  });

  it("emits Pi-shaped ledger entries for changed components", () => {
    const result = compactPromptComponentsWithEntries(
      {
        experiment_log:
          "## RLM Experiment Log\n\n"
          + "### Generation 1\n"
          + "noise line\n".repeat(120)
          + "\n### Generation 9\n"
          + "- Root cause: stale hints amplified retries.\n",
      },
      {
        context: { run_id: "run-1", scenario: "grid_ctf", generation: 3 },
        parentId: "prev1234",
        idFactory: () => "abcd1234",
        timestampFactory: () => "2026-04-29T17:30:00Z",
      },
    );

    expect(result.components.experiment_log).not.toBe("");
    expect(result.entries).toHaveLength(1);
    const entry = result.entries[0];
    expect(entry).toEqual({
      type: "compaction",
      id: "abcd1234",
      parentId: "prev1234",
      timestamp: "2026-04-29T17:30:00Z",
      summary: entry.summary,
      firstKeptEntryId: "component:experiment_log:kept",
      tokensBefore: entry.tokensBefore,
      details: {
        component: "experiment_log",
        source: "prompt_components",
        tokensAfter: entry.details?.tokensAfter,
        contentLengthBefore: entry.details?.contentLengthBefore,
        contentLengthAfter: entry.details?.contentLengthAfter,
        run_id: "run-1",
        scenario: "grid_ctf",
        generation: 3,
      },
    });
    expect(entry.tokensBefore).toBeGreaterThan(Number(entry.details?.tokensAfter));
    expect(entry.summary).toContain("## Critical Context");
    expect(entry.summary).toContain("stale hints amplified retries");
  });

  it("extracts promotable lines from report-like markdown", () => {
    const lines = extractPromotableLines(
      "# Session Report\n\n"
      + "## Findings\n"
      + "- Root cause: stale score snapshots.\n"
      + "- Recommendation: refresh trajectory before prompt injection.\n",
    );

    expect(lines).toEqual([
      "Root cause: stale score snapshots.",
      "Recommendation: refresh trajectory before prompt injection.",
    ]);
  });

  it("compacts public prompt bundles before callers apply their own budgets", () => {
    const bundle = buildPromptBundle({
      scenarioRules: "Follow the rules.",
      strategyInterface: "Return JSON.",
      evaluationCriteria: "Maximize score.",
      playbook: "",
      trajectory: "",
      lessons: "## Lessons\n" + [
        ...Array.from({ length: 119 }, (_, index) => `- old lesson ${index + 1} ${"x".repeat(120)}`),
        "- newest lesson keep me",
      ].join("\n"),
      tools: "",
      hints: "",
      analysis: "",
    });

    expect(bundle.competitor).toContain("newest lesson keep me");
    expect(bundle.competitor).toContain("old lesson 117");
    expect(bundle.competitor).not.toContain("old lesson 1 ");
    expect(bundle.competitor).toContain("condensed structured context");
  });
});
