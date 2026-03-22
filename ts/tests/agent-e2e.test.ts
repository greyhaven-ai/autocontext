/**
 * Agent Self-Improvement E2E tests.
 *
 * Exercises the full AutoContext pipeline: task creation → judge evaluation →
 * improvement loop → task queue → skill export, all with mock providers.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import type {
  LLMProvider,
  CompletionResult,
  AgentTaskInterface,
  AgentTaskResult,
} from "../src/types/index.js";
import { ImprovementLoop } from "../src/execution/improvement-loop.js";
import {
  TaskRunner,
  SimpleAgentTask,
  enqueueTask,
} from "../src/execution/task-runner.js";
import { JudgeExecutor } from "../src/execution/judge-executor.js";
import { LLMJudge } from "../src/judge/index.js";
import { parseJudgeResponse } from "../src/judge/parse.js";
import { SQLiteStore } from "../src/storage/index.js";
import { createAgentTask } from "../src/scenarios/agent-task-factory.js";
import type { AgentTaskSpec } from "../src/scenarios/agent-task-spec.js";
import {
  SkillPackage,
  exportAgentTaskSkill,
} from "../src/knowledge/skill-package.js";
import { DirectAPIRuntime } from "../src/runtimes/direct-api.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const MIGRATIONS_DIR = join(import.meta.dirname ?? ".", "..", "migrations");

function makeJudgeResponse(score: number, dims?: Record<string, number>): string {
  return (
    `Evaluation:\n<!-- JUDGE_RESULT_START -->\n` +
    JSON.stringify({
      score,
      reasoning: `test reasoning (score=${score})`,
      dimensions: dims ?? { accuracy: score },
    }) +
    `\n<!-- JUDGE_RESULT_END -->`
  );
}

/**
 * Build a mock LLMProvider.
 *
 * - `judgeScores`: successive scores returned for judge calls (cycles).
 * - `generationText`: text returned for non-judge completions.
 */
function makeMockProvider(opts?: {
  judgeScores?: number[];
  generationText?: string;
}): LLMProvider & { callCount: number } {
  const scores = opts?.judgeScores ?? [0.7];
  const genText = opts?.generationText ?? "Generated output.";
  let idx = 0;

  const provider: LLMProvider & { callCount: number } = {
    name: "mock",
    callCount: 0,
    defaultModel: () => "mock-model",
    complete: async (args): Promise<CompletionResult> => {
      provider.callCount++;
      const isJudge =
        args.systemPrompt.includes("expert judge") ||
        args.userPrompt.includes("JUDGE_RESULT");

      if (isJudge) {
        const score = scores[Math.min(idx, scores.length - 1)];
        idx++;
        return {
          text: makeJudgeResponse(score),
          model: "mock-model",
          usage: {},
        };
      }
      // Generation / revision call
      return {
        text: `${genText} [call ${provider.callCount}]`,
        model: "mock-model",
        usage: {},
      };
    },
  };
  return provider;
}

function createTempStore(): { store: SQLiteStore; tmpDir: string } {
  const tmpDir = mkdtempSync(join(tmpdir(), "autocontext-e2e-"));
  const store = new SQLiteStore(join(tmpDir, "test.db"));
  store.migrate(MIGRATIONS_DIR);
  return { store, tmpDir };
}

// ---------------------------------------------------------------------------
// Agent Self-Improvement E2E
// ---------------------------------------------------------------------------

