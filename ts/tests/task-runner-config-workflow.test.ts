import { describe, expect, it } from "vitest";

import {
  buildEnqueueTaskConfig,
  parseTaskConfig,
  serializeTaskResult,
} from "../src/execution/task-runner-config.js";

describe("task runner config workflow", () => {
  it("parses queue config JSON into runtime config", () => {
    expect(
      parseTaskConfig(JSON.stringify({
        max_rounds: 4,
        quality_threshold: 0.85,
        min_rounds: 2,
        browser_url: "https://example.com",
        task_prompt: "Write a summary",
        rubric: "Be clear",
        delegated_results: [{
          score: 0.8,
          reasoning: "delegated",
          dimension_scores: { clarity: 0.8 },
        }],
        rlm_enabled: true,
        rlm_max_turns: 3,
      })),
    ).toMatchObject({
      maxRounds: 4,
      qualityThreshold: 0.85,
      minRounds: 2,
      browserUrl: "https://example.com",
      taskPrompt: "Write a summary",
      rubric: "Be clear",
      delegatedResults: [{
        score: 0.8,
        reasoning: "delegated",
        dimensionScores: { clarity: 0.8 },
      }],
      rlm: {
        enabled: true,
        maxTurns: 3,
      },
    });
  });

  it("serializes completed task results with optional RLM sessions", () => {
    const payload = JSON.parse(serializeTaskResult({
      rounds: [{
        roundNumber: 1,
        output: "draft",
        score: 0.7,
        reasoning: "good",
        dimensionScores: { quality: 0.7 },
        isRevision: false,
        judgeFailed: false,
      }],
      bestOutput: "best",
      bestScore: 0.9,
      bestRound: 1,
      totalRounds: 1,
      metThreshold: true,
      judgeFailures: 0,
      terminationReason: "threshold_met",
      dimensionTrajectory: {},
      totalInternalRetries: 0,
      durationMs: 12,
      judgeCalls: 1,
    }, [{ phase: "generate", content: "draft" } as never]));

    expect(payload.best_score).toBe(0.9);
    expect(payload.duration_ms).toBe(12);
    expect(payload.judge_calls).toBe(1);
    expect(payload.rlm_sessions).toEqual([{ phase: "generate", content: "draft" }]);
  });

  it("builds snake_case enqueue config fields only for provided values", () => {
    expect(buildEnqueueTaskConfig({
      taskPrompt: "Prompt",
      browserUrl: "https://example.com",
      minRounds: 3,
      rlmEnabled: true,
      rlmModel: "claude",
    })).toEqual({
      task_prompt: "Prompt",
      browser_url: "https://example.com",
      min_rounds: 3,
      rlm_enabled: true,
      rlm_model: "claude",
    });

    expect(buildEnqueueTaskConfig()).toBeUndefined();
  });
});
