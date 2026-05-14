import { describe, expect, it } from "vitest";

import {
  buildContextSelectionReport,
  type ContextSelectionDecisionInput,
} from "../src/knowledge/context-selection-report.js";

function decision(overrides: Partial<ContextSelectionDecisionInput> = {}): ContextSelectionDecisionInput {
  return {
    run_id: "run-1",
    scenario_name: "grid_ctf",
    generation: 4,
    stage: "generation_prompt_context",
    created_at: "2026-01-02T03:04:05+00:00",
    candidates: [
      {
        artifact_id: "playbook",
        artifact_type: "prompt_component",
        source: "prompt_assembly",
        candidate_token_estimate: 100,
        selected_token_estimate: 20,
        selected: true,
        selection_reason: "trimmed",
        candidate_content_hash: "candidate",
        selected_content_hash: "selected",
      },
    ],
    metadata: {
      context_budget_telemetry: {
        input_token_estimate: 120,
        output_token_estimate: 20,
        dedupe_hit_count: 1,
        component_cap_hit_count: 2,
        trimmed_component_count: 1,
      },
      prompt_compaction_cache: {
        hits: 0,
        misses: 10,
        lookups: 10,
      },
    },
    ...overrides,
  };
}

describe("context selection report", () => {
  it("builds Python-parity budget/cache telemetry cards and markdown", () => {
    const report = buildContextSelectionReport([decision()]);
    const payload = report.toDict();
    const cards = Object.fromEntries(payload.telemetry_cards.map((card) => [card.key, card]));
    const markdown = report.toMarkdown();

    expect(payload.summary.budget_token_reduction).toBe(100);
    expect(cards.context_budget.severity).toBe("warning");
    expect(cards.context_budget.value).toBe("100 est. tokens reduced");
    expect(cards.context_budget.detail).toContain("1 trims");
    expect(cards.semantic_compaction_cache.severity).toBe("warning");
    expect(cards.semantic_compaction_cache.value).toBe("0.0% hit rate");
    expect(cards.diagnostics.severity).toBe("warning");
    expect(markdown).toContain("## Context Budget");
    expect(markdown).toContain("- Token reduction: 100");
    expect(markdown).toContain("## Semantic Compaction Cache");
    expect(markdown).toContain("- Hit rate: 0.0%");
  });

  it("rejects mixed run reports like the Python report builder", () => {
    expect(() =>
      buildContextSelectionReport([
        decision(),
        decision({ run_id: "run-2" }),
      ]),
    ).toThrow(/single run_id/);
  });
});