describe("Agent Self-Improvement E2E", () => {
  let tmpDir: string;
  let store: SQLiteStore;

  beforeEach(() => {
    const s = createTempStore();
    tmpDir = s.tmpDir;
    store = s.store;
  });

  afterEach(() => {
    store.close();
    rmSync(tmpDir, { recursive: true, force: true });
  });

  it("creates task, evaluates output, and scores it", async () => {
    const provider = makeMockProvider({ judgeScores: [0.72] });

    const spec: AgentTaskSpec = {
      taskPrompt: "Write a short essay about AI safety.",
      judgeRubric: "Evaluate clarity, accuracy, and depth on a 0–1 scale.",
      outputFormat: "free_text",
      judgeModel: "mock-model",
      maxRounds: 1,
      qualityThreshold: 0.9,
    };

    const task = createAgentTask({ spec, name: "ai_safety_essay", provider });
    const state = task.initialState();
    expect(state.taskName).toBe("ai_safety_essay");

    const result = await task.evaluateOutput("AI safety is important because…", state);
    expect(result.score).toBeCloseTo(0.72, 1);
    expect(result.reasoning).toContain("test reasoning");
    expect(result.dimensionScores).toHaveProperty("accuracy");
  });

  it("improvement loop improves score across rounds", async () => {
    const provider = makeMockProvider({
      judgeScores: [0.4, 0.6, 0.85],
      generationText: "Revised output",
    });

    const task = new SimpleAgentTask(
      "Summarize quantum computing in 100 words.",
      "Evaluate accuracy and conciseness.",
      provider,
      "mock-model",
    );

    const loop = new ImprovementLoop({
      task,
      maxRounds: 5,
      qualityThreshold: 0.9,
    });

    const result = await loop.run({
      initialOutput: "Quantum computing uses qubits.",
      state: {},
    });

    expect(result.rounds.length).toBeGreaterThanOrEqual(3);
    expect(result.bestScore).toBeCloseTo(0.85, 1);
    // Scores should be non-decreasing among valid rounds
    const validScores = result.rounds
      .filter((r) => !r.judgeFailed)
      .map((r) => r.score);
    for (let i = 1; i < validScores.length; i++) {
      expect(validScores[i]).toBeGreaterThanOrEqual(validScores[i - 1]);
    }
  });

  it("RLM can bootstrap and revise outputs in the improvement surface", async () => {
    const provider: LLMProvider = {
      name: "rlm-e2e",
      defaultModel: () => "mock-model",
      complete: async (args) => {
        if (args.systemPrompt.includes("expert judge")) {
          return {
            text: makeJudgeResponse(0.95),
            model: "mock-model",
            usage: {},
          };
        }
        if (args.systemPrompt.includes("REPL-loop mode")) {
          if (args.userPrompt.includes("Current output:")) {
            return {
              text: '<code>answer.ready = true;\nanswer.content = "RLM revised answer";</code>',
              model: "mock-model",
              usage: {},
            };
          }
          return {
            text: '<code>answer.ready = true;\nanswer.content = "RLM initial answer";</code>',
            model: "mock-model",
            usage: {},
          };
        }
        return {
          text: "fallback output",
          model: "mock-model",
          usage: {},
        };
      },
    };

    const task = new SimpleAgentTask(
      "Explain why testing matters.",
      "Evaluate clarity and correctness.",
      provider,
      "mock-model",
      undefined,
      { enabled: true, maxTurns: 2 },
    );

    const initialOutput = await task.generateOutput();
    expect(initialOutput).toBe("RLM initial answer");

    const loop = new ImprovementLoop({
      task,
      maxRounds: 2,
      qualityThreshold: 0.9,
    });

    const result = await loop.run({
      initialOutput,
      state: {},
    });

    expect(result.bestOutput).toBe("RLM initial answer");
    expect(result.metThreshold).toBe(true);
    const sessions = task.getRlmSessions();
    expect(sessions.map((session) => session.phase)).toEqual(["generate"]);
  });

  it("full pipeline: create → queue → run → export", async () => {
    const provider = makeMockProvider({
      judgeScores: [0.92],
      generationText: "Excellent summary of distributed systems.",
    });

    // 1. Enqueue
    const taskId = enqueueTask(store, "distributed_systems", {
      taskPrompt: "Write about distributed consensus algorithms.",
      rubric: "Evaluate correctness and depth.",
      maxRounds: 2,
      qualityThreshold: 0.9,
    });
    expect(store.pendingTaskCount()).toBe(1);

    // 2. Run via TaskRunner
    const runner = new TaskRunner({ store, provider, model: "mock-model" });
    const completed = await runner.runOnce();

    expect(completed).not.toBeNull();
    expect(completed!.status).toBe("completed");
    expect(completed!.best_score).toBeGreaterThanOrEqual(0.9);
    expect(completed!.met_threshold).toBe(1); // SQLite stores as 1/0
    expect(store.pendingTaskCount()).toBe(0);

    // 3. Export as skill
    const skill = exportAgentTaskSkill({
      scenarioName: "distributed_systems",
      taskPrompt: "Write about distributed consensus algorithms.",
      judgeRubric: "Evaluate correctness and depth.",
      outputFormat: "free_text",
      playbook: "Focus on Raft and Paxos.",
      lessons: ["Clarity matters.", "Include examples."],
      bestOutputs: [
        {
          output: completed!.best_output!,
          score: completed!.best_score!,
          reasoning: "Solid coverage.",
        },
      ],
    });

    expect(skill.scenarioName).toBe("distributed_systems");
    expect(skill.bestScore).toBeGreaterThanOrEqual(0.9);
    const md = skill.toSkillMarkdown();
    expect(md).toContain("distributed consensus");
    expect(md).toContain("Clarity matters.");
  });

  it("human feedback calibrates future evaluations", () => {
    // Store feedback
    store.insertHumanFeedback(
      "code_review",
      "def foo(): pass",
      0.3,
      "Too trivial, no error handling",
    );
    store.insertHumanFeedback(
      "code_review",
      "def foo():\n  try:\n    ...\n  except Exception as e:\n    log(e)",
      0.8,
      "Good error handling pattern",
    );
    store.insertHumanFeedback("code_review", "print('hello')", 0.1, "Not a function");

    // Retrieve calibration examples (most recently inserted first by rowid/created_at)
    const examples = store.getCalibrationExamples("code_review", 5);
    expect(examples.length).toBe(3);
    // All scores present
    const scores = examples.map((e) => e.human_score).sort();
    expect(scores).toEqual([0.1, 0.3, 0.8]);

    // Verify they can be fed as calibration
    const calibration = examples.map((e) => ({
      agent_output: e.agent_output,
      human_score: e.human_score,
      human_notes: e.human_notes,
    }));
    expect(calibration).toHaveLength(3);
    expect(calibration[0]).toHaveProperty("human_score");
  });
});

