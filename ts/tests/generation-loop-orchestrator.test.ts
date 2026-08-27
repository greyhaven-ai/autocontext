import { describe, expect, it } from "vitest";

import {
  completeGenerationLoopRun,
  createGenerationLoopOrchestration,
  failGenerationLoopRun,
  finalizeGenerationCycle,
  getActiveGenerationPhase,
  recordAdvancedGenerationResult,
  startNextGeneration,
} from "../src/loop/generation-loop-orchestrator.js";
import {
  applyGenerationPhaseDecision,
  markAwaitingCompetitorResult,
  markAwaitingTournamentResult,
  type GenerationAttempt,
} from "../src/loop/generation-phase-state.js";

function makeAttempt(
  gateDecision: GenerationAttempt["gateDecision"],
  bestScore: number,
): GenerationAttempt {
  return {
    competitorPrompt: "prompt",
    competitorResultText: '{"aggression":0.5}',
    strategy: { aggression: 0.5 },
    tournamentResult: {
      matches: [],
      meanScore: bestScore,
      bestScore,
      wins: 1,
      losses: 0,
      elo: 1000 + bestScore * 10,
    },
    gateDecision,
  };
}

describe("generation loop orchestrator", () => {
  it("starts a run with run-start payload and empty cycle progress", () => {
    const orchestration = createGenerationLoopOrchestration({
      runId: "run-1",
      scenarioName: "grid_ctf",
      targetGenerations: 3,
      startedAtMs: 100,
    });

    expect(orchestration.runState.status).toBe("running");
    expect(orchestration.cycleState.completedGenerations).toBe(0);
    expect(orchestration.events.runStarted).toEqual({
      run_id: "run-1",
      scenario: "grid_ctf",
      target_generations: 3,
    });
  });

  it("starts a generation and emits generation boundary events", () => {
    const orchestration = startNextGeneration(
      createGenerationLoopOrchestration({
        runId: "run-1",
        scenarioName: "grid_ctf",
        targetGenerations: 3,
        startedAtMs: 100,
      }),
      true,
    );

    expect(getActiveGenerationPhase(orchestration).generation).toBe(1);
    expect(orchestration.events.generationStarted).toEqual({
      run_id: "run-1",
      generation: 1,
    });
    expect(orchestration.events.agentsStarted).toEqual({
      run_id: "run-1",
      generation: 1,
      roles: ["competitor", "analyst", "coach", "curator"],
    });
  });

  it("records an advanced generation and finalizes the cycle", () => {
    const started = startNextGeneration(
      createGenerationLoopOrchestration({
        runId: "run-1",
        scenarioName: "grid_ctf",
        targetGenerations: 2,
        startedAtMs: 100,
      }),
      false,
    );
    const advanced = recordAdvancedGenerationResult(started, {
      generation: 1,
      bestScore: 0.7,
      elo: 1010,
    });
    const phaseState = applyGenerationPhaseDecision(
      markAwaitingTournamentResult(
        markAwaitingCompetitorResult(getActiveGenerationPhase(advanced)),
      ),
      makeAttempt("advance", 0.7),
    );
    const completed = finalizeGenerationCycle(advanced, phaseState, {
      runId: "run-1",
      generation: 1,
      meanScore: 0.6,
      bestScore: 0.7,
      elo: 1010,
      gateDecision: "advance",
    });

    expect(completed.runState.bestScore).toBe(0.7);
    expect(completed.cycleState.completedGenerations).toBe(1);
    expect(completed.events.generationCompleted).toEqual({
      run_id: "run-1",
      generation: 1,
      mean_score: 0.6,
      best_score: 0.7,
      elo: 1010,
      gate_decision: "advance",
    });
  });

  it("completes and fails runs with stable payloads", () => {
    const completed = completeGenerationLoopRun(
      createGenerationLoopOrchestration({
        runId: "run-1",
        scenarioName: "grid_ctf",
        targetGenerations: 1,
        startedAtMs: 100,
      }),
      {
        finishedAtMs: 150,
        sessionReportPath: "/tmp/report.md",
        deadEndsFound: 2,
      },
    );
    const failed = failGenerationLoopRun(
      createGenerationLoopOrchestration({
        runId: "run-2",
        scenarioName: "grid_ctf",
        targetGenerations: 1,
        startedAtMs: 100,
      }),
      {
        finishedAtMs: 180,
        error: "boom",
      },
    );

    expect(completed.runState.status).toBe("completed");
    expect(completed.events.runCompleted).toEqual({
      run_id: "run-1",
      completed_generations: 0,
      best_score: 0,
      elo: 1000,
      session_report_path: "/tmp/report.md",
      dead_ends_found: 2,
    });
    expect(failed.runState.status).toBe("failed");
    expect(failed.events.runFailed).toEqual({
      run_id: "run-2",
      error: "boom",
    });
  });
});
