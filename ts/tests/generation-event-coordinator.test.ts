import { describe, expect, it } from "vitest";

import {
  buildAgentsStartedPayload,
  buildGateDecidedPayload,
  buildGenerationCompletedPayload,
  buildGenerationStartedPayload,
  buildRunCompletedPayload,
  buildRunFailedPayload,
  buildRunStartedPayload,
  buildTournamentCompletedPayload,
} from "../src/loop/generation-event-coordinator.js";

describe("generation event coordinator", () => {
  it("builds run and generation start payloads", () => {
    expect(
      buildRunStartedPayload({
        runId: "run-1",
        scenarioName: "grid_ctf",
        targetGenerations: 3,
      }),
    ).toEqual({
      run_id: "run-1",
      scenario: "grid_ctf",
      target_generations: 3,
    });

    expect(buildGenerationStartedPayload("run-1", 2)).toEqual({
      run_id: "run-1",
      generation: 2,
    });
  });

  it("builds agent and tournament payloads", () => {
    expect(buildAgentsStartedPayload("run-1", 2, true)).toEqual({
      run_id: "run-1",
      generation: 2,
      roles: ["competitor", "analyst", "coach", "curator"],
    });

    expect(
      buildTournamentCompletedPayload("run-1", 2, {
        meanScore: 0.55,
        bestScore: 0.7,
        wins: 3,
        losses: 1,
      }),
    ).toEqual({
      run_id: "run-1",
      generation: 2,
      mean_score: 0.55,
      best_score: 0.7,
      wins: 3,
      losses: 1,
    });
  });

  it("builds gate, generation, and run completion payloads", () => {
    expect(buildGateDecidedPayload("run-1", 2, "retry", 0.01, 0.005)).toEqual({
      run_id: "run-1",
      generation: 2,
      decision: "retry",
      delta: 0.01,
      threshold: 0.005,
    });

    expect(
      buildGenerationCompletedPayload("run-1", 2, {
        meanScore: 0.5,
        bestScore: 0.8,
        elo: 1012,
        gateDecision: "advance",
      }),
    ).toEqual({
      run_id: "run-1",
      generation: 2,
      mean_score: 0.5,
      best_score: 0.8,
      elo: 1012,
      gate_decision: "advance",
    });

    expect(
      buildRunCompletedPayload({
        runId: "run-1",
        completedGenerations: 3,
        bestScore: 0.8,
        currentElo: 1012,
        sessionReportPath: "/tmp/report.md",
        deadEndsFound: 1,
      }),
    ).toEqual({
      run_id: "run-1",
      completed_generations: 3,
      best_score: 0.8,
      elo: 1012,
      session_report_path: "/tmp/report.md",
      dead_ends_found: 1,
    });
  });

  it("builds failure payloads", () => {
    expect(buildRunFailedPayload("run-1", "boom")).toEqual({
      run_id: "run-1",
      error: "boom",
    });
  });
});
