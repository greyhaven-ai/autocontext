import { describe, expect, it } from "vitest";

import {
  parseWatchIntervalSeconds,
  renderRunShow,
  renderRunStatus,
  resolveRunId,
} from "../src/cli/run-inspection-command-workflow.js";

const run = {
  run_id: "run-123",
  scenario: "grid_ctf",
  target_generations: 3,
  executor_mode: "local",
  status: "completed",
  agent_provider: "deterministic",
  created_at: "2026-04-30T00:00:00Z",
  updated_at: "2026-04-30T00:01:00Z",
};

const generations = [
  {
    generation_index: 1,
    mean_score: 0.2,
    best_score: 0.4,
    elo: 1200,
    gate_decision: "advance",
    status: "completed",
    duration_seconds: 1,
    created_at: "2026-04-30T00:00:10Z",
    updated_at: "2026-04-30T00:00:20Z",
  },
  {
    generation_index: 2,
    mean_score: 0.3,
    best_score: 0.9,
    elo: 1300,
    gate_decision: "advance",
    status: "completed",
    duration_seconds: 1,
    created_at: "2026-04-30T00:00:30Z",
    updated_at: "2026-04-30T00:00:40Z",
  },
];

describe("run inspection command workflow", () => {
  it("accepts run ids as either plain positionals or named options", () => {
    expect(resolveRunId({}, ["run-positional"], "show")).toBe("run-positional");
    expect(resolveRunId({ "run-id": "run-named" }, ["run-positional"], "show")).toBe("run-named");
  });

  it("renders concise run status with latest progress", () => {
    const text = renderRunStatus(run, generations, false);

    expect(text).toContain("Run run-123");
    expect(text).toContain("Generations: 2/3");
    expect(text).toContain("Latest best score: 0.900");
  });

  it("shows the best generation when requested", () => {
    const text = renderRunShow(run, generations, { best: true });

    expect(text).toContain("Generation: 2");
    expect(text).toContain("Best score: 0.900");
  });

  it("validates watch intervals", () => {
    expect(parseWatchIntervalSeconds("0.5")).toBe(0.5);
    expect(() => parseWatchIntervalSeconds("0")).toThrow("--interval");
  });
});
