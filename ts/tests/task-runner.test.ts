import { describe, it, expect, beforeEach } from "vitest";
import { mkdtempSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { SQLiteStore } from "../src/storage/index.js";
import { TaskRunner, SimpleAgentTask, enqueueTask } from "../src/execution/task-runner.js";
import type { LLMProvider, CompletionResult } from "../src/types/index.js";

const MIGRATIONS_DIR = join(import.meta.dirname, "..", "migrations");

function createStore(): SQLiteStore {
  const dir = mkdtempSync(join(tmpdir(), "autocontext-runner-"));
  const store = new SQLiteStore(join(dir, "test.db"));
  store.migrate(MIGRATIONS_DIR);
  return store;
}

function makeMockProvider(response = "mock output"): LLMProvider {
  let calls = 0;
  return {
    name: "mock",
    defaultModel: () => "mock",
    complete: async (opts) => {
      calls++;
      // If it's a judge call, return structured response
      if (opts.systemPrompt.includes("judge")) {
        return {
          text: `<!-- JUDGE_RESULT_START -->\n{"score": 0.9, "reasoning": "Good", "dimensions": {"quality": 0.9}}\n<!-- JUDGE_RESULT_END -->`,
          usage: {},
        };
      }
      return { text: response, usage: {} };
    },
  };
}

function makeRlmProvider(opts?: {
  draft?: string;
  revision?: string;
  judgeScore?: number;
}): LLMProvider {
  const draft = opts?.draft ?? "RLM draft output";
  const revision = opts?.revision ?? "RLM revised output";
  const judgeScore = opts?.judgeScore ?? 0.9;

  return {
    name: "rlm-mock",
    defaultModel: () => "mock",
    complete: async (prompt) => {
      if (prompt.systemPrompt.includes("expert judge")) {
        return {
          text:
            "<!-- JUDGE_RESULT_START -->\n" +
            JSON.stringify({
              score: judgeScore,
              reasoning: "Judge approved",
              dimensions: { quality: judgeScore },
            }) +
            "\n<!-- JUDGE_RESULT_END -->",
          usage: {},
        };
      }

      if (prompt.systemPrompt.includes("REPL-loop mode")) {
        if (prompt.userPrompt.includes("Current output:")) {
          return {
            text: `<code>answer.ready = true;\nanswer.content = ${JSON.stringify(revision)};</code>`,
            usage: {},
          };
        }
        return {
          text: `<code>answer.ready = true;\nanswer.content = ${JSON.stringify(draft)};</code>`,
          usage: {},
        };
      }

      return { text: "fallback output", usage: {} };
    },
  };
}

describe("enqueueTask", () => {
  it("creates task with UUID", () => {
    const store = createStore();
    const id = enqueueTask(store, "test-spec", { taskPrompt: "Do something" });
    expect(id).toMatch(/^[0-9a-f-]{36}$/);
    expect(store.pendingTaskCount()).toBe(1);
  });

  it("sets priority", () => {
    const store = createStore();
    enqueueTask(store, "low", { priority: 1 });
    enqueueTask(store, "high", { priority: 10 });
    const task = store.dequeueTask();
    expect(task!.spec_name).toBe("high");
  });
});

describe("TaskRunner", () => {
  it("processes a task end-to-end", async () => {
    const store = createStore();
    enqueueTask(store, "test-spec", {
      taskPrompt: "Write a greeting",
      rubric: "Be friendly",
      initialOutput: "Hello!",
    });

    const runner = new TaskRunner({
      store,
      provider: makeMockProvider(),
    });

    const result = await runner.runOnce();
    expect(result).not.toBeNull();
    expect(result!.status).toBe("completed");
    expect(result!.best_score).toBe(0.9);
    expect(runner.tasksProcessed).toBe(1);
  });

  it("returns null on empty queue", async () => {
    const store = createStore();
    const runner = new TaskRunner({ store, provider: makeMockProvider() });
    expect(await runner.runOnce()).toBeNull();
  });

  it("handles provider errors gracefully", async () => {
    const store = createStore();
    enqueueTask(store, "fail-spec", { initialOutput: "test" });

    const failProvider: LLMProvider = {
      name: "fail",
      defaultModel: () => "m",
      complete: async () => {
        throw new Error("API down");
      },
    };

    const runner = new TaskRunner({ store, provider: failProvider });
    const result = await runner.runOnce();
    expect(result).not.toBeNull();
    expect(result!.status).toBe("failed");
    expect(result!.error).toContain("API down");
    // Verify no stack trace in error (only message stored)
    expect(result!.error).not.toContain("\n    at ");
  });

  it("rejects invalid config via Zod validation", async () => {
    const store = createStore();
    const provider = makeMockProvider();
    // max_rounds must be a positive integer, not a string
    store.enqueueTask("bad", "test_spec", 0, { max_rounds: "not_a_number" });
    const runner = new TaskRunner({ store, provider });
    const result = await runner.runOnce();
    expect(result!.status).toBe("failed");
    expect(result!.error).toContain("Expected number");
  });

  it("includes duration_ms in completed task result", async () => {
    const store = createStore();
    enqueueTask(store, "timing-spec", {
      taskPrompt: "Write a poem",
      rubric: "Be creative",
      initialOutput: "Roses are red",
    });

    const runner = new TaskRunner({
      store,
      provider: makeMockProvider(),
    });

    const result = await runner.runOnce();
    expect(result).not.toBeNull();
    expect(result!.status).toBe("completed");
    expect(result!.result_json).toBeDefined();
    const parsed = JSON.parse(result!.result_json!);
    expect(parsed.duration_ms).toBeTypeOf("number");
    expect(parsed.duration_ms).toBeGreaterThanOrEqual(0);
  });

  it("processes delegated evaluations without requiring the provider to judge", async () => {
    const store = createStore();
    enqueueTask(store, "delegated-spec", {
      taskPrompt: "Write a greeting",
      rubric: "Be friendly",
      initialOutput: "Hello there",
      maxRounds: 1,
      delegatedResults: [
        {
          score: 0.87,
          reasoning: "Delegated externally",
          dimensionScores: { friendliness: 0.87 },
        },
      ],
    });

    const failJudgeProvider: LLMProvider = {
      name: "fail-judge",
      defaultModel: () => "mock",
      complete: async () => {
        throw new Error("provider judging should not be called");
      },
    };

    const runner = new TaskRunner({
      store,
      provider: failJudgeProvider,
    });

    const result = await runner.runOnce();
    expect(result).not.toBeNull();
    expect(result!.status).toBe("completed");
    expect(result!.best_score).toBe(0.87);
    expect(result!.best_output).toBe("Hello there");
  });

  it("uses RLM to bootstrap initial output and persists session traces", async () => {
    const store = createStore();
    enqueueTask(store, "rlm-spec", {
      taskPrompt: "Write a greeting",
      rubric: "Be friendly",
      rlmEnabled: true,
      rlmMaxTurns: 2,
    });

    const runner = new TaskRunner({
      store,
      provider: makeRlmProvider({ draft: "Hello from RLM" }),
    });

    const result = await runner.runOnce();
    expect(result).not.toBeNull();
    expect(result!.status).toBe("completed");
    expect(result!.best_output).toBe("Hello from RLM");

    const parsed = JSON.parse(result!.result_json!);
    expect(parsed.rlm_sessions.length).toBeGreaterThanOrEqual(1);
    expect(parsed.rlm_sessions[0].phase).toBe("generate");
    expect(parsed.rlm_sessions[0].content).toBe("Hello from RLM");
  });
});

describe("TaskRunner.runBatch", () => {
  it("processes multiple tasks concurrently", async () => {
    const store = createStore();
    enqueueTask(store, "spec-1", {
      taskPrompt: "Task 1",
      rubric: "Be good",
      initialOutput: "Output 1",
    });
    enqueueTask(store, "spec-2", {
      taskPrompt: "Task 2",
      rubric: "Be good",
      initialOutput: "Output 2",
    });
    enqueueTask(store, "spec-3", {
      taskPrompt: "Task 3",
      rubric: "Be good",
      initialOutput: "Output 3",
    });

    const runner = new TaskRunner({
      store,
      provider: makeMockProvider(),
      concurrency: 3,
    });

    const count = await runner.runBatch();
    expect(count).toBe(3);
    expect(runner.tasksProcessed).toBe(3);
    expect(store.pendingTaskCount()).toBe(0);
  });

  it("returns 0 on empty queue", async () => {
    const store = createStore();
    const runner = new TaskRunner({
      store,
      provider: makeMockProvider(),
      concurrency: 2,
    });
    expect(await runner.runBatch()).toBe(0);
  });

  it("respects limit parameter", async () => {
    const store = createStore();
    enqueueTask(store, "s1", { initialOutput: "o", taskPrompt: "t", rubric: "r" });
    enqueueTask(store, "s2", { initialOutput: "o", taskPrompt: "t", rubric: "r" });
    enqueueTask(store, "s3", { initialOutput: "o", taskPrompt: "t", rubric: "r" });

    const runner = new TaskRunner({
      store,
      provider: makeMockProvider(),
      concurrency: 10,
    });

    const count = await runner.runBatch(2);
    expect(count).toBe(2);
    expect(store.pendingTaskCount()).toBe(1);
  });
});

describe("minRounds wiring (AC-53)", () => {
  it("enqueueTask passes minRounds to config", () => {
    const store = createStore();
    const id = enqueueTask(store, "test", { minRounds: 3 });
    const task = store.getTask(id);
    expect(task).not.toBeNull();
    const config = JSON.parse(task!.config_json!);
    expect(config.min_rounds).toBe(3);
    store.close();
  });

  it("enqueueTask defaults to no min_rounds in config when not specified", () => {
    const store = createStore();
    const id = enqueueTask(store, "test", { taskPrompt: "hello" });
    const task = store.getTask(id);
    const config = JSON.parse(task!.config_json!);
    // min_rounds should not be in config when not explicitly set
    expect(config.min_rounds).toBeUndefined();
    store.close();
  });
});

describe("SimpleAgentTask", () => {
  it("generates and revises output", async () => {
    const provider = makeMockProvider("generated text");
    const task = new SimpleAgentTask("Write something", "Be good", provider);
    const output = await task.generateOutput();
    expect(output).toBe("generated text");

    const revised = await task.reviseOutput(
      output,
      { score: 0.5, reasoning: "Needs work", dimensionScores: {} },
      {},
    );
    expect(revised).toBe("generated text"); // Mock returns same for non-judge calls
  });

  it("can revise through RLM mode", async () => {
    const task = new SimpleAgentTask(
      "Write something",
      "Be good",
      makeRlmProvider({ revision: "RLM fixed draft" }),
      "mock-model",
      undefined,
      { enabled: true, maxTurns: 2 },
    );

    await task.evaluateOutput("Original draft", {}, {
      referenceContext: "Trusted facts",
      requiredConcepts: ["clarity"],
    });

    const revised = await task.reviseOutput(
      "Original draft",
      { score: 0.4, reasoning: "Needs work", dimensionScores: { quality: 0.4 } },
      {},
    );

    expect(revised).toBe("RLM fixed draft");
    expect(task.getRlmSessions()).toHaveLength(1);
    expect(task.getRlmSessions()[0].phase).toBe("revise");
  });
});
