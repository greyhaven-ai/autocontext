import { mkdirSync, symlinkSync, writeFileSync } from "node:fs";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { loadContextSelectionDecisions } from "../src/knowledge/context-selection-store.js";

let dir: string;

function decisionPayload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema_version: 1,
    run_id: "run-1",
    scenario_name: "grid_ctf",
    generation: 1,
    stage: "generation_prompt_context",
    created_at: "2026-01-02T03:04:05.000Z",
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
    metrics: {
      candidate_count: 1,
      selected_count: 1,
      candidate_token_estimate: 100,
      selected_token_estimate: 20,
    },
    candidates: [{
      artifact_id: "playbook",
      artifact_type: "prompt_component",
      source: "prompt_assembly",
      candidate_token_estimate: 100,
      selected_token_estimate: 20,
      selected: true,
      selection_reason: "retained_after_prompt_assembly",
      candidate_content_hash: "candidate",
      selected_content_hash: "selected",
    }],
    ...overrides,
  };
}

function writeDecision(name: string, payload: Record<string, unknown>): void {
  const contextDir = join(dir, "runs", "run-1", "context_selection");
  mkdirSync(contextDir, { recursive: true });
  writeFileSync(join(contextDir, name), JSON.stringify(payload, null, 2), "utf-8");
}

function writeDecisionUnder(root: string, name: string, payload: Record<string, unknown>): void {
  const contextDir = join(root, "context_selection");
  mkdirSync(contextDir, { recursive: true });
  writeFileSync(join(contextDir, name), JSON.stringify(payload, null, 2), "utf-8");
}

describe("context selection store", () => {
  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "context-selection-store-"));
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("loads only validated persisted decision files for one run", () => {
    writeDecision("gen_1_generation_prompt_context.json", decisionPayload());
    writeDecision("summary.json", decisionPayload({ generation: 99 }));
    writeDecision("gen_2_generation_prompt_context.json", decisionPayload({
      generation: 2,
      schema_version: 999,
    }));

    const decisions = loadContextSelectionDecisions(join(dir, "runs"), "run-1");

    expect(decisions).toHaveLength(1);
    expect(decisions[0]).toMatchObject({
      run_id: "run-1",
      scenario_name: "grid_ctf",
      generation: 1,
      stage: "generation_prompt_context",
    });
  });

  it("rejects run ids that escape the runs root", () => {
    expect(() => loadContextSelectionDecisions(join(dir, "runs"), "../outside")).toThrow(
      /escapes runs root/,
    );
  });

  it("rejects run directory symlinks that escape the runs root", () => {
    const runsRoot = join(dir, "runs");
    const outsideRun = join(dir, "outside-run");
    mkdirSync(runsRoot, { recursive: true });
    writeDecisionUnder(outsideRun, "gen_1_generation_prompt_context.json", decisionPayload({
      run_id: "link",
    }));
    symlinkSync(outsideRun, join(runsRoot, "link"), "dir");

    expect(() => loadContextSelectionDecisions(runsRoot, "link")).toThrow(
      /escapes runs root/,
    );
  });

  it("rejects context-selection directory symlinks that escape the run root", () => {
    const runsRoot = join(dir, "runs");
    const runRoot = join(runsRoot, "run-1");
    const outsideContextRoot = join(dir, "outside-context");
    mkdirSync(runRoot, { recursive: true });
    writeDecisionUnder(outsideContextRoot, "gen_1_generation_prompt_context.json", decisionPayload());
    symlinkSync(join(outsideContextRoot, "context_selection"), join(runRoot, "context_selection"), "dir");

    expect(() => loadContextSelectionDecisions(runsRoot, "run-1")).toThrow(
      /escapes run root/,
    );
  });

  it("rejects decision file symlinks that escape the context-selection directory", () => {
    const runsRoot = join(dir, "runs");
    const contextDir = join(runsRoot, "run-1", "context_selection");
    const outsideFile = join(dir, "outside-decision.json");
    mkdirSync(contextDir, { recursive: true });
    writeFileSync(
      outsideFile,
      JSON.stringify(decisionPayload(), null, 2),
      "utf-8",
    );
    symlinkSync(outsideFile, join(contextDir, "gen_1_generation_prompt_context.json"));

    expect(() => loadContextSelectionDecisions(runsRoot, "run-1")).toThrow(
      /escapes context-selection directory/,
    );
  });
});