// ---------------------------------------------------------------------------
// MCP Tool Flow E2E (testing underlying functions, not transport)
// ---------------------------------------------------------------------------

describe("MCP Tool Flow E2E", () => {
  let tmpDir: string;
  let store: SQLiteStore;

  beforeEach(() => {
    const s = createTempStore();
    tmpDir = s.tmpDir;
    store = s.store;
  });

  afterEach(() => {
    store.close();
    rmSync(tmpDir, { recursive: true, force: true });
  });

  it("evaluate → improve → export flow", async () => {
    const provider = makeMockProvider({
      judgeScores: [0.55, 0.78, 0.91],
      generationText: "Improved version",
    });

    // Step 1: One-shot evaluate (mirrors evaluate_output MCP tool)
    const judge = new LLMJudge({
      provider,
      model: "mock-model",
      rubric: "Evaluate quality on 0–1 scale.",
    });
    const evalResult = await judge.evaluate({
      taskPrompt: "Write about microservices.",
      agentOutput: "Microservices are small services.",
    });
    expect(evalResult.score).toBeCloseTo(0.55, 1);

    // Step 2: Run improvement loop (mirrors run_improvement_loop MCP tool)
    const task = new SimpleAgentTask(
      "Write about microservices.",
      "Evaluate quality on 0–1 scale.",
      provider,
      "mock-model",
    );
    const loop = new ImprovementLoop({
      task,
      maxRounds: 5,
      qualityThreshold: 0.9,
    });
    const loopResult = await loop.run({
      initialOutput: "Microservices are small services.",
      state: {},
    });
    expect(loopResult.metThreshold).toBe(true);
    expect(loopResult.bestScore).toBeGreaterThanOrEqual(0.9);

    // Step 3: Queue task (mirrors queue_task MCP tool)
    const taskId = enqueueTask(store, "microservices_essay", {
      taskPrompt: "Write about microservices.",
      rubric: "Evaluate quality.",
      initialOutput: loopResult.bestOutput,
      maxRounds: 1,
      qualityThreshold: 0.85,
    });
    const queued = store.getTask(taskId);
    expect(queued).not.toBeNull();
    expect(queued!.status).toBe("pending");

    // Step 4: Export
    const skill = exportAgentTaskSkill({
      scenarioName: "microservices",
      taskPrompt: "Write about microservices.",
      judgeRubric: "Evaluate quality.",
      outputFormat: "free_text",
      playbook: "Cover decomposition, communication, and deployment.",
      lessons: ["Keep services focused.", "Use async messaging."],
      bestOutputs: [
        {
          output: loopResult.bestOutput,
          score: loopResult.bestScore,
          reasoning: "Good coverage.",
        },
      ],
    });
    const dict = skill.toDict();
    expect(dict.scenario_name).toBe("microservices");
    expect(dict.task_prompt).toBe("Write about microservices.");
    expect(dict.best_score).toBeGreaterThanOrEqual(0.9);
  });
});

