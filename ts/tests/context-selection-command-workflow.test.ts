import { spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");

let dir: string;

function persistDecision(): void {
  const contextDir = join(dir, "runs", "run-cli", "context_selection");
  mkdirSync(contextDir, { recursive: true });
  writeFileSync(
    join(contextDir, "gen_1_generation_prompt_context.json"),
    JSON.stringify({
      schema_version: 1,
      run_id: "run-cli",
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
    }, null, 2),
    "utf-8",
  );
}

describe("context-selection CLI", () => {
  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "context-selection-cli-"));
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("renders JSON telemetry from persisted run artifacts", () => {
    persistDecision();

    const result = spawnSync(
      "npx",
      ["tsx", CLI, "context-selection", "--run-id", "run-cli", "--json"],
      {
        cwd: dir,
        encoding: "utf-8",
        timeout: 15000,
        env: { ...process.env, NODE_NO_WARNINGS: "1" },
      },
    );

    expect(result.status).toBe(0);
    const parsed = JSON.parse(result.stdout);
    expect(parsed).toMatchObject({
      status: "completed",
      run_id: "run-cli",
      summary: expect.objectContaining({
        budget_token_reduction: 100,
      }),
      telemetry_cards: expect.arrayContaining([
        expect.objectContaining({ key: "context_budget", severity: "warning" }),
      ]),
    });
  }, 15000);

  it("fails clearly when no persisted context-selection artifacts exist", () => {
    const result = spawnSync(
      "npx",
      ["tsx", CLI, "context-selection", "--run-id", "missing-run", "--json"],
      {
        cwd: dir,
        encoding: "utf-8",
        timeout: 15000,
        env: { ...process.env, NODE_NO_WARNINGS: "1" },
      },
    );

    expect(result.status).toBe(1);
    const parsed = JSON.parse(result.stdout);
    expect(parsed).toMatchObject({
      status: "failed",
      run_id: "missing-run",
      error: expect.stringContaining("No context selection artifacts"),
    });
  }, 15000);
});