// ---------------------------------------------------------------------------
// Agent Runtime E2E
// ---------------------------------------------------------------------------

describe("Agent Runtime E2E", () => {
  it("generates and revises with mock provider", async () => {
    const provider = makeMockProvider({ generationText: "Initial draft" });
    const runtime = new DirectAPIRuntime(provider, "mock-model");

    // Generate
    const gen = await runtime.generate({
      prompt: "Explain event sourcing.",
    });
    expect(gen.text).toContain("Initial draft");

    // Revise
    const rev = await runtime.revise({
      prompt: "Explain event sourcing.",
      previousOutput: gen.text,
      feedback: "Add more examples and clarify terminology.",
    });
    expect(rev.text).toContain("Initial draft");
    // Revision should be a distinct call
    expect(provider.callCount).toBe(2);
  });

  it("runtime output feeds into improvement loop", async () => {
    const provider = makeMockProvider({
      judgeScores: [0.5, 0.88],
      generationText: "Runtime output about event sourcing",
    });
    const runtime = new DirectAPIRuntime(provider, "mock-model");

    // Generate initial output via runtime
    const gen = await runtime.generate({
      prompt: "Explain event sourcing patterns.",
    });

    // Feed into improvement loop
    const task = new SimpleAgentTask(
      "Explain event sourcing patterns.",
      "Evaluate depth and accuracy.",
      provider,
      "mock-model",
    );
    const loop = new ImprovementLoop({
      task,
      maxRounds: 3,
      qualityThreshold: 0.85,
    });
    const result = await loop.run({
      initialOutput: gen.text,
      state: {},
    });

    expect(result.rounds.length).toBeGreaterThanOrEqual(2);
    expect(result.bestScore).toBeGreaterThanOrEqual(0.85);
    // First round used runtime output
    expect(result.rounds[0].output).toContain("Runtime output");
  });

  it("JudgeExecutor handles context validation", async () => {
    const provider = makeMockProvider({ judgeScores: [0.75] });

    const spec: AgentTaskSpec = {
      taskPrompt: "Analyze data.",
      judgeRubric: "Check completeness.",
      outputFormat: "free_text",
      judgeModel: "mock-model",
      maxRounds: 1,
      qualityThreshold: 0.9,
      requiredContextKeys: ["dataset"],
    };

    const task = createAgentTask({ spec, name: "data_analysis", provider });
    const executor = new JudgeExecutor(task);

    // Missing required context key → score 0
    const failResult = await executor.execute("Analysis results...", {});
    expect(failResult.score).toBe(0);
    expect(failResult.reasoning).toContain("Context validation failed");

    // With required key → normal evaluation
    const okResult = await executor.execute("Analysis results...", {
      dataset: "test_data.csv",
    });
    expect(okResult.score).toBeCloseTo(0.75, 1);
  });
});
